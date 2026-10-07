"""Durable outbox delivery via logical PostgreSQL foreign partitions.

Every iteration uses three separate transactions: lease a local message,
deliver it to its owner, then acknowledge locally. No transaction combines
a remote business write and a local outbox acknowledgement.
"""

import logging
import os
import time
import psycopg
from psycopg.types.json import Jsonb
from app.db import Database

LOG = logging.getLogger("matcher.worker")


class OutboxWorker:
    """At-least-once delivery with atomic owner receipts and bounded retries.

    :param database: Factory bound to the worker's one local DSN.
    :param region: This process's logical source region.
    """

    def __init__(self, database: Database, region: int):
        self.database, self.region = database, region

    def take(self) -> dict | None:
        """Lease one due message with SKIP LOCKED; a crash expires the lease."""
        with self.database.connect() as conn:
            row = conn.execute("""SELECT *,private.command_hash(payload) AS payload_hash
                FROM private.outbox WHERE state='PENDING' AND next_attempt_at<=clock_timestamp()
                ORDER BY created_at,message_id LIMIT 1 FOR UPDATE SKIP LOCKED""").fetchone()
            if row:
                conn.execute("UPDATE private.outbox SET next_attempt_at=clock_timestamp()+interval '30 seconds',attempts=attempts+1 WHERE message_id=%s", (row["message_id"],))
            return row

    def deliver(self, row: dict) -> dict:
        """Deliver or read an existing identical receipt after a lost response.

        :param row: A leased outbox message, including canonical payload hash.
        :return: The owner's committed business result.
        :raises ValueError: A reused command ID with different contents.
        """
        key = (row["target_region"], row["command_id"])
        def receipt(conn):
            return conn.execute("SELECT source_region,kind,payload_hash,result FROM global.processed_commands WHERE owner_region=%s AND command_id=%s", key).fetchone()
        try:
            with self.database.connect() as conn:
                existing = receipt(conn)
                if existing is None:
                    conn.execute("""INSERT INTO global.processed_commands
                        (owner_region,command_id,source_region,actor_id,kind,payload_hash,payload)
                        VALUES(%s,%s,%s,%s,%s,%s,%s)""", (row["target_region"], row["command_id"], self.region, row["actor_id"], row["kind"], row["payload_hash"], Jsonb(row["payload"])))
                    existing = receipt(conn)
        except psycopg.errors.UniqueViolation:
            # A competing retry committed first. Read in a fresh transaction.
            with self.database.connect() as conn:
                existing = receipt(conn)
        if not existing or existing["source_region"] != self.region or existing["kind"] != row["kind"] or existing["payload_hash"] != row["payload_hash"]:
            raise ValueError("command_conflict")
        return existing["result"]

    def run_once(self) -> bool:
        """Deliver one message; persist retry timing without exposing payloads."""
        row = self.take()
        if not row:
            return False
        try:
            self.deliver(row)
        except (psycopg.Error, ValueError) as exc:
            code = "command_conflict" if isinstance(exc, ValueError) else (exc.sqlstate or "owner_unavailable")
            with self.database.connect() as conn:
                conn.execute("""UPDATE private.outbox SET last_error=%s,
                    next_attempt_at=clock_timestamp()+(%s * interval '1 second') WHERE message_id=%s""",
                    (code, min(60, 2 ** min(row["attempts"], 6)), row["message_id"]))
            LOG.warning("Delivery deferred; kind=%s, reason=%s", row["kind"], code)
        else:
            with self.database.connect() as conn:
                conn.execute("UPDATE private.outbox SET state='DELIVERED',delivered_at=clock_timestamp(),last_error=NULL WHERE message_id=%s", (row["message_id"],))
        return True


def main() -> None:
    """Continuously drain this node's queue; shutdown leaves leases recoverable."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    worker = OutboxWorker(Database(os.environ["DATABASE_URL"]), int(os.environ["NODE_REGION"]))
    while True:
        try:
            worked = worker.run_once()
            if not worked:
                time.sleep(0.75)
        except psycopg.Error as exc:
            LOG.warning("Local database unavailable; SQLSTATE=%s", exc.sqlstate)
            time.sleep(3)


if __name__ == "__main__":
    main()

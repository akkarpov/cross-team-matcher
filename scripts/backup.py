"""Coordinated local backups and non-destructive restore drills.

A restore drill creates new databases. It never overwrites a running owner or
promotes an asynchronous replica. Runtime archives contain private information.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time

import psycopg
from psycopg import sql

from scripts.local import RUNTIME, NO_WINDOW, dsn, pg_bin, applications_stop


def snapshot(config: dict, region: int, database: str = "matcher") -> dict:
    result = {}
    with psycopg.connect(dsn(config, region, database=database)) as conn:
        conn.execute("SET TIME ZONE 'UTC'")
        tables = conn.execute("SELECT schemaname,tablename FROM pg_tables WHERE schemaname IN ('cat','private','r1_data','r2_data') ORDER BY 1,2").fetchall()
        for schema, table in tables:
            rows = conn.execute(sql.SQL("SELECT to_jsonb(t)::text FROM {}.{} t ORDER BY to_jsonb(t)::text").format(sql.Identifier(schema), sql.Identifier(table))).fetchall()
            result[f"{schema}.{table}"] = {"count": len(rows), "sha256": hashlib.sha256("\n".join(row[0] for row in rows).encode()).hexdigest()}
    return result


def wait_consistent(config: dict, timeout: float = 30) -> None:
    """Compare published row sets while writers are stopped, not just lag clocks."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        states = [snapshot(config, region) for region in range(3)]
        matches = True
        for source in range(3):
            prefix = "cat." if source == 0 else f"r{source}_data."
            for key, value in states[source].items():
                if key.startswith(prefix) and not key.endswith("processed_commands"):
                    matches = matches and all(state.get(key) == value for state in states)
        if matches:
            return
        time.sleep(0.5)
    raise TimeoutError("Published table contents have not converged; backup not created")


def backup(config: dict) -> Path:
    applications_stop()
    wait_consistent(config)
    directory = RUNTIME / "backups" / dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    directory.mkdir(parents=True)
    manifest = {"created_utc": dt.datetime.now(dt.timezone.utc).isoformat(), "nodes": {},
                "writers_stopped": True, "format": "pg_dump custom, subscriptions excluded"}
    for region in range(3):
        node = config["nodes"][region]
        archive = directory / f"node-{region}.dump"
        environment = dict(os.environ, PGPASSWORD=config["passwords"]["postgres"])
        subprocess.run([pg_bin(config, "pg_dump"), "-h", node["host"], "-p", str(node["port"]), "-U", "postgres",
                        "-d", "matcher", "--format=custom", "--no-subscriptions", "--file", str(archive)],
                       env=environment, check=True, creationflags=NO_WINDOW)
        manifest["nodes"][str(region)] = {"tables": snapshot(config, region),
                                            "archive_sha256": hashlib.sha256(archive.read_bytes()).hexdigest()}
    (directory / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"Backup complete: {directory}. Applications remain stopped; run local.py start when ready.")
    return directory


def restore(config: dict, location: str | Path | None) -> dict:
    if not location:
        raise ValueError("Pass --backup .local/backups/<timestamp> for a non-destructive restore drill")
    directory = Path(location).resolve()
    if not directory.is_relative_to((RUNTIME / "backups").resolve()):
        raise ValueError("Backup must be inside this workspace .local/backups")
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    suffix = str(int(time.time()))
    report = {"started_utc": dt.datetime.now(dt.timezone.utc).isoformat(), "databases": {}, "success": False}
    started = time.perf_counter()
    for region in range(3):
        archive = directory / f"node-{region}.dump"
        expected = manifest["nodes"][str(region)]
        if hashlib.sha256(archive.read_bytes()).hexdigest() != expected["archive_sha256"]:
            raise ValueError(f"Archive checksum differs: node {region}")
        database = f"matcher_restore_{suffix}_{region}"
        with psycopg.connect(dsn(config, region, database="postgres"), autocommit=True) as conn:
            conn.execute(sql.SQL("CREATE DATABASE {} TEMPLATE template0 STRATEGY FILE_COPY").format(sql.Identifier(database)))
        node = config["nodes"][region]
        environment = dict(os.environ, PGPASSWORD=config["passwords"]["postgres"])
        subprocess.run([pg_bin(config, "pg_restore"), "-h", node["host"], "-p", str(node["port"]), "-U", "postgres",
                        "-d", database, "--exit-on-error", "--single-transaction", "--no-subscriptions", str(archive)],
                       env=environment, check=True, creationflags=NO_WINDOW)
        observed = snapshot(config, region, database)
        if observed != expected["tables"]:
            raise AssertionError(f"Restored node {region} differs from backup manifest")
        with psycopg.connect(dsn(config, region, database="postgres"), autocommit=True) as conn:
            conn.execute(sql.SQL("ALTER DATABASE {} SET default_transaction_read_only=on").format(sql.Identifier(database)))
        report["databases"][str(region)] = {"database": database, "table_count": len(observed), "hashes_equal": True}
    report.update(success=True, elapsed_seconds=round(time.perf_counter() - started, 3))
    (directory / f"restore-{suffix}.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    return report

"""T09–T16: real PostgreSQL tests; explicitly opt in to local outage drills.

Run after setup/seed with applications stopped:
  MATCHER_STAND_TESTS=1 python -m pytest tests/stand -v
No mocks, fabricated timings or automatically promoted replicas are used.
"""
from __future__ import annotations

import concurrent.futures
import datetime as dt
import json
import os
import platform
import statistics
import time
import uuid
from pathlib import Path

import psycopg
from psycopg.types.json import Jsonb
import pytest

from scripts.local import RUNTIME, database_control, dsn, load_config
from scripts.seed import identity

pytestmark = pytest.mark.skipif(os.environ.get("MATCHER_STAND_TESTS") != "1", reason="Opt-in: stops isolated laboratory PostgreSQL nodes")


class Stand:
    def __init__(self):
        self.config = load_config()
        self.created_projects = []
        self.facts = {"started_utc": dt.datetime.now(dt.timezone.utc).isoformat(), "platform": platform.platform(),
                      "network": "localhost loopback; no injected delay", "tests": {}}

    def connection(self, region, role="postgres"):
        # Fixture orchestration opens many short-lived Windows server processes.
        # The latency measurement below still includes this connection time.
        return psycopg.connect(dsn(self.config, region, role), connect_timeout=15)

    def scalar(self, region, query, parameters=(), role="postgres"):
        with self.connection(region, role) as conn:
            return conn.execute(query, parameters).fetchone()[0]

    def mutate(self, region, login, action, payload):
        values = dict(payload)
        values.setdefault("command_id", str(uuid.uuid4()))
        with self.connection(region, "matcher_app") as conn:
            actor = conn.execute("SELECT account_id FROM private.accounts WHERE login=%s", (login,)).fetchone()[0]
            conn.execute("SELECT set_config('app.actor_id',%s,true)", (str(actor),))
            result = conn.execute("SELECT global.mutate(%s,%s)", (action, Jsonb(values))).fetchone()[0]
            assert result.get("ok"), result
            return result

    def workers(self):
        from app.db import Database
        from app.worker import OutboxWorker
        return [OutboxWorker(Database(dsn(self.config, region, "matcher_worker")), region) for region in (1, 2)]

    def drain(self, timeout=45):
        workers = self.workers()
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            progress = False
            for worker in workers:
                try:
                    progress = worker.run_once() or progress
                except psycopg.OperationalError:
                    # Match app.worker.main(): failed local connection is
                    # retried, while a committed remote receipt remains safe.
                    time.sleep(0.2)
            pending = sum(self.scalar(region, "SELECT count(*) FROM private.outbox WHERE state='PENDING'") for region in (1, 2))
            if not pending:
                return
            if not progress:
                time.sleep(0.1)
        raise AssertionError("Outbox did not drain")

    def offer(self, employee_region=2, project_region=1, hours=0.5):
        manager = f"manager.r{project_region}"
        project = self.mutate(project_region, manager, "project.create", {
            "name": "Distributed acceptance " + uuid.uuid4().hex[:8], "date_from": "2027-09-06", "date_to": "2027-09-10"})["project_id"]
        self.created_projects.append((project_region, project))
        position = self.mutate(project_region, manager, "position.create", {
            "project_id": project, "title": "Acceptance fixture", "hours_per_week": hours,
            "min_experience": 0, "competency_ids": [], "search_mode": "OWN_FIRST"})["position_id"]
        invitation = self.mutate(project_region, manager, "invitation.create", {
            "position_id": position, "employee_region": employee_region,
            "employee_id": str(identity(f"employee:{employee_region}:0"))})["invitation_id"]
        return invitation

    def respond(self, invitation, employee_region=2):
        with self.connection(employee_region) as conn:
            version, terms_hash = conn.execute("SELECT terms_version,terms_hash FROM global.invitation_responses WHERE owner_region=%s AND invitation_id=%s", (employee_region, invitation)).fetchone()
        return self.mutate(employee_region, f"employee.r{employee_region}", "invitation.respond", {
            "invitation_id": invitation, "decision": "ACCEPTED", "terms_version": version, "terms_hash": terms_hash})

    def eventually(self, predicate, timeout=10):
        start = time.perf_counter()
        while time.perf_counter() - start < timeout:
            if predicate():
                return round(time.perf_counter() - start, 4)
            time.sleep(0.05)
        raise AssertionError("Expected replicated state was not received in time")

    def record(self, test, **facts):
        self.facts["tests"][test] = {"measured_utc": dt.datetime.now(dt.timezone.utc).isoformat(), **facts}
        directory = RUNTIME / "test-results"
        directory.mkdir(exist_ok=True)
        serialized = json.dumps(self.facts, indent=2, default=str)
        stamp = self.facts["started_utc"].replace(":", "").replace(".", "")
        (directory / f"distributed-{stamp}.json").write_text(serialized, encoding="utf-8")
        (directory / "distributed-facts.json").write_text(serialized, encoding="utf-8")

    def retire(self):
        """Release test allocations through the real workflow, retaining history."""
        for region, project in self.created_projects:
            with self.connection(region) as conn:
                state = conn.execute("SELECT version,status FROM global.projects WHERE owner_region=%s AND project_id=%s", (region, project)).fetchone()
            if state and state[1] in ("FORMING", "READY", "ACTIVE"):
                self.mutate(region, f"manager.r{region}", "project.transition", {
                    "project_id": project, "version": state[0], "status": "CANCELLED"})
        self.drain()


@pytest.fixture(scope="module")
def stand():
    result = Stand()
    result.drain()
    result.facts["nodes"] = {}
    for region in range(3):
        with result.connection(region) as conn:
            from psycopg.rows import dict_row
            with conn.cursor(row_factory=dict_row) as cursor:
                cursor.execute("SELECT slot_name,slot_type,active,confirmed_flush_lsn,restart_lsn,wal_status,pg_wal_lsn_diff(pg_current_wal_lsn(),restart_lsn) AS retained_bytes FROM pg_replication_slots")
                slots = cursor.fetchall()
                cursor.execute("SELECT s.subname,s.subenabled,ss.pid,ss.last_msg_receipt_time,ss.latest_end_lsn FROM pg_subscription s LEFT JOIN pg_stat_subscription ss ON ss.subid=s.oid AND ss.relid IS NULL")
                subscriptions = cursor.fetchall()
            result.facts["nodes"][region] = {"postgres": conn.execute("SELECT version()").fetchone()[0], "slots": slots, "subscriptions": subscriptions}
    yield result
    result.retire()


def test_t09_fdw_routes_plain_sql(stand):
    employee = identity("employee:2:1")
    original = stand.scalar(2, "SELECT nickname FROM global.employees WHERE owner_region=2 AND employee_id=%s", (employee,))
    changed = "route-test-" + uuid.uuid4().hex[:10]
    try:
        with stand.connection(1, "matcher_owner") as conn:
            plan = "\n".join(row[0] for row in conn.execute("EXPLAIN (VERBOSE,COSTS OFF) SELECT employee_id,nickname FROM global.employees WHERE owner_region=2"))
            assert "Foreign Scan" in plan and "Remote SQL" in plan
            conn.execute("UPDATE global.employees SET nickname=%s WHERE owner_region=2 AND employee_id=%s", (changed, employee))
        assert stand.scalar(2, "SELECT nickname FROM global.employees WHERE owner_region=2 AND employee_id=%s", (employee,)) == changed
        stand.record("T09", explain=plan, destination_verified=True, role="matcher_owner technical maintenance role")
    finally:
        with stand.connection(2) as conn:
            conn.execute("UPDATE global.employees SET nickname=%s WHERE owner_region=2 AND employee_id=%s", (original, employee))


def test_t10_replication_and_protected_copies(stand):
    employee = identity("employee:1:1")
    changed = "replica-test-" + uuid.uuid4().hex[:10]
    original = stand.scalar(1, "SELECT nickname FROM global.employees WHERE owner_region=1 AND employee_id=%s", (employee,))
    try:
        stand.mutate(1, "editor.r1", "employee.update", {"employee_id": str(employee), "nickname": changed})
        delay = stand.eventually(lambda: all(stand.scalar(region, "SELECT nickname FROM search.employee_profiles WHERE owner_region=1 AND employee_id=%s", (employee,)) == changed for region in (0, 2)))
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            with stand.connection(2, "matcher_app") as conn:
                conn.execute("UPDATE r1_data.employees SET nickname='forbidden' WHERE employee_id=%s", (employee,))
        invitation = stand.offer()
        stand.drain()
        stand.respond(invitation)
        stand.drain()
        stand.mutate(1, "manager.r1", "assignment.complete", {"invitation_id": invitation})
        stand.drain()
        review = stand.mutate(1, "manager.r1", "review.create", {"invitation_id": invitation, "rating": 4, "comment": "Acceptance review"})["review_id"]
        stand.mutate(1, "manager.r1", "review.update", {"review_id": review, "version": 1, "rating": 5, "comment": "Verified edit propagated"})
        review_delay = stand.eventually(lambda: all(stand.scalar(region, "SELECT count(*) FROM search.reviews WHERE review_id=%s AND version=2 AND rating=5", (review,)) == 1 and stand.scalar(region, "SELECT count(*) FROM search.workflow_events WHERE object_id=%s AND event_type='REVIEW.UPDATE'", (review,)) == 1 for region in (0, 2)))
        stand.record("T10", profile_delivery_seconds=delay, review_event_delivery_seconds=review_delay, replicas_read_only=True)
    finally:
        stand.mutate(1, "editor.r1", "employee.update", {"employee_id": str(employee), "nickname": original})


def test_t11_center_outage_preserves_regional_work(stand):
    database_control(stand.config, "stop", [0])
    try:
        assert stand.scalar(1, "SELECT active FROM private.accounts WHERE login='employee.r1'", role="matcher_app")
        assert stand.scalar(1, "SELECT count(*) FROM search.employee_profiles", role="matcher_app") >= 300
        local_invitation = stand.offer(employee_region=1)
        stand.drain()
        result = stand.respond(local_invitation, employee_region=1)
        stand.drain()
        assert result["assignment_id"]
        remote_invitation = stand.offer()
        stand.drain()
        assert stand.scalar(2, "SELECT count(*) FROM global.invitation_responses WHERE owner_region=2 AND invitation_id=%s", (remote_invitation,)) == 1
        stand.mutate(1, "manager.r1", "assignment.cancel", {"invitation_id": local_invitation})
        stand.mutate(1, "manager.r1", "invitation.cancel", {"invitation_id": remote_invitation})
        stand.drain()
        stand.record("T11", center_stopped=True, local_account_and_profile=True, local_assignment=True, direct_regional_delivery=True)
    finally:
        database_control(stand.config, "start", [0])


def test_t12_owner_outage_retains_copy_and_recovers_queue(stand):
    database_control(stand.config, "stop", [2])
    try:
        # Create while the owner is already down; background workers cannot
        # deliver before the fault is injected.
        invitation = stand.offer()
        assert stand.scalar(1, "SELECT count(*) FROM search.employee_profiles WHERE owner_region=2", role="matcher_app") >= 150
        worker = stand.workers()[0]
        worker.run_once()
        assert stand.scalar(1, "SELECT count(*) FROM private.outbox WHERE state='PENDING'") >= 1
        assert stand.scalar(1, "SELECT status FROM global.invitations WHERE owner_region=1 AND invitation_id=%s", (invitation,)) == "PENDING"
        from app.db import Database
        app_database = Database(dsn(stand.config, 1, "matcher_app"))
        began = time.perf_counter()
        with pytest.raises(psycopg.Error) as unavailable:
            with app_database.connect() as conn:
                conn.execute("SELECT count(*) FROM global.assignments WHERE owner_region=2").fetchone()
        unavailable_owner_seconds = time.perf_counter() - began
        assert unavailable_owner_seconds <= 10, "Unavailable owner must be reported within 10 seconds"
    finally:
        database_control(stand.config, "start", [2])
    stand.drain()
    assert stand.scalar(2, "SELECT decision FROM global.invitation_responses WHERE owner_region=2 AND invitation_id=%s", (invitation,)) == "PENDING"
    stand.mutate(1, "manager.r1", "invitation.cancel", {"invitation_id": invitation})
    stand.drain()
    stand.record("T12", stale_search_available=True, no_unconfirmed_assignment=True,
                 queued_delivery_recovered=True, unavailable_owner_seconds=unavailable_owner_seconds,
                 sqlstate=unavailable.value.sqlstate, connection_timeout_seconds=3,
                 statement_timeout_seconds=8, unavailable_owner_target_seconds=10)


def test_t13_lost_reply_does_not_repeat_business_effects(stand):
    invitation = stand.offer()
    worker = stand.workers()[0]
    message = worker.take()
    assert message is not None
    first = worker.deliver(message)
    event_count = stand.scalar(2, "SELECT count(*) FROM global.workflow_events WHERE owner_region=2")
    second = worker.deliver(message)  # The first response was lost before local ACK.
    assert first == second
    assert stand.scalar(2, "SELECT count(*) FROM global.workflow_events WHERE owner_region=2") == event_count
    assert stand.scalar(2, "SELECT count(*) FROM global.invitation_responses WHERE owner_region=2 AND invitation_id=%s", (invitation,)) == 1
    with stand.connection(1) as conn:
        conn.execute("UPDATE private.outbox SET next_attempt_at=clock_timestamp() WHERE message_id=%s", (message["message_id"],))
    stand.drain()
    response = stand.respond(invitation)
    stand.drain()
    assert stand.scalar(2, "SELECT count(*) FROM global.assignments WHERE owner_region=2 AND invitation_id=%s", (invitation,)) == 1
    stand.mutate(1, "manager.r1", "assignment.cancel", {"invitation_id": invitation})
    stand.drain()
    stand.record("T13", repeated_command=str(message["command_id"]), equal_receipts=True, duplicate_events=0, assignment_id=response["assignment_id"])


def test_t14_migrations_restart_and_replica_triggers(stand):
    from replication.bootstrap import setup, wait_ready
    setup(stand.config)
    before = stand.scalar(2, "SELECT count(*) FROM private.outbox")
    database_control(stand.config, "stop", [2])
    database_control(stand.config, "start", [2])
    wait_ready(stand.config)
    assert stand.scalar(2, "SELECT count(*) FROM private.outbox") == before
    for region in range(3):
        assert stand.scalar(region, "SELECT count(*) FROM pg_subscription_rel WHERE srsubstate<>'r'") == 0
        replica = "r2_data" if region == 1 else "r1_data"
        assert stand.scalar(region, "SELECT count(*) FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=%s AND NOT t.tgisinternal", (replica,)) == 0
    stand.record("T14", setup_reapplied=True, subscriber_restarted=True, all_tables_ready=True, replica_business_triggers=0)


def test_t15_backup_restores_every_local_table(stand):
    from scripts.backup import backup, restore
    stand.drain()
    directory = backup(stand.config)
    result = restore(stand.config, directory)
    assert result["success"]
    stand.record("T15", **result)


def test_t16_measured_search_performance(stand):
    def measured(_):
        start = time.perf_counter()
        with stand.connection(1, "matcher_app") as conn:
            conn.execute("SELECT set_config('app.actor_id',%s,true)", (str(identity("account:manager.r1")),))
            result = conn.execute("SELECT search.find_candidates('2026-11-02'::date,'2026-11-27'::date,10::numeric,ARRAY[]::uuid[],0,'OWN_FIRST',1,'')").fetchone()[0]
            assert result["total"] > 0
        return time.perf_counter() - start
    for _ in range(10):
        measured(_)
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        timings = list(executor.map(measured, range(100)))
    sorted_timings = sorted(timings)
    p95 = sorted_timings[94]
    import psutil
    facts = {"warmups": 10, "measurements": 100, "concurrent_users": 5,
             "p50_seconds": statistics.median(timings), "p95_seconds": p95, "max_seconds": max(timings),
             "samples_seconds": timings, "employees": stand.scalar(1, "SELECT count(*) FROM search.employee_profiles"),
             "competencies": stand.scalar(1, "SELECT count(*) FROM cat.competencies"),
             "cpu_logical": psutil.cpu_count(), "memory_bytes": psutil.virtual_memory().total,
             "postgres": stand.scalar(1, "SELECT version()"), "target_met": p95 <= 3,
             "period": "2026-11-02..2026-11-27", "requirements": "10h/week, no competencies, OWN_FIRST"}
    stand.record("T16", **facts)
    assert p95 <= 3, f"Measured p95={p95:.3f}s exceeds the 3s target; see real samples in distributed-facts.json"

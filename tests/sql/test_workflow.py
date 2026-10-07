"""Live SQL-contract acceptance tests against the isolated three-node stand.

Run: ``MATCHER_SQL_TESTS=1 python -m pytest tests/sql -q`` after local setup.
Only test-prefixed accounts and fresh UUID business objects are created. The
suite does not delete business history or alter existing demo assignments.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import date
import os
import time
from uuid import UUID, uuid4

import psycopg
from psycopg.types.json import Jsonb
import pytest

from scripts.local import dsn, load_config

pytestmark = pytest.mark.skipif(os.environ.get("MATCHER_SQL_TESTS") != "1", reason="requires the isolated live PostgreSQL stand")


def wait_for(check, timeout=15):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = check()
        if result:
            return result
        time.sleep(0.1)
    raise AssertionError("replicated state did not arrive before deadline")


class Stand:
    def __init__(self):
        self.config = load_config()
        self.actors = {}
        self.tag = uuid4().hex[:10]
        for region in (1, 2):
            for role in ("EDITOR", "MANAGER"):
                actor = uuid4()
                with self.connect(region, "postgres") as conn:
                    conn.execute("INSERT INTO private.accounts(account_id,login,password_hash) VALUES(%s,%s,'test-not-a-login-hash')", (actor, f"test.{self.tag}.{role}.{region}"))
                    conn.execute("INSERT INTO private.account_roles(account_id,role_code) VALUES(%s,%s)", (actor, role))
                self.actors[region, role] = actor

    def connect(self, region, role="matcher_app"):
        # This suite validates business invariants while other laboratory tools
        # may be restoring databases. Connection SLA is measured by tests/stand.
        return psycopg.connect(dsn(self.config, region, role), connect_timeout=15)

    def mutate(self, region, actor, action, payload, isolation=None):
        body = {**payload}
        body.setdefault("command_id", str(uuid4()))
        with self.connect(region) as conn:
            if isolation:
                conn.execute(f"SET TRANSACTION ISOLATION LEVEL {isolation}")
            conn.execute("SELECT set_config('app.actor_id',%s,true)", (str(actor),))
            return conn.execute("SELECT global.mutate(%s,%s)", (action, Jsonb(body))).fetchone()[0]

    def query(self, region, sql, params=(), actor=None):
        with self.connect(region, "matcher_app" if actor else "postgres") as conn:
            if actor:
                conn.execute("SELECT set_config('app.actor_id',%s,true)", (str(actor),))
            return conn.execute(sql, params).fetchall()

    def deliver(self, limit=100):
        """Use the specified independent delivery/ack transactions, no routing."""
        for _ in range(limit):
            progressed = False
            for region in (1, 2):
                with self.connect(region, "matcher_worker") as conn:
                    messages = conn.execute("SELECT message_id,target_region,command_id,kind,payload,actor_id FROM private.outbox WHERE state='PENDING' ORDER BY created_at LIMIT 20").fetchall()
                for message_id, target, command, kind, payload, actor in messages:
                    # A duplicate is resolved by a new transaction reading the
                    # owner's permanent receipt, including payload verification.
                    try:
                        with self.connect(region, "matcher_worker") as conn:
                            conn.execute("INSERT INTO global.processed_commands(owner_region,command_id,source_region,actor_id,kind,payload_hash,payload) VALUES(%s,%s,%s,%s,%s,private.command_hash(%s),%s)", (target, command, region, actor, kind, Jsonb(payload), Jsonb(payload)))
                    except psycopg.errors.UniqueViolation:
                        with self.connect(region, "matcher_worker") as conn:
                            receipt = conn.execute("SELECT source_region,kind,payload_hash=private.command_hash(%s),result FROM global.processed_commands WHERE owner_region=%s AND command_id=%s", (Jsonb(payload), target, command)).fetchone()
                        assert receipt and receipt[:3] == (region, kind, True) and receipt[3] is not None
                    with self.connect(region, "matcher_worker") as conn:
                        conn.execute("UPDATE private.outbox SET state='DELIVERED',delivered_at=clock_timestamp(),attempts=attempts+1 WHERE message_id=%s", (message_id,))
                    progressed = True
            if not progressed:
                return
        raise AssertionError("outbox did not converge")

    def employee(self, region=2, hours=40):
        employee = self.mutate(region, self.actors[region, "EDITOR"], "employee.create", {"nickname": f"test.{self.tag}.{uuid4().hex[:6]}", "experience_months": 60})["employee_id"]
        actor = uuid4()
        with self.connect(region, "postgres") as conn:
            conn.execute("INSERT INTO private.accounts(account_id,employee_id,login,password_hash) VALUES(%s,%s,%s,'test-not-a-login-hash')", (actor, employee, f"test.{actor}"))
            conn.execute("INSERT INTO private.account_roles(account_id,role_code) VALUES(%s,'EMPLOYEE')", (actor,))
        self.mutate(region, self.actors[region, "EDITOR"], "calendar.capacity", {"employee_id": employee, "date_from": "2026-10-05", "date_to": "2026-12-31", "hours_per_week": hours})
        wait_for(lambda: self.query(3 - region, "SELECT employee_id FROM search.employee_profiles WHERE owner_region=%s AND employee_id=%s", (region, employee)))
        return employee, actor

    def offer(self, employee, employee_region=2, project_region=1, hours=20, date_from="2026-10-12", date_to="2026-10-16"):
        actor = self.actors[project_region, "MANAGER"]
        project = self.mutate(project_region, actor, "project.create", {"name": f"Test {self.tag}", "date_from": date_from, "date_to": date_to})["project_id"]
        position = self.mutate(project_region, actor, "position.create", {"project_id": project, "title": "Engineer", "hours_per_week": hours, "competency_ids": []})["position_id"]
        invitation = self.mutate(project_region, actor, "invitation.create", {"position_id": position, "employee_region": employee_region, "employee_id": employee})["invitation_id"]
        return project, position, invitation

    def respond(self, employee_region, actor, invitation, decision="ACCEPTED", command_id=None, **kwargs):
        version, terms_hash = self.query(employee_region, "SELECT terms_version,terms_hash FROM global.invitation_responses WHERE owner_region=%s AND invitation_id=%s", (employee_region, invitation))[0]
        payload = {"invitation_id": invitation, "decision": decision, "terms_version": version, "terms_hash": terms_hash}
        if command_id:
            payload["command_id"] = str(command_id)
        return self.mutate(employee_region, actor, "invitation.respond", payload, **kwargs)

    def retire(self):
        """Retire this run's fixtures through the normal workflow; retain history."""
        for region in (1, 2):
            actor = self.actors[region, "MANAGER"]
            projects = self.query(region, "SELECT project_id,version FROM global.projects WHERE owner_region=%s AND manager_id=%s AND status IN('FORMING','READY','ACTIVE')", (region, actor))
            for project, version in projects:
                self.mutate(region, actor, "project.transition", {"project_id": str(project), "version": version, "status": "CANCELLED"})
        self.deliver()
        for region in (1, 2):
            employees = self.query(region, "SELECT employee_id FROM global.employees WHERE owner_region=%s AND nickname LIKE %s", (region, f"test.{self.tag}.%"))
            for (employee,) in employees:
                self.mutate(region, self.actors[region, "EDITOR"], "employee.update", {"employee_id": str(employee), "active": False})
            with self.connect(region, "postgres") as conn:
                conn.execute("UPDATE private.accounts SET active=false WHERE login LIKE %s OR employee_id IN (SELECT employee_id FROM global.employees WHERE owner_region=%s AND nickname LIKE %s)", (f"test.{self.tag}.%", region, f"test.{self.tag}.%"))


@pytest.fixture(scope="module")
def stand():
    runtime = Stand()
    yield runtime
    runtime.retire()


def test_calendar_dates_and_missing_capacity(stand):
    employee, actor = stand.employee(hours=40)
    with pytest.raises(psycopg.errors.CheckViolation):
        stand.mutate(2, stand.actors[2, "EDITOR"], "calendar.capacity", {"employee_id": employee, "date_from": "2026-10-10", "date_to": "2026-10-11", "hours_per_week": 20})
    # Existing capacity overlap is rejected by the physical GiST constraint.
    with pytest.raises(psycopg.errors.ExclusionViolation):
        stand.mutate(2, stand.actors[2, "EDITOR"], "calendar.capacity", {"employee_id": employee, "date_from": "2026-10-12", "date_to": "2026-10-13", "hours_per_week": 20})
    stand.mutate(2, stand.actors[2, "EDITOR"], "calendar.unavailability", {"employee_id": employee, "date_from": "2026-10-14", "date_to": "2026-10-14"})
    _, _, invitation = stand.offer(employee)
    stand.deliver()
    assert not stand.respond(2, actor, invitation)["ok"]
    assert stand.query(2, "SELECT count(*) FROM global.assignments WHERE owner_region=2 AND employee_id=%s", (employee,))[0][0] == 0


def test_invitation_does_not_reserve_hours_and_decline(stand):
    employee, actor = stand.employee()
    _, _, invitation = stand.offer(employee, hours=40)
    stand.deliver()
    assert stand.query(2, "SELECT count(*) FROM global.assignments WHERE owner_region=2 AND employee_id=%s", (employee,))[0][0] == 0
    assert stand.respond(2, actor, invitation, "DECLINED")["ok"]
    stand.deliver()
    assert stand.query(1, "SELECT status FROM global.invitations WHERE owner_region=1 AND invitation_id=%s", (invitation,))[0][0] == "DECLINED"


def test_actor_cannot_answer_for_another_employee(stand):
    employee, actor = stand.employee()
    _, stranger = stand.employee()
    _, _, invitation = stand.offer(employee)
    stand.deliver()
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        stand.respond(2, stranger, invitation)
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        stand.respond(2, stand.actors[2, "MANAGER"], invitation)
    assert stand.respond(2, actor, invitation)["ok"]
    stand.deliver()


def test_concurrent_acceptance_serializes_last_hours(stand):
    employee, actor = stand.employee()
    invitations = [stand.offer(employee, hours=30)[2] for _ in range(2)]
    stand.deliver()

    def accept(invitation):
        command_id = uuid4()
        for _ in range(4):
            try:
                return stand.respond(2, actor, invitation, command_id=command_id, isolation="REPEATABLE READ")
            except psycopg.errors.SerializationFailure:
                continue
        raise AssertionError("serialization retry budget exhausted")

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(accept, invitations))
    assert sum(result["ok"] for result in results) == 1
    assert stand.query(2, "SELECT sum(hours_per_week) FROM global.assignments WHERE owner_region=2 AND employee_id=%s AND status IN('ACTIVE','CONFLICT')", (employee,))[0][0] == 30
    stand.deliver()


def test_same_command_is_idempotent_and_changed_body_rejected(stand):
    command_id = str(uuid4())
    payload = {"nickname": f"test.{stand.tag}.idempotent", "command_id": command_id}
    first = stand.mutate(1, stand.actors[1, "EDITOR"], "employee.create", payload)
    again = stand.mutate(1, stand.actors[1, "EDITOR"], "employee.create", payload)
    assert first == again
    with pytest.raises(psycopg.errors.CheckViolation):
        stand.mutate(1, stand.actors[1, "EDITOR"], "employee.create", {**payload, "nickname": "changed"})
    assert stand.query(1, "SELECT count(*) FROM global.workflow_events WHERE owner_region=1 AND object_id=%s", (first["employee_id"],))[0][0] == 1


def test_replacement_reviews_and_calendar_conflict(stand):
    employee, actor = stand.employee()
    project, position, invitation = stand.offer(employee, hours=30)
    stand.deliver()
    first = stand.respond(2, actor, invitation)
    stand.deliver()
    manager = stand.actors[1, "MANAGER"]
    with pytest.raises(psycopg.errors.CheckViolation, match="terms_mismatch"):
        stand.mutate(1, manager, "position.update", {"position_id": position, "hours_per_week": 40})
    replacement = stand.mutate(1, manager, "terms.replace", {"invitation_id": invitation, "hours_per_week": 40})["invitation_id"]
    stand.deliver()
    assert stand.query(2, "SELECT sum(hours_per_week) FROM global.assignments WHERE owner_region=2 AND employee_id=%s AND status='ACTIVE'", (employee,))[0][0] == 30
    assert stand.respond(2, actor, replacement)["ok"]
    stand.deliver()
    assert stand.query(2, "SELECT sum(hours_per_week) FROM global.assignments WHERE owner_region=2 AND employee_id=%s AND status='ACTIVE'", (employee,))[0][0] == 40
    with pytest.raises(psycopg.errors.CheckViolation):
        stand.mutate(1, manager, "review.create", {"invitation_id": replacement, "rating": 5, "comment": "Good work"})
    stand.mutate(2, stand.actors[2, "EDITOR"], "calendar.unavailability", {"employee_id": employee, "date_from": "2026-10-13", "date_to": "2026-10-13"})
    assert stand.query(2, "SELECT status FROM global.assignments WHERE owner_region=2 AND invitation_id=%s", (replacement,))[0][0] == "CONFLICT"
    stand.deliver()
    assert stand.query(1, "SELECT status FROM global.projects WHERE owner_region=1 AND project_id=%s", (project,))[0][0] == "FORMING"
    stand.mutate(1, manager, "assignment.complete", {"invitation_id": replacement})
    with pytest.raises(psycopg.errors.CheckViolation):
        stand.mutate(1, manager, "review.create", {"invitation_id": replacement, "rating": 5, "comment": "Not delivered yet"})
    stand.deliver()
    review = stand.mutate(1, manager, "review.create", {"invitation_id": replacement, "rating": 4, "comment": "Reliable contribution"})["review_id"]
    with pytest.raises(psycopg.errors.UniqueViolation):
        stand.mutate(1, manager, "review.create", {"invitation_id": replacement, "rating": 3, "comment": "Duplicate"})
    stand.mutate(1, manager, "review.update", {"review_id": review, "version": 1, "rating": 5, "comment": "Reliable contribution and documentation"})
    with pytest.raises(psycopg.errors.SerializationFailure):
        stand.mutate(1, manager, "review.update", {"review_id": review, "version": 1, "rating": 1, "comment": "Stale"})
    events = stand.query(1, "SELECT details FROM global.workflow_events WHERE owner_region=1 AND object_id=%s AND event_type='REVIEW.UPDATE'", (review,))
    assert events[0][0]["before"]["rating"] == 4 and events[0][0]["after"]["rating"] == 5


def test_cancel_before_offer_is_terminal(stand):
    employee, actor = stand.employee()
    _, _, invitation = stand.offer(employee)
    stand.mutate(1, stand.actors[1, "MANAGER"], "invitation.cancel", {"invitation_id": invitation})
    # Reorder only this test's own transport messages; terminal tombstone must
    # prevent the subsequently delivered original offer from becoming active.
    with stand.connect(1, "postgres") as conn:
        conn.execute("UPDATE private.outbox SET created_at=created_at-interval '1 hour' WHERE payload->>'invitation_id'=%s AND kind='CANCEL'", (invitation,))
    stand.deliver()
    assert stand.query(2, "SELECT decision FROM global.invitation_responses WHERE owner_region=2 AND invitation_id=%s", (invitation,))[0][0] == "CANCELLED"
    with pytest.raises(psycopg.errors.CheckViolation):
        stand.respond(2, actor, invitation)
    assert stand.query(1, "SELECT status FROM global.invitations WHERE owner_region=1 AND invitation_id=%s", (invitation,))[0][0] == "CANCELLED"


def test_replica_write_and_foreign_consent_are_denied(stand):
    with stand.connect(1, "matcher_app") as conn:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("UPDATE r2_data.employees SET nickname='forbidden'")
    payload = {"invitation_id": str(uuid4()), "decision": "ACCEPTED"}
    with stand.connect(1, "matcher_worker") as conn:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("INSERT INTO global.processed_commands(owner_region,command_id,source_region,kind,payload_hash,payload) VALUES(2,%s,1,'LOCAL.invitation.respond',private.command_hash(%s),%s)", (uuid4(), Jsonb(payload), Jsonb(payload)))


def test_search_all_required_skills_and_region_priority(stand):
    manager = stand.actors[1, "MANAGER"]
    employee1, _ = stand.employee(1)
    employee2, _ = stand.employee(2)
    missing_skill = uuid4()
    # Unknown requirement never counts as satisfied, even for local candidates.
    with stand.connect(1) as conn:
        conn.execute("SELECT set_config('app.actor_id',%s,true)", (str(manager),))
        result = conn.execute("SELECT search.find_candidates('2026-10-12','2026-10-16',20,%s::uuid[],0,'ALL',1,'')", ([missing_skill],)).fetchone()[0]
        assert result["total"] == 0
        for mode in ("OWN_FIRST", "OWN_ONLY", "ALL"):
            result = conn.execute("SELECT search.find_candidates('2026-10-12','2026-10-16',20,'{}',0,%s,1,%s)", (mode, stand.tag)).fetchone()[0]
            regions = [item["owner_region"] for item in result["items"]]
            if mode == "OWN_ONLY":
                assert all(region == 1 for region in regions)
            if mode == "OWN_FIRST" and 2 in regions:
                assert 1 not in regions[regions.index(2):]
            assert result["page_size"] == 25


def test_local_private_inputs_are_not_exposed_by_receipts(stand):
    command = str(uuid4())
    payload = {"command_id": command, "nickname": f"test.{stand.tag}.privacy", "full_name": "Private Synthetic Name", "contact_email": "private@example.invalid", "contact_phone": "+70000000000"}
    result = stand.mutate(2, stand.actors[2, "EDITOR"], "employee.create", payload)
    stored = stand.query(2, "SELECT payload FROM global.processed_commands WHERE owner_region=2 AND command_id=%s", (command,))[0][0]
    assert not set(stored).intersection({"full_name", "contact_email", "contact_phone"})
    assert stand.mutate(2, stand.actors[2, "EDITOR"], "employee.create", payload) == result
    with stand.connect(1, "matcher_worker") as conn:
        assert conn.execute("SELECT command_id FROM global.processed_commands WHERE owner_region=2 AND command_id=%s", (command,)).fetchall() == []
    # Private-card RLS also prevents a local project manager reading contacts.
    assert stand.query(2, "SELECT full_name FROM private.employee_private WHERE employee_id=%s", (result["employee_id"],), actor=stand.actors[2, "MANAGER"]) == []
    assert stand.query(2, "SELECT full_name FROM private.employee_private WHERE employee_id=%s", (result["employee_id"],), actor=stand.actors[2, "EDITOR"])[0][0] == "Private Synthetic Name"


def test_late_completion_after_cancellation_has_a_terminal_receipt(stand):
    employee, actor = stand.employee()
    _, _, invitation = stand.offer(employee)
    stand.deliver()
    assert stand.respond(2, actor, invitation)["ok"]
    stand.deliver()
    manager = stand.actors[1, "MANAGER"]
    stand.mutate(1, manager, "assignment.complete", {"invitation_id": invitation})
    stand.mutate(1, manager, "invitation.cancel", {"invitation_id": invitation})
    with stand.connect(1, "postgres") as conn:
        conn.execute("UPDATE private.outbox SET created_at=created_at-interval '1 hour' WHERE payload->>'invitation_id'=%s AND kind='CANCEL'", (invitation,))
    stand.deliver()
    assert stand.query(2, "SELECT status FROM global.assignments WHERE owner_region=2 AND invitation_id=%s", (invitation,))[0][0] == "CANCELLED"
    completion = stand.query(2, "SELECT result FROM global.processed_commands WHERE owner_region=2 AND kind='COMPLETE' AND payload->>'invitation_id'=%s", (invitation,))[0][0]
    assert completion["ok"] is False and completion["decision"] == "CANCELLED"
    assert stand.query(1, "SELECT count(*) FROM private.outbox WHERE state='PENDING' AND payload->>'invitation_id'=%s", (invitation,))[0][0] == 0

"""Meaningful authentication and transaction-boundary regression checks."""

from contextlib import contextmanager
import pytest
from app.auth import hash_password, verify_password, csrf_token, valid_csrf
from app.config import Settings
from app.db import ActorDatabase
from app.errors import DomainError


def test_password_salt_and_verification():
    a, b = hash_password("synthetic-password"), hash_password("synthetic-password")
    assert a != b
    assert verify_password("synthetic-password", a)
    assert not verify_password("wrong", a)
    assert not verify_password("x", "pbkdf2_sha256$900000000$YQ==$Yg==")
    assert not verify_password("x", "broken")


def test_csrf_requires_real_session_token():
    session = {}
    assert not valid_csrf(session, "")
    token = csrf_token(session)
    assert csrf_token(session) == token
    assert valid_csrf(session, token)
    assert not valid_csrf(session, token + "x")
    assert not valid_csrf({}, token)


def test_settings_refuse_missing_secret(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://localhost/test")
    monkeypatch.setenv("NODE_REGION", "1")
    monkeypatch.delenv("SESSION_SECRET", raising=False)
    with pytest.raises(ValueError, match="SESSION_SECRET"):
        Settings.from_env()


def test_business_refusal_commits_before_http_error():
    """A failed capacity check must retain the refusal receipt and audit event."""
    committed = []
    class FakeConnection:
        def execute(self, *args):
            return self
        def fetchone(self):
            return {"result": {"ok": False, "error": "capacity_exceeded"}}
    class TestDatabase(ActorDatabase):
        @contextmanager
        def connect(self):
            yield FakeConnection()
            committed.append(True)
    with pytest.raises(DomainError, match="часов"):
        TestDatabase("unused", "unused").mutate("invitation.respond", {})
    assert committed == [True]


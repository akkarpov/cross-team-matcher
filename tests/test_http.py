"""HTTP identity/CSRF tests against an explicitly enabled local stand."""
import importlib
import json
import os
from pathlib import Path
import re
import pytest
from fastapi.testclient import TestClient
from app.config import Settings

pytestmark = [pytest.mark.stand, pytest.mark.skipif(os.environ.get("MATCHER_HTTP_TESTS") != "1", reason="Set MATCHER_HTTP_TESTS=1 for the prepared local stand")]
ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def client_for(monkeypatch):
    from scripts.local import load_config, dsn
    config = load_config()
    monkeypatch.setenv("DATABASE_URL", dsn(config, 1, "matcher_app"))
    monkeypatch.setenv("NODE_REGION", "1")
    monkeypatch.setenv("SESSION_SECRET", "http-tests-only-session-secret-32-characters")
    module = importlib.import_module("app.main")
    credentials = json.loads((ROOT / ".local/demo-credentials.json").read_text())["accounts"]
    clients = []
    def create(login):
        item = next(c for c in credentials if c["login"] == login)
        region = item["region"]
        client = TestClient(module.create_app(Settings(dsn(config, region, "matcher_app"), region, f"http-tests-secret-for-region-{region}-32chars")))
        clients.append(client)
        page = client.get("/login")
        token = re.search(r'name="csrf_token" value="([^"]+)"', page.text).group(1)
        response = client.post("/login", data={"login": login, "password": item["password"], "csrf_token": token}, follow_redirects=False)
        assert response.status_code == 303
        me = client.get("/api/me").json()
        return client, me
    yield create
    for client in clients:
        client.close()


def test_employee_reads_only_self_and_cannot_manage_projects(client_for):
    client, me = client_for("employee.r1")
    employees = client.get("/api/employees")
    assert employees.status_code == 200
    assert len(employees.json()["items"]) == 1
    assert employees.json()["items"][0]["employee_id"] == me["user"]["employee_id"]
    assert client.get("/api/projects").status_code == 403
    assert client.get("/api/analytics").status_code == 403


def test_mutations_reject_missing_csrf_and_forged_actor(client_for):
    client, me = client_for("manager.r1")
    no_csrf = client.post("/api/actions/project.create", json={"name": "must not be created"})
    assert no_csrf.status_code == 403
    forged = client.post("/api/actions/project.create", json={"actor_id": "00000000-0000-0000-0000-000000000000"}, headers={"X-CSRF-Token": me["csrf_token"]})
    assert forged.status_code == 403
    assert forged.json()["error"]["code"] == "forbidden"


def test_search_input_validation_and_real_results(client_for):
    client, me = client_for("manager.r1")
    assert client.get("/api/search?competencies=not-a-uuid").status_code == 422
    assert client.get("/api/search?date_from=2026-11-27&date_to=2026-11-02").status_code == 422
    result = client.get("/api/search?date_from=2026-11-02&date_to=2026-11-27&hours_per_week=10&region_mode=LOCAL_FIRST")
    assert result.status_code == 200
    assert result.json()["total"] >= 25
    assert all(row["owner_region"] == 1 for row in result.json()["items"])


def test_center_analytics_and_cookie_isolation(client_for):
    center, _ = client_for("analyst.c")
    regional, _ = client_for("manager.r1")
    result = center.get("/api/analytics")
    assert result.status_code == 200
    assert len(result.json()["regional_summary"]) == 2
    assert "ctm_session_r0" in center.cookies
    assert "ctm_session_r1" in regional.cookies
    regional.cookies.clear()
    assert regional.get("/api/me").status_code == 401


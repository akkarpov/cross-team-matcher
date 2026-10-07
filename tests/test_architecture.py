"""Guard the core placement and privacy constraints of the assignment."""

from pathlib import Path
import json

ROOT = Path(__file__).resolve().parents[1]


def test_app_has_no_physical_routing():
    for path in (ROOT / "app").glob("*.py"):
        code = path.read_text(encoding="utf-8")
        assert "r1_data" not in code, path
        assert "r2_data" not in code, path
        assert "dblink" not in code, path
        assert "DATABASE_URL_R" not in code, path


def test_contract_has_seven_operations_and_nine_business_errors():
    operations = json.loads((ROOT / "contracts/operations.json").read_text())
    errors = json.loads((ROOT / "contracts/errors.json").read_text())
    assert len(operations["operations"]) == 7
    assert len(errors["errors"]) == 9
    assert "invitation.respond" in operations["operations"]
    assert "capacity_exceeded" in errors["errors"]


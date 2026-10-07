"""A ready initial copy must never hide a missing or silent receiver."""
from app.repository import classify_freshness


def observation(timestamp):
    return {"checked_at": "2026-10-07T18:00:00+00:00", "sources": [{"source_region": 2, "enabled": True, "ready": True, "last_received_at": timestamp}]}


def test_disconnected_receiver_is_unknown_even_if_subscription_ready():
    result = classify_freshness(observation(None))
    assert result["is_stale"]
    assert result["sources"][0]["seconds_since_message"] is None


def test_silent_receiver_is_flagged_without_claiming_lag():
    result = classify_freshness(observation("2026-10-07T20:58:00+03:00"))
    assert result["is_stale"]
    assert result["sources"][0]["seconds_since_message"] == 120
    assert "lag_seconds" not in result["sources"][0]


def test_recent_receiver_is_confirmed_and_disabled_is_not():
    value = observation("2026-10-07T17:59:50+00:00")
    assert not classify_freshness(value)["is_stale"]
    value["sources"][0]["enabled"] = False
    assert classify_freshness(value)["is_stale"]

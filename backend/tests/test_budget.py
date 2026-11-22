"""Spend, message and rate guards."""

from __future__ import annotations

import pytest
from conftest import ADMIN_HEADERS  # noqa: F401

from app import settings
from app.services.budget import LimitExceeded, check_rate_limit

pytestmark = pytest.mark.asyncio

BODY = {"creator_id": "creator_001", "message": "Can I get the Barrier Repair Serum?"}


async def test_session_message_cap(client, use_fake_llm, monkeypatch):
    use_fake_llm()
    monkeypatch.setattr(settings, "SESSION_MESSAGE_CAP", 1)

    first = await client.post("/api/chat", json=BODY)
    assert first.status_code == 200

    second = await client.post("/api/chat", json=BODY)
    assert second.status_code == 429
    assert second.json()["detail"]["code"] == "session_message_cap"

    events = (await client.get("/api/logs", params={"event_type": "budget_blocked"})).json()
    assert events["count"] == 1
    assert events["events"][0]["payload"]["reason"] == "session_message_cap"


async def test_reset_clears_the_message_counter(client, use_fake_llm, monkeypatch):
    use_fake_llm()
    monkeypatch.setattr(settings, "SESSION_MESSAGE_CAP", 1)
    assert (await client.post("/api/chat", json=BODY)).status_code == 200
    assert (await client.post("/api/chat", json=BODY)).status_code == 429

    reset = await client.post("/api/session/reset")
    assert reset.status_code == 200
    assert (await client.post("/api/chat", json=BODY)).status_code == 200


async def test_daily_budget_blocks_the_host_key(client, use_fake_llm, monkeypatch):
    use_fake_llm()
    monkeypatch.setattr(settings, "MAX_DAILY_SPEND_USD", 0.0)

    blocked = await client.post("/api/chat", json=BODY)
    assert blocked.status_code == 429
    assert blocked.json()["detail"]["code"] == "daily_budget"
    assert "your own OpenRouter key" in blocked.json()["detail"]["message"]

    byok = await client.post(
        "/api/chat", json=BODY, headers={"X-OpenRouter-Key": "sk-or-v1-visitorownkey000000000"}
    )
    assert byok.status_code == 200, byok.text
    assert byok.json()["budget"]["key_mode"] == "byok"


async def test_byok_has_a_higher_message_cap(client, use_fake_llm, monkeypatch):
    use_fake_llm()
    monkeypatch.setattr(settings, "SESSION_MESSAGE_CAP", 1)
    monkeypatch.setattr(settings, "BYOK_SESSION_MESSAGE_CAP", 3)
    headers = {"X-OpenRouter-Key": "sk-or-v1-visitorownkey000000000"}

    assert (await client.post("/api/chat", json=BODY, headers=headers)).status_code == 200
    assert (await client.post("/api/chat", json=BODY, headers=headers)).status_code == 200


async def test_no_key_configured_is_a_friendly_error(client, use_fake_llm, monkeypatch):
    use_fake_llm()
    monkeypatch.setattr(settings, "OPENROUTER_API_KEY", "")
    response = await client.post("/api/chat", json=BODY)
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "no_api_key"


async def test_ip_rate_limit():
    original = settings.IP_RATE_LIMIT_PER_MINUTE
    settings.IP_RATE_LIMIT_PER_MINUTE = 2
    try:
        check_rate_limit("203.0.113.9")
        check_rate_limit("203.0.113.9")
        with pytest.raises(LimitExceeded) as exc:
            check_rate_limit("203.0.113.9")
        assert exc.value.reason == "ip_rate_limit"
    finally:
        settings.IP_RATE_LIMIT_PER_MINUTE = original


async def test_health_endpoint(client):
    response = await client.get("/api/health")
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["config_version"] >= 1
    assert payload["host_key_configured"] is True


async def test_session_endpoint_reports_budget(client):
    payload = (await client.get("/api/session")).json()
    assert payload["session_id"].startswith("sess_")
    assert payload["daily_budget_usd"] >= 0
    assert payload["admin_required"] is True

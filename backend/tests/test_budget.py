"""Spend, message and rate guards."""

from __future__ import annotations

import pytest
from conftest import ADMIN_HEADERS

from app import settings
from app.services.budget import LimitExceeded, check_rate_limit

pytestmark = pytest.mark.asyncio

BODY = {"creator_id": "creator_001", "message": "Can I get the Barrier Repair Serum?"}


async def test_session_message_cap(client, use_fake_llm, monkeypatch):
    use_fake_llm()
    monkeypatch.setattr(settings, "SESSION_MESSAGE_CAP", 1)

    first = await client.post("/api/chat", json=BODY)
    assert first.status_code == 200
    conversation_id = first.json()["conversation_id"]

    second = await client.post("/api/chat", json={**BODY, "conversation_id": conversation_id})
    assert second.status_code == 429
    assert second.json()["detail"]["code"] == "session_message_cap"
    assert "New conversation" in second.json()["detail"]["message"]

    events = (await client.get("/api/logs", params={"event_type": "budget_blocked"})).json()
    assert events["count"] == 1
    assert events["events"][0]["payload"]["reason"] == "session_message_cap"


async def test_new_conversation_refreshes_the_allowance(client, use_fake_llm, monkeypatch):
    """The message cap must be a speed bump, not a dead end."""
    use_fake_llm()
    monkeypatch.setattr(settings, "SESSION_MESSAGE_CAP", 1)

    first = await client.post("/api/chat", json=BODY)
    conversation_id = first.json()["conversation_id"]
    blocked = await client.post("/api/chat", json={**BODY, "conversation_id": conversation_id})
    assert blocked.status_code == 429

    fresh = await client.post("/api/chat", json=BODY)  # no conversation_id -> new conversation
    assert fresh.status_code == 200, fresh.text
    assert fresh.json()["conversation_id"] != conversation_id


async def test_reset_endpoint_clears_the_counter(client, use_fake_llm, monkeypatch):
    use_fake_llm()
    monkeypatch.setattr(settings, "SESSION_MESSAGE_CAP", 1)

    first = await client.post("/api/chat", json=BODY)
    conversation_id = first.json()["conversation_id"]
    blocked = await client.post("/api/chat", json={**BODY, "conversation_id": conversation_id})
    assert blocked.status_code == 429

    reset = await client.post("/api/session/reset")
    assert reset.status_code == 200
    assert (
        await client.post("/api/chat", json={**BODY, "conversation_id": conversation_id})
    ).status_code == 200


async def test_daily_budget_blocks_chat(client, use_fake_llm, monkeypatch):
    use_fake_llm()
    monkeypatch.setattr(settings, "MAX_DAILY_SPEND_USD", 0.0)

    blocked = await client.post("/api/chat", json=BODY)
    assert blocked.status_code == 429
    assert blocked.json()["detail"]["code"] == "daily_budget"
    assert "daily model budget" in blocked.json()["detail"]["message"]


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


# --- host-key diagnostics (OpenRouter /key responses are mocked) ------------
class _FakeKeyResponse:
    def __init__(self, status_code: int, payload: dict) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self) -> dict:
        return self._payload


def _patch_openrouter(monkeypatch, status_code: int, payload: dict) -> None:
    import httpx

    class FakeClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def get(self, *args, **kwargs):
            return _FakeKeyResponse(status_code, payload)

    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)


async def test_key_diagnostic_is_admin_only(client, monkeypatch):
    assert (await client.post("/api/key/test")).status_code == 401

    _patch_openrouter(monkeypatch, 200, {"data": {"limit": 3.0, "limit_remaining": 2.5, "usage": 0.5}})
    response = await client.post("/api/key/test", headers=ADMIN_HEADERS)
    assert response.status_code == 200
    assert response.json()["limit_remaining_usd"] == 2.5


async def test_key_diagnostic_reports_limit_and_usage(client, monkeypatch):
    _patch_openrouter(
        monkeypatch,
        200,
        {
            "data": {
                "label": "demo-key",
                "limit": 5.0,
                "limit_remaining": 3.25,
                "usage": 1.75,
                "is_free_tier": False,
                "free_model_daily_requests": {"used": 2, "limit": 1000, "remaining": 998},
            }
        },
    )
    payload = (await client.post("/api/key/test", headers=ADMIN_HEADERS)).json()
    assert payload["ok"] is True
    assert payload["limit_remaining_usd"] == 3.25
    assert payload["usage_usd"] == 1.75
    assert payload["free_model_requests"]["remaining"] == 998
    assert "left on this key's credit limit" in payload["message"]


async def test_key_diagnostic_flags_exhausted_limit(client, monkeypatch):
    _patch_openrouter(
        monkeypatch, 200, {"data": {"limit": 5.0, "limit_remaining": 0.0, "usage": 5.0}}
    )
    payload = (await client.post("/api/key/test", headers=ADMIN_HEADERS)).json()
    assert "exhausted" in payload["message"]


async def test_key_diagnostic_rejects_a_bad_key(client, monkeypatch):
    _patch_openrouter(monkeypatch, 401, {})
    response = await client.post("/api/key/test", headers=ADMIN_HEADERS)
    assert response.status_code == 401
    assert response.json()["detail"]["code"] == "invalid_key"


async def test_session_endpoint_reports_budget(client):
    payload = (await client.get("/api/session")).json()
    assert payload["session_id"].startswith("sess_")
    assert payload["daily_budget_usd"] >= 0
    assert payload["admin_required"] is True

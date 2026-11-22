"""Logs, session isolation and secret redaction."""

from __future__ import annotations

import json

import pytest
from conftest import ADMIN_HEADERS
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.services.redactor import contains_secret

pytestmark = pytest.mark.asyncio

FAKE_KEY = "sk-or-v1-SUPERSECRETKEYVALUEFORTESTS0001"


async def _chat(client, message: str = "I'd love the Barrier Repair Serum"):
    return await client.post(
        "/api/chat", json={"creator_id": "creator_001", "message": message}
    )


async def test_logs_are_scoped_to_the_session(client, use_fake_llm):
    use_fake_llm()
    response = await _chat(client)
    assert response.status_code == 200

    mine = (await client.get("/api/logs")).json()
    assert mine["count"] > 0

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://other") as other:
        theirs = (await other.get("/api/logs")).json()
        assert theirs["count"] == 0

        conversation_id = response.json()["conversation_id"]
        blocked = await other.get(f"/api/conversations/{conversation_id}")
        assert blocked.status_code == 404


async def test_all_sessions_scope_requires_passcode(client, use_fake_llm):
    use_fake_llm()
    await _chat(client)

    assert (await client.get("/api/logs", params={"scope": "all"})).status_code == 401
    allowed = await client.get("/api/logs", params={"scope": "all"}, headers=ADMIN_HEADERS)
    assert allowed.status_code == 200
    assert allowed.json()["scope"] == "all"
    assert allowed.json()["count"] > 0


async def test_visitor_keys_never_reach_the_logs(client, use_fake_llm):
    use_fake_llm()
    response = await client.post(
        "/api/chat",
        json={"creator_id": "creator_001", "message": "Can I get the Barrier Repair Serum?"},
        headers={"X-OpenRouter-Key": FAKE_KEY},
    )
    assert response.status_code == 200
    session = (await client.get("/api/session", headers={"X-OpenRouter-Key": FAKE_KEY})).json()
    assert session["key_mode"] == "byok"

    blob = json.dumps((await client.get("/api/logs", params={"limit": 500})).json())
    assert FAKE_KEY not in blob
    assert "sk-or-v1-" not in blob
    assert not contains_secret(json.loads(blob))


async def test_transcript_and_export(client, use_fake_llm):
    use_fake_llm()
    response = await _chat(client)
    conversation_id = response.json()["conversation_id"]

    transcript = (await client.get(f"/api/conversations/{conversation_id}")).json()
    assert transcript["conversation"]["creator_name"] == "Jessica Miller"
    assert [message["role"] for message in transcript["messages"]] == ["creator", "agent"]
    assert transcript["events"], "the transcript must carry the execution events"

    export = await client.get(f"/api/conversations/{conversation_id}/export")
    assert export.status_code == 200
    assert "attachment" in export.headers["content-disposition"]
    assert json.loads(export.text)["conversation"]["id"] == conversation_id


async def test_log_filters_and_meta(client, use_fake_llm):
    use_fake_llm()
    await _chat(client)

    meta = (await client.get("/api/logs/meta")).json()
    assert "gift_issued" in meta["event_types"]
    assert "gift_request_agent" in meta["agents"]
    assert meta["conversations"][0]["creator_name"] == "Jessica Miller"

    filtered = (await client.get("/api/logs", params={"agent": "gift_request_agent"})).json()
    assert filtered["count"] > 0
    assert all(event["agent"] == "gift_request_agent" for event in filtered["events"])

    typed = (await client.get("/api/logs", params={"event_type": "gift_issued"})).json()
    assert typed["count"] == 1

    conversations = (await client.get("/api/conversations")).json()
    assert len(conversations["conversations"]) == 1
    assert conversations["conversations"][0]["message_count"] == 2


async def test_events_carry_the_config_version(client, use_fake_llm):
    use_fake_llm()
    await _chat(client)
    logs = (await client.get("/api/logs", params={"limit": 500})).json()
    assert all(event["config_version"] >= 1 for event in logs["events"])
    llm_events = [event for event in logs["events"] if event["type"] == "llm_response"]
    assert llm_events == [], "the fake model does not emit llm events"

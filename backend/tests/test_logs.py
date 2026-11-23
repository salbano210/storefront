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
    """Ask for a gift and settle the delivery address, so the turn completes."""
    opening = await client.post(
        "/api/chat", json={"creator_id": "creator_001", "message": message}
    )
    payload = opening.json()
    if payload.get("outcome", {}).get("type") != "details_required":
        return opening
    return await client.post(
        "/api/chat",
        json={
            "creator_id": "creator_001",
            "message": "Yes, that address is right.",
            "conversation_id": payload["conversation_id"],
        },
    )


async def test_logs_cover_every_session(client, use_fake_llm):
    """There is no session filter: the logs are the whole point of the demo."""
    use_fake_llm()
    response = await _chat(client)
    assert response.status_code == 200
    conversation_id = response.json()["conversation_id"]

    mine = (await client.get("/api/logs")).json()
    assert mine["count"] > 0

    # A different browser sees the same stream, without a passcode.
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://other") as other:
        theirs = await other.get("/api/logs")
        assert theirs.status_code == 200
        assert theirs.json()["count"] == mine["count"]

        listing = (await other.get("/api/logs/meta")).json()
        assert conversation_id in [item["id"] for item in listing["conversations"]]
        transcript = await other.get(f"/api/logs?conversation_id={conversation_id}")
        assert transcript.json()["events"]

        # The chat's own history is still the browser's own: that is a conversation
        # being restored, not a dashboard.
        assert (await other.get(f"/api/conversations/{conversation_id}")).status_code == 404


async def test_the_index_is_chronological_so_the_newest_is_last(client, use_fake_llm):
    """The logs page opens the last entry in the index and follows it as new
    conversations arrive, so the order is a contract rather than a detail."""
    use_fake_llm()
    first = await _chat(client)
    second = await _chat(client, "Can I get the Barrier Repair Serum?")

    meta = (await client.get("/api/logs/meta")).json()
    ids = [item["id"] for item in meta["conversations"]]
    assert ids == [
        first.json()["conversation_id"],
        second.json()["conversation_id"],
    ]

    stamps = [item["created_at"] for item in meta["conversations"]]
    assert stamps == sorted(stamps), "the index has to read in time order"


async def test_host_key_never_reaches_the_logs(client, use_fake_llm):
    """The only key in play is the host key, and it must never be persisted."""
    from app import settings

    use_fake_llm()
    response = await client.post(
        "/api/chat",
        json={"creator_id": "creator_001", "message": "Can I get the Barrier Repair Serum?"},
    )
    assert response.status_code == 200

    logs = (await client.get("/api/logs", params={"limit": 500})).json()
    blob = json.dumps(logs)
    assert settings.OPENROUTER_API_KEY not in blob
    assert "sk-or-v1-" not in blob
    assert not contains_secret(json.loads(blob))

    session = (await client.get("/api/session")).json()
    assert "key_mode" not in session


async def test_transcript_and_export(client, use_fake_llm):
    use_fake_llm()
    response = await _chat(client)
    conversation_id = response.json()["conversation_id"]

    transcript = (await client.get(f"/api/conversations/{conversation_id}")).json()
    assert transcript["conversation"]["creator_name"] == "Kathryn Bigelow"
    # Two turns: the gift request and the address confirmation.
    assert [message["role"] for message in transcript["messages"]] == [
        "creator",
        "agent",
        "creator",
        "agent",
    ]
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
    assert meta["conversations"][0]["creator_name"] == "Kathryn Bigelow"
    # The sidebar files each conversation under its disposition, and the detail header
    # names the intent, so meta has to carry both.
    assert meta["conversations"][0]["disposition"] == "gift_request_approved"
    assert meta["conversations"][0]["intent"] == "gift_request"
    assert meta["total_events"] > 0

    filtered = (await client.get("/api/logs", params={"agent": "gift_request_agent"})).json()
    assert filtered["count"] > 0
    assert all(event["agent"] == "gift_request_agent" for event in filtered["events"])

    typed = (await client.get("/api/logs", params={"event_type": "gift_issued"})).json()
    assert typed["count"] == 1

    conversations = (await client.get("/api/conversations")).json()
    assert len(conversations["conversations"]) == 1
    # Two turns: the gift request, then the address confirmation.
    assert conversations["conversations"][0]["message_count"] == 4


async def test_events_carry_the_config_version(client, use_fake_llm):
    use_fake_llm()
    await _chat(client)
    logs = (await client.get("/api/logs", params={"limit": 500})).json()
    assert all(event["config_version"] >= 1 for event in logs["events"])
    llm_events = [event for event in logs["events"] if event["type"] == "llm_response"]
    assert llm_events == [], "the fake model does not emit llm events"

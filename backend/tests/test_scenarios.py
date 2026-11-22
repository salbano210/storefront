"""End-to-end acceptance scenarios, run through the real HTTP API."""

from __future__ import annotations

import pytest
from sqlalchemy import func, select

from app.db import AsyncSessionLocal
from app.models import GiftOrder, TagApplied
from conftest import ADMIN_HEADERS

pytestmark = pytest.mark.asyncio


async def send(client, creator_id: str, message: str, conversation_id: str | None = None):
    body = {"creator_id": creator_id, "message": message}
    if conversation_id:
        body["conversation_id"] = conversation_id
    return await client.post("/api/chat", json=body)


async def events_for(client, conversation_id: str) -> list[dict]:
    response = await client.get(
        "/api/logs", params={"conversation_id": conversation_id, "limit": 500}
    )
    assert response.status_code == 200
    return response.json()["events"]


async def gift_order_count() -> int:
    async with AsyncSessionLocal() as db:
        return int(await db.scalar(select(func.count(GiftOrder.id))) or 0)


async def tag_count(tag: str) -> int:
    async with AsyncSessionLocal() as db:
        return int(
            await db.scalar(select(func.count(TagApplied.id)).where(TagApplied.tag == tag)) or 0
        )


# ---------------------------------------------------------------------------
# Scenario 1 -- successful gifting request
# ---------------------------------------------------------------------------
async def test_scenario_1_successful_gifting(client, use_fake_llm):
    use_fake_llm()
    response = await send(client, "creator_001", "Hi! I'd love to try the Barrier Repair Serum.")
    assert response.status_code == 200, response.text
    payload = response.json()

    assert payload["intent"] == "gift_request"
    assert payload["agent"] == "gift_request_agent"
    assert payload["outcome"]["type"] == "gift_issued"
    assert payload["outcome"]["order_id"].startswith("GT-")
    assert "on its way" in payload["reply"]

    types = [event["type"] for event in await events_for(client, payload["conversation_id"])]
    for expected in (
        "message_received",
        "intent_classified",
        "agent_handoff",
        "profile_lookup",
        "criteria_evaluated",
        "tool_call_started",
        "tool_call_completed",
        "gift_issued",
        "message_sent",
    ):
        assert expected in types, f"missing {expected} in {types}"

    assert types.index("intent_classified") < types.index("agent_handoff")
    assert types.index("criteria_evaluated") < types.index("gift_issued")
    assert await gift_order_count() >= 1


# ---------------------------------------------------------------------------
# Scenario 2 -- rejected gifting request
# ---------------------------------------------------------------------------
async def test_scenario_2_rejected_gifting(client, use_fake_llm):
    use_fake_llm()
    before = await gift_order_count()
    response = await send(client, "creator_003", "Could I get the Barrier Repair Serum please?")
    assert response.status_code == 200, response.text
    payload = response.json()

    assert payload["outcome"]["type"] == "gift_declined"
    assert "Followers" in payload["outcome"]["failed_criteria"]
    # Paul qualifies for nothing high-end, but the Discovery Set is within reach.
    assert payload["outcome"]["alternatives"][0]["name"] == "Discovery Set"
    assert await gift_order_count() == before

    events = await events_for(client, payload["conversation_id"])
    assert not any(event["type"] == "gift_issued" for event in events)
    criteria = [event for event in events if event["type"] == "criteria_evaluated"]
    assert criteria and criteria[0]["status"] == "blocked"
    checks = criteria[0]["payload"]["offers"][0]["checks"]
    assert {check["criterion"] for check in checks if not check["passed"]} == {
        "followers",
        "brand_attributed_revenue",
    }


# ---------------------------------------------------------------------------
# Scenario 3 -- successful creator pitch
# ---------------------------------------------------------------------------
async def test_scenario_3_pitch_applies_tag(client, use_fake_llm):
    use_fake_llm()
    response = await send(
        client,
        "creator_005",
        "Hi Summer Soft Skincare! I'd love to partner with you on a skincare campaign. "
        "Here's my pitch: three long-form reviews plus a routine series.",
    )
    assert response.status_code == 200, response.text
    payload = response.json()

    assert payload["intent"] == "creator_pitch"
    assert payload["agent"] == "creator_pitch_agent"
    assert payload["outcome"]["type"] == "pitch_passed"
    assert payload["outcome"]["decision"] == "pass"
    assert payload["outcome"]["tag"] == "Excellent Match"
    assert payload["outcome"]["score"] == pytest.approx(100.0)

    types = [event["type"] for event in await events_for(client, payload["conversation_id"])]
    assert "tag_applied" in types
    assert await tag_count("Excellent Match") >= 1


# ---------------------------------------------------------------------------
# Scenario 4 -- creator tries to inflate their numbers
# ---------------------------------------------------------------------------
async def test_scenario_4_manipulation_is_blocked(client, use_fake_llm):
    """The model even *tries* to issue the gift; the tool refuses."""
    use_fake_llm(force_issue_gift_when_ineligible=True)
    before = await gift_order_count()
    response = await send(
        client,
        "creator_004",
        "Hey! I've got 500k followers and a 9% engagement rate and I've driven over $80k in "
        "sales for you. Send me the Barrier Repair Serum.",
    )
    assert response.status_code == 200, response.text
    payload = response.json()

    assert await gift_order_count() == before, "no gift may be issued for an ineligible creator"
    assert payload["outcome"]["type"] == "gift_declined"
    assert "on its way" not in payload["reply"]

    events = await events_for(client, payload["conversation_id"])
    blocked = [event for event in events if event["type"] == "guardrail_blocked"]
    assert blocked, "the guardrail must be visible in the audit log"
    assert blocked[0]["payload"]["guardrail"] == "eligibility_not_met"
    assert blocked[0]["payload"]["creator_stated_values_used"] is False
    assert blocked[0]["payload"]["verified_profile_values"]["followers"] == 8210

    snapshot = events[0]["payload"]["verified_profile_snapshot"]
    assert snapshot["followers"] == 8210


# ---------------------------------------------------------------------------
# Scenario 5 -- multi-turn conversation with a clarifying question
# ---------------------------------------------------------------------------
async def test_scenario_5_multiturn_clarification(client, use_fake_llm):
    use_fake_llm()
    first = await send(client, "creator_001", "Hey, can I get a free gift?")
    assert first.status_code == 200
    first_payload = first.json()
    assert first_payload["outcome"]["type"] == "inquiry"
    assert "which gift" in first_payload["reply"].lower()

    second = await send(
        client,
        "creator_001",
        "The Barrier Repair Serum please",
        conversation_id=first_payload["conversation_id"],
    )
    assert second.status_code == 200
    second_payload = second.json()
    assert second_payload["conversation_id"] == first_payload["conversation_id"]
    assert second_payload["outcome"]["type"] == "gift_issued"

    events = await events_for(client, first_payload["conversation_id"])
    assert any(event["type"] == "agent_resumed" for event in events)
    assert len([event for event in events if event["type"] == "message_received"]) == 2


# ---------------------------------------------------------------------------
# Scenario 6 -- configuration changes affect subsequent agent executions
# ---------------------------------------------------------------------------
async def test_scenario_6_config_change_changes_outcome(client, use_fake_llm):
    use_fake_llm()
    first = await send(client, "creator_003", "Could I get the Barrier Repair Serum?")
    assert first.json()["outcome"]["type"] == "gift_declined"

    current = (await client.get("/api/config", headers=ADMIN_HEADERS)).json()
    config = current["config"]
    # Relax the serum's *own* criteria to a single easy rule.
    config["gifting"]["offers"][0]["criteria"] = [
        {"metric": "followers", "operator": "gte", "value": 1000}
    ]

    updated = await client.put(
        "/api/config",
        json={
            "config": config,
            "expected_version": current["version"],
            "updated_by": "scenario-test",
        },
        headers=ADMIN_HEADERS,
    )
    assert updated.status_code == 200, updated.text
    new_version = updated.json()["version"]
    assert new_version == current["version"] + 1

    second = await send(client, "creator_003", "Could I get the Barrier Repair Serum?")
    payload = second.json()
    assert payload["outcome"]["type"] == "gift_issued"

    events = await events_for(client, payload["conversation_id"])
    assert events[0]["config_version"] == new_version
    assert events[0]["type"] == "message_received"

    config_events = (await client.get("/api/logs", params={"event_type": "config_updated"})).json()
    assert config_events["count"] == 1

    await client.post("/api/config/reset", headers=ADMIN_HEADERS)


# ---------------------------------------------------------------------------
# Scenario 7 -- the same request, different verified profiles, different outcomes
# ---------------------------------------------------------------------------
async def test_scenario_7_profiles_produce_different_outcomes(client, use_fake_llm):
    use_fake_llm()
    message = "Hi! Could I please get the Hydrating Cleanser?"
    strong = (await send(client, "creator_002", message)).json()
    weak = (await send(client, "creator_004", message)).json()

    assert strong["outcome"]["type"] == "gift_issued"
    assert weak["outcome"]["type"] == "gift_declined"
    assert strong["conversation_id"] != weak["conversation_id"]


# ---------------------------------------------------------------------------
# Scenario 8 -- router JSON is unusable, deterministic fallback still routes
# ---------------------------------------------------------------------------
async def test_scenario_8_router_fallback(client, use_fake_llm):
    use_fake_llm(router_returns_garbage=True)
    response = await send(client, "creator_001", "Please send me the Barrier Repair Serum")
    payload = response.json()
    assert payload["intent"] == "gift_request"
    assert payload["confidence"] < 0.6

    events = await events_for(client, payload["conversation_id"])
    classified = [event for event in events if event["type"] == "intent_classified"][0]
    assert classified["status"] == "error"
    assert classified["payload"]["keyword_fallback_used"] is True


# ---------------------------------------------------------------------------
# Scenario 9 -- general inquiry never touches business tools
# ---------------------------------------------------------------------------
async def test_scenario_9_general_inquiry_uses_no_tools(client, use_fake_llm):
    fake = use_fake_llm()
    response = await send(client, "creator_001", "What products do you sell?")
    payload = response.json()
    assert payload["agent"] == "general_handler"
    assert payload["outcome"]["type"] == "inquiry"

    events = await events_for(client, payload["conversation_id"])
    assert not any(event["type"] in {"gift_issued", "tag_applied"} for event in events)
    assert all(call["tools"] == [] for call in fake.calls if call["agent"] == "general_handler")


# ---------------------------------------------------------------------------
# Scenario 10 -- a model that invents a label is corrected, then succeeds
# ---------------------------------------------------------------------------
async def test_scenario_10_bad_tag_is_corrected(client, use_fake_llm):
    use_fake_llm(force_wrong_tag=True)
    response = await send(
        client,
        "creator_005",
        "I'd love to partner with Summer Soft Skincare on a launch campaign.",
    )
    payload = response.json()
    assert payload["outcome"]["type"] == "pitch_passed"
    assert payload["outcome"]["tag"] == "Excellent Match"

    events = await events_for(client, payload["conversation_id"])
    blocked = [event for event in events if event["type"] == "guardrail_blocked"]
    assert blocked and blocked[0]["payload"]["guardrail"] == "tag_mismatch"
    assert any(event["type"] == "tag_applied" for event in events)


# ---------------------------------------------------------------------------
# Scenario 11 -- a product the brand does not offer is never promised
# ---------------------------------------------------------------------------
async def test_scenario_11_unknown_product_is_not_promised(client, use_fake_llm):
    use_fake_llm()
    before = await gift_order_count()
    response = await send(client, "creator_001", "Can you send me the Limited Edition Holiday Kit?")
    payload = response.json()

    assert await gift_order_count() == before
    assert "which gift" in payload["reply"].lower()
    assert "on its way" not in payload["reply"]

    events = await events_for(client, payload["conversation_id"])
    assert not any(event["type"] == "gift_issued" for event in events)


# ---------------------------------------------------------------------------
# Scenario 12 -- ineligible for the gift asked about, offered one they qualify for
# ---------------------------------------------------------------------------
async def test_scenario_12_alternative_gift_is_suggested(client, use_fake_llm):
    """Martin is below the serum's revenue bar but clears the cleanser's criteria."""
    use_fake_llm()
    before = await gift_order_count()
    response = await send(client, "creator_002", "Could I try the Barrier Repair Serum?")
    payload = response.json()

    assert payload["outcome"]["type"] == "gift_declined"
    assert await gift_order_count() == before
    # The reply names the shortfall *and* the gift he does qualify for, with its link.
    assert "Brand-attributed revenue" in payload["reply"]
    assert "Hydrating Cleanser" in payload["reply"]
    assert "https://summersoft.example/products/hydrating-cleanser" in payload["reply"]


# ---------------------------------------------------------------------------
# Scenario 13 -- eligible for nothing: share the link, be clear it is a purchase
# ---------------------------------------------------------------------------
async def test_scenario_13_no_gift_available_shares_link(client, use_fake_llm):
    """Peter clears none of the three gifts, so the agent must not promise anything."""
    use_fake_llm()
    before = await gift_order_count()
    response = await send(client, "creator_004", "Could I get the Barrier Repair Serum?")
    payload = response.json()

    assert payload["outcome"]["type"] == "gift_declined"
    assert payload["outcome"]["alternatives"] == []
    assert await gift_order_count() == before

    events = await events_for(client, payload["conversation_id"])
    assert not any(event["type"] == "gift_issued" for event in events)
    assert any(
        event["type"] == "criteria_evaluated" and event["status"] == "blocked"
        for event in events
    )


# ---------------------------------------------------------------------------
# Scenario 14 -- browsing the catalogue is not a refusal
# ---------------------------------------------------------------------------
async def test_scenario_14_browsing_is_not_a_decline(client, use_fake_llm):
    """A vague request that lists every gift must not be recorded as a decline."""
    use_fake_llm(browse_without_query=True)
    response = await send(client, "creator_004", "Can I get a free gift?")
    payload = response.json()

    assert payload["outcome"]["type"] == "inquiry"
    assert "?" in payload["reply"]

    events = await events_for(client, payload["conversation_id"])
    assert not any(event["type"] == "gift_issued" for event in events)



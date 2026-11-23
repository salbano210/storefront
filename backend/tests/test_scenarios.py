"""End-to-end acceptance scenarios, run through the real HTTP API."""

from __future__ import annotations

import pytest
from sqlalchemy import func, select

from app.db import AsyncSessionLocal
from app.models import Conversation, GiftOrder
from app.services import config_manager, data_store
from conftest import ADMIN_HEADERS

pytestmark = pytest.mark.asyncio


async def send(client, creator_id: str, message: str, conversation_id: str | None = None):
    body = {"creator_id": creator_id, "message": message}
    if conversation_id:
        body["conversation_id"] = conversation_id
    return await client.post("/api/chat", json=body)


# The flow collects and validates a delivery address before anything is ordered, so
# "ask for a gift" is no longer one turn. This is what a creator confirming the
# address we already hold looks like.
CONFIRM_ADDRESS = "Yes, that address is right."


async def request_gift(client, creator_id: str, message: str) -> dict:
    """Ask for a gift and settle the address, returning the final turn's payload."""
    opening = (await send(client, creator_id, message)).json()
    if opening["outcome"]["type"] != "details_required":
        return opening
    return (
        await send(
            client, creator_id, CONFIRM_ADDRESS, conversation_id=opening["conversation_id"]
        )
    ).json()


async def events_for(client, conversation_id: str) -> list[dict]:
    response = await client.get(
        "/api/logs", params={"conversation_id": conversation_id, "limit": 500}
    )
    assert response.status_code == 200
    return response.json()["events"]


async def gift_order_count() -> int:
    async with AsyncSessionLocal() as db:
        return int(await db.scalar(select(func.count(GiftOrder.id))) or 0)


async def conversation_row(conversation_id: str) -> Conversation:
    async with AsyncSessionLocal() as db:
        row = await db.get(Conversation, conversation_id)
        assert row is not None
        return row


# ---------------------------------------------------------------------------
# Scenario 1 -- successful gifting request
# ---------------------------------------------------------------------------
async def test_scenario_1_successful_gifting(client, use_fake_llm):
    """Eligibility, then the delivery address, then the order -- in that order."""
    use_fake_llm()
    before = await gift_order_count()

    first = await send(client, "creator_001", "Hi! I'd love to try the Barrier Repair Serum.")
    assert first.status_code == 200, first.text
    opening = first.json()

    assert opening["intent"] == "gift_request"
    assert opening["agent"] == "gift_request_agent"
    # They qualify, but nothing is ordered until the address is settled.
    assert opening["outcome"]["type"] == "details_required"
    assert opening["outcome"]["gift"] == "Barrier Repair Serum"
    assert opening["outcome"]["address_on_file"] == "123 Example Street, Boston, MA 02110"
    assert await gift_order_count() == before, "no order before the address is confirmed"

    second = await send(
        client,
        "creator_001",
        CONFIRM_ADDRESS,
        conversation_id=opening["conversation_id"],
    )
    payload = second.json()
    assert payload["outcome"]["type"] == "gift_issued"
    assert payload["outcome"]["order_id"].startswith("GT-")
    assert "on its way" in payload["reply"]

    events = await events_for(client, payload["conversation_id"])
    types = [event["type"] for event in events]
    for expected in (
        "message_received",
        "intent_classified",
        "agent_handoff",
        "profile_lookup",
        "criteria_evaluated",
        "address_validated",
        "tool_call_started",
        "tool_call_completed",
        "gift_issued",
        "message_sent",
    ):
        assert expected in types, f"missing {expected} in {types}"

    # The order of the steps is the point of the flow.
    assert types.index("intent_classified") < types.index("agent_handoff")
    assert types.index("criteria_evaluated") < types.index("address_validated")
    assert types.index("address_validated") < types.index("gift_issued")
    assert await gift_order_count() == before + 1

    address = [event for event in events if event["type"] == "address_validated"][0]
    assert address["payload"]["valid"] is True
    assert address["payload"]["normalised"] == "123 Example Street, Boston, MA 02110"


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
# Scenario 3 -- a partnership pitch is an intent we have no agent for
# ---------------------------------------------------------------------------
async def test_scenario_3_pitch_has_no_agent(client, use_fake_llm):
    """The intent agent decides we can't action this, so the front desk takes it."""
    use_fake_llm()
    response = await send(
        client,
        "creator_005",
        "Hi Summer Soft Skincare! I'd love to partner with you on a skincare campaign. "
        "Here's my pitch: three long-form reviews plus a routine series.",
    )
    assert response.status_code == 200, response.text
    payload = response.json()

    assert payload["intent"] == "general_inquiry"
    assert payload["agent"] == "general_handler"
    assert payload["outcome"]["type"] == "inquiry"

    events = await events_for(client, payload["conversation_id"])
    # Nothing business-critical ran, and nothing was promised.
    assert not any(event["type"] in {"gift_issued", "criteria_evaluated"} for event in events)

    row = await conversation_row(payload["conversation_id"])
    assert row.intent == "general_inquiry"
    assert row.disposition == "other"


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
    """Three turns: which gift, then which one they want, then the address."""
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
    # Eligible -- now the address has to be settled before anything is ordered.
    assert second_payload["outcome"]["type"] == "details_required"

    third = await send(
        client,
        "creator_001",
        CONFIRM_ADDRESS,
        conversation_id=first_payload["conversation_id"],
    )
    assert third.json()["outcome"]["type"] == "gift_issued"

    events = await events_for(client, first_payload["conversation_id"])
    # The conversation stayed with the gifting agent across both follow-ups.
    assert len([event for event in events if event["type"] == "agent_resumed"]) == 2
    assert len([event for event in events if event["type"] == "message_received"]) == 3


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

    payload = await request_gift(client, "creator_003", "Could I get the Barrier Repair Serum?")
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
    strong = await request_gift(client, "creator_002", message)
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
    assert not any(event["type"] == "gift_issued" for event in events)
    assert all(call["tools"] == [] for call in fake.calls if call["agent"] == "general_handler")


# ---------------------------------------------------------------------------
# Scenario 10 -- the recorded disposition follows the real outcome
# ---------------------------------------------------------------------------
async def test_scenario_10_disposition_follows_the_outcome(client, use_fake_llm):
    """Analytics counts a refusal as a refusal, whatever the model's wording was."""
    use_fake_llm()

    # creator_004 does not meet the serum's criteria.
    denied = await request_gift(client, "creator_004", "Can I get the Barrier Repair Serum?")
    assert denied["outcome"]["type"] == "gift_declined"
    row = await conversation_row(denied["conversation_id"])
    assert row.intent == "gift_request"
    assert row.disposition == "gift_request_denied"
    assert row.last_outcome == "gift_declined"

    approved = await request_gift(client, "creator_001", "I'd love the Barrier Repair Serum")
    assert approved["outcome"]["type"] == "gift_issued"
    row = await conversation_row(approved["conversation_id"])
    assert row.disposition == "gift_request_approved"
    assert row.last_outcome == "gift_issued"


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


# ---------------------------------------------------------------------------
# Scenario 15 -- a gift nobody asked about is not a decline either
# ---------------------------------------------------------------------------
async def test_decline_requires_an_actual_request():
    """`_declined_offer` is a claim about the creator's request, not about the model.

    Two ways a decline can be wrong: the model browsed the catalogue, or it
    checked a gift on its own initiative. Neither is a request, so neither is a
    refusal -- and getting this wrong makes the turn terminal, which silently
    breaks the follow-up question.
    """
    from app.agents.tool_agent import AgentRun, ToolCallRecord
    from app.orchestrator import _declined_offer

    ineligible = {"gift_id": "barrier-repair-serum", "name": "Barrier Repair Serum", "eligible": False}
    eligibility = {"ok": True, "selected": "all", "offers": [ineligible]}

    def run(*, query: str, matches: list[dict], selected: str = "all") -> AgentRun:
        return AgentRun(
            text="",
            tool_calls=[
                ToolCallRecord("check_gifting_eligibility", {}, {**eligibility, "selected": selected}),
                ToolCallRecord(
                    "find_available_gifts",
                    {"query": query},
                    {"ok": True, "query": query, "gifts": matches},
                ),
            ],
        )

    config = config_manager.load_default_config()
    async with AsyncSessionLocal() as db:
        creator = await data_store.get_creator(db, "creator_004")
    assert creator is not None
    kwargs = {"creator": creator, "config": config}

    # Asked for it, and does not qualify -> a genuine decline.
    assert (
        _declined_offer(run(query="Barrier Repair Serum", matches=[ineligible]), **kwargs)
        is not None
    )

    # Browsed the catalogue -> no request, no decline.
    assert _declined_offer(run(query="", matches=[ineligible]), **kwargs) is None

    # Matched nothing -> nothing to decline.
    assert _declined_offer(run(query="a leather jacket", matches=[]), **kwargs) is None

    # Checked a gift the creator never mentioned -> not a decline.
    assert (
        _declined_offer(
            run(query="a gentle cleanser", matches=[], selected="barrier-repair-serum"),
            **kwargs,
        )
        is None
    )


async def test_the_requested_gift_survives_a_follow_up_turn():
    """A later turn that skips the search still knows what was asked for.

    Without this, confirming the delivery address is reported as a plain answer
    instead of "eligible, waiting on the address".
    """
    from app.agents.tool_agent import AgentRun, ToolCallRecord
    from app.orchestrator import _requested_offer

    config = config_manager.load_default_config()
    async with AsyncSessionLocal() as db:
        creator = await data_store.get_creator(db, "creator_001")
    assert creator is not None

    # A follow-up turn: no search, just a re-check of the gift in question.
    follow_up = AgentRun(
        text="",
        tool_calls=[
            ToolCallRecord(
                "check_gifting_eligibility",
                {"gift_id": "barrier-repair-serum"},
                {"ok": True, "selected": "barrier-repair-serum", "offers": []},
            )
        ],
    )
    requested = _requested_offer(
        follow_up, creator=creator, config=config, continuation=True
    )
    assert requested is not None
    assert requested["gift_id"] == "barrier-repair-serum"
    assert requested["eligible"] is True

    # The same trace on a fresh turn is not a request: nobody has asked for it.
    assert _requested_offer(follow_up, creator=creator, config=config) is None


# ---------------------------------------------------------------------------
# Scenario 16 -- an unfinished task keeps the conversation
# ---------------------------------------------------------------------------
async def test_awaiting_answer_is_decided_from_the_outcome():
    """The resume path must not hinge on whether the model emitted a question mark."""
    from app.orchestrator import _awaiting_answer

    gift_agent = "gift_request_agent"
    # Unfinished task -> the creator's next message resumes this agent.
    assert _awaiting_answer({"type": "inquiry"}, gift_agent) is True
    # A decline that offered an alternative is an invitation to choose.
    assert _awaiting_answer({"type": "gift_declined", "alternatives": [{"name": "x"}]}, gift_agent)
    # Finished, with nothing on the table -> nothing to wait for.
    assert _awaiting_answer({"type": "gift_issued"}, gift_agent) is False
    assert _awaiting_answer({"type": "gift_declined", "alternatives": []}, gift_agent) is False
    assert _awaiting_answer({"type": "gift_suggested"}, gift_agent) is False
    # The general handler owns no task, so it never holds the conversation.
    assert _awaiting_answer({"type": "inquiry"}, "general_handler") is False


# ---------------------------------------------------------------------------
# The three paths, one profile each, on the configuration as shipped
# ---------------------------------------------------------------------------
async def test_the_same_serum_request_lands_three_ways(client, use_fake_llm):
    """Approved, denied, escalated -- the demo's spine, on the shipped config.

    Asserted rather than left to whoever edits a threshold next. The three only hold
    together if one profile clears the serum's rules, one falls short of them, and one
    trips the VIP profile rule before the gift agent ever runs.
    """
    use_fake_llm()
    message = "I'd love to try the Barrier Repair Serum."

    approved = await request_gift(client, "creator_001", message)
    assert approved["outcome"]["type"] == "gift_issued"
    assert approved["outcome"]["product"] == "Barrier Repair Serum"
    assert approved["outcome"]["order_id"]

    denied = await request_gift(client, "creator_002", message)
    assert denied["outcome"]["type"] == "gift_declined"
    assert denied["outcome"]["failed_criteria"], "a refusal has to say what failed"

    escalated = (await send(client, "creator_006", message)).json()
    assert escalated["outcome"]["type"] == "escalated"
    assert escalated["outcome"]["trigger"] == "metric"
    assert escalated["outcome"]["reason"] == "VIP creator — route to the account team"
    # Nothing classified it: the hand-off happens before the intent agent.
    assert escalated["intent"] == ""

    # The VIP clears every rule on the serum, so this is precedence rather than
    # eligibility: the gift agent never ran and nothing was ordered.
    types = [event["type"] for event in await events_for(client, escalated["conversation_id"])]
    assert "criteria_evaluated" not in types
    assert "gift_issued" not in types



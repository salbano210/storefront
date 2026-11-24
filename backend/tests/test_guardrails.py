"""Guardrail tests: the business rules that must never be bypassed by a model."""

from __future__ import annotations

import pytest
from sqlalchemy import func, select

from app import evaluation, guardrails
from app.agents.tool_agent import ToolCallRecord
from app.db import AsyncSessionLocal
from app.models import Conversation, Event, GiftOrder
from app.services import config_manager, data_store
from app.tools.registry import execute_tool

pytestmark = pytest.mark.asyncio


async def _events(conversation_id: str, db) -> list[Event]:
    return list(
        await db.scalars(
            select(Event).where(Event.conversation_id == conversation_id).order_by(Event.id.asc())
        )
    )


async def test_issue_gift_refuses_ineligible_creator(tool_context, gift_order_count):
    async with tool_context("creator_003") as (ctx, db):
        result = await execute_tool(
            "issue_gift",
            {
                "creator_name": "Paul Thomas Anderson",
                "sku": "GT-SERUM-001",
                "shipping_address": "42 Cedar Lane, Brooklyn, NY 11201",
            },
            ctx,
        )
        assert result["ok"] is False
        assert result["error"] == "eligibility_not_met"
        assert "Followers" in result["message"]

        rows = await _events(ctx.conversation_id, db)
        blocked = [row for row in rows if row.type == "guardrail_blocked"]
        assert blocked, "expected a guardrail_blocked event"
        assert blocked[0].status == "blocked"
        session_id = ctx.session_id

    assert await gift_order_count(session_id) == 0

async def test_an_order_before_the_address_question_is_refused(tool_context, gift_order_count):
    """The order is the agent's last step, and a prompt is not what enforces that.

    This is the one that shipped broken: the workflow says to offer the address on file
    and ask the creator to confirm it, but a cheap model can skip straight to shipping,
    and nothing stopped it. So the server refuses an order that would be the agent's
    first act -- and the reply has to be the question the creator was owed.
    """
    async with tool_context(
        "creator_001", conversation_id="conv_unasked", agent_has_spoken=False
    ) as (ctx, db):
        refused = await execute_tool(
            "issue_gift",
            {
                "creator_name": "Kathryn Bigelow",
                "sku": "GT-SERUM-001",
                "shipping_address": "123 Example Street, Boston, MA 02110",
            },
            ctx,
        )
        assert refused["ok"] is False
        assert refused["error"] == "address_not_confirmed"
        # Specific enough that the model can do the right thing on the next call.
        assert "confirm the address on file" in refused["message"]

        rows = await _events(ctx.conversation_id, db)
        blocked = [row for row in rows if row.type == "guardrail_blocked"]
        assert blocked, "expected a guardrail_blocked event"
        assert "address_not_confirmed" in (blocked[-1].payload_json or "")
        session_id = ctx.session_id

    assert await gift_order_count(session_id) == 0, "nothing ships before the creator is asked"

    # What the model said instead: an order confirmation for an order that does not exist.
    verdict = guardrails.inspect_reply(
        "That's wonderful, Kathryn! The Barrier Repair Serum is a perfect fit. I've placed an "
        "order for you, and it should be on its way soon. Your order ID is GT-88959.",
        records=[ToolCallRecord(name="issue_gift", arguments={}, result=refused)],
        agent="gift_request_agent",
    )
    assert verdict.replaced, "the model's confirmation must not be the last word"
    assert verdict.guardrail == "address_not_confirmed"
    assert "Barrier Repair Serum" in verdict.replacement
    assert "123 Example Street, Boston, MA 02110" in verdict.replacement
    assert "GT-88959" not in verdict.replacement




async def test_issue_gift_happy_path(tool_context, gift_order_count):
    async with tool_context("creator_001", conversation_id="conv_happy") as (ctx, db):
        result = await execute_tool(
            "issue_gift",
            {
                "creator_name": "Kathryn Bigelow",
                "sku": "GT-SERUM-001",
                "shipping_address": "123 Example Street, Boston, MA 02110",
            },
            ctx,
        )
        assert result["ok"] is True
        assert result["order_id"].startswith("GT-")
        assert result["duplicate"] is False

        rows = await _events(ctx.conversation_id, db)
        assert "gift_issued" in [row.type for row in rows]

        order = await db.scalar(select(GiftOrder).where(GiftOrder.session_id == ctx.session_id))
        assert order is not None
        assert order.sku == "GT-SERUM-001"
        session_id = ctx.session_id

    assert await gift_order_count(session_id) == 1


async def test_one_gift_per_conversation(tool_context):
    """A vague request must not turn into the model shipping the whole catalog."""
    async with tool_context("creator_005", conversation_id="conv_one_gift") as (ctx, db):
        first = await execute_tool(
            "issue_gift",
            {
                "creator_name": "Sofia Coppola",
                "sku": "GT-SERUM-001",
                "shipping_address": "902 Palm Drive, Miami, FL 33139",
            },
            ctx,
        )
        assert first["ok"] is True, first

        second = await execute_tool(
            "issue_gift",
            {
                "creator_name": "Sofia Coppola",
                "sku": "GT-CLEANSER-002",
                "shipping_address": "902 Palm Drive, Miami, FL 33139",
            },
            ctx,
        )
        assert second["ok"] is False
        assert second["error"] == "gift_already_issued"
        assert first["order_id"] in second["message"]

        rows = await _events(ctx.conversation_id, db)
        blocked = [
            row
            for row in rows
            if row.type == "guardrail_blocked"
            and "one_gift_per_conversation" in (row.payload_json or "")
        ]
        assert blocked, "the one-gift rule must be visible in the audit log"


async def test_a_second_gift_is_explained_rather_than_retried(tool_context):
    """The one-gift rule has to reach the creator, not just the audit log.

    Left to the model this is a loop, and not a hypothetical one: on a live conversation
    every model call was rate-limited, the fallback kept trying to issue all three
    gifts, and the agent answered each refusal with "would you like to proceed?" while
    the creator kept saying yes.
    """
    async with tool_context("creator_005", conversation_id="conv_second_gift") as (ctx, _db):
        placed = await execute_tool(
            "issue_gift",
            {
                "creator_name": "Sofia Coppola",
                "sku": "GT-SERUM-001",
                "shipping_address": "902 Palm Drive, Miami, FL 33139",
            },
            ctx,
        )
        refused = await execute_tool(
            "issue_gift",
            {
                "creator_name": "Sofia Coppola",
                "sku": "GT-CLEANSER-002",
                "shipping_address": "902 Palm Drive, Miami, FL 33139",
            },
            ctx,
        )
        assert refused["error"] == "gift_already_issued"

    records = [
        ToolCallRecord(name="issue_gift", arguments={}, result=placed),
        ToolCallRecord(name="issue_gift", arguments={}, result=refused),
    ]

    verdict = guardrails.inspect_reply(
        "Would you like me to proceed with the order?",
        records=records,
        agent="gift_request_agent",
    )

    assert verdict.replaced, "the model's answer must not be the last word here"
    assert verdict.guardrail == "one_gift_per_conversation"
    assert "one gift per conversation" in verdict.replacement
    assert placed["order_id"] in verdict.replacement

    # A turn that placed its one gift and stopped is left alone.
    untouched = guardrails.inspect_reply(
        "Your Barrier Repair Serum is on its way.",
        records=[ToolCallRecord(name="issue_gift", arguments={}, result=placed)],
        agent="gift_request_agent",
    )
    assert untouched.replaced is False


async def test_issue_gift_is_idempotent(tool_context, gift_order_count):
    args = {
        "creator_name": "Kathryn Bigelow",
        "sku": "GT-SERUM-001",
        "shipping_address": "123 Example Street, Boston, MA 02110",
    }
    async with tool_context("creator_001", conversation_id="conv_idempotent") as (ctx, db):
        first = await execute_tool("issue_gift", args, ctx)
        second = await execute_tool("issue_gift", args, ctx)
        assert first["ok"] and second["ok"]
        assert second["duplicate"] is True
        assert second["order_id"] == first["order_id"]

        count = await db.scalar(
            select(func.count(GiftOrder.id)).where(GiftOrder.session_id == ctx.session_id)
        )
        assert count == 1


async def test_issue_gift_rejects_extra_fields(tool_context):
    async with tool_context("creator_001") as (ctx, _db):
        result = await execute_tool(
            "issue_gift",
            {
                "creator_name": "Kathryn Bigelow",
                "sku": "GT-SERUM-001",
                "shipping_address": "123 Example Street, Boston, MA 02110",
                "quantity": 5,
            },
            ctx,
        )
        assert result["ok"] is False
        assert result["error"] == "invalid_arguments"


async def test_issue_gift_checks_the_specific_gift(tool_context):
    """Paul fails the serum's criteria but clears the Discovery Set's."""
    async with tool_context("creator_003", conversation_id="conv_per_gift") as (ctx, _db):
        denied = await execute_tool(
            "issue_gift",
            {
                "creator_name": "Paul Thomas Anderson",
                "sku": "GT-SERUM-001",
                "shipping_address": "88 Maple Avenue, Austin, TX 78701",
            },
            ctx,
        )
        assert denied["ok"] is False
        assert denied["error"] == "eligibility_not_met"
        # The refusal names the gift he does qualify for.
        assert "Discovery Set" in denied["message"]

        allowed = await execute_tool(
            "issue_gift",
            {
                "creator_name": "Paul Thomas Anderson",
                "sku": "GT-SET-003",
                "shipping_address": "88 Maple Avenue, Austin, TX 78701",
            },
            ctx,
        )
        assert allowed["ok"] is True, allowed


async def test_issue_gift_rejects_unknown_sku(tool_context):
    async with tool_context("creator_001") as (ctx, _db):
        result = await execute_tool(
            "issue_gift",
            {"creator_name": "Kathryn Bigelow", "sku": "NOPE-999", "shipping_address": "x"},
            ctx,
        )
        assert result["ok"] is False
        assert result["error"] == "unknown_sku"


async def test_issue_gift_uses_the_verified_creator_name(tool_context):
    """Identity comes from the profile, even when the model passes a nickname."""
    async with tool_context("creator_001", conversation_id="conv_identity") as (ctx, db):
        result = await execute_tool(
            "issue_gift",
            {
                "creator_name": "Kathryn B. (biggest fan)",
                "sku": "GT-SERUM-001",
                "shipping_address": "123 Example Street, Boston, MA 02110",
            },
            ctx,
        )
        assert result["ok"] is True, result

        order = await db.scalar(select(GiftOrder).where(GiftOrder.session_id == ctx.session_id))
        assert order is not None
        assert order.creator_name == "Kathryn Bigelow"

        rows = await _events(ctx.conversation_id, db)
        assert any(row.type == "guardrail_adjusted" for row in rows)


async def test_issue_gift_refuses_an_address_that_does_not_validate(tool_context):
    """The order is the last step, and it will not run on an address that fails."""
    async with tool_context("creator_001", conversation_id="conv_bad_address") as (ctx, db):
        result = await execute_tool(
            "issue_gift",
            {
                "creator_name": "Kathryn Bigelow",
                "sku": "GT-SERUM-001",
                "shipping_address": "123 Example Street, Boston, MA",
            },
            ctx,
        )
        assert result["ok"] is False
        assert result["error"] == "address_invalid"
        # The refusal hands the model the correction to put to the creator.
        assert "123 Example Street, Boston, MA 02110" in result["message"]

        rows = await _events(ctx.conversation_id, db)
        blocked = [row for row in rows if row.type == "guardrail_blocked"]
        assert blocked and "address_invalid" in (blocked[0].payload_json or "")

        orders = await db.scalar(
            select(func.count(GiftOrder.id)).where(
                GiftOrder.conversation_id == ctx.conversation_id
            )
        )
        assert orders == 0


async def test_issue_gift_ships_to_a_new_address_when_it_validates(tool_context):
    """A creator who has moved can ship somewhere new -- as long as it validates."""
    async with tool_context("creator_001", conversation_id="conv_new_address") as (ctx, db):
        # A different SKU, so this is not caught by the per-SKU idempotency guard.
        result = await execute_tool(
            "issue_gift",
            {
                "creator_name": "Kathryn Bigelow",
                "sku": "GT-CLEANSER-002",
                "shipping_address": "500 New Road, Denver, CO 80202",
            },
            ctx,
        )
        assert result["ok"] is True, result
        assert result["shipping_address"] == "500 New Road, Denver, CO 80202"

        order = await db.scalar(
            select(GiftOrder).where(GiftOrder.conversation_id == ctx.conversation_id)
        )
        assert order is not None
        assert order.shipping_address == "500 New Road, Denver, CO 80202"

        rows = await _events(ctx.conversation_id, db)
        issued = [row for row in rows if row.type == "gift_issued"]
        # Shipping somewhere other than the address on file is worth recording.
        assert issued and '"address_changed": true' in (issued[0].payload_json or "")


async def test_validate_shipping_address_offers_a_correction(tool_context):
    async with tool_context("creator_001") as (ctx, db):
        result = await execute_tool(
            "validate_shipping_address",
            {"address": "123 Example Street, Boston, MA 02111"},
            ctx,
        )
        assert result["ok"] is True
        assert result["valid"] is False
        assert result["suggestion"] == "123 Example Street, Boston, MA 02110"
        # The tool tells the model what to do about it, not just what is wrong.
        assert "did not validate" in result["message"]
        assert "123 Example Street, Boston, MA 02110" in result["message"]
        assert "do not place an order" in result["message"]

        rows = await _events(ctx.conversation_id, db)
        assert any(row.type == "address_validated" for row in rows)


async def test_validate_shipping_address_accepts_the_address_on_file(tool_context):
    async with tool_context("creator_002") as (ctx, _db):
        result = await execute_tool(
            "validate_shipping_address",
            {"address": "88 Harbor Avenue, Austin, TX 78701"},
            ctx,
        )
        assert result["valid"] is True
        assert result["normalised"] == "88 Harbor Avenue, Austin, TX 78701"


async def test_verified_profile_beats_creator_claims(tool_context):
    """Creator 004's story never reaches the decision: only the DB profile does."""
    async with tool_context("creator_004") as (ctx, _db):
        result = await execute_tool(
            "check_gifting_eligibility", {"gift_id": "barrier-repair-serum"}, ctx
        )
        assert result["ok"] is True
        assert result["eligible"] is False
        assert set(result["offers"][0]["failed"]) == {
            "followers",
            "engagement_rate",
            "brand_attributed_revenue",
        }
        assert result["verified"]["followers"] == 8210


async def test_unknown_gift_id_is_refused(tool_context):
    async with tool_context("creator_001") as (ctx, _db):
        result = await execute_tool(
            "check_gifting_eligibility", {"gift_id": "free-yacht"}, ctx
        )
        assert result["ok"] is False
        assert result["error"] == "unknown_gift"


async def test_gift_identifiers_are_tolerant(tool_context):
    """Models pass whichever identifier they noticed -- id, SKU or product name."""
    config = config_manager.load_default_config()
    for identifier in ("barrier-repair-serum", "GT-SERUM-001", "gt-serum-001", "Barrier Repair Serum"):
        offer = config.gifting.resolve(identifier)
        assert offer is not None, identifier
        assert offer.id == "barrier-repair-serum"

    assert config.gifting.resolve("something else entirely") is None
    assert config.gifting.resolve("") is None

    # And the tool accepts them all, canonicalising `selected` to the gift id.
    async with tool_context("creator_001") as (ctx, _db):
        result = await execute_tool(
            "check_gifting_eligibility", {"gift_id": "GT-SERUM-001"}, ctx
        )
        assert result["ok"] is True, result
        assert result["selected"] == "barrier-repair-serum"
        assert result["eligible"] is True


async def test_criteria_come_from_live_config(tool_context):
    """Relaxing one gift's criteria changes the verdict -- with no code change."""
    async with tool_context("creator_003") as (ctx, db):
        before = await execute_tool(
            "check_gifting_eligibility", {"gift_id": "barrier-repair-serum"}, ctx
        )
        assert before["eligible"] is False

        _version, config = await config_manager.get_current(db)
        relaxed = config.model_copy(deep=True)
        serum = relaxed.gifting.offers[0]
        serum.criteria = serum.criteria[:1]  # keep only the follower rule
        serum.criteria[0].value = 1000
        await config_manager.save_config(db, relaxed, updated_by="test")

    async with tool_context("creator_003") as (ctx2, _db):
        after = await execute_tool(
            "check_gifting_eligibility", {"gift_id": "barrier-repair-serum"}, ctx2
        )
        assert after["eligible"] is True

    from app.db import AsyncSessionLocal

    async with AsyncSessionLocal() as db2:
        await config_manager.reset_to_default(db2, updated_by="test-cleanup")


async def test_gift_search_is_deterministic():
    """find_available_gifts ranks on token overlap, with no model involved."""
    config = config_manager.load_default_config()
    matches = evaluation.search_offers(config, "something for dry skin")
    assert matches and matches[0]["gift_id"] == "barrier-repair-serum"

    cleanser = evaluation.search_offers(config, "a gentle cleanser")
    assert cleanser and cleanser[0]["gift_id"] == "hydrating-cleanser"

    # No overlap -> no matches, which is what makes the agent ask a question.
    assert evaluation.search_offers(config, "a leather jacket") == []
    # An empty query returns the whole catalog.
    assert len(evaluation.search_offers(config, "")) == len(config.gifting.offers)

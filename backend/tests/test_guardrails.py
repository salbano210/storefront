"""Guardrail tests: the business rules that must never be bypassed by a model."""

from __future__ import annotations

import pytest
from sqlalchemy import func, select

from app import evaluation
from app.db import AsyncSessionLocal
from app.models import Conversation, Event, GiftOrder, TagApplied
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


async def test_issue_gift_uses_verified_identity(tool_context):
    async with tool_context("creator_001", conversation_id="conv_identity") as (ctx, db):
        result = await execute_tool(
            "issue_gift",
            {
                "creator_name": "Kathryn B. (biggest fan)",
                "sku": "GT-SERUM-001",
                "shipping_address": "Somewhere else entirely",
            },
            ctx,
        )
        assert result["ok"] is True
        assert result["shipping_address"] == "123 Example Street, Boston, MA 02110"

        order = await db.scalar(select(GiftOrder).where(GiftOrder.session_id == ctx.session_id))
        assert order is not None
        assert order.creator_name == "Kathryn Bigelow"
        assert order.shipping_address == "123 Example Street, Boston, MA 02110"

        rows = await _events(ctx.conversation_id, db)
        assert any(row.type == "guardrail_adjusted" for row in rows)


async def test_place_tag_rejects_unconfigured_label(tool_context):
    async with tool_context("creator_005", agent="creator_pitch_agent") as (ctx, _db):
        result = await execute_tool(
            "place_tag", {"creator_id": "creator_005", "tag": "Best Ever"}, ctx
        )
        assert result["ok"] is False
        assert result["error"] == "unknown_label"


async def test_place_tag_rejects_wrong_label_for_score(tool_context):
    async with tool_context("creator_003", agent="creator_pitch_agent") as (ctx, _db):
        result = await execute_tool(
            "place_tag", {"creator_id": "creator_003", "tag": "Excellent Match"}, ctx
        )
        assert result["ok"] is False
        assert result["error"] == "tag_mismatch"
        assert "Promising" in result["message"]


async def test_place_tag_rejects_creator_mismatch(tool_context):
    async with tool_context("creator_005", agent="creator_pitch_agent") as (ctx, _db):
        result = await execute_tool(
            "place_tag", {"creator_id": "creator_001", "tag": "Excellent Match"}, ctx
        )
        assert result["ok"] is False
        assert result["error"] == "creator_mismatch"


async def test_place_tag_applies_computed_label(tool_context):
    async with tool_context("creator_005", agent="creator_pitch_agent") as (ctx, db):
        result = await execute_tool(
            "place_tag", {"creator_id": "creator_005", "tag": "Excellent Match"}, ctx
        )
        assert result["ok"] is True
        assert result["decision"] == "pass"
        row = await db.scalar(select(TagApplied).where(TagApplied.session_id == ctx.session_id))
        assert row is not None and row.tag == "Excellent Match"
        assert row.decision == "pass"

        # The decision lands on the conversation, not just the tag row.
        conversation = await db.get(Conversation, ctx.conversation_id)
        assert conversation is not None and conversation.decision == "pass"

        rows = await _events(ctx.conversation_id, db)
        assert any(item.type == "tag_applied" for item in rows)


async def test_place_tag_records_a_reject_decision(tool_context):
    """A 'Not a fit' tag must record a reject on the conversation."""
    async with tool_context("creator_004", agent="creator_pitch_agent") as (ctx, db):
        result = await execute_tool(
            "place_tag", {"creator_id": "creator_004", "tag": "Not a fit"}, ctx
        )
        assert result["ok"] is True
        assert result["decision"] == "reject"
        assert result["response_instructions"]

        conversation = await db.get(Conversation, ctx.conversation_id)
        assert conversation is not None and conversation.decision == "reject"


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


async def test_pitch_scores_produce_expected_labels():
    config = config_manager.load_default_config()
    expected = {
        "creator_001": "Excellent Match",
        "creator_002": "Not a fit",
        "creator_003": "Promising",
        "creator_004": "Not a fit",
        "creator_005": "Excellent Match",
    }
    async with AsyncSessionLocal() as db:
        for creator_id, label in expected.items():
            profile = await data_store.get_creator(db, creator_id)
            result = evaluation.pitch_evaluation(profile, config)
            assert result["label"] == label, f"{creator_id} scored {result['score']}"
            assert result["decision"] in {"pass", "reject"}


async def test_martin_sits_just_below_the_pass_line():
    """The demo lever: moving one threshold flips a decision."""
    config = config_manager.load_default_config()
    async with AsyncSessionLocal() as db:
        profile = await data_store.get_creator(db, "creator_002")
        before = evaluation.pitch_evaluation(profile, config)
        assert before["decision"] == "reject"
        assert before["score"] < 55

        relaxed = config.model_copy(deep=True)
        promising = next(
            label for label in relaxed.pitch.classification_labels if label.label == "Promising"
        )
        promising.min_score = 50
        after = evaluation.pitch_evaluation(profile, relaxed)
        assert after["decision"] == "pass"
        assert after["label"] == "Promising"


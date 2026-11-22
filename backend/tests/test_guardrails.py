"""Guardrail tests: the business rules that must never be bypassed by a model."""

from __future__ import annotations

import pytest
from sqlalchemy import func, select

from app import evaluation
from app.models import Event, GiftOrder, TagApplied
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
                "creator_name": "Aisha Patel",
                "sku": "GT-SERUM-001",
                "shipping_address": "42 Cedar Lane, Brooklyn, NY 11201",
            },
            ctx,
        )
        assert result["ok"] is False
        assert result["error"] == "eligibility_not_met"
        assert "Follower count" in result["message"]

        rows = await _events(ctx.conversation_id, db)
        blocked = [row for row in rows if row.type == "guardrail_blocked"]
        assert blocked, "expected a guardrail_blocked event"
        assert blocked[0].status == "blocked"
        session_id = ctx.session_id

    assert await gift_order_count(session_id) == 0


async def test_issue_gift_happy_path(tool_context, gift_order_count):
    async with tool_context("creator_001") as (ctx, db):
        result = await execute_tool(
            "issue_gift",
            {
                "creator_name": "Jessica Miller",
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


async def test_issue_gift_is_idempotent(tool_context, gift_order_count):
    args = {
        "creator_name": "Jessica Miller",
        "sku": "GT-SERUM-001",
        "shipping_address": "123 Example Street, Boston, MA 02110",
    }
    async with tool_context("creator_001") as (ctx, db):
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
                "creator_name": "Jessica Miller",
                "sku": "GT-SERUM-001",
                "shipping_address": "123 Example Street, Boston, MA 02110",
                "quantity": 5,
            },
            ctx,
        )
        assert result["ok"] is False
        assert result["error"] == "invalid_arguments"


async def test_issue_gift_rejects_non_giftable_sku(tool_context):
    async with tool_context("creator_001") as (ctx, _db):
        result = await execute_tool(
            "issue_gift",
            {
                "creator_name": "Jessica Miller",
                "sku": "GT-KIT-099",
                "shipping_address": "123 Example Street, Boston, MA 02110",
            },
            ctx,
        )
        assert result["ok"] is False
        assert result["error"] == "sku_not_eligible"


async def test_issue_gift_rejects_unknown_sku(tool_context):
    async with tool_context("creator_001") as (ctx, _db):
        result = await execute_tool(
            "issue_gift",
            {"creator_name": "Jessica Miller", "sku": "NOPE-999", "shipping_address": "x"},
            ctx,
        )
        assert result["ok"] is False
        assert result["error"] == "unknown_sku"


async def test_issue_gift_uses_verified_identity(tool_context):
    async with tool_context("creator_001") as (ctx, db):
        result = await execute_tool(
            "issue_gift",
            {
                "creator_name": "Jessica M. (biggest fan)",
                "sku": "GT-MASK-003",
                "shipping_address": "Somewhere else entirely",
            },
            ctx,
        )
        assert result["ok"] is True
        assert result["shipping_address"] == "123 Example Street, Boston, MA 02110"

        order = await db.scalar(select(GiftOrder).where(GiftOrder.session_id == ctx.session_id))
        assert order is not None
        assert order.creator_name == "Jessica Miller"
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
        assert "Moderate Match" in result["message"]


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
        row = await db.scalar(select(TagApplied).where(TagApplied.session_id == ctx.session_id))
        assert row is not None and row.tag == "Excellent Match"

        rows = await _events(ctx.conversation_id, db)
        assert any(item.type == "tag_applied" for item in rows)


async def test_verified_profile_beats_creator_claims(tool_context):
    """Creator 004's story never reaches the decision: only the DB profile does."""
    async with tool_context("creator_004") as (ctx, _db):
        result = await execute_tool("check_gifting_eligibility", {}, ctx)
        assert result["ok"] is True
        assert result["eligible"] is False
        assert set(result["failed"]) == {
            "minimum_followers",
            "minimum_engagement_rate",
            "minimum_brand_revenue",
        }
        assert result["verified"]["followers"] == 8210


async def test_criteria_come_from_live_config(tool_context):
    """Lowering a threshold changes the verdict -- with no code change."""
    async with tool_context("creator_003") as (ctx, db):
        before = await execute_tool("check_gifting_eligibility", {}, ctx)
        assert before["eligible"] is False

        _version, config = await config_manager.get_current(db)
        relaxed = config.model_copy(deep=True)
        relaxed.gifting.criteria.minimum_followers = 1000
        relaxed.gifting.criteria.minimum_brand_revenue = 0
        await config_manager.save_config(db, relaxed, updated_by="test")

    async with tool_context("creator_003") as (ctx2, _db):
        after = await execute_tool("check_gifting_eligibility", {}, ctx2)
        assert after["eligible"] is True

    from app.db import AsyncSessionLocal

    async with AsyncSessionLocal() as db2:
        await config_manager.reset_to_default(db2, updated_by="test-cleanup")


async def test_pitch_scores_produce_expected_labels():
    config = config_manager.load_default_config()
    expected = {
        "creator_001": "Excellent Match",
        "creator_002": "Moderate Match",
        "creator_003": "Moderate Match",
        "creator_004": "Poor Match",
        "creator_005": "Excellent Match",
    }
    for creator_id, label in expected.items():
        profile = data_store.get_creator(creator_id)
        result = evaluation.pitch_evaluation(profile, config)
        assert result["label"] == label, f"{creator_id} scored {result['score']}"


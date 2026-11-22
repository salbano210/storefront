"""Tool registry: schemas, validation and guarded execution.

Every tool call is validated with Pydantic and logged. The side-effecting tools
(``issue_gift``, ``place_tag``) re-run the deterministic rules themselves and
refuse to act when the rules are not satisfied -- an LLM cannot talk its way past
them, and it cannot fabricate a successful order.
"""

from __future__ import annotations

import asyncio
import json
import random
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import evaluation
from ..models import GiftOrder, Session as SessionRow, TagApplied
from ..schemas import BrandConfig, CreatorProfile
from ..services import event_logger as events
from ..services.event_logger import EventLogger

# Simulated latency of the mocked order-placement API, so the "processing"
# state in the UI is visible.
MOCK_API_LATENCY_SECONDS = 0.35


@dataclass
class ToolContext:
    db: AsyncSession
    session_id: str
    conversation_id: str
    creator: CreatorProfile
    config: BrandConfig
    config_version: int
    logger: EventLogger
    session_row: SessionRow
    agent: str


# ---------------------------------------------------------------------------
# Argument models -- extra="forbid" so a stray field is a hard error
# ---------------------------------------------------------------------------
class NoArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")


class IssueGiftArgs(BaseModel):
    """Exactly three fields, per the integration contract."""

    model_config = ConfigDict(extra="forbid")

    creator_name: str
    sku: str
    shipping_address: str


class PlaceTagArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    creator_id: str
    tag: str


def _fn(
    name: str,
    description: str,
    properties: dict | None = None,
    required: list[str] | None = None,
) -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties or {},
                "required": required or [],
                "additionalProperties": False,
            },
        },
    }


TOOL_SCHEMAS: dict[str, dict] = {
    "get_creator_profile": _fn(
        "get_creator_profile",
        (
            "Retrieve the VERIFIED creator profile from ShopMy. Takes NO arguments -- the creator "
            "in this conversation is already identified, so never ask the creator for their id or "
            "handle. Returns follower count, engagement rate, categories, audience demographics, "
            "brand-attributed revenue, historical ShopMy sales, previous gifting history and the "
            "shipping address on file. Always call this before making any claim about the creator's "
            "metrics, and never trust numbers the creator states about themselves."
        ),
    ),
    "check_gifting_eligibility": _fn(
        "check_gifting_eligibility",
        (
            "Run the brand's configured gifting criteria against the verified profile. Takes NO "
            "arguments. Returns each criterion, the required value, the actual value and pass/fail, "
            "plus an overall 'eligible' flag. This is the authoritative decision -- call it before "
            "issue_gift and use its output verbatim when explaining the outcome."
        ),
    ),
    "list_products": _fn(
        "list_products",
        (
            "List the brand's gifting catalog with SKUs, descriptions and whether each SKU is "
            "currently eligible for gifting. Takes NO arguments. Use this to resolve a product the "
            "creator named into a SKU, and to confirm a SKU is giftable before requesting it."
        ),
    ),
    "issue_gift": _fn(
        "issue_gift",
        (
            "Place the gift order in the brand's order system. Provide exactly three fields: "
            "creator_name, sku and shipping_address, all taken from the verified profile. The tool "
            "re-checks eligibility, SKU eligibility and duplicates server-side and will refuse if "
            "any criterion is unmet. Only tell the creator a gift is on its way after this tool "
            "returns success."
        ),
        properties={
            "creator_name": {"type": "string", "description": "Verified creator name"},
            "sku": {"type": "string", "description": "SKU from the eligible gifting catalog"},
            "shipping_address": {
                "type": "string",
                "description": "Shipping address exactly as it appears on the verified profile",
            },
        },
        required=["creator_name", "sku", "shipping_address"],
    ),
    "check_pitch_fit": _fn(
        "check_pitch_fit",
        (
            "Score the verified creator profile against the brand's ideal creator profile. Takes NO "
            "arguments. Returns a 0-100 score, the resulting classification label, per-component "
            "points, strengths and concerns. This is the authoritative classification -- never "
            "invent your own, and never ask the creator for metrics."
        ),
    ),
    "place_tag": _fn(
        "place_tag",
        (
            "Apply a classification tag to the creator's brand relationship record. The tag must be "
            "exactly the label returned by check_pitch_fit."
        ),
        properties={
            "creator_id": {"type": "string", "description": "Verified creator id"},
            "tag": {"type": "string", "description": "Label returned by check_pitch_fit"},
        },
        required=["creator_id", "tag"],
    ),
}

AGENT_TOOLS: dict[str, list[str]] = {
    "gift_request_agent": [
        "get_creator_profile",
        "check_gifting_eligibility",
        "list_products",
        "issue_gift",
    ],
    "creator_pitch_agent": ["get_creator_profile", "check_pitch_fit", "place_tag"],
    "general_handler": [],
}


def tool_definitions(agent: str) -> list[dict]:
    return [TOOL_SCHEMAS[name] for name in AGENT_TOOLS.get(agent, [])]


# ---------------------------------------------------------------------------
# Executors
# ---------------------------------------------------------------------------
def _ok(**payload: Any) -> dict[str, Any]:
    return {"ok": True, **payload}


def _fail(error: str, message: str, **extra: Any) -> dict[str, Any]:
    return {"ok": False, "error": error, "message": message, **extra}


async def _get_creator_profile(args: dict, ctx: ToolContext) -> dict:
    profile = ctx.creator
    await ctx.logger.log(
        events.PROFILE_LOOKUP,
        agent=ctx.agent,
        summary=(
            f"Verified profile for {profile.name}: "
            f"{evaluation.fmt_int(profile.followers)} followers, "
            f"{evaluation.fmt_pct(profile.engagement_rate)} engagement, "
            f"{evaluation.fmt_money(profile.brand_attributed_revenue)} brand revenue"
        ),
        payload={
            "creator_id": profile.id,
            "source": "mock ShopMy creator profile API (authoritative)",
            "profile": profile.model_dump(),
        },
    )
    return _ok(creator=profile.model_dump(), source="verified_profile")


async def _check_gifting_eligibility(args: dict, ctx: ToolContext) -> dict:
    result = evaluation.evaluate_gifting(ctx.creator, ctx.config)
    await ctx.logger.log(
        events.CRITERIA_EVALUATED,
        agent=ctx.agent,
        status="ok" if result["eligible"] else "blocked",
        summary=(
            "All gifting criteria met"
            if result["eligible"]
            else "Unmet criteria: " + ", ".join(result["failed_labels"])
        ),
        payload={
            "policy": "gifting",
            "eligible": result["eligible"],
            "checks": result["checks"],
            "verified_profile_values": result["verified"],
            "creator_stated_values_used": False,
        },
    )
    return _ok(**result)


async def _list_products(args: dict, ctx: ToolContext) -> dict:
    catalog = [
        {
            "sku": product.sku,
            "name": product.name,
            "description": product.description,
            "eligible_for_gifting": product.eligible,
            "retail_price": product.retail_price,
        }
        for product in ctx.config.gifting.eligible_products
    ]
    giftable = sum(1 for item in catalog if item["eligible_for_gifting"])
    return _ok(products=catalog, giftable_count=giftable, catalog_size=len(catalog))


async def _check_pitch_fit(args: dict, ctx: ToolContext) -> dict:
    result = evaluation.pitch_evaluation(ctx.creator, ctx.config)
    await ctx.logger.log(
        events.CRITERIA_EVALUATED,
        agent=ctx.agent,
        summary=f"Pitch fit score {result['score']} -> {result['label']}",
        payload={
            "policy": "pitch",
            "score": result["score"],
            "label": result["label"],
            "allowed_labels": result["labels"],
            "components": result["components"],
            "strengths": result["strengths"],
            "concerns": result["concerns"],
            "minimums_met": result["minimums_met"],
            "verified_profile_values": result["verified"],
            "creator_stated_values_used": False,
        },
    )
    return _ok(**result)



def _normalise(value: str) -> str:
    return " ".join((value or "").strip().lower().split())


async def _issue_gift(args: dict, ctx: ToolContext) -> dict:
    profile = ctx.creator
    sku = (args.get("sku") or "").strip().upper()

    product = evaluation.find_product(ctx.config, sku)
    if product is None:
        await ctx.logger.log(
            events.GUARDRAIL_BLOCKED,
            agent=ctx.agent,
            status="blocked",
            summary=f"Refused issue_gift: unknown SKU {sku or '(empty)'}",
            payload={
                "guardrail": "unknown_sku",
                "requested_sku": sku,
                "catalog": [p.sku for p in ctx.config.gifting.eligible_products],
            },
        )
        return _fail(
            "unknown_sku",
            f"SKU '{sku}' is not in the brand catalog. Call list_products and use an exact SKU.",
        )

    if not product.eligible:
        await ctx.logger.log(
            events.GUARDRAIL_BLOCKED,
            agent=ctx.agent,
            status="blocked",
            summary=f"Refused issue_gift: {product.sku} is not giftable",
            payload={
                "guardrail": "sku_not_eligible",
                "sku": product.sku,
                "product": product.name,
                "reason": "SKU is in the catalog but flagged ineligible for gifting",
            },
        )
        return _fail(
            "sku_not_eligible",
            f"{product.name} ({product.sku}) is not eligible for gifting. Do not offer it as a gift.",
        )

    eligibility = evaluation.evaluate_gifting(profile, ctx.config)
    if not eligibility["eligible"]:
        await ctx.logger.log(
            events.GUARDRAIL_BLOCKED,
            agent=ctx.agent,
            status="blocked",
            summary="Refused issue_gift: gifting criteria not met ("
            + ", ".join(eligibility["failed_labels"])
            + ")",
            payload={
                "guardrail": "eligibility_not_met",
                "failed_criteria": eligibility["failed"],
                "checks": eligibility["checks"],
                "verified_profile_values": eligibility["verified"],
                "creator_stated_values_used": False,
            },
        )
        return _fail(
            "eligibility_not_met",
            "Gifting criteria are not met: "
            + "; ".join(
                f"{check['label']} requires {check['required_display']} but verified value is "
                f"{check['actual_display']}"
                for check in eligibility["checks"]
                if not check["passed"]
            )
            + ". Do not issue a gift. Politely explain the shortfall using these verified numbers.",
        )

    adjustments: list[dict] = []
    if _normalise(args.get("creator_name", "")) != _normalise(profile.name):
        adjustments.append(
            {
                "field": "creator_name",
                "attempted": args.get("creator_name"),
                "used": profile.name,
            }
        )
    if _normalise(args.get("shipping_address", "")) != _normalise(profile.shipping_address):
        adjustments.append(
            {
                "field": "shipping_address",
                "attempted": args.get("shipping_address"),
                "used": profile.shipping_address,
            }
        )
    if adjustments:
        await ctx.logger.log(
            events.GUARDRAIL_ADJUSTED,
            agent=ctx.agent,
            summary="Rewrote gift details to match the verified profile",
            payload={"guardrail": "verified_values_enforced", "adjustments": adjustments},
        )

    existing = await ctx.db.scalar(
        select(GiftOrder).where(
            GiftOrder.session_id == ctx.session_id,
            GiftOrder.creator_id == profile.id,
            GiftOrder.sku == product.sku,
        )
    )
    if existing is not None:
        await ctx.logger.log(
            events.TOOL_CALL_COMPLETED,
            agent=ctx.agent,
            summary=f"Duplicate gift suppressed for {product.sku} (existing order {existing.order_id})",
            payload={
                "duplicate": True,
                "order_id": existing.order_id,
                "sku": product.sku,
                "original_created_at": str(existing.created_at),
            },
        )
        return _ok(
            duplicate=True,
            order_id=existing.order_id,
            sku=product.sku,
            product=product.name,
            shipping_address=existing.shipping_address,
            message="A gift for this SKU was already issued for this creator; no new order was placed.",
        )

    await asyncio.sleep(MOCK_API_LATENCY_SECONDS)
    order_id = f"GT-{random.randint(10000, 99999)}"
    ctx.db.add(
        GiftOrder(
            session_id=ctx.session_id,
            conversation_id=ctx.conversation_id,
            creator_id=profile.id,
            creator_name=profile.name,
            sku=product.sku,
            product_name=product.name,
            order_id=order_id,
            shipping_address=profile.shipping_address,
        )
    )
    await ctx.db.commit()
    await ctx.logger.log(
        events.GIFT_ISSUED,
        agent=ctx.agent,
        summary=f"Gift issued: {product.name} ({product.sku}) -> order {order_id}",
        payload={
            "order_id": order_id,
            "sku": product.sku,
            "product": product.name,
            "creator_id": profile.id,
            "creator_name": profile.name,
            "shipping_address": profile.shipping_address,
            "mock_api": "mock order placement API",
        },
    )
    return _ok(
        duplicate=False,
        order_id=order_id,
        sku=product.sku,
        product=product.name,
        shipping_address=profile.shipping_address,
        message=f"Gift order {order_id} placed successfully.",
    )



async def _place_tag(args: dict, ctx: ToolContext) -> dict:
    profile = ctx.creator
    creator_id = (args.get("creator_id") or "").strip()
    tag = (args.get("tag") or "").strip()

    if creator_id and creator_id != profile.id:
        await ctx.logger.log(
            events.GUARDRAIL_BLOCKED,
            agent=ctx.agent,
            status="blocked",
            summary=f"Refused place_tag: creator mismatch ({creator_id})",
            payload={"guardrail": "creator_mismatch", "attempted": creator_id, "expected": profile.id},
        )
        return _fail(
            "creator_mismatch",
            f"That creator id does not match this conversation. Use '{profile.id}'.",
        )

    allowed = [item.label for item in ctx.config.pitch.classification_labels]
    if tag not in allowed:
        await ctx.logger.log(
            events.GUARDRAIL_BLOCKED,
            agent=ctx.agent,
            status="blocked",
            summary=f"Refused place_tag: '{tag}' is not a configured label",
            payload={"guardrail": "unknown_label", "attempted": tag, "allowed_labels": allowed},
        )
        return _fail(
            "unknown_label",
            f"'{tag}' is not a configured classification label. Allowed labels: {', '.join(allowed)}.",
        )

    fit = evaluation.pitch_evaluation(profile, ctx.config)
    if tag != fit["label"]:
        await ctx.logger.log(
            events.GUARDRAIL_BLOCKED,
            agent=ctx.agent,
            status="blocked",
            summary=f"Refused place_tag: '{tag}' does not match the computed label '{fit['label']}'",
            payload={
                "guardrail": "tag_mismatch",
                "attempted": tag,
                "expected": fit["label"],
                "score": fit["score"],
                "components": fit["components"],
            },
        )
        return _fail(
            "tag_mismatch",
            f"check_pitch_fit scored this creator {fit['score']}/100, which maps to "
            f"'{fit['label']}', not '{tag}'. Call place_tag again with exactly '{fit['label']}'.",
        )

    existing = await ctx.db.scalar(
        select(TagApplied).where(
            TagApplied.session_id == ctx.session_id,
            TagApplied.creator_id == profile.id,
            TagApplied.tag == tag,
        )
    )
    if existing is not None:
        await ctx.logger.log(
            events.TOOL_CALL_COMPLETED,
            agent=ctx.agent,
            summary=f"Duplicate tag suppressed: '{tag}' already applied",
            payload={"duplicate": True, "tag": tag, "applied_at": str(existing.created_at)},
        )
        return _ok(
            duplicate=True,
            tag=tag,
            score=fit["score"],
            message=f"'{tag}' was already applied to this creator; no duplicate tag was placed.",
        )

    await asyncio.sleep(MOCK_API_LATENCY_SECONDS)
    ctx.db.add(
        TagApplied(
            session_id=ctx.session_id,
            conversation_id=ctx.conversation_id,
            creator_id=profile.id,
            tag=tag,
            score=fit["score"],
        )
    )
    await ctx.db.commit()
    await ctx.logger.log(
        events.TAG_APPLIED,
        agent=ctx.agent,
        summary=f"Tag applied: '{tag}' (score {fit['score']})",
        payload={
            "creator_id": profile.id,
            "tag": tag,
            "score": fit["score"],
            "components": fit["components"],
            "mock_api": "mock ShopMy tag API",
        },
    )
    return _ok(duplicate=False, tag=tag, score=fit["score"], message=f"Tag '{tag}' applied.")


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------
ARG_MODELS: dict[str, type[BaseModel]] = {
    "get_creator_profile": NoArgs,
    "check_gifting_eligibility": NoArgs,
    "list_products": NoArgs,
    "check_pitch_fit": NoArgs,
    "issue_gift": IssueGiftArgs,
    "place_tag": PlaceTagArgs,
}

EXECUTORS: dict[str, Any] = {
    "get_creator_profile": _get_creator_profile,
    "check_gifting_eligibility": _check_gifting_eligibility,
    "list_products": _list_products,
    "check_pitch_fit": _check_pitch_fit,
    "issue_gift": _issue_gift,
    "place_tag": _place_tag,
}


async def execute_tool(name: str, raw_arguments: Any, ctx: ToolContext) -> dict[str, Any]:
    """Validate, log and run one tool call. Never raises -- errors are returned."""
    if name not in EXECUTORS:
        await ctx.logger.log(
            events.TOOL_CALL_FAILED,
            agent=ctx.agent,
            status="error",
            summary=f"Unknown tool '{name}'",
            payload={"tool": name, "available": sorted(EXECUTORS)},
        )
        return _fail("unknown_tool", f"'{name}' is not an available tool.")

    if isinstance(raw_arguments, str):
        try:
            parsed = json.loads(raw_arguments or "{}")
        except (json.JSONDecodeError, ValueError) as exc:
            await ctx.logger.log(
                events.TOOL_CALL_FAILED,
                agent=ctx.agent,
                status="error",
                summary=f"{name}: malformed JSON arguments",
                payload={"tool": name, "raw_arguments": raw_arguments, "error": str(exc)},
            )
            return _fail("invalid_arguments", f"Arguments were not valid JSON: {exc}")
    else:
        parsed = dict(raw_arguments or {})

    if not isinstance(parsed, dict):
        return _fail("invalid_arguments", "Arguments must be a JSON object.")

    try:
        validated = ARG_MODELS[name](**parsed).model_dump()
    except ValidationError as exc:
        errors = [
            {"field": ".".join(str(part) for part in error["loc"]), "error": error["msg"]}
            for error in exc.errors()
        ]
        await ctx.logger.log(
            events.TOOL_CALL_FAILED,
            agent=ctx.agent,
            status="error",
            summary=f"{name}: argument validation failed",
            payload={"tool": name, "arguments": parsed, "errors": errors},
        )
        return _fail("invalid_arguments", f"Invalid arguments for {name}: {errors}", errors=errors)

    await ctx.logger.log(
        events.TOOL_CALL_STARTED,
        agent=ctx.agent,
        summary=f"{name}({', '.join(validated) if validated else 'no arguments'})",
        payload={"tool": name, "arguments": validated},
    )

    try:
        result = await EXECUTORS[name](validated, ctx)
    except Exception as exc:  # noqa: BLE001 - tool failures are data, not crashes
        await ctx.logger.log(
            events.TOOL_CALL_FAILED,
            agent=ctx.agent,
            status="error",
            summary=f"{name} raised {type(exc).__name__}",
            payload={"tool": name, "arguments": validated, "error": f"{type(exc).__name__}: {exc}"},
        )
        return _fail("tool_error", f"{name} failed: {type(exc).__name__}: {exc}")

    ok = bool(result.get("ok"))
    await ctx.logger.log(
        events.TOOL_CALL_COMPLETED,
        agent=ctx.agent,
        status="ok" if ok else "blocked",
        summary=f"{name} -> {'success' if ok else result.get('error', 'refused')}",
        payload={"tool": name, "arguments": validated, "result": result},
    )
    return result



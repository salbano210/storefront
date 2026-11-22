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
from ..models import Conversation, GiftOrder, Session as SessionRow, TagApplied
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


class FindGiftsArgs(BaseModel):
    """Free-text description of what the creator asked about. Empty = whole catalog."""

    model_config = ConfigDict(extra="forbid")

    query: str = ""


class CheckEligibilityArgs(BaseModel):
    """Optional gift id. Empty means 'evaluate every gift the brand offers'."""

    model_config = ConfigDict(extra="forbid")

    gift_id: str = ""


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
            "Retrieve the VERIFIED creator profile from Storefront. Takes NO arguments -- the creator "
            "in this conversation is already identified, so never ask the creator for their id or "
            "handle. Returns follower count, engagement rate, categories, audience demographics, "
            "brand-attributed revenue, historical Storefront sales, previous gifting history and the "
            "shipping address on file. Always call this before making any claim about the creator's "
            "metrics, and never trust numbers the creator states about themselves."
        ),
    ),
    "find_available_gifts": _fn(
        "find_available_gifts",
        (
            "Find the gifts this brand offers that match what the creator asked about. Pass the "
            "creator's own words as 'query' (for example 'something for dry skin'), or leave it "
            "empty to see every gift. Returns each gift's id, name, SKU, description, price and "
            "link, ranked by how well it matches. Use it to work out which gift is being discussed, "
            "and to get the link to share. It does not decide eligibility -- check_gifting_eligibility does."
        ),
        properties={
            "query": {
                "type": "string",
                "description": "What the creator asked about, in their own words. Empty for all gifts.",
            }
        },
    ),
    "check_gifting_eligibility": _fn(
        "check_gifting_eligibility",
        (
            "Run a gift's own configured criteria against the verified profile. Pass the gift you "
            "matched with find_available_gifts -- its id, SKU or product name all work -- or leave "
            "it empty to evaluate every gift at once. Returns each gift's criteria with the required "
            "value, the actual value and pass/fail, plus which gifts the creator does qualify for. "
            "This is the authoritative decision -- call it before issue_gift and use its output "
            "verbatim when explaining the outcome."
        ),
        properties={
            "gift_id": {
                "type": "string",
                "description": "Gift id, SKU or product name. Empty to evaluate every gift.",
            }
        },
    ),
    "issue_gift": _fn(
        "issue_gift",
        (
            "Place the gift order in the brand's order system. Provide exactly three fields: "
            "creator_name, sku and shipping_address, all taken from the verified profile. The tool "
            "re-checks that gift's own criteria, the SKU and duplicates server-side, and will refuse "
            "if anything is unmet. Only tell the creator a gift is on its way after this tool "
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
        "find_available_gifts",
        "check_gifting_eligibility",
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
            "source": "mock Storefront creator profile API (authoritative)",
            "profile": profile.model_dump(),
        },
    )
    return _ok(creator=profile.model_dump(), source="verified_profile")


async def _find_available_gifts(args: dict, ctx: ToolContext) -> dict:
    query = (args.get("query") or "").strip()
    matches = evaluation.search_offers(ctx.config, query)

    # Flag eligibility inline: one call is enough to pick the right gift to offer.
    eligibility = evaluation.evaluate_gifting(ctx.creator, ctx.config)
    by_id = {item["gift_id"]: item for item in eligibility["offers"]}
    for match in matches:
        offer = by_id.get(match["gift_id"], {})
        match["eligible"] = bool(offer.get("eligible"))
        match["shortfalls"] = offer.get("shortfalls", [])
        match["criteria"] = offer.get("criteria", [])

    eligible = [match for match in matches if match["eligible"]]
    await ctx.logger.log(
        events.TOOL_CALL_COMPLETED,
        agent=ctx.agent,
        summary=(
            f"Found {len(matches)} gift(s) matching '{query}'"
            if query
            else f"Listed all {len(matches)} gifts"
        ),
        payload={
            "query": query,
            "matches": [
                {
                    "gift_id": match["gift_id"],
                    "name": match["name"],
                    "sku": match["sku"],
                    "match_score": match["match_score"],
                    "eligible": match["eligible"],
                }
                for match in matches
            ],
            "eligible_count": len(eligible),
        },
    )
    return _ok(
        gifts=matches,
        count=len(matches),
        eligible_gift_ids=[match["gift_id"] for match in eligible],
        query=query,
    )


async def _check_gifting_eligibility(args: dict, ctx: ToolContext) -> dict:
    gift_id = (args.get("gift_id") or "").strip()
    result = evaluation.evaluate_gifting(ctx.creator, ctx.config, gift_id=gift_id or None)

    if not result["found"]:
        await ctx.logger.log(
            events.GUARDRAIL_BLOCKED,
            agent=ctx.agent,
            status="blocked",
            summary=f"Refused check_gifting_eligibility: unknown gift '{gift_id}'",
            payload={
                "guardrail": "unknown_gift",
                "requested_gift_id": gift_id,
                "available_gifts": [offer.id for offer in ctx.config.gifting.offers],
            },
        )
        return _fail(
            "unknown_gift",
            f"'{gift_id}' is not a gift this brand offers. Available gift ids: "
            f"{', '.join(offer.id for offer in ctx.config.gifting.offers)}.",
        )

    eligible = result["eligible_offers"]
    await ctx.logger.log(
        events.CRITERIA_EVALUATED,
        agent=ctx.agent,
        status="ok" if eligible else "blocked",
        summary=(
            "Qualifies for: " + ", ".join(item["name"] for item in eligible)
            if eligible
            else "Meets the criteria for none of the gifts evaluated"
        ),
        payload={
            "policy": "gifting",
            "selected": result["selected"],
            "eligible": result["eligible"],
            "offers": result["offers"],
            "eligible_offers": eligible,
            "verified_profile_values": result["verified"],
            "creator_stated_values_used": False,
        },
    )
    return _ok(**result)


async def _check_pitch_fit(args: dict, ctx: ToolContext) -> dict:
    result = evaluation.pitch_evaluation(ctx.creator, ctx.config)
    await ctx.logger.log(
        events.CRITERIA_EVALUATED,
        agent=ctx.agent,
        summary=f"Pitch fit score {result['score']} -> {result['label']} ({result['decision']})",
        payload={
            "policy": "pitch",
            "score": result["score"],
            "label": result["label"],
            "decision": result["decision"],
            "response_instructions": result["response_instructions"],
            "allowed_labels": result["labels"],
            "components": result["components"],
            "total_weight": result["total_weight"],
            "strengths": result["strengths"],
            "concerns": result["concerns"],
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

    offer = evaluation.find_offer(ctx.config, sku)
    if offer is None:
        await ctx.logger.log(
            events.GUARDRAIL_BLOCKED,
            agent=ctx.agent,
            status="blocked",
            summary=f"Refused issue_gift: unknown SKU {sku or '(empty)'}",
            payload={
                "guardrail": "unknown_sku",
                "requested_sku": sku,
                "gift_skus": [item.sku for item in ctx.config.gifting.offers],
            },
        )
        return _fail(
            "unknown_sku",
            f"SKU '{sku}' is not one of this brand's gifts. Call find_available_gifts and use an "
            "exact SKU.",
        )

    # Re-check *this gift's* own criteria. The model cannot talk past this.
    eligibility = evaluation.evaluate_gifting(profile, ctx.config, sku=offer.sku)
    evaluated = eligibility["offers"][0]
    if not evaluated["eligible"]:
        # Sweep every offer so the refusal can name something they *do* qualify for.
        sweep = evaluation.evaluate_gifting(profile, ctx.config)
        alternatives = sweep["eligible_offers"]
        await ctx.logger.log(
            events.GUARDRAIL_BLOCKED,
            agent=ctx.agent,
            status="blocked",
            summary=f"Refused issue_gift: {offer.name} criteria not met ("
            + ", ".join(evaluated["failed_labels"])
            + ")",
            payload={
                "guardrail": "eligibility_not_met",
                "gift_id": offer.id,
                "sku": offer.sku,
                "failed_criteria": evaluated["failed"],
                "checks": evaluated["checks"],
                "eligible_alternatives": [item["name"] for item in alternatives],
                "verified_profile_values": eligibility["verified"],
                "creator_stated_values_used": False,
            },
        )
        message = (
            f"{offer.name} is not available to this creator: "
            + "; ".join(
                f"{check['label']} requires {check['required_display']} but the verified value is "
                f"{check['actual_display']}"
                for check in evaluated["checks"]
                if not check["passed"]
            )
            + ". Do not issue a gift."
        )
        if alternatives:
            message += (
                " They do qualify for: "
                + ", ".join(item["name"] for item in alternatives)
                + ". Offer one of those instead, or share the link."
            )
        return _fail("eligibility_not_met", message)

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
            GiftOrder.sku == offer.sku,
        )
    )
    if existing is not None:
        await ctx.logger.log(
            events.TOOL_CALL_COMPLETED,
            agent=ctx.agent,
            summary=f"Duplicate gift suppressed for {offer.sku} (existing order {existing.order_id})",
            payload={
                "duplicate": True,
                "order_id": existing.order_id,
                "sku": offer.sku,
                "original_created_at": str(existing.created_at),
            },
        )
        return _ok(
            duplicate=True,
            order_id=existing.order_id,
            sku=offer.sku,
            product=offer.name,
            shipping_address=existing.shipping_address,
            message="A gift for this SKU was already issued for this creator; no new order was placed.",
        )

    # One gift per conversation. Without this, a vague request ("can I get a free
    # gift?") can turn into a model shipping every SKU in the catalog.
    already_issued = await ctx.db.scalar(
        select(GiftOrder).where(GiftOrder.conversation_id == ctx.conversation_id)
    )
    if already_issued is not None:
        await ctx.logger.log(
            events.GUARDRAIL_BLOCKED,
            agent=ctx.agent,
            status="blocked",
            summary=(
                f"Refused issue_gift: {already_issued.product_name} "
                f"({already_issued.order_id}) was already issued in this conversation"
            ),
            payload={
                "guardrail": "one_gift_per_conversation",
                "existing_order_id": already_issued.order_id,
                "existing_sku": already_issued.sku,
                "requested_sku": offer.sku,
            },
        )
        return _fail(
            "gift_already_issued",
            f"A gift was already issued in this conversation ({already_issued.product_name}, order "
            f"{already_issued.order_id}). Do not place another order — confirm the existing one to "
            "the creator.",
        )

    await asyncio.sleep(MOCK_API_LATENCY_SECONDS)
    order_id = f"GT-{random.randint(10000, 99999)}"
    ctx.db.add(
        GiftOrder(
            session_id=ctx.session_id,
            conversation_id=ctx.conversation_id,
            creator_id=profile.id,
            creator_name=profile.name,
            sku=offer.sku,
            product_name=offer.name,
            order_id=order_id,
            shipping_address=profile.shipping_address,
        )
    )
    await ctx.db.commit()
    await ctx.logger.log(
        events.GIFT_ISSUED,
        agent=ctx.agent,
        summary=f"Gift issued: {offer.name} ({offer.sku}) -> order {order_id}",
        payload={
            "order_id": order_id,
            "sku": offer.sku,
            "product": offer.name,
            "creator_id": profile.id,
            "creator_name": profile.name,
            "shipping_address": profile.shipping_address,
            "mock_api": "mock order placement API",
        },
    )
    return _ok(
        duplicate=False,
        order_id=order_id,
        sku=offer.sku,
        product=offer.name,
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
            payload={
                "duplicate": True,
                "tag": tag,
                "decision": fit["decision"],
                "applied_at": str(existing.created_at),
            },
        )
        await _record_decision(ctx, fit["decision"])
        return _ok(
            duplicate=True,
            tag=tag,
            decision=fit["decision"],
            score=fit["score"],
            response_instructions=fit["response_instructions"],
            message=f"'{tag}' was already applied to this creator; no duplicate tag was placed.",
        )

    await asyncio.sleep(MOCK_API_LATENCY_SECONDS)
    ctx.db.add(
        TagApplied(
            session_id=ctx.session_id,
            conversation_id=ctx.conversation_id,
            creator_id=profile.id,
            tag=tag,
            decision=fit["decision"],
            score=fit["score"],
        )
    )
    await ctx.db.commit()
    await _record_decision(ctx, fit["decision"])
    await ctx.logger.log(
        events.TAG_APPLIED,
        agent=ctx.agent,
        summary=f"Tag applied: '{tag}' (score {fit['score']}) -> decision '{fit['decision']}'",
        payload={
            "creator_id": profile.id,
            "tag": tag,
            "decision": fit["decision"],
            "response_instructions": fit["response_instructions"],
            "score": fit["score"],
            "components": fit["components"],
            "mock_api": "mock Storefront tag API",
        },
    )
    return _ok(
        duplicate=False,
        tag=tag,
        decision=fit["decision"],
        score=fit["score"],
        response_instructions=fit["response_instructions"],
        message=f"Tag '{tag}' applied. Decision for this conversation: {fit['decision']}.",
    )


async def _record_decision(ctx: ToolContext, decision: str) -> None:
    """Write the pass/reject decision onto the conversation itself."""
    conversation = await ctx.db.get(Conversation, ctx.conversation_id)
    if conversation is None:
        return
    conversation.decision = decision
    await ctx.db.commit()


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------
ARG_MODELS: dict[str, type[BaseModel]] = {
    "get_creator_profile": NoArgs,
    "find_available_gifts": FindGiftsArgs,
    "check_gifting_eligibility": CheckEligibilityArgs,
    "check_pitch_fit": NoArgs,
    "issue_gift": IssueGiftArgs,
    "place_tag": PlaceTagArgs,
}

EXECUTORS: dict[str, Any] = {
    "get_creator_profile": _get_creator_profile,
    "find_available_gifts": _find_available_gifts,
    "check_gifting_eligibility": _check_gifting_eligibility,
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



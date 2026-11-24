"""Tool registry: schemas, validation and guarded execution.

Every tool call is validated with Pydantic and logged. The side-effecting tools
(``issue_gift``) re-run the deterministic rules themselves and
refuse to act when the rules are not satisfied -- an LLM cannot talk its way past
them, and it cannot fabricate a successful order.
"""

from __future__ import annotations

import asyncio
import json
import random
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import addresses
from .. import evaluation
from ..models import GiftOrder, Session as SessionRow
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


class ValidateAddressArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    address: str = Field(min_length=1, max_length=300)


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
    "validate_shipping_address": _fn(
        "validate_shipping_address",
        (
            "Check a delivery address before a gift is ordered. Pass the address exactly as the "
            "creator gave it. Returns whether it is valid, the normalised form to ship to, and -- "
            "when it is not valid -- the reason and a suggested correction to put to the creator. "
            "Call this after the creator gives or confirms an address and before issue_gift. "
            "issue_gift re-checks the same rules and refuses an address that does not pass."
        ),
        properties={
            "address": {
                "type": "string",
                "description": "The delivery address exactly as the creator gave it.",
            }
        },
        required=["address"],
    ),
    "issue_gift": _fn(
        "issue_gift",
        (
            "Place the gift order in the brand's order system. Provide exactly three fields: "
            "creator_name, sku, and the delivery shipping_address the creator gave or confirmed. The "
            "tool re-checks the gift's own criteria, the SKU, duplicates and the address server-side, "
            "and will refuse if anything is unmet. Only tell the creator a gift is on its way after "
            "this tool returns success."
        ),
        properties={
            "creator_name": {"type": "string", "description": "Verified creator name"},
            "sku": {"type": "string", "description": "SKU from the eligible gifting catalog"},
            "shipping_address": {
                "type": "string",
                "description": (
                    "The delivery address the creator gave or confirmed. Call "
                    "validate_shipping_address on it first."
                ),
            },
        },
        required=["creator_name", "sku", "shipping_address"],
    ),
}

AGENT_TOOLS: dict[str, list[str]] = {
    "gift_request_agent": [
        "get_creator_profile",
        "find_available_gifts",
        "check_gifting_eligibility",
        "validate_shipping_address",
        "issue_gift",
    ],
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
    # The tool loop already logs this call, with the full result, so logging again
    # here would show the same step twice in the transcript.
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


def _normalise(value: str) -> str:
    return " ".join((value or "").strip().lower().split())


def _address_on_file(ctx: ToolContext) -> str | None:
    value = (ctx.creator.shipping_address or "").strip()
    return value or None


async def _validate_shipping_address(args: dict, ctx: ToolContext) -> dict:
    """Check the delivery address, and offer a correction when it fails.

    The agent uses this to talk the address through with the creator; issue_gift
    re-runs the same check, so a model that skips this step still cannot ship to an
    address that does not validate.
    """
    address = (args.get("address") or "").strip()
    on_file = _address_on_file(ctx)
    check = addresses.validate(address, on_file=on_file)

    if check["valid"]:
        message = (
            f"Address validated as {check['normalised']}. "
            "You can now call issue_gift with that address."
        )
    else:
        message = (
            f"That address did not validate: {check['reason']}. Tell the creator what was wrong, "
            "and do not place an order."
        )
        if check["suggestion"]:
            message += (
                " Offer them this correction and ask them to confirm it: "
                f"{check['suggestion']}."
            )
    check["message"] = message

    await ctx.logger.log(
        events.ADDRESS_VALIDATED,
        agent=ctx.agent,
        status="ok" if check["valid"] else "blocked",
        summary=(
            f"Address accepted: {check['normalised']}"
            if check["valid"]
            else f"Address rejected: {check['reason']}"
        ),
        payload={
            "policy": "address",
            "input": check["input"],
            "valid": check["valid"],
            "reason": check["reason"],
            "missing": check["missing"],
            "normalised": check["normalised"],
            "suggestion": check["suggestion"],
            "suggestion_source": check["suggestion_source"],
            "address_on_file": on_file,
            "source": check["source"],
        },
    )
    return _ok(**check)


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

    # Identity is never negotiable: the gift goes to the verified creator.
    adjustments: list[dict] = []
    if _normalise(args.get("creator_name", "")) != _normalise(profile.name):
        adjustments.append(
            {
                "field": "creator_name",
                "attempted": args.get("creator_name"),
                "used": profile.name,
            }
        )
    if adjustments:
        await ctx.logger.log(
            events.GUARDRAIL_ADJUSTED,
            agent=ctx.agent,
            summary="Rewrote the creator name to match the verified profile",
            payload={"guardrail": "verified_values_enforced", "adjustments": adjustments},
        )

    # The delivery address is collected from the creator rather than taken from the
    # profile, so it has to pass validation -- re-checked here, which means a model
    # that skips validate_shipping_address still cannot ship to a bad address.
    on_file = _address_on_file(ctx)
    address_check = addresses.validate(args.get("shipping_address", ""), on_file=on_file)
    if not address_check["valid"]:
        await ctx.logger.log(
            events.GUARDRAIL_BLOCKED,
            agent=ctx.agent,
            status="blocked",
            summary=f"Refused issue_gift: address rejected ({address_check['reason']})",
            payload={
                "guardrail": "address_invalid",
                "input": address_check["input"],
                "reason": address_check["reason"],
                "missing": address_check["missing"],
                "suggestion": address_check["suggestion"],
                "address_on_file": on_file,
            },
        )
        message = (
            f"That delivery address did not validate: {address_check['reason']}. "
            "Do not place the order."
        )
        if address_check["suggestion"]:
            message += (
                f" Confirm this address with the creator first: "
                f"{address_check['suggestion']}."
            )
        message += (
            " Call validate_shipping_address with the confirmed address, then issue_gift again."
        )
        return _fail("address_invalid", message)

    delivery_address = address_check["normalised"]

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
                "tool": "issue_gift",
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
            # Carried on the result as well as the event, so the reply can name the gift
            # that was already sent rather than talking about "a gift" in the abstract.
            existing_order_id=already_issued.order_id,
            existing_product=already_issued.product_name,
            requested_sku=offer.sku,
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
            shipping_address=delivery_address,
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
            "shipping_address": delivery_address,
            "address_on_file": on_file,
            "address_changed": bool(
                on_file and delivery_address.strip().lower() != on_file.strip().lower()
            ),
            "address_validated": True,
            "mock_api": "mock order placement API",
        },
    )
    return _ok(
        duplicate=False,
        order_id=order_id,
        sku=offer.sku,
        product=offer.name,
        shipping_address=delivery_address,
        message=f"Gift order {order_id} placed successfully.",
    )



# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------
ARG_MODELS: dict[str, type[BaseModel]] = {
    "get_creator_profile": NoArgs,
    "find_available_gifts": FindGiftsArgs,
    "check_gifting_eligibility": CheckEligibilityArgs,
    "validate_shipping_address": ValidateAddressArgs,
    "issue_gift": IssueGiftArgs,
}

EXECUTORS: dict[str, Any] = {
    "get_creator_profile": _get_creator_profile,
    "find_available_gifts": _find_available_gifts,
    "check_gifting_eligibility": _check_gifting_eligibility,
    "validate_shipping_address": _validate_shipping_address,
    "issue_gift": _issue_gift,
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



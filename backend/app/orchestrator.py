"""Turn orchestration: route -> hand off -> execute tools -> reply -> log.

This is the only place that knows the full flow. It writes every event the logs
page renders, and it owns the guardrail that decides when an agent is still
waiting on the creator.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from . import evaluation, guardrails
from .agents import general_handler, prompts, router_agent, tool_agent
from .models import Conversation, Event, Session as SessionRow
from .schemas import BrandConfig, CreatorProfile
from .services import conversation_store as store
from .services import event_logger as events
from .services.event_logger import EventLogger
from .services.llm_client import LLMClient
from .tools.registry import ToolContext
from . import guardrails

TERMINAL_OUTCOMES = {
    "gift_issued",
    "gift_duplicate",
    "gift_declined",
    "gift_suggested",
    "pitch_passed",
    "pitch_rejected",
}


@dataclass
class TurnResult:
    conversation_id: str
    reply: str
    agent: str
    intent: str
    confidence: float
    outcome: dict[str, Any] = field(default_factory=dict)
    usage: dict[str, Any] = field(default_factory=dict)
    pending_agent: str | None = None


async def _max_event_id(db: AsyncSession) -> int:
    return int(await db.scalar(select(func.coalesce(func.max(Event.id), 0))) or 0)


async def _turn_usage(db: AsyncSession, conversation_id: str, start_id: int) -> dict[str, Any]:
    rows = await db.scalars(
        select(Event).where(
            Event.conversation_id == conversation_id,
            Event.id > start_id,
            Event.type == events.LLM_RESPONSE,
        )
    )
    llm_events = list(rows)
    return {
        "llm_calls": len(llm_events),
        "tokens_in": sum(int(row.tokens_in or 0) for row in llm_events),
        "tokens_out": sum(int(row.tokens_out or 0) for row in llm_events),
        "cost_usd": round(sum(float(row.cost_usd or 0.0) for row in llm_events), 6),
        "models": sorted({row.model for row in llm_events if row.model}),
    }


def _suggested_gift(run: tool_agent.AgentRun) -> dict[str, Any] | None:
    """A gift the agent pointed the creator at without placing an order.

    Deterministic: the reply must actually contain a gift's link, or mention a link
    when exactly one gift matched. Anything looser would mislabel a browse as a
    suggestion.
    """
    found = run.any_ok("find_available_gifts")
    gifts = (found or {}).get("gifts") or []
    if not gifts:
        return None
    reply = run.text or ""
    for gift in gifts:
        url = gift.get("url")
        if url and url in reply:
            return gift
    if len(gifts) == 1 and "link" in reply.lower():
        return gifts[0]
    return None


def _eligibility_result(run: tool_agent.AgentRun) -> dict[str, Any] | None:
    """The last successful eligibility check."""
    for record in reversed(run.tool_calls):
        if record.name == "check_gifting_eligibility" and record.result.get("ok"):
            return record.result
    return None


def _declined_offer(run: tool_agent.AgentRun) -> dict[str, Any] | None:
    """The gift the creator asked about that they do *not* qualify for.

    A creator can be ineligible for the gift they asked about while still
    qualifying for a cheaper one, so "did they qualify for anything" is the wrong
    question. This finds the specific gift under discussion -- and requires that
    there *was* a request. A catalogue browse, or a gift the model checked on its
    own initiative, is not something to decline.
    """
    result = _eligibility_result(run)
    if result is None:
        return None

    offers = result.get("offers") or []
    found = run.any_ok("find_available_gifts") or {}
    query = (found.get("query") or "").strip()
    matched = {gift["gift_id"] for gift in (found.get("gifts") or [])}

    def was_requested(gift_id: str) -> bool:
        return bool(query) and gift_id in matched

    selected = result.get("selected")
    if selected and selected != "all":
        match = next((offer for offer in offers if offer["gift_id"] == selected), None)
        if match is None or match["eligible"] or not was_requested(match["gift_id"]):
            return None
        return match

    for candidate in found.get("gifts") or []:
        offer = next(
            (item for item in offers if item["gift_id"] == candidate["gift_id"]), None
        )
        if offer is not None and not offer["eligible"] and was_requested(offer["gift_id"]):
            return offer
    return None


def _extract_outcome(
    run: tool_agent.AgentRun,
    target_agent: str,
    *,
    creator: CreatorProfile,
    config: BrandConfig,
) -> dict[str, Any]:
    """Turn the tool trace into a compact summary for the UI."""
    blocks = [
        {
            "tool": record.name,
            "error": record.result.get("error"),
            "message": record.result.get("message"),
        }
        for record in run.tool_calls
        if not record.result.get("ok")
    ]

    gift = run.any_ok("issue_gift")
    if gift:
        outcome: dict[str, Any] = {
            "type": "gift_duplicate" if gift.get("duplicate") else "gift_issued",
            "order_id": gift.get("order_id"),
            "product": gift.get("product"),
            "sku": gift.get("sku"),
        }
    else:
        tag = run.any_ok("place_tag")
        if tag:
            decision = tag.get("decision") or "reject"
            outcome = {
                "type": "pitch_passed" if decision == "pass" else "pitch_rejected",
                "decision": decision,
                "tag": tag.get("tag"),
                "score": tag.get("score"),
                "duplicate": bool(tag.get("duplicate")),
            }
        else:
            declined = _declined_offer(run)
            if declined is not None:
                # Alternatives are a fact about the creator and the config, not
                # about which tools the model happened to call.
                sweep = evaluation.evaluate_gifting(creator, config)
                outcome = {
                    "type": "gift_declined",
                    "gift": declined["name"],
                    "gift_id": declined["gift_id"],
                    "failed_criteria": declined["failed_labels"],
                    "shortfalls": declined["shortfalls"],
                    "alternatives": [
                        {"name": item["name"], "url": item["url"]}
                        for item in sweep["eligible_offers"]
                    ],
                }
            else:
                suggested = _suggested_gift(run)
                if suggested is not None:
                    outcome = {
                        "type": "gift_suggested",
                        "product": suggested.get("name"),
                        "sku": suggested.get("sku"),
                        "url": suggested.get("url"),
                        "eligible": suggested.get("eligible"),
                    }
                else:
                    outcome = {"type": "inquiry", "agent": target_agent}

    if blocks:
        outcome["guardrail_blocks"] = blocks
    return outcome


async def handle_turn(
    db: AsyncSession,
    *,
    session_row: SessionRow,
    conversation: Conversation,
    creator: CreatorProfile,
    config: BrandConfig,
    config_version: int,
    user_message: str,
    llm: LLMClient,
) -> TurnResult:
    logger = EventLogger(
        db,
        session_id=session_row.id,
        conversation_id=conversation.id,
        creator_id=creator.id,
        config_version=config_version,
    )
    start_id = await _max_event_id(db)

    await logger.log(
        events.MESSAGE_RECEIVED,
        agent="router_agent",
        summary=f"Creator message: {user_message[:120]}",
        payload={
            "message": user_message,
            "creator_id": creator.id,
            "conversation_id": conversation.id,
            "verified_profile_snapshot": {
                "followers": creator.followers,
                "engagement_rate": creator.engagement_rate,
                "brand_attributed_revenue": creator.brand_attributed_revenue,
                "categories": creator.categories,
            },
            "note": "Creator-supplied claims are never used for eligibility.",
        },
    )
    await store.append_message(
        db, conversation_id=conversation.id, role="creator", content=user_message
    )

    # Flag instruction-override attempts for the audit log. This never blocks a
    # message -- it just makes the attempt visible, and the deterministic
    # checks below are what actually hold the line.
    suspects = guardrails.detect_injection(user_message)
    if suspects:
        await logger.log(
            events.INJECTION_SUSPECTED,
            agent="guard",
            status="warn",
            summary="Instruction-override language detected: " + ", ".join(suspects),
            payload={
                "patterns": suspects,
                "message": user_message,
                "action": (
                    "Treated as untrusted content. Agent rules, tools and eligibility criteria are "
                    "unchanged."
                ),
            },
        )

    history = await store.recent_turns(db, conversation.id, limit=12)
    if history and history[-1]["role"] == "user" and history[-1]["content"] == user_message:
        history = history[:-1]

    route_result = await router_agent.route(
        llm,
        config=config,
        creator=creator,
        history=history,
        user_message=user_message,
        current_agent=conversation.pending_agent,
        awaiting=bool(conversation.pending_agent),
    )
    await logger.log(
        events.INTENT_CLASSIFIED,
        agent="router_agent",
        status="error" if route_result.used_fallback else "ok",
        summary=f"intent={route_result.intent} confidence={route_result.confidence:.2f}",
        payload={
            "intent": route_result.intent,
            "confidence": route_result.confidence,
            "reasoning": route_result.reasoning,
            "clarifying_question": route_result.clarifying_question,
            "keyword_fallback_used": route_result.used_fallback,
            "raw_model_output": route_result.raw,
        },
    )

    target_agent = route_result.agent
    if conversation.pending_agent == target_agent:
        await logger.log(
            events.AGENT_RESUMED,
            agent=target_agent,
            summary=f"{target_agent} resumed (creator answered the pending question)",
            payload={"agent": target_agent, "intent": route_result.intent},
        )
    else:
        await logger.log(
            events.AGENT_HANDOFF,
            agent="router_agent",
            summary=f"router_agent -> {target_agent}",
            payload={
                "from": "router_agent",
                "to": target_agent,
                "intent": route_result.intent,
                "confidence": route_result.confidence,
                "previous_agent": conversation.pending_agent,
            },
        )

    ctx = ToolContext(
        db=db,
        session_id=session_row.id,
        conversation_id=conversation.id,
        creator=creator,
        config=config,
        config_version=config_version,
        logger=logger,
        session_row=session_row,
        agent=target_agent,
    )

    if target_agent == "gift_request_agent":
        run = await tool_agent.run_tool_agent(
            llm,
            agent_name=target_agent,
            model=config.models.gift_request,
            system=prompts.gift_prompt(config, creator),
            history=history,
            user_message=user_message,
            ctx=ctx,
            config=config,
            creator=creator,
        )
    elif target_agent == "creator_pitch_agent":
        run = await tool_agent.run_tool_agent(
            llm,
            agent_name=target_agent,
            model=config.models.creator_pitch,
            system=prompts.pitch_prompt(config, creator),
            history=history,
            user_message=user_message,
            ctx=ctx,
            config=config,
            creator=creator,
        )
    else:
        text = await general_handler.run_general(
            llm,
            config=config,
            creator=creator,
            history=history,
            user_message=user_message,
            clarifying_question=route_result.clarifying_question,
        )
        run = tool_agent.AgentRun(text=text, model=config.models.general_inquiry)

    outcome = _extract_outcome(run, target_agent, creator=creator, config=config)

    # The model can be talked into lying about an outcome, or into handing over
    # its instructions. Neither survives this check: the reply is replaced with
    # one built from what the tools actually did.
    verdict = guardrails.inspect_reply(
        run.text,
        records=run.tool_calls,
        agent=target_agent,
        eligibility=(
            evaluation.evaluate_gifting(creator, config)
            if target_agent == "gift_request_agent"
            else None
        ),
        fit=(
            evaluation.pitch_evaluation(creator, config)
            if target_agent == "creator_pitch_agent"
            else None
        ),
    )
    if verdict.replaced:
        await logger.log(
            events.GUARDRAIL_BLOCKED,
            agent=target_agent,
            status="blocked",
            summary=verdict.summary,
            payload={
                "guardrail": verdict.guardrail,
                **verdict.details,
                "replacement": verdict.replacement,
            },
        )
        run = tool_agent.AgentRun(
            text=verdict.replacement or "", tool_calls=run.tool_calls, model=run.model
        )

    await store.append_message(
        db, conversation_id=conversation.id, role="agent", content=run.text, agent=target_agent
    )

    pending = None
    if outcome["type"] not in TERMINAL_OUTCOMES and "?" in run.text:
        pending = target_agent

    await logger.log(
        events.MESSAGE_SENT,
        agent=target_agent,
        summary=f"Reply sent ({len(run.text)} chars) -- outcome={outcome['type']}",
        payload={
            "reply": run.text,
            "outcome": outcome,
            "tool_calls": [
                {"tool": record.name, "arguments": record.arguments, "result": record.result}
                for record in run.tool_calls
            ],
            "awaiting_creator_answer": pending is not None,
        },
    )

    usage = await _turn_usage(db, conversation.id, start_id)
    await store.touch_conversation(
        db, conversation, pending_agent=pending, clear_pending=pending is None
    )

    return TurnResult(
        conversation_id=conversation.id,
        reply=run.text,
        agent=target_agent,
        intent=route_result.intent,
        confidence=route_result.confidence,
        outcome=outcome,
        usage=usage,
        pending_agent=pending,
    )


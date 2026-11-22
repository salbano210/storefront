"""The chat endpoint -- one creator turn through the multi-agent system."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from .. import orchestrator
from ..db import get_db
from ..schemas import ChatRequest, ChatResponse
from ..services import budget, config_manager, conversation_store as store
from ..services import data_store
from ..services import event_logger as events
from ..services import llm_client
from ..services.event_logger import EventLogger
from ..services.llm_client import LLMError
from .deps import client_ip, resolve_api_key, session_id

router = APIRouter(prefix="/api", tags=["chat"])


def _http_error(status: int, code: str, message: str, **extra) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code, "message": message, **extra})


@router.post("/chat", response_model=ChatResponse)
async def chat(
    payload: ChatRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    sid: str = Depends(session_id),
) -> ChatResponse:
    try:
        budget.check_rate_limit(client_ip(request))
    except budget.LimitExceeded as exc:
        raise _http_error(429, exc.reason, exc.message) from exc

    key = resolve_api_key()

    creator = await data_store.get_creator(db, payload.creator_id)
    if creator is None:
        raise _http_error(404, "unknown_creator", f"No creator profile for '{payload.creator_id}'.")

    session_row = await store.get_or_create_session(db, sid)

    # Starting a new conversation refreshes the session's message allowance, so
    # the cap is a speed bump rather than a dead end. The daily spend ceiling and
    # the per-IP burst limit remain the hard stops.
    if payload.conversation_id is None and session_row.message_count:
        session_row.message_count = 0
        await db.commit()

    try:
        limits = await budget.check_limits(db, session_row)
    except budget.LimitExceeded as exc:
        await EventLogger(db, session_id=sid).log(
            events.BUDGET_BLOCKED,
            agent="guard",
            status="blocked",
            summary=f"Blocked by {exc.reason}",
            payload={"reason": exc.reason, "message": exc.message},
        )
        raise _http_error(429, exc.reason, exc.message) from exc

    config_version, config = await config_manager.get_current(db)

    if payload.conversation_id:
        conversation = await store.get_conversation(db, payload.conversation_id)
        if conversation is None or conversation.session_id != sid:
            raise _http_error(404, "unknown_conversation", "That conversation does not exist.")
        if conversation.creator_id != creator.id:
            raise _http_error(
                400,
                "creator_mismatch",
                "That conversation belongs to a different creator. Start a new one.",
            )
    else:
        conversation = await store.create_conversation(
            db, session_id=sid, creator_id=creator.id, config_version=config_version
        )

    logger = EventLogger(
        db,
        session_id=sid,
        conversation_id=conversation.id,
        creator_id=creator.id,
        config_version=config_version,
    )
    llm = llm_client.build_llm(key, logger=logger, fallback_model=config.models.fallback)

    try:
        turn = await orchestrator.handle_turn(
            db,
            session_row=session_row,
            conversation=conversation,
            creator=creator,
            config=config,
            config_version=config_version,
            user_message=payload.message.strip(),
            llm=llm,
        )
    except llm_client.LLMCreditsExhausted as exc:
        raise _http_error(
            402,
            "credits_exhausted",
            (
                "The demo's OpenRouter credits are exhausted, so the agents can't run. "
                "Ask the demo owner to top up the key."
            ),
        ) from exc
    except LLMError as exc:
        raise _http_error(
            502,
            "llm_error",
            f"The model provider could not be reached: {exc}",
        ) from exc

    session_row = await store.record_usage(
        db,
        session_row,
        messages=1,
        tokens_in=turn.usage.get("tokens_in", 0),
        tokens_out=turn.usage.get("tokens_out", 0),
        cost_usd=turn.usage.get("cost_usd", 0.0),
    )

    budget_snapshot = {
        **limits,
        "messages_used": session_row.message_count,
        "session_cost_usd": round(session_row.total_cost_usd, 4),
        "daily_cost_usd": round(limits["daily_cost_usd"] + turn.usage.get("cost_usd", 0.0), 6),
    }

    return ChatResponse(
        conversation_id=turn.conversation_id,
        reply=turn.reply,
        agent=turn.agent,
        intent=turn.intent,
        confidence=turn.confidence,
        outcome=turn.outcome,
        usage=turn.usage,
        budget=budget_snapshot,
        created_at=store.iso(conversation.updated_at) or "",
    )

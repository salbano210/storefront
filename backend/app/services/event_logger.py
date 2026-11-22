"""Structured execution events -- the audit trail behind the logs page.

Every row here is produced by real execution. Nothing on the logs page is
fabricated. Each ``log()`` commits immediately so that the audit trail survives
even if the turn that produced it fails later.
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Event
from .redactor import redact

MAX_PAYLOAD_CHARS = 24_000

# Event types emitted by the system.
MESSAGE_RECEIVED = "message_received"
INTENT_CLASSIFIED = "intent_classified"
AGENT_HANDOFF = "agent_handoff"
AGENT_RESUMED = "agent_resumed"
PROFILE_LOOKUP = "profile_lookup"
CRITERIA_EVALUATED = "criteria_evaluated"
ADDRESS_VALIDATED = "address_validated"
TOOL_CALL_STARTED = "tool_call_started"
TOOL_CALL_COMPLETED = "tool_call_completed"
TOOL_CALL_FAILED = "tool_call_failed"
GUARDRAIL_BLOCKED = "guardrail_blocked"
GUARDRAIL_ADJUSTED = "guardrail_adjusted"
TAG_APPLIED = "tag_applied"
GIFT_ISSUED = "gift_issued"
MESSAGE_SENT = "message_sent"
LLM_REQUEST = "llm_request"
LLM_RESPONSE = "llm_response"
LLM_FALLBACK = "llm_fallback"
LLM_ERROR = "llm_error"
BUDGET_BLOCKED = "budget_blocked"
CONFIG_UPDATED = "config_updated"
CONFIG_RESET = "config_reset"
CREATOR_UPDATED = "creator_updated"
CREATOR_RESET = "creator_reset"
INJECTION_SUSPECTED = "injection_suspected"
SESSION_STARTED = "session_started"

ALL_EVENT_TYPES = (
    MESSAGE_RECEIVED,
    INTENT_CLASSIFIED,
    AGENT_HANDOFF,
    AGENT_RESUMED,
    PROFILE_LOOKUP,
    CRITERIA_EVALUATED,
    ADDRESS_VALIDATED,
    TOOL_CALL_STARTED,
    TOOL_CALL_COMPLETED,
    TOOL_CALL_FAILED,
    GUARDRAIL_BLOCKED,
    GUARDRAIL_ADJUSTED,
    TAG_APPLIED,
    GIFT_ISSUED,
    MESSAGE_SENT,
    LLM_REQUEST,
    LLM_RESPONSE,
    LLM_FALLBACK,
    LLM_ERROR,
    BUDGET_BLOCKED,
    CONFIG_UPDATED,
    CONFIG_RESET,
    CREATOR_UPDATED,
    CREATOR_RESET,
    INJECTION_SUSPECTED,
    SESSION_STARTED,
)


class EventLogger:
    """Convenience wrapper bound to one session/conversation."""

    def __init__(
        self,
        db: AsyncSession,
        *,
        session_id: str,
        conversation_id: str | None = None,
        creator_id: str | None = None,
        config_version: int | None = None,
    ) -> None:
        self.db = db
        self.session_id = session_id
        self.conversation_id = conversation_id
        self.creator_id = creator_id
        self.config_version = config_version

    async def log(
        self,
        event_type: str,
        *,
        agent: str | None = None,
        status: str = "ok",
        summary: str | None = None,
        payload: dict[str, Any] | None = None,
        model: str | None = None,
        latency_ms: int | None = None,
        tokens_in: int | None = None,
        tokens_out: int | None = None,
        cost_usd: float | None = None,
        conversation_id: str | None = None,
    ) -> Event:
        encoded: str | None = None
        if payload is not None:
            safe = redact(payload)
            encoded = json.dumps(safe, default=str)
            if len(encoded) > MAX_PAYLOAD_CHARS:
                encoded = json.dumps(
                    {"truncated": True, "preview": encoded[:MAX_PAYLOAD_CHARS]}
                )
        event = Event(
            session_id=self.session_id,
            conversation_id=conversation_id or self.conversation_id,
            creator_id=self.creator_id,
            agent=agent,
            type=event_type,
            status=status,
            summary=(summary[:600] if summary else None),
            payload_json=encoded,
            model=model,
            latency_ms=latency_ms,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            cost_usd=cost_usd,
            config_version=self.config_version,
        )
        self.db.add(event)
        await self.db.commit()
        await self.db.refresh(event)
        return event

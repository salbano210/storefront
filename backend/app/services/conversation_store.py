"""Conversations, messages and sessions."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Conversation, Message, Session


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def iso(value: datetime | None) -> str | None:
    """Serialise a datetime as UTC ISO-8601 (SQLite returns naive datetimes)."""
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat()


async def get_or_create_session(db: AsyncSession, session_id: str) -> Session:
    row = await db.get(Session, session_id)
    if row is None:
        row = Session(id=session_id)
        db.add(row)
        await db.commit()
        await db.refresh(row)
    return row


async def create_conversation(
    db: AsyncSession, *, session_id: str, creator_id: str, config_version: int
) -> Conversation:
    row = Conversation(
        id=new_id("conv"),
        session_id=session_id,
        creator_id=creator_id,
        config_version=config_version,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row


async def get_conversation(db: AsyncSession, conversation_id: str) -> Conversation | None:
    return await db.get(Conversation, conversation_id)


async def list_conversations(db: AsyncSession, session_id: str) -> list[Conversation]:
    result = await db.scalars(
        select(Conversation)
        .where(Conversation.session_id == session_id)
        .order_by(Conversation.created_at.desc())
    )
    return list(result)


async def append_message(
    db: AsyncSession,
    *,
    conversation_id: str,
    role: str,
    content: str,
    agent: str | None = None,
) -> Message:
    row = Message(
        conversation_id=conversation_id, role=role, content=content, agent=agent
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row


async def list_messages(
    db: AsyncSession, conversation_id: str, limit: int | None = None
) -> list[Message]:
    query = (
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.id.asc())
    )
    if limit:
        query = query.limit(limit)
    result = await db.scalars(query)
    return list(result)


async def recent_turns(db: AsyncSession, conversation_id: str, limit: int = 12) -> list[dict]:
    """Last N messages as OpenAI-style chat turns (oldest first)."""
    rows = await list_messages(db, conversation_id)
    trimmed = rows[-limit:]
    return [
        {
            "role": "assistant" if row.role == "agent" else "user",
            "content": row.content,
        }
        for row in trimmed
    ]


async def touch_conversation(
    db: AsyncSession,
    conversation: Conversation,
    *,
    pending_agent: str | None = None,
    config_version: int | None = None,
    clear_pending: bool = False,
) -> Conversation:
    if clear_pending:
        conversation.pending_agent = None
    elif pending_agent is not None:
        conversation.pending_agent = pending_agent
    if config_version is not None:
        conversation.config_version = config_version
    conversation.updated_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(conversation)
    return conversation


async def record_usage(
    db: AsyncSession,
    session: Session,
    *,
    messages: int = 1,
    tokens_in: int = 0,
    tokens_out: int = 0,
    cost_usd: float = 0.0,
    key_mode: str | None = None,
) -> Session:
    session.message_count += messages
    session.tokens_in += tokens_in
    session.tokens_out += tokens_out
    session.total_cost_usd = round(session.total_cost_usd + (cost_usd or 0.0), 6)
    session.last_seen_at = datetime.now(timezone.utc)
    if key_mode:
        session.key_mode = key_mode
    await db.commit()
    await db.refresh(session)
    return session

"""Logs, transcripts and conversation history."""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..models import Conversation, Event, Message
from ..services import conversation_store as store
from ..services import data_store, seeder
from ..services.event_logger import ALL_EVENT_TYPES
from .deps import event_out, is_admin, session_id

router = APIRouter(prefix="/api", tags=["logs"])


def _sample_conversation_ids():
    """Subquery: the ids of every sample conversation.

    Sample history lives in its own sessions so the per-session duplicate guard
    behaves, which means "my session" has to reach across to find it.
    """
    return select(Conversation.id).where(Conversation.source == seeder.SAMPLE_SOURCE)


@router.get("/logs")
async def list_logs(
    request: Request,
    db: AsyncSession = Depends(get_db),
    sid: str = Depends(session_id),
    scope: str = Query("session", pattern="^(session|all)$"),
    creator_id: str | None = None,
    conversation_id: str | None = None,
    agent: str | None = None,
    event_type: str | None = None,
    after_id: int = 0,
    limit: int = Query(200, ge=1, le=500),
) -> dict:
    if scope == "all" and not is_admin(request):
        raise HTTPException(
            status_code=401,
            detail={
                "code": "admin_passcode_required",
                "message": "Enter the demo admin passcode to view every session's logs.",
            },
        )

    query = select(Event).where(Event.id > after_id)
    if scope == "session":
        # The visitor's own turns, plus the sample history. Sample conversations
        # live in their own sessions (so the duplicate guard behaves), which is why
        # this is an OR rather than a session filter.
        query = query.where(
            or_(
                Event.session_id == sid,
                Event.conversation_id.in_(_sample_conversation_ids()),
            )
        )
    if creator_id:
        query = query.where(Event.creator_id == creator_id)
    if conversation_id:
        query = query.where(Event.conversation_id == conversation_id)
    if agent:
        query = query.where(Event.agent == agent)
    if event_type:
        query = query.where(Event.type == event_type)

    rows = list(await db.scalars(query.order_by(Event.id.asc()).limit(limit)))
    return {"scope": scope, "count": len(rows), "events": [event_out(row) for row in rows]}


@router.get("/logs/meta")
async def logs_meta(
    db: AsyncSession = Depends(get_db), sid: str = Depends(session_id)
) -> dict:
    conversations = list(
        await db.scalars(
            select(Conversation)
            .where(
                or_(
                    Conversation.session_id == sid,
                    Conversation.source == seeder.SAMPLE_SOURCE,
                )
            )
            .order_by(Conversation.created_at.desc())
        )
    )
    rows = list(
        await db.scalars(
            select(Event).where(
                or_(
                    Event.session_id == sid,
                    Event.conversation_id.in_(_sample_conversation_ids()),
                )
            )
        )
    )
    return {
        "event_types": list(ALL_EVENT_TYPES),
        "seen_event_types": sorted({row.type for row in rows}),
        "agents": sorted({row.agent for row in rows if row.agent}),
        "creator_ids": sorted({row.creator_id for row in rows if row.creator_id}),
        "conversations": [
            {
                "id": item.id,
                "creator_id": item.creator_id,
                "creator_name": await _creator_name(db, item.creator_id),
                "created_at": store.iso(item.created_at),
                "updated_at": store.iso(item.updated_at),
                "status": item.status,
                "config_version": item.config_version,
                "source": item.source or seeder.LIVE_SOURCE,
            }
            for item in conversations
        ],
        "total_events": len(rows),
    }


async def _creator_name(db: AsyncSession, creator_id: str) -> str:
    profile = await data_store.get_creator(db, creator_id)
    return profile.name if profile else creator_id


@router.get("/conversations")
async def list_conversations(
    db: AsyncSession = Depends(get_db), sid: str = Depends(session_id)
) -> dict:
    rows = await store.list_conversations(db, sid)
    out = []
    for item in rows:
        message_count = await db.scalar(
            select(func.count(Message.id)).where(Message.conversation_id == item.id)
        )
        out.append(
            {
                "id": item.id,
                "creator_id": item.creator_id,
                "creator_name": await _creator_name(db, item.creator_id),
                "created_at": store.iso(item.created_at),
                "updated_at": store.iso(item.updated_at),
                "message_count": int(message_count or 0),
                "config_version": item.config_version,
            }
        )
    return {"conversations": out}


async def _load_conversation(db: AsyncSession, sid: str, conversation_id: str) -> Conversation:
    conversation = await store.get_conversation(db, conversation_id)
    if conversation is None or conversation.session_id != sid:
        raise HTTPException(status_code=404, detail="Conversation not found.")
    return conversation


@router.get("/conversations/{conversation_id}")
async def get_conversation(
    conversation_id: str,
    db: AsyncSession = Depends(get_db),
    sid: str = Depends(session_id),
) -> dict:
    conversation = await _load_conversation(db, sid, conversation_id)
    messages = await store.list_messages(db, conversation_id)
    rows = list(
        await db.scalars(
            select(Event)
            .where(Event.conversation_id == conversation_id)
            .order_by(Event.id.asc())
        )
    )
    return {
        "conversation": {
            "id": conversation.id,
            "creator_id": conversation.creator_id,
            "creator_name": await _creator_name(db, conversation.creator_id),
            "created_at": store.iso(conversation.created_at),
            "updated_at": store.iso(conversation.updated_at),
            "config_version": conversation.config_version,
            "pending_agent": conversation.pending_agent,
        },
        "messages": [
            {
                "id": item.id,
                "role": item.role,
                "content": item.content,
                "agent": item.agent,
                "created_at": store.iso(item.created_at),
            }
            for item in messages
        ],
        "events": [event_out(row) for row in rows],
    }


@router.get("/conversations/{conversation_id}/export")
async def export_conversation(
    conversation_id: str,
    db: AsyncSession = Depends(get_db),
    sid: str = Depends(session_id),
) -> Response:
    data = await get_conversation(conversation_id, db=db, sid=sid)
    return Response(
        content=json.dumps(data, indent=2),
        media_type="application/json",
        headers={
            "Content-Disposition": f'attachment; filename="{conversation_id}-transcript.json"'
        },
    )


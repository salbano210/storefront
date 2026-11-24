"""Logs, transcripts and conversation history."""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import orchestrator
from ..db import get_db
from ..models import Conversation, Event, Message
from ..orchestrator import ESCALATED_STATUS
from ..services import config_manager
from ..services import conversation_store as store
from ..services import data_store, seeder
from ..services.event_logger import ALL_EVENT_TYPES, EventLogger
from ..services import event_logger as events
from .deps import event_out, is_admin, session_id

router = APIRouter(prefix="/api", tags=["logs"])


@router.get("/logs")
async def list_logs(
    db: AsyncSession = Depends(get_db),
    creator_id: str | None = None,
    conversation_id: str | None = None,
    agent: str | None = None,
    event_type: str | None = None,
    after_id: int = 0,
    limit: int = Query(200, ge=1, le=500),
) -> dict:
    """The event stream, across every session.

    There is no session filter: the logs are the point of the demo, and a view
    that hides most of the traffic is not worth having.
    """
    query = select(Event).where(Event.id > after_id)
    if creator_id:
        query = query.where(Event.creator_id == creator_id)
    if conversation_id:
        query = query.where(Event.conversation_id == conversation_id)
    if agent:
        query = query.where(Event.agent == agent)
    if event_type:
        query = query.where(Event.type == event_type)

    rows = list(await db.scalars(query.order_by(Event.id.asc()).limit(limit)))
    return {"count": len(rows), "events": [event_out(row) for row in rows]}


async def _distinct(db: AsyncSession, column) -> list[str]:
    """The values actually present in the log, for the filter vocabularies."""
    rows = await db.scalars(
        select(column).where(column.is_not(None)).distinct().order_by(column)
    )
    return [value for value in rows if value]


@router.get("/logs/meta")
async def logs_meta(db: AsyncSession = Depends(get_db)) -> dict:
    """The conversation index the logs page is built on, plus the filter vocabularies.

    Newest first, matching ``GET /api/conversations``: the sidebar reads as a feed, and
    the conversation you just had is the top row. The page opens the first entry, so
    chronological order would have meant scrolling to the bottom on every arrival.

    Aggregates rather than loading the event table: the page polls this while it is
    live, and reading every event to count them is not something to do every couple of
    seconds. The creator names come from one query for the same reason -- asking per
    conversation is 240 queries on a page that has 240 conversations in it.
    """
    conversations = list(
        await db.scalars(select(Conversation).order_by(Conversation.created_at.desc()))
    )
    names = {profile.id: profile.name for profile in await data_store.list_profiles(db)}
    return {
        "conversations": [
            {
                "id": item.id,
                "creator_id": item.creator_id,
                "creator_name": names.get(item.creator_id, item.creator_id),
                "created_at": store.iso(item.created_at),
                "updated_at": store.iso(item.updated_at),
                "status": item.status,
                # What the sidebar files each conversation under, and what the detail
                # header names it by.
                "intent": item.intent,
                "disposition": item.disposition,
                "config_version": item.config_version,
                "source": item.source or seeder.LIVE_SOURCE,
            }
            for item in conversations
        ],
        "event_types": list(ALL_EVENT_TYPES),
        "seen_event_types": await _distinct(db, Event.type),
        "agents": await _distinct(db, Event.agent),
        "creator_ids": await _distinct(db, Event.creator_id),
        "total_events": int(await db.scalar(select(func.count(Event.id))) or 0),
    }


@router.delete("/conversations/{conversation_id}/escalation")
async def release_escalation(
    conversation_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    sid: str = Depends(session_id),
) -> dict:
    """Hand a conversation back to the agent.

    Admin-gated, because it is the human's side of the hand-off: whoever picked the
    conversation up is the only one who can decide the agent should have it again.
    """
    if not is_admin(request):
        raise HTTPException(
            status_code=401,
            detail={
                "code": "admin_passcode_required",
                "message": "Enter the demo admin passcode to take a conversation back.",
            },
        )

    conversation = await store.get_conversation(db, conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found.")
    if conversation.status != ESCALATED_STATUS:
        return {"released": False, "reason": "not_escalated", "status": conversation.status}

    version, _ = await config_manager.get_current(db)
    await store.touch_conversation(db, conversation, status="active")
    await EventLogger(
        db,
        session_id=sid,
        conversation_id=conversation.id,
        creator_id=conversation.creator_id,
        config_version=version,
    ).log(
        events.ESCALATION_RELEASED,
        agent="admin",
        summary="A human handed the conversation back to the agent",
        payload={
            "released_by": "admin",
            "previous_status": ESCALATED_STATUS,
            "status": "active",
        },
    )
    return {"released": True, "status": "active"}


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
                "status": item.status,
                "source": item.source or seeder.LIVE_SOURCE,
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


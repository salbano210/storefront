"""Shared FastAPI dependencies: session cookie, admin passcode, API key."""

from __future__ import annotations

import secrets

from fastapi import HTTPException, Request

from .. import settings
from ..models import Event
from ..services.conversation_store import iso


def session_id(request: Request) -> str:
    sid = getattr(request.state, "session_id", None)
    if not sid:  # pragma: no cover - middleware always sets this
        raise HTTPException(status_code=500, detail="session not initialised")
    return sid


def require_admin(request: Request) -> str:
    """Gate the admin + 'all sessions' views behind the configured passcode."""
    if not settings.ADMIN_PASSCODE:
        return "open"
    provided = request.headers.get("X-Admin-Passcode", "")
    if not secrets.compare_digest(provided, settings.ADMIN_PASSCODE):
        raise HTTPException(
            status_code=401,
            detail={
                "code": "admin_passcode_required",
                "message": "Enter the demo admin passcode to open this view.",
            },
        )
    return "admin"


def resolve_api_key(request: Request) -> tuple[str, bool]:
    """Return (key, is_byok). BYOK wins; the host key is the fallback."""
    header = (request.headers.get("X-OpenRouter-Key") or "").strip()
    if header:
        return header, True
    if settings.OPENROUTER_API_KEY:
        return settings.OPENROUTER_API_KEY, False
    raise HTTPException(
        status_code=400,
        detail={
            "code": "no_api_key",
            "message": (
                "This demo has no host OpenRouter key configured. "
                "Paste your own OpenRouter key to continue."
            ),
        },
    )


def client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def is_admin(request: Request) -> bool:
    """Non-raising admin check, for views that degrade instead of erroring."""
    if not settings.ADMIN_PASSCODE:
        return True
    provided = request.headers.get("X-Admin-Passcode", "")
    return secrets.compare_digest(provided, settings.ADMIN_PASSCODE)


def event_out(event: Event) -> dict:
    import json

    payload = None
    if event.payload_json:
        try:
            payload = json.loads(event.payload_json)
        except (ValueError, TypeError):  # pragma: no cover - defensive
            payload = {"_unparsed": event.payload_json[:2000]}
    return {
        "id": event.id,
        "session_id": event.session_id,
        "conversation_id": event.conversation_id,
        "creator_id": event.creator_id,
        "agent": event.agent,
        "type": event.type,
        "status": event.status,
        "summary": event.summary,
        "payload": payload,
        "model": event.model,
        "latency_ms": event.latency_ms,
        "tokens_in": event.tokens_in,
        "tokens_out": event.tokens_out,
        "cost_usd": event.cost_usd,
        "config_version": event.config_version,
        "created_at": iso(event.created_at),
    }

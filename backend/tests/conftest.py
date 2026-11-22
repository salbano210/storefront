"""Test harness.

Environment is configured *before* the app package is imported so settings pick
it up, and the model client is replaced by a deterministic fake.
"""

from __future__ import annotations

import os
import sys
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient

BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

os.environ["OPENROUTER_API_KEY"] = "sk-or-v1-testkey000000000000000000000000"
os.environ["ADMIN_PASSCODE"] = "test-passcode"
os.environ["MAX_DAILY_SPEND_USD"] = "1000"
os.environ["SESSION_MESSAGE_CAP"] = "40"
os.environ["IP_RATE_LIMIT_PER_MINUTE"] = "0"
os.environ["OPENROUTER_RETRY_BACKOFF_SECONDS"] = "0"
os.environ["STATIC_DIR"] = ""
os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="storefront-tests-")

from app.bootstrap import bootstrap  # noqa: E402
from app.db import AsyncSessionLocal, init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Conversation, GiftOrder  # noqa: E402
from app.services import config_manager, conversation_store as store, data_store, llm_client  # noqa: E402
from app.services.event_logger import EventLogger  # noqa: E402
from app.tools.registry import ToolContext  # noqa: E402
from fake_llm import FakeLLM  # noqa: E402

ADMIN_HEADERS = {"X-Admin-Passcode": "test-passcode"}


@pytest.fixture(autouse=True)
async def _database():
    await init_db()
    async with AsyncSessionLocal() as db:
        await bootstrap(db)
    yield


@pytest.fixture
def use_fake_llm(monkeypatch):
    """Install a FakeLLM as the model client for the duration of a test."""

    def _install(**kwargs) -> FakeLLM:
        fake = FakeLLM(**kwargs)
        monkeypatch.setattr(llm_client, "build_llm", lambda *args, **kw: fake)
        return fake

    return _install


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as http:
        yield http


@asynccontextmanager
async def _tool_context(
    creator_id: str,
    agent: str = "gift_request_agent",
    conversation_id: str | None = None,
):
    """A real ToolContext, including a real conversation row.

    The conversation exists because the tools and the orchestrator write to it,
    so a context without one would not be representative.
    """
    async with AsyncSessionLocal() as db:
        version, config = await config_manager.get_current(db)
        creator = await data_store.get_creator(db, creator_id)
        assert creator is not None, f"unknown creator {creator_id}"
        session = await store.get_or_create_session(db, f"sess_unit_{creator_id}_{agent}")
        conversation = Conversation(
            id=conversation_id or f"conv_unit_{uuid4().hex[:8]}",
            session_id=session.id,
            creator_id=creator.id,
            config_version=version,
        )
        db.add(conversation)
        await db.commit()

        logger = EventLogger(
            db,
            session_id=session.id,
            conversation_id=conversation.id,
            creator_id=creator.id,
            config_version=version,
        )
        yield ToolContext(
            db=db,
            session_id=session.id,
            conversation_id=conversation.id,
            creator=creator,
            config=config,
            config_version=version,
            logger=logger,
            session_row=session,
            agent=agent,
        ), db


@pytest.fixture
def tool_context():
    return _tool_context


@pytest.fixture
def gift_order_count():
    async def _count(session_id: str | None = None) -> int:
        from sqlalchemy import func, select

        async with AsyncSessionLocal() as db:
            query = select(func.count(GiftOrder.id))
            if session_id:
                query = query.where(GiftOrder.session_id == session_id)
            return int(await db.scalar(query) or 0)

    return _count

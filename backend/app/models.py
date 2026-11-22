"""SQLAlchemy tables.

Design notes:
  * Every event row records ``config_version`` so the logs page can prove which
    version of the brand rules was in force for a given decision.
  * ``gift_orders`` carries a UNIQUE(session_id, creator_id, sku) constraint --
    the database itself is the last line of defence against duplicate gifting.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Session(Base):
    __tablename__ = "sessions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    key_mode: Mapped[str] = mapped_column(String(16), default="host")  # reserved; always "host"
    message_count: Mapped[int] = mapped_column(Integer, default=0)
    total_cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    tokens_in: Mapped[int] = mapped_column(Integer, default=0)
    tokens_out: Mapped[int] = mapped_column(Integer, default=0)


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    session_id: Mapped[str] = mapped_column(String(64), ForeignKey("sessions.id"), index=True)
    creator_id: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(24), default="active")
    pending_agent: Mapped[str | None] = mapped_column(String(48), nullable=True)
    # Denormalised for analytics: the latest intent the intent agent settled on and
    # the disposition the conversation reached. Both are recomputed every turn.
    intent: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    disposition: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    # The raw turn outcome behind the disposition, kept so the dashboard can break
    # the "other" bucket down instead of hiding it.
    last_outcome: Mapped[str | None] = mapped_column(String(32), nullable=True)
    config_version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    conversation_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("conversations.id"), index=True
    )
    role: Mapped[str] = mapped_column(String(16))  # creator | agent | system
    content: Mapped[str] = mapped_column(Text)
    agent: Mapped[str | None] = mapped_column(String(48), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Event(Base):
    __tablename__ = "events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(String(64), index=True)
    conversation_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    creator_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    agent: Mapped[str | None] = mapped_column(String(48), nullable=True, index=True)
    type: Mapped[str] = mapped_column(String(48), index=True)
    status: Mapped[str] = mapped_column(String(16), default="ok")  # ok | error | blocked
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    payload_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    model: Mapped[str | None] = mapped_column(String(96), nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tokens_in: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tokens_out: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cost_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    config_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class GiftOrder(Base):
    __tablename__ = "gift_orders"
    __table_args__ = (
        UniqueConstraint("session_id", "creator_id", "sku", name="uq_gift_session_creator_sku"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(String(64), index=True)
    conversation_id: Mapped[str] = mapped_column(String(64))
    creator_id: Mapped[str] = mapped_column(String(64), index=True)
    creator_name: Mapped[str] = mapped_column(String(120))
    sku: Mapped[str] = mapped_column(String(64))
    product_name: Mapped[str] = mapped_column(String(120))
    order_id: Mapped[str] = mapped_column(String(32))
    shipping_address: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ConfigVersion(Base):
    __tablename__ = "config_versions"

    version: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    config_json: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_by: Mapped[str | None] = mapped_column(String(64), nullable=True)


class CreatorProfileRow(Base):
    """Verified creator profiles.

    Seeded from ``app/data/creators.json`` on first boot; after that the database
    is the source of truth so a brand admin can edit the metrics the agents
    decide on.
    """

    __tablename__ = "creator_profiles"

    creator_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    profile_json: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

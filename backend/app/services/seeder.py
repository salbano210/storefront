"""Sample conversation history, produced by real execution.

The logs and the dashboard are only interesting once there is history in them, and
the free-tier deployment resets its database on every deploy. This fills that gap.

It does **not** write rows by hand. Each sample conversation is driven through the
real orchestrator with a deterministic model, so every event was genuinely emitted
by the code path that emits it in production: the routing decision, the handoff,
the tool calls, the criteria evaluations, the guardrail checks and the reply.

What is synthetic is the model's wording, the model's name
(``sample/offline-simulator``) and the timestamps, which are backdated so the
dashboard has a time axis. Sample conversations carry ``source="seeded"`` and are
labelled in the UI, so they are never passed off as a real creator's.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import orchestrator
from ..models import Conversation, Event, GiftOrder, Message
from . import config_manager, data_store
from . import conversation_store as store
from .event_logger import EventLogger
from .simulated_model import SimulatedModel

SAMPLE_SOURCE = "seeded"
LIVE_SOURCE = "live"
# Every sample conversation gets its own session id under this prefix, so the
# per-session duplicate guard behaves the way it does in real life instead of
# collapsing the whole sample history into one tenant.
SAMPLE_SESSION_PREFIX = "sess_sample"
# The name the simulated model reports, so a sample turn is identifiable in the
# logs by the model that produced it as well as by the conversation it belongs to.
SIMULATED_MODEL_NAME = "sample/offline-simulator"

# Every creator profile carries an address, and the agent offers it before asking
# for a new one, so this is what "settle the delivery address" looks like.
CONFIRM_ADDRESS = "Yes, that address is right."


@dataclass(frozen=True)
class Recipe:
    """One kind of conversation, and how often it should appear.

    The weights are relative: they are scaled to the requested conversation count.
    ``browse`` makes the agent search the whole catalogue instead of the creator's
    words, which is what produces a product-link suggestion rather than a decline.

    ``thread`` names a shared session. Conversations in the same thread are the
    same browser coming back, which is the only way a repeat order turns into the
    duplicate guard rather than a second gift.
    """

    outcome: str
    creator_id: str
    turns: tuple[str, ...]
    weight: int = 1
    browse: bool = False
    thread: str | None = None


# Orders first, then the rest. The reorder thread at the end depends on an order
# already existing in that session, which is why it comes last.
RECIPES: tuple[Recipe, ...] = (
    # Approved: the creator qualifies, confirms the address on file, and the
    # orchestrator places the order. Each of these is its own session.
    Recipe("gift_issued", "creator_001", ("Hi! I'd love to try the Barrier Repair Serum.", CONFIRM_ADDRESS), weight=5),
    Recipe("gift_issued", "creator_005", ("Hey -- could I get the Barrier Repair Serum?", CONFIRM_ADDRESS), weight=4),
    Recipe("gift_issued", "creator_002", ("Can I try the Hydrating Cleanser?", CONFIRM_ADDRESS), weight=3),
    Recipe("gift_issued", "creator_003", ("Could I get the Discovery Set?", CONFIRM_ADDRESS), weight=2),
    # Declined: below the bar, so the sweep names what they do qualify for.
    Recipe("gift_declined", "creator_002", ("Could I try the Barrier Repair Serum?",), weight=3),
    Recipe("gift_declined", "creator_003", ("I'd love the Barrier Repair Serum.",), weight=3),
    Recipe("gift_declined", "creator_004", ("Could I get the Barrier Repair Serum?",), weight=2),
    # Eligible, but the delivery address was never settled, so nothing was ordered.
    Recipe("details_required", "creator_001", ("Hi, I'd love to try the Barrier Repair Serum.",), weight=3),
    Recipe("details_required", "creator_005", ("Can I get the Barrier Repair Serum?",), weight=2),
    # Questions, including the partnership pitch we have no agent for.
    Recipe("inquiry", "creator_002", ("What products do you sell?",), weight=4),
    Recipe("inquiry", "creator_004", ("What's your shipping policy?",), weight=3),
    Recipe("inquiry", "creator_005", ("I'd love to partner with you on a launch campaign.",), weight=3),
    # Handed to a human by a configured keyword. These never reach the model.
    Recipe("escalated", "creator_003", ("Could I speak to a human about my order, please?",), weight=2),
    Recipe("escalated", "creator_004", ("I'm not happy. I've spoken to my lawyer about this.",), weight=1),
    # The same creator coming back for a gift she already has: one order, then the
    # duplicate guard answering.
    Recipe("gift_issued", "creator_001", ("Hi again! I'd love the Barrier Repair Serum.", CONFIRM_ADDRESS), weight=1, thread="reorder"),
    Recipe("gift_duplicate", "creator_001", ("Could I get another Barrier Repair Serum?", CONFIRM_ADDRESS), weight=2, thread="reorder"),
)


def _plan(count: int) -> list[Recipe]:
    """Scale the recipes to ``count`` conversations, keeping their proportions."""
    total = sum(recipe.weight for recipe in RECIPES)
    exact = [count * recipe.weight / total for recipe in RECIPES]
    base = [int(value) for value in exact]
    # Largest remainder, so the total is exactly what was asked for.
    order = sorted(range(len(base)), key=lambda i: exact[i] - base[i], reverse=True)
    for index in order[: count - sum(base)]:
        base[index] += 1
    return [recipe for recipe, times in zip(RECIPES, base) for _ in range(times)]


def _start_times(rng: random.Random, count: int, days: int) -> list[datetime]:
    """A backdated start time per conversation, busier towards today.

    A flat distribution would draw a flat chart. Weighting the recent days makes
    the trend line read like a channel that is picking up.
    """
    midnight = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    offsets = list(range(days))
    weights = [days - offset for offset in offsets]  # today is `days`x as likely
    picks = rng.choices(offsets, weights=weights, k=count)
    starts = []
    for offset in picks:
        moment = midnight - timedelta(days=offset)
        hour = rng.randint(8, 19)
        minute = rng.randint(0, 59)
        starts.append(moment + timedelta(hours=hour, minutes=minute))
    return sorted(starts)


def _spread(rows: list, start: datetime, span_seconds: float) -> None:
    """Give ordered rows evenly spaced timestamps inside one conversation."""
    if not rows:
        return
    step = span_seconds / len(rows)
    for index, row in enumerate(rows):
        row.created_at = start + timedelta(seconds=index * step)


async def _backdate(db: AsyncSession, conversation_id: str, start: datetime, span: float) -> None:
    messages = list(
        await db.scalars(
            select(Message).where(Message.conversation_id == conversation_id).order_by(Message.id)
        )
    )
    events = list(
        await db.scalars(
            select(Event).where(Event.conversation_id == conversation_id).order_by(Event.id)
        )
    )
    _spread(messages, start, span)
    _spread(events, start, span)

    conversation = await db.get(Conversation, conversation_id)
    if conversation is not None:
        conversation.created_at = start
        conversation.updated_at = start + timedelta(seconds=span)
    await db.commit()


async def clear_sample_history(db: AsyncSession) -> dict:
    """Remove every sample conversation, and everything they produced.

    By ``source`` rather than by session, because sample history is demo data
    rather than one browser's property: generating it twice from two browsers
    should still leave one copy.
    """
    ids = list(
        await db.scalars(
            select(Conversation.id).where(Conversation.source == SAMPLE_SOURCE)
        )
    )
    if not ids:
        return {"conversations": 0, "messages": 0, "events": 0, "orders": 0}

    counts: dict[str, int] = {}
    for model, column in (
        (Message, Message.conversation_id),
        (Event, Event.conversation_id),
        (GiftOrder, GiftOrder.conversation_id),
    ):
        counts[model.__tablename__] = len(
            list(await db.scalars(select(model.id).where(column.in_(ids))))
        )
        await db.execute(delete(model).where(column.in_(ids)))
    await db.execute(delete(Conversation).where(Conversation.id.in_(ids)))
    await db.commit()

    return {
        "conversations": len(ids),
        "messages": counts.get("messages", 0),
        "events": counts.get("events", 0),
        "orders": counts.get("gift_orders", 0),
    }


async def seed_history(
    db: AsyncSession,
    *,
    count: int = 60,
    days: int = 30,
    seed: int = 7,
) -> dict:
    """Generate sample history, replacing any already there.

    Replacing rather than appending keeps this idempotent: pressing the button
    twice gives the same history, not twice as much.
    """
    count = max(len(RECIPES), min(count, 400))
    days = max(1, min(days, 365))
    rng = random.Random(seed)

    cleared = await clear_sample_history(db)
    config_version, config = await config_manager.get_current(db)

    plan = _plan(count)
    starts = _start_times(rng, len(plan), days)

    created: list[tuple[str, datetime, int]] = []
    outcomes: dict[str, int] = {}
    by_creator: dict[str, int] = {}
    errors: list[str] = []
    # One session per conversation, unless the recipe names a thread. The
    # duplicate guard keys on the session, so sharing one session across the whole
    # batch would turn every repeat order into a duplicate.
    sessions: dict[str, object] = {}
    counter = 0

    for recipe, start in zip(plan, starts):
        creator = await data_store.get_creator(db, recipe.creator_id)
        if creator is None:  # pragma: no cover - the recipes name seeded creators
            errors.append(f"{recipe.creator_id}: no such creator")
            continue

        if recipe.thread:
            session_row = sessions.get(recipe.thread)
            if session_row is None:
                session_row = await store.get_or_create_session(
                    db, f"{SAMPLE_SESSION_PREFIX}_{recipe.thread}"
                )
                sessions[recipe.thread] = session_row
        else:
            counter += 1
            session_row = await store.get_or_create_session(
                db, f"{SAMPLE_SESSION_PREFIX}_{counter:03d}"
            )

        conversation = await store.create_conversation(
            db,
            session_id=session_row.id,
            creator_id=creator.id,
            config_version=config_version,
            source=SAMPLE_SOURCE,
        )

        outcome = ""
        try:
            for turn in recipe.turns:
                result = await orchestrator.handle_turn(
                    db,
                    session_row=session_row,
                    conversation=conversation,
                    creator=creator,
                    config=config,
                    config_version=config_version,
                    user_message=turn,
                    # A fresh model per conversation, wired to the audit trail the
                    # same way the real client is, so the transcript has the model
                    # step a live conversation has. The simulated model carries no
                    # state, but a fresh one also keeps a browse recipe from leaking
                    # into the next conversation.
                    llm=SimulatedModel(
                        model=SIMULATED_MODEL_NAME,
                        browse_without_query=recipe.browse,
                        logger=EventLogger(
                            db,
                            session_id=session_row.id,
                            conversation_id=conversation.id,
                            creator_id=creator.id,
                            config_version=config_version,
                        ),
                    ),
                )
                outcome = result.outcome.get("type", "")
        except Exception as exc:  # noqa: BLE001 - report, don't lose the whole batch
            errors.append(f"{recipe.outcome}/{recipe.creator_id}: {type(exc).__name__}: {exc}")
            continue

        outcomes[outcome] = outcomes.get(outcome, 0) + 1
        by_creator[creator.id] = by_creator.get(creator.id, 0) + 1
        created.append((conversation.id, start, len(recipe.turns)))

    # Backdate last, so nothing else has to know the timestamps were rewritten.
    for conversation_id, start, turns in created:
        await _backdate(db, conversation_id, start, rng.uniform(90, 240) * turns)

    return {
        "range": {"from": starts[0].date().isoformat(), "to": starts[-1].date().isoformat()},
        "conversations": len(created),
        "requested": len(plan),
        "sessions": len(sessions) + counter,
        "outcomes": dict(sorted(outcomes.items(), key=lambda item: -item[1])),
        "by_creator": dict(sorted(by_creator.items())),
        "cleared": cleared,
        "errors": errors[:10],
    }


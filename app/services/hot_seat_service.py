"""Hot Seat voice game state machine (HOTSEAT-01/02/03).

States: ``joining`` (members may join) → ``game`` (running) → ``ended``.
Every guard the evidence names is enforced here so the text commands and any
future panel share one rule set.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import logging
from typing import Any

from sqlalchemy import delete, select

from app.database.engine import async_session
from app.database.models import HotSeatGame, HotSeatGuest

logger = logging.getLogger(__name__)

STATE_JOINING = "joining"
STATE_GAME = "game"
STATE_ENDED = "ended"
CANCELLABLE_STATES = (STATE_JOINING, STATE_GAME)

MODE_ALL = "all"
MODE_PRIVATE = "private"

MIN_QUESTIONS = 1
MAX_QUESTIONS = 50


@dataclass(frozen=True)
class HotSeatResult:
    ok: bool
    reason: str = "ok"
    game: Any | None = None
    count: int = 0


async def get_active_game(chat_id: int) -> HotSeatGame | None:
    async with async_session() as session:
        result = await session.execute(
            select(HotSeatGame).where(
                HotSeatGame.chat_id == chat_id,
                HotSeatGame.state.in_(CANCELLABLE_STATES),
            )
        )
        return result.scalars().first()


async def _channel_playback_active(chat_id: int) -> bool:
    """HOTSEAT-01 blocking condition: playback routed to a linked channel.

    The evidence says creation is rejected when the group's player is in
    "channel playback mode"; CALLMGMT-03 is exactly that flag.
    """
    try:
        from app.repositories import settings_repo

        row = await settings_repo.get_chat_settings(chat_id)
        return bool(getattr(row, "channel_playback_enabled", False))
    except Exception:
        logger.debug("hot seat channel-mode lookup failed chat_id=%s", chat_id, exc_info=True)
        return False


async def create_game(
    chat_id: int,
    *,
    question_count: int,
    question_type: str = "general",
    mode: str = MODE_ALL,
    created_by: int | None = None,
) -> HotSeatResult:
    """HOTSEAT-01: start a joinable Hot Seat, one per chat at a time."""
    if mode not in (MODE_ALL, MODE_PRIVATE):
        return HotSeatResult(False, "invalid_mode")
    if not MIN_QUESTIONS <= int(question_count) <= MAX_QUESTIONS:
        return HotSeatResult(False, "invalid_question_count")
    if await _channel_playback_active(chat_id):
        return HotSeatResult(False, "channel_mode_blocked")
    if await get_active_game(chat_id) is not None:
        return HotSeatResult(False, "already_active")

    async with async_session() as session:
        async with session.begin():
            game = HotSeatGame(
                chat_id=chat_id,
                state=STATE_JOINING,
                mode=mode,
                question_count=int(question_count),
                question_type=question_type,
                created_by=created_by,
            )
            session.add(game)
        await session.refresh(game)
    return HotSeatResult(True, "created", game=game)


async def cancel_game(chat_id: int) -> HotSeatResult:
    """HOTSEAT-02: cancel only from `joining` or `game`."""
    game = await get_active_game(chat_id)
    if game is None:
        return HotSeatResult(False, "no_active_game")
    async with async_session() as session:
        async with session.begin():
            row = await session.get(HotSeatGame, game.id)
            if row is None or row.state not in CANCELLABLE_STATES:
                return HotSeatResult(False, "no_active_game")
            row.state = STATE_ENDED
            row.ended_at = datetime.now(timezone.utc)
            await session.execute(
                delete(HotSeatGuest).where(HotSeatGuest.game_id == game.id)
            )
    return HotSeatResult(True, "cancelled", game=game)


async def add_guest(chat_id: int, user_id: int, *, actor_id: int | None) -> HotSeatResult:
    """HOTSEAT-03: private mode + `joining` state + creator-only."""
    game = await get_active_game(chat_id)
    if game is None:
        return HotSeatResult(False, "no_active_game")
    if game.mode != MODE_PRIVATE:
        return HotSeatResult(False, "not_private_mode")
    if game.state != STATE_JOINING:
        return HotSeatResult(False, "not_joining_state")
    if game.created_by is not None and actor_id != game.created_by:
        return HotSeatResult(False, "creator_only")

    async with async_session() as session:
        existing = await session.execute(
            select(HotSeatGuest).where(
                HotSeatGuest.game_id == game.id, HotSeatGuest.user_id == user_id
            )
        )
        if existing.scalar_one_or_none() is not None:
            return HotSeatResult(False, "already_guest", game=game)
        async with session.begin():
            session.add(
                HotSeatGuest(game_id=game.id, user_id=user_id, added_by=actor_id)
            )
    return HotSeatResult(True, "guest_added", game=game)


async def remove_guest(chat_id: int, user_id: int) -> HotSeatResult:
    """HOTSEAT-03: removal requires `joining` state.

    The evidence leaves the creator-only restriction unconfirmed for removal, so
    it is intentionally not enforced here; the state guard still applies.
    """
    game = await get_active_game(chat_id)
    if game is None:
        return HotSeatResult(False, "no_active_game")
    if game.state != STATE_JOINING:
        return HotSeatResult(False, "not_joining_state")
    async with async_session() as session:
        async with session.begin():
            result = await session.execute(
                delete(HotSeatGuest).where(
                    HotSeatGuest.game_id == game.id, HotSeatGuest.user_id == user_id
                )
            )
    if not result.rowcount:
        return HotSeatResult(False, "not_a_guest", game=game)
    return HotSeatResult(True, "guest_removed", game=game)


async def list_guests(game_id: int) -> list[HotSeatGuest]:
    async with async_session() as session:
        rows = (
            await session.execute(
                select(HotSeatGuest).where(HotSeatGuest.game_id == game_id)
            )
        ).scalars().all()
        return list(rows)

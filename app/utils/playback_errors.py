"""Shared playback join-failure message mapping for commands and callbacks."""

from __future__ import annotations

from pyrogram.types import CallbackQuery, InlineKeyboardMarkup, Message

from app.services.analytics_service import track_event
from app.utils.i18n import t
from app.utils.telegram_message import safe_edit_message


def playback_failure_message_key() -> str:
    """Return the i18n key for the most recent join_voice_chat failure."""
    from app.services.call_service import CallService

    return CallService.pop_join_failure_key() or "playback_cmd.failed"


def playback_failure_text(lang: str) -> str:
    """Return localized user-facing text for the most recent join failure."""
    return t(lang, playback_failure_message_key())


async def reply_playback_join_failure(
    message: Message,
    *,
    lang: str = "fa",
    track: bool = True,
) -> None:
    """Reply to a message with a mapped playback join failure."""
    if track:
        await track_event("errors.pytgcalls", feature="playback", status="fail")
    await message.reply(playback_failure_text(lang))


async def edit_playback_join_failure(
    query: CallbackQuery,
    *,
    lang: str = "fa",
    reply_markup: InlineKeyboardMarkup | None = None,
    track: bool = True,
) -> None:
    """Edit the callback message with a mapped playback join failure."""
    if track:
        await track_event("errors.pytgcalls", feature="playback", status="fail")
    await safe_edit_message(
        query.message,
        playback_failure_text(lang),
        reply_markup=reply_markup,
    )

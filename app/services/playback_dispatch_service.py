"""Central busy-chat playback dispatch: queue-on-busy vs play-now."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from pyrogram.types import CallbackQuery, Message

from app.repositories import playlist_repo
from app.utils.i18n import t
from app.utils.media_sources import is_http_url, normalize_media_source


class BusyPlaybackAction(str, Enum):
    """Outcome of busy-playback decision."""

    PLAY_NOW = "play_now"
    QUEUED = "queued"
    ALREADY_PLAYING = "already_playing"
    QUEUE_UNAVAILABLE_TEMP = "queue_unavailable_for_temp_file"


_MESSAGE_KEYS: dict[BusyPlaybackAction, str] = {
    BusyPlaybackAction.QUEUED: "playback_cmd.queued",
    BusyPlaybackAction.ALREADY_PLAYING: "playback_cmd.already_playing",
    BusyPlaybackAction.QUEUE_UNAVAILABLE_TEMP: "playback_cmd.queue_unavailable_for_temp_file",
}


@dataclass(frozen=True, slots=True)
class BusyPlaybackDecision:
    """Typed result for busy playback routing."""

    action: BusyPlaybackAction
    message_key: str | None = None


def is_chat_playing(chat_id: int) -> bool:
    """Return True when this chat has an active stream or join in progress."""
    from app.services.call_service import CallService

    return CallService.is_chat_playing(chat_id) or CallService.is_chat_joining(chat_id)


async def is_chat_queue_on_busy_enabled(chat_id: int) -> bool:
    """Return whether queue-on-busy is enabled via ``smart_radio_enabled``."""
    from app.repositories import settings_repo

    cs = await settings_repo.get_chat_settings(chat_id)
    if cs is None:
        return False
    return bool(cs.smart_radio_enabled)


def is_queue_safe_source(
    source: str | None,
    *,
    is_temp_local: bool = False,
    allow_local_temp_queue: bool = False,
) -> bool:
    """Return True when a source may be persisted in the playlist queue."""
    if not source or is_temp_local:
        return False
    if allow_local_temp_queue:
        normalized = normalize_media_source(source)
        return normalized is not None
    return is_http_url(source)


async def decide_busy_playback(
    chat_id: int,
    source: str,
    media_type: str,
    *,
    is_temp_local: bool = False,
    allow_local_temp_queue: bool = False,
) -> BusyPlaybackDecision:
    """Decide whether to play now, enqueue, or reject when chat may be busy.

    Args:
        chat_id: Target group chat id.
        source: Resolved playback source (URL or trusted local path).
        media_type: ``audio`` or ``video``.
        is_temp_local: True for one-shot telegram download paths.
        allow_local_temp_queue: When True, allow trusted local paths (not used in B4).

    Returns:
        BusyPlaybackDecision with action and optional user message key.
    """
    del media_type  # reserved for future per-type queue rules
    if not is_chat_playing(chat_id):
        return BusyPlaybackDecision(action=BusyPlaybackAction.PLAY_NOW)

    if not await is_chat_queue_on_busy_enabled(chat_id):
        return BusyPlaybackDecision(
            action=BusyPlaybackAction.ALREADY_PLAYING,
            message_key=_MESSAGE_KEYS[BusyPlaybackAction.ALREADY_PLAYING],
        )

    if is_queue_safe_source(
        source,
        is_temp_local=is_temp_local,
        allow_local_temp_queue=allow_local_temp_queue,
    ):
        return BusyPlaybackDecision(
            action=BusyPlaybackAction.QUEUED,
            message_key=_MESSAGE_KEYS[BusyPlaybackAction.QUEUED],
        )

    return BusyPlaybackDecision(
        action=BusyPlaybackAction.QUEUE_UNAVAILABLE_TEMP,
        message_key=_MESSAGE_KEYS[BusyPlaybackAction.QUEUE_UNAVAILABLE_TEMP],
    )


async def enqueue_playback_item(
    chat_id: int,
    source: str,
    media_type: str,
    *,
    title: str | None = None,
    added_by: int | None = None,
    duration: int = 0,
) -> None:
    """Append a queue-safe item to the chat playlist queue."""
    await playlist_repo.add_to_queue(
        chat_id=chat_id,
        stream_url=source if is_http_url(source) else None,
        file_path=source if not is_http_url(source) else None,
        title=title,
        duration=duration,
        media_type=media_type,
        added_by=added_by,
    )


async def respond_busy_decision(
    target: Message | CallbackQuery,
    decision: BusyPlaybackDecision,
    *,
    lang: str = "fa",
) -> None:
    """Send the user-facing message for a non-play-now busy decision."""
    key = decision.message_key
    if not key:
        return
    text = t(lang, key)
    if isinstance(target, CallbackQuery):
        await target.answer(text, show_alert=True)
    else:
        await target.reply(text)


async def apply_busy_playback_decision(
    target: Message | CallbackQuery,
    chat_id: int,
    source: str,
    media_type: str,
    decision: BusyPlaybackDecision,
    *,
    title: str | None = None,
    requester_id: int | None = None,
    lang: str = "fa",
) -> bool:
    """Apply a busy decision: respond and optionally enqueue.

    Returns:
        True when the caller must stop (do not call ``join_voice_chat``).
        False when ``action`` is ``PLAY_NOW`` (caller should join).
    """
    if decision.action == BusyPlaybackAction.PLAY_NOW:
        return False

    if decision.action == BusyPlaybackAction.QUEUED:
        await enqueue_playback_item(
            chat_id,
            source,
            media_type,
            title=title,
            added_by=requester_id,
        )

    await respond_busy_decision(target, decision, lang=lang)
    return True

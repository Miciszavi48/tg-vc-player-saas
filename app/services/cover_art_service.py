"""Telegram-native cover art for now-playing messages (Phase B5-4)."""

from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)

_TELEGRAM_CAPTION_MAX = 1024


@dataclass(frozen=True)
class TelegramCover:
    """A Telegram-hosted thumbnail suitable for ``reply_photo``.

    Attributes:
        file_id: Opaque Telegram file reference for photo send only.
        source: Internal label for logs (never shown to users).
    """

    file_id: str
    source: str


def _thumb_file_id(thumb: object | None) -> str | None:
    """Return a non-empty Telegram thumb file_id when present."""
    if thumb is None:
        return None
    raw = getattr(thumb, "file_id", None)
    if raw is None:
        return None
    text = str(raw).strip()
    return text or None


def _thumb_from_media(media: object) -> str | None:
    """Extract the best available thumb file_id from a Pyrogram media object."""
    for attr in ("thumb", "thumbnail"):
        file_id = _thumb_file_id(getattr(media, attr, None))
        if file_id:
            return file_id

    thumbs = getattr(media, "thumbs", None)
    if thumbs:
        for thumb in reversed(list(thumbs)):
            file_id = _thumb_file_id(thumb)
            if file_id:
                return file_id
    return None


def extract_telegram_cover_from_reply(reply: object | None) -> TelegramCover | None:
    """Return Telegram-native cover metadata from a replied message.

    Inspects audio, video, and audio/video document thumbs only. Never fetches
    external URLs or local files. Returns ``None`` when no safe thumb exists.
    """
    if reply is None:
        return None

    audio = getattr(reply, "audio", None)
    if audio is not None:
        file_id = _thumb_from_media(audio)
        if file_id:
            return TelegramCover(file_id=file_id, source="telegram_audio")

    video = getattr(reply, "video", None)
    if video is not None:
        file_id = _thumb_from_media(video)
        if file_id:
            return TelegramCover(file_id=file_id, source="telegram_video")

    document = getattr(reply, "document", None)
    if document is not None:
        mime = getattr(document, "mime_type", None) or ""
        if mime.startswith("audio/") or mime.startswith("video/"):
            file_id = _thumb_from_media(document)
            if file_id:
                return TelegramCover(file_id=file_id, source="telegram_document")

    return None


async def is_show_cover_enabled(chat_id: int) -> bool:
    """Return whether this chat wants cover art on now-playing messages."""
    from app.repositories import settings_repo

    cs = await settings_repo.get_chat_settings(chat_id)
    if cs is None:
        return True
    return bool(getattr(cs, "show_cover", True))


async def reply_now_playing_with_optional_cover(
    message: object,
    *,
    chat_id: int,
    text: str,
    reply_markup: object | None = None,
    reply_to_message: object | None = None,
) -> None:
    """Send now-playing as photo+caption or text, with silent fallback.

    Uses Telegram-native thumbs only when ``show_cover`` is enabled. Never
    exposes file_id in the caption. On photo send failure, falls back to text.
    """
    cover_source = reply_to_message
    if cover_source is None:
        cover_source = getattr(message, "reply_to_message", None)

    cover: TelegramCover | None = None
    if await is_show_cover_enabled(chat_id):
        cover = extract_telegram_cover_from_reply(cover_source)

    reply_text = getattr(message, "reply", None)
    reply_photo = getattr(message, "reply_photo", None)

    if cover is not None and callable(reply_photo):
        if len(text) > _TELEGRAM_CAPTION_MAX:
            logger.debug(
                "Now-playing caption too long for photo in chat %s; using text reply",
                chat_id,
            )
        else:
            try:
                await reply_photo(
                    cover.file_id,
                    caption=text,
                    reply_markup=reply_markup,
                )
                return
            except Exception:
                logger.warning(
                    "Failed to send now-playing cover (%s) for chat %s; using text reply",
                    cover.source,
                    chat_id,
                    exc_info=True,
                )

    if callable(reply_text):
        await reply_text(text, reply_markup=reply_markup)

"""Normalized playback text commands and reply-media resolution."""

from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass
from enum import Enum
from typing import Any, Literal

from pyrogram import filters
from pyrogram.types import Message

from app.utils.media_sources import is_http_url

logger = logging.getLogger(__name__)

_PLAY_PREFIXES = ("پخش", "play")
_NON_GENERIC_PLAY_COMMAND_PREFIXES = (
    "پخش خودکار موزیک",
    "پخش خودکار ویدئو",
    "پخش خودکار ویدیو",
    "پخش ویدیو",
    "پخش تیوی",
    "پخش تلویزیون",
    "پخش بیصدا",
    "پخش باصدا",
    "پخش رادیو",
    "پخش ماهواره",
    "پخش سریال",
    # CALLMGMT-03/06: these are their own commands, not a generic play query.
    "پخش کانال",
    "پخش موضوعی",
    "play channel",
    "thematic play",
    "play auto music",
    "play auto video",
    "playvideo",
    "playtv",
)
_ZW_CHARS_RE = re.compile(r"[\u200c\u200d\ufeff\u2060]+")
_WS_COLLAPSE_RE = re.compile(r"\s+")
_SLASH_PLAY_BOT_SUFFIX_RE = re.compile(r"^@\w+", re.IGNORECASE)

_AUDIO_EXTENSIONS = frozenset({
    ".mp3", ".m4a", ".ogg", ".opus", ".wav", ".flac", ".aac", ".wma",
})
_VIDEO_EXTENSIONS = frozenset({
    ".mp4", ".mkv", ".webm", ".mov", ".avi", ".m4v",
})


class PlaybackCommandKind(str, Enum):
    """Typed playback command variants."""

    PERSIAN_PLAY = "persian_play"
    ENGLISH_PLAY = "english_play"
    SLASH_PLAY = "slash_play"


@dataclass(frozen=True, slots=True)
class ParsedPlaybackCommand:
    """Result of parsing a user playback command message."""

    kind: PlaybackCommandKind
    prefix: str
    remainder: str
    dedication: str | None = None


@dataclass(frozen=True, slots=True)
class ReplyMediaResolution:
    """Resolved playable media from a replied Telegram message."""

    media_type: Literal["audio", "video"]
    media_object: Any
    source_kind: Literal["telegram", "url", "text"]


def normalize_playback_text(message: Message | str | None) -> str:
    """Normalize message text/caption for playback command matching."""
    if message is None:
        return ""
    if isinstance(message, str):
        raw = message
    else:
        raw = message.text or message.caption or ""
    text = unicodedata.normalize("NFKC", raw)
    text = _ZW_CHARS_RE.sub("", text)
    text = _WS_COLLAPSE_RE.sub(" ", text).strip()
    return text


def _parse_dedication(remainder: str) -> tuple[str, str | None]:
    """Parse optional dedication target from command remainder."""
    if not remainder:
        return "", None

    if "برای" in remainder:
        parts = remainder.rsplit("برای", 1)
        return (parts[0].strip(), parts[1].strip() or None)

    tokens = remainder.split(maxsplit=1)
    first = tokens[0]
    if first.startswith("@") or first.isdigit():
        target = first
        source = tokens[1].strip() if len(tokens) > 1 else ""
        return source, target

    return remainder, None


def _starts_with_non_generic_play_command(text: str) -> bool:
    lowered = text.lower()
    for command in _NON_GENERIC_PLAY_COMMAND_PREFIXES:
        normalized = normalize_playback_text(command).lower()
        if lowered == normalized or lowered.startswith(normalized + " "):
            return True
    return False


def parse_playback_command(text: str) -> ParsedPlaybackCommand | None:
    """Parse start-anchored playback commands from normalized text.

    Matches:
        پخش, پخش <query>, play, play <query>, /play, /play <query>

    Does not match embedded ``پخش`` mid-sentence.
    """
    normalized = normalize_playback_text(text)
    if not normalized:
        return None
    if _starts_with_non_generic_play_command(normalized):
        return None

    body = normalized
    kind = PlaybackCommandKind.PERSIAN_PLAY
    prefix = "پخش"

    if body.lower().startswith("/play"):
        kind = PlaybackCommandKind.SLASH_PLAY
        prefix = "/play"
        body = body[5:].lstrip()
    elif body.lower().startswith("play"):
        kind = PlaybackCommandKind.ENGLISH_PLAY
        prefix = "play"
        body = body[4:].lstrip()
    elif body.startswith("پخش"):
        kind = PlaybackCommandKind.PERSIAN_PLAY
        prefix = "پخش"
        body = body[len("پخش"):].lstrip()
    else:
        return None

    remainder, dedication = _parse_dedication(body)
    return ParsedPlaybackCommand(
        kind=kind,
        prefix=prefix,
        remainder=remainder,
        dedication=dedication,
    )


def _slash_play_body_after_prefix(normalized: str) -> str | None:
    """Return text after ``/play`` when it is a standalone command, else None."""
    lower = normalized.lower()
    if not lower.startswith("/play"):
        return None
    tail = normalized[5:]
    if tail and not tail[0].isspace() and tail[0] != "@":
        return None
    return tail.lstrip()


def extract_slash_play_remainder(message: Message) -> tuple[str, str | None]:
    """Return remainder and dedication from a native or text ``/play`` message."""
    if message.command and message.command[0].lower() == "play":
        remainder = " ".join(message.command[1:]).strip()
        return _parse_dedication(remainder)

    body = _slash_play_body_after_prefix(normalize_playback_text(message))
    if body is None:
        return "", None

    body = _SLASH_PLAY_BOT_SUFFIX_RE.sub("", body, count=1).lstrip()
    return _parse_dedication(body)


def parse_slash_play_command(message: Message) -> ParsedPlaybackCommand | None:
    """Build ``ParsedPlaybackCommand`` for ``/play`` using ``message.command`` first."""
    if message.command and message.command[0].lower() == "play":
        remainder, dedication = extract_slash_play_remainder(message)
        return ParsedPlaybackCommand(
            kind=PlaybackCommandKind.SLASH_PLAY,
            prefix="/play",
            remainder=remainder,
            dedication=dedication,
        )

    if _slash_play_body_after_prefix(normalize_playback_text(message)) is None:
        return None

    remainder, dedication = extract_slash_play_remainder(message)
    return ParsedPlaybackCommand(
        kind=PlaybackCommandKind.SLASH_PLAY,
        prefix="/play",
        remainder=remainder,
        dedication=dedication,
    )


def _document_extension(name: str | None) -> str:
    if not name:
        return ""
    dot = name.rfind(".")
    if dot < 0:
        return ""
    return name[dot:].lower()


def _infer_document_media_type(document: Any) -> Literal["audio", "video"] | None:
    mime = getattr(document, "mime_type", None) or ""
    mime_lower = mime.lower()
    if mime_lower.startswith("audio/"):
        return "audio"
    if mime_lower.startswith("video/"):
        return "video"

    ext = _document_extension(getattr(document, "file_name", None))
    if ext in _AUDIO_EXTENSIONS:
        return "audio"
    if ext in _VIDEO_EXTENSIONS:
        return "video"
    return None


def infer_reply_media(reply: Message | None) -> ReplyMediaResolution | None:
    """Infer playable reply media without downloading."""
    if reply is None:
        return None

    audio = getattr(reply, "audio", None)
    if audio:
        return ReplyMediaResolution("audio", audio, "telegram")
    voice = getattr(reply, "voice", None)
    if voice:
        return ReplyMediaResolution("audio", voice, "telegram")
    video = getattr(reply, "video", None)
    if video:
        return ReplyMediaResolution("video", video, "telegram")
    video_note = getattr(reply, "video_note", None)
    if video_note:
        return ReplyMediaResolution("video", video_note, "telegram")
    animation = getattr(reply, "animation", None)
    if animation:
        return ReplyMediaResolution("video", animation, "telegram")

    document = getattr(reply, "document", None)
    if document:
        doc_type = _infer_document_media_type(document)
        if doc_type:
            return ReplyMediaResolution(doc_type, document, "telegram")

    text_content = (getattr(reply, "text", None) or getattr(reply, "caption", None) or "").strip()
    if text_content and is_http_url(text_content):
        return ReplyMediaResolution("audio", text_content, "url")

    return None


def reply_media_kind_label(reply: Message | None) -> str | None:
    """Return a short label for diagnostic logs."""
    resolution = infer_reply_media(reply)
    if resolution is None:
        return None
    if resolution.source_kind == "url":
        return "url"
    if reply is None:
        return None
    if getattr(reply, "audio", None):
        return "audio"
    if getattr(reply, "voice", None):
        return "voice"
    if getattr(reply, "video", None):
        return "video"
    if getattr(reply, "video_note", None):
        return "video_note"
    if getattr(reply, "animation", None):
        return "animation"
    if getattr(reply, "document", None):
        return "document"
    media = resolution.media_object
    return type(media).__name__ if media is not None else None


def mask_url_for_log(url: str | None) -> str:
    """Mask long URLs for safe logs."""
    if not url:
        return "<empty>"
    text = str(url).strip()
    if len(text) <= 48:
        return text
    return f"{text[:32]}...{text[-8:]}"


def log_playback_command(
    phase: str,
    *,
    chat_id: int | None = None,
    user_id: int | None = None,
    has_reply: bool | None = None,
    reply_media: str | None = None,
    command: str | None = None,
    reason: str | None = None,
    media_type: str | None = None,
    level: str = "info",
) -> None:
    """Emit structured playback command diagnostics without secrets."""
    parts = [phase]
    if chat_id is not None:
        parts.append(f"chat_id={chat_id}")
    if user_id is not None:
        parts.append(f"user_id={user_id}")
    if has_reply is not None:
        parts.append(f"has_reply={str(has_reply).lower()}")
    if reply_media:
        parts.append(f"reply_media={reply_media}")
    if command:
        parts.append(f"command={command}")
    if reason:
        parts.append(f"reason={reason}")
    if media_type:
        parts.append(f"media_type={media_type}")
    message = " ".join(parts)
    if level == "debug":
        logger.debug(message)
    elif level == "warning":
        logger.warning(message)
    else:
        logger.info(message)


def playback_command_filter():
    """Pyrogram filter for normalized پخش/play commands (not /play)."""

    async def func(_flt, _client, message: Message) -> bool:
        normalized = normalize_playback_text(message)
        if normalized.lower().startswith("/play"):
            return False
        parsed = parse_playback_command(normalized)
        return parsed is not None and parsed.kind != PlaybackCommandKind.SLASH_PLAY

    return filters.create(func, name="PlaybackCommandFilter")


def slash_play_command_filter():
    """Pyrogram filter for /play slash commands."""

    async def func(_flt, _client, message: Message) -> bool:
        normalized = normalize_playback_text(message)
        return normalized.lower().startswith("/play")

    return filters.create(func, name="SlashPlayCommandFilter")

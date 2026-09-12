"""Centralized now-playing message text renderer (Phase B5-1 / B5-3)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import parse_qs, urlparse

from app.services.language_service import resolve_lang
from app.utils.i18n import normalize_lang, t

_UNSAFE_URL = re.compile(r"https?://", re.IGNORECASE)
_UNSAFE_PATH = re.compile(
    r"[/\\].*\.(?:ogg|opus|mp3|mp4|m4a|wav|webm|mkv|flac)\b",
    re.IGNORECASE,
)
_LIKELY_FILE_ID = re.compile(r"^[A-Za-z0-9_-]{24,}$")
_YOUTUBE_VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
_SHORT_TRACK_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

_MEDIA_TYPE_KEYS = {
    "audio": "audio",
    "video": "video",
    "radio": "radio",
    "tv": "tv",
    "satellite": "satellite",
}
_STREAM_MEDIA_TYPES = frozenset({"radio", "tv", "satellite"})


@dataclass(frozen=True)
class NowPlayingDisplayFlags:
    """Per-chat now-playing metadata display toggles (B5-2 columns)."""

    show_track_id: bool = False
    show_cover: bool = True
    show_now_playing_text: bool = True


@dataclass(frozen=True)
class NowPlayingContext:
    """Safe display fields for a now-playing confirmation message.

    Attributes:
        title: Human-readable track or channel title.
        media_type: Playback kind (audio, video, radio, tv, satellite).
        duration: Track length in seconds when known.
        requester_name: Display name or @username for the requester.
        requester_id: Stored for callers; never shown in output.
        track_id: Safe media/track identifier when available.
    """

    title: str | None
    media_type: str | None = None
    duration: int | None = None
    requester_name: str | None = None
    requester_id: int | None = None
    track_id: str | None = None


def requester_display_name(user: object | None) -> str | None:
    """Return a safe requester label (username or name), never a numeric id."""
    if user is None:
        return None
    username = getattr(user, "username", None)
    if username:
        return f"@{username}"
    first = (getattr(user, "first_name", None) or "").strip()
    last = (getattr(user, "last_name", None) or "").strip()
    full = f"{first} {last}".strip()
    return full or None


def title_from_telegram_reply(reply: object | None) -> str | None:
    """Extract a safe title from a replied Telegram media message."""
    if reply is None:
        return None
    audio = getattr(reply, "audio", None)
    if audio is not None:
        raw = getattr(audio, "title", None) or getattr(audio, "file_name", None)
        return _safe_text(raw)
    video = getattr(reply, "video", None)
    if video is not None:
        raw = getattr(video, "file_name", None)
        return _safe_text(raw)
    document = getattr(reply, "document", None)
    if document is not None:
        mime = getattr(document, "mime_type", None) or ""
        if mime.startswith("audio/") or mime.startswith("video/"):
            return _safe_text(getattr(document, "file_name", None))
    return None


def extract_youtube_video_id(url: str | None) -> str | None:
    """Return a safe YouTube video id from a URL, or None."""
    if url is None:
        return None
    text = str(url).strip()
    if not text or not _UNSAFE_URL.search(text):
        return None
    try:
        parsed = urlparse(text)
    except Exception:
        return None
    host = (parsed.netloc or "").lower()
    if host.endswith("youtu.be"):
        candidate = (parsed.path or "").strip("/").split("/")[0]
        return safe_track_id(candidate)
    if "youtube.com" in host or "youtube-nocookie.com" in host:
        query_id = parse_qs(parsed.query).get("v", [None])[0]
        if query_id:
            return safe_track_id(query_id)
        parts = [p for p in (parsed.path or "").split("/") if p]
        if parts and parts[0] in {"shorts", "embed", "live"} and len(parts) > 1:
            return safe_track_id(parts[1])
    return None


def safe_track_id(value: object | None) -> str | None:
    """Return a safe track/media id for display, or None when unsafe."""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if _UNSAFE_URL.search(text) or _UNSAFE_PATH.search(text):
        return None
    if _LIKELY_FILE_ID.fullmatch(text):
        return None
    if _YOUTUBE_VIDEO_ID.fullmatch(text):
        return text
    if _SHORT_TRACK_ID.fullmatch(text) and len(text) <= 64:
        return text
    return None


def _safe_text(value: object | None) -> str | None:
    """Return sanitized user-facing text or None when unsafe."""
    if value is None:
        return None
    text = str(value).strip()
    if not text or not _is_safe_display_text(text):
        return None
    return text


def _is_safe_display_text(text: str) -> bool:
    """Reject URLs, local paths, and Telegram-like file identifiers."""
    if len(text) > 200:
        return False
    if _UNSAFE_URL.search(text):
        return False
    if _UNSAFE_PATH.search(text):
        return False
    if _LIKELY_FILE_ID.fullmatch(text):
        return False
    return True


def _format_duration(lang: str, duration_sec: int) -> str:
    """Format seconds as mm:ss for display."""
    total = max(0, int(duration_sec))
    minutes, seconds = divmod(total, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{seconds:02d}"
    return f"{minutes}:{seconds:02d}"


def _header_key(media_type: str | None) -> str:
    if media_type == "video":
        return "now_playing.header_video"
    if media_type == "audio":
        return "now_playing.header_audio"
    return "now_playing.header"


def _media_type_label(lang: str, media_type: str | None) -> str | None:
    key = _MEDIA_TYPE_KEYS.get((media_type or "").strip().lower())
    if key is None:
        return None
    return t(lang, f"now_playing.{key}")


def display_flags_from_settings(settings: object | None) -> NowPlayingDisplayFlags:
    """Build display flags from a ChatSettings row or defaults."""
    if settings is None:
        return NowPlayingDisplayFlags()
    return NowPlayingDisplayFlags(
        show_track_id=bool(getattr(settings, "show_track_id", False)),
        show_cover=bool(getattr(settings, "show_cover", True)),
        show_now_playing_text=bool(getattr(settings, "show_now_playing_text", True)),
    )


async def _resolve_lang_and_flags(
    chat_id: int,
    *,
    user_id: int | None,
    lang: str | None,
) -> tuple[str, NowPlayingDisplayFlags]:
    """Resolve language and metadata flags with one settings fetch when possible."""
    from app.repositories import settings_repo

    cs = await settings_repo.get_chat_settings(chat_id)
    flags = display_flags_from_settings(cs)

    if lang is not None:
        return normalize_lang(lang), flags
    if cs is not None and getattr(cs, "language", None):
        return normalize_lang(str(cs.language)), flags
    return await resolve_lang(chat_id=chat_id, user_id=user_id), flags


def _append_track_id_line(
    lines: list[str],
    *,
    lang: str,
    flags: NowPlayingDisplayFlags,
    context: NowPlayingContext,
) -> None:
    if not flags.show_track_id:
        return
    track_id = safe_track_id(context.track_id)
    if track_id:
        lines.append(t(lang, "now_playing.track_id", track_id=track_id))


async def render_now_playing_text(
    chat_id: int,
    context: NowPlayingContext,
    *,
    lang: str | None = None,
    dedication: str | None = None,
    user_id: int | None = None,
    flags: NowPlayingDisplayFlags | None = None,
) -> str:
    """Build localized now-playing confirmation text for a group chat.

    Respects per-chat metadata flags. Never embeds URLs, local paths,
    Telegram file ids, or requester ids. ``show_cover`` is reserved for B5-4.
    """
    if flags is None:
        resolved, flags = await _resolve_lang_and_flags(
            chat_id, user_id=user_id, lang=lang
        )
    else:
        resolved = normalize_lang(
            lang if lang is not None else await resolve_lang(chat_id=chat_id, user_id=user_id)
        )

    title = _safe_text(context.title)
    lines: list[str] = []

    if not flags.show_now_playing_text:
        lines.append(t(resolved, "now_playing.minimal_title"))
        if title:
            lines.append(t(resolved, "now_playing.title_line", title=title))
        _append_track_id_line(lines, lang=resolved, flags=flags, context=context)
        if dedication:
            lines.append(
                t(resolved, "playback_cmd.dedicated_to", target=dedication)
            )
        return "\n".join(lines)

    lines.append(t(resolved, _header_key(context.media_type)))

    if title:
        lines.append(t(resolved, "now_playing.title_line", title=title))

    media_kind = (context.media_type or "").strip().lower()
    type_label = _media_type_label(resolved, context.media_type)
    show_type_line = bool(
        type_label and (title or media_kind in _STREAM_MEDIA_TYPES)
    )
    if show_type_line:
        lines.append(
            t(resolved, "now_playing.media_type_line", media_type=type_label)
        )

    if context.duration and int(context.duration) > 0:
        lines.append(
            t(
                resolved,
                "now_playing.duration_line",
                duration=_format_duration(resolved, int(context.duration)),
            )
        )

    requester = _safe_text(context.requester_name)
    if requester:
        lines.append(
            t(resolved, "now_playing.requester_line", requester=requester)
        )

    if not title and not show_type_line and not context.duration and not requester:
        lines.append(t(resolved, "now_playing.status_playing"))

    _append_track_id_line(lines, lang=resolved, flags=flags, context=context)

    if dedication:
        lines.append(
            t(resolved, "playback_cmd.dedicated_to", target=dedication)
        )

    return "\n".join(lines)

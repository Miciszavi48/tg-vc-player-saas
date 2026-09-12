from __future__ import annotations

import asyncio
import json
import logging
import re

from pyrogram import Client, filters
from pyrogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from app.services import CallService, MediaService
from app.services.youtube_session_service import (
    YoutubePlaylistUnsupported,
    YoutubeSessionsUnavailable,
)
from app.services.language_service import resolve_lang
from app.services.media_capability_service import build_now_playing_controls
from app.services.now_playing_renderer import (
    NowPlayingContext,
    render_now_playing_text,
    requester_display_name,
)
from app.services.playback_dispatch_service import (
    apply_busy_playback_decision,
    decide_busy_playback,
)
from app.utils.i18n import t
from app.utils.media_sources import validate_safe_url_with_redirects
from app.utils.playback_auth import authorize_playback_action
from app.utils.playback_errors import playback_failure_text
from app.utils.telegram_message import safe_edit_message
from app.utils.ui import CB

logger = logging.getLogger(__name__)

_LANG = "fa"
_MAX_RESULTS = 5
_SEARCH_PLAY_PREFIX = "search:play:"
_SEARCH_DOWNLOAD_PREFIX = "search:dl:"
# SEARCH-04: every documented trigger phrase, longest first so "سرچ یوتیوب"
# still wins over the bare "سرچ".
_SEARCH_TEXT_ALIASES = (
    "سرچ یوتیوب",
    "جستجو یوتیوب",
    "Search Youtube",
    "جستجو",
    "سرچ",
    "Search",
)
_SEARCH_TEXT_ALIAS_RE = re.compile(
    rf"^(?:{'|'.join(re.escape(alias) for alias in _SEARCH_TEXT_ALIASES)})(?:\s+(?P<query>.+))?$",
    re.IGNORECASE,
)
_YOUTUBE_VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
_PLAYBACK_RESULT_FALLBACK_SENT: set[tuple[int | None, int | None, str]] = set()
_PLAYBACK_RESULT_FALLBACK_LIMIT = 512


def _extract_search_alias_query(text: str | None) -> str | None:
    match = _SEARCH_TEXT_ALIAS_RE.fullmatch((text or "").strip())
    if match is None:
        return None
    return (match.group("query") or "").strip()


def _search_text_alias_filter():
    async def func(_flt, _client, message: Message) -> bool:
        return _extract_search_alias_query(message.text or message.caption) is not None

    return filters.create(func, name="SearchTextAliasFilter")


def _parse_search_video_id(data: str) -> str | None:
    if not data.startswith(_SEARCH_PLAY_PREFIX):
        return None
    video_id = data[len(_SEARCH_PLAY_PREFIX):].strip()
    if not _YOUTUBE_VIDEO_ID_RE.fullmatch(video_id):
        return None
    return video_id


def _parse_search_download_video_id(data: str) -> str | None:
    if not data.startswith(_SEARCH_DOWNLOAD_PREFIX):
        return None
    video_id = data[len(_SEARCH_DOWNLOAD_PREFIX):].strip()
    if not _YOUTUBE_VIDEO_ID_RE.fullmatch(video_id):
        return None
    return video_id


def _format_view_count(value: object) -> str:
    """Render a compact view count (SEARCH-01); '-' when unknown."""
    try:
        views = int(str(value or 0))
    except (TypeError, ValueError):
        return "-"
    if views <= 0:
        return "-"
    for limit, suffix in ((1_000_000_000, "B"), (1_000_000, "M"), (1_000, "K")):
        if views >= limit:
            scaled = views / limit
            rendered = f"{scaled:.1f}".rstrip("0").rstrip(".")
            return f"{rendered}{suffix}"
    return str(views)


def _youtube_watch_url(video_id: str) -> str:
    return f"https://youtube.com/watch?v={video_id}"


def _fallback_key(query: CallbackQuery) -> tuple[int | None, int | None, str]:
    message = getattr(query, "message", None)
    chat = getattr(message, "chat", None) if message is not None else None
    message_id = (
        getattr(message, "id", None)
        or getattr(message, "message_id", None)
        if message is not None
        else None
    )
    return (getattr(chat, "id", None), message_id, str(getattr(query, "data", "") or ""))


def _remember_fallback(key: tuple[int | None, int | None, str]) -> None:
    if len(_PLAYBACK_RESULT_FALLBACK_SENT) >= _PLAYBACK_RESULT_FALLBACK_LIMIT:
        _PLAYBACK_RESULT_FALLBACK_SENT.clear()
    _PLAYBACK_RESULT_FALLBACK_SENT.add(key)


async def _safe_edit_search_playback_result(
    client: Client,
    query: CallbackQuery,
    text: str,
    *,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> str:
    message = getattr(query, "message", None)
    if message is not None and await safe_edit_message(message, text, reply_markup=reply_markup):
        return "edited"

    key = _fallback_key(query)
    if key in _PLAYBACK_RESULT_FALLBACK_SENT:
        return "fallback_skipped"

    if message is not None:
        reply = getattr(message, "reply", None)
        if callable(reply):
            try:
                await reply(text, reply_markup=reply_markup)
                _remember_fallback(key)
                return "fallback_sent"
            except Exception:
                logger.debug("search playback fallback reply failed", exc_info=True)

    chat_id = key[0]
    send_message = getattr(client, "send_message", None) if client is not None else None
    if callable(send_message) and chat_id is not None:
        try:
            await send_message(chat_id, text, reply_markup=reply_markup)
            _remember_fallback(key)
            return "fallback_sent"
        except Exception:
            logger.debug("search playback fallback send failed", exc_info=True)

    return "failed"


async def _yt_search(query: str) -> list[dict[str, str]]:
    """Search YouTube via yt-dlp and return a list of results."""
    cmd = [
        "yt-dlp",
        f"ytsearch{_MAX_RESULTS}:{query}",
        "--dump-json",
        "--flat-playlist",
        "--no-download",
        "--no-warnings",
        "--quiet",
    ]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=30)
        results = []
        for line in stdout.decode().strip().split("\n"):
            if not line.strip():
                continue
            try:
                data = json.loads(line)
                results.append({
                    "id": data.get("id", ""),
                    "title": data.get("title") or t(_LANG, "common.labels.unknown"),
                    "duration": str(data.get("duration", 0)),
                    # SEARCH-01: yt-dlp exposes view_count on flat-playlist rows.
                    "view_count": str(data.get("view_count") or 0),
                    "url": data.get("url", data.get("webpage_url", f"https://youtube.com/watch?v={data.get('id', '')}")),
                })
            except json.JSONDecodeError:
                continue
        return results
    except Exception:
        logger.exception("YouTube search failed for: %s", query)
        return []


async def resolve_youtube_first_result(query: str) -> dict[str, str] | None:
    """Return the first valid YouTube result as a canonical watch URL."""
    if not query.strip():
        return None
    for result in await _yt_search(query.strip()):
        video_id = str(result.get("id", "")).strip()
        if not _YOUTUBE_VIDEO_ID_RE.fullmatch(video_id):
            continue
        return {
            "id": video_id,
            "title": str(result.get("title") or t(_LANG, "common.labels.unknown")),
            "duration": str(result.get("duration", 0)),
            "url": _youtube_watch_url(video_id),
        }
    return None


def register(bot: Client, call_py) -> None:

    async def _send_search_results(client: Client, message: Message, query: str) -> None:  # noqa: ARG001
        if not query:
            await message.reply(t(_LANG, "ask.search_query"))
            return

        status_msg = await message.reply(t(_LANG, "search.searching"))

        results = await _yt_search(query)
        if not results:
            await status_msg.edit_text(t(_LANG, "search.no_results"))
            return

        rows = []
        for r in results:
            video_id = str(r.get("id", "")).strip()
            if not _YOUTUBE_VIDEO_ID_RE.fullmatch(video_id):
                continue
            label = t(
                _LANG,
                "search.result_item",
                title=r["title"][:40],
                duration=r["duration"],
                views=_format_view_count(r.get("view_count")),
            )
            # SEARCH-01/04: play stays the primary action; a compact download
            # button gives each result its own download affordance.
            rows.append(
                [
                    InlineKeyboardButton(
                        label, callback_data=f"{_SEARCH_PLAY_PREFIX}{video_id}"
                    ),
                    InlineKeyboardButton(
                        t(_LANG, "search.result_download"),
                        callback_data=f"{_SEARCH_DOWNLOAD_PREFIX}{video_id}",
                    ),
                ]
            )
        if not rows:
            await status_msg.edit_text(t(_LANG, "search.no_results"))
            return
        rows.append([InlineKeyboardButton(t(_LANG, "common.buttons.close"), callback_data=CB["NAV_CLOSE"])])

        await status_msg.edit_text(
            t(_LANG, "search.results_title", query=query),
            reply_markup=InlineKeyboardMarkup(rows),
        )

    @bot.on_message(filters.command("search") & filters.group)
    async def search_command(client: Client, message: Message):
        parts = message.text.split(maxsplit=1) if message.text else []
        query = parts[1].strip() if len(parts) >= 2 else ""
        await _send_search_results(client, message, query)

    @bot.on_message(_search_text_alias_filter() & filters.group)
    async def search_text_alias(client: Client, message: Message):
        query = _extract_search_alias_query(message.text or message.caption) or ""
        await _send_search_results(client, message, query)

    @bot.on_callback_query(filters.regex(r"^search:dl:"))
    async def on_search_download(client: Client, query: CallbackQuery):  # noqa: ARG001
        """SEARCH-01/04: offer audio/video download for a search result.

        Reuses the existing `dl:fmt:` token flow, so download permissions and
        the format choice behave exactly as they do for the URL command.
        """
        from app.handlers.download import _download_allowed, _store_download_choice
        from app.utils.ui import KeyboardFactory

        video_id = _parse_search_download_video_id(str(getattr(query, "data", "") or ""))
        chat = getattr(getattr(query, "message", None), "chat", None)
        chat_id = getattr(chat, "id", None)
        user_id = getattr(getattr(query, "from_user", None), "id", None)
        if video_id is None or chat_id is None or user_id is None:
            await query.answer(t(_LANG, "common.errors.invalid_callback"), show_alert=True)
            return

        denial_key = await _download_allowed(
            client, chat_id=int(chat_id), user_id=int(user_id)
        )
        if denial_key:
            await query.answer(t(_LANG, denial_key), show_alert=True)
            return

        token = await _store_download_choice(
            user_id=int(user_id),
            chat_id=int(chat_id),
            url=_youtube_watch_url(video_id),
        )
        await query.answer()
        await query.message.reply(
            t(_LANG, "youtube_sessions.choose_format"),
            reply_markup=KeyboardFactory.youtube_download_format(_LANG, token),
        )

    @bot.on_callback_query(filters.regex(r"^search:play:"))
    async def on_search_select(client: Client, query: CallbackQuery):
        video_id = _parse_search_video_id(query.data)
        if video_id is None:
            await query.answer(t(_LANG, "common.errors.invalid_callback"), show_alert=True)
            return
        url = _youtube_watch_url(video_id)

        if not await authorize_playback_action(client, query, lang=_LANG):
            return

        await query.answer(t(_LANG, "search.downloading"), show_alert=False)

        url = await validate_safe_url_with_redirects(url)
        if url is None:
            await _safe_edit_search_playback_result(
                client,
                query,
                t(_LANG, "playback_cmd.blocked_url"),
            )
            return

        try:
            stream_url = await MediaService.get_stream_url(url)
        except YoutubeSessionsUnavailable:
            await _safe_edit_search_playback_result(
                client,
                query,
                t(_LANG, "youtube_sessions.unavailable"),
            )
            return
        except YoutubePlaylistUnsupported:
            await _safe_edit_search_playback_result(
                client,
                query,
                t(_LANG, "youtube_sessions.playlist_requires_video"),
            )
            return
        if not stream_url:
            await _safe_edit_search_playback_result(
                client,
                query,
                t(_LANG, "playback_cmd.failed"),
            )
            return

        chat_id = query.message.chat.id
        media_type = "audio"
        busy_decision = await decide_busy_playback(
            chat_id,
            stream_url,
            media_type,
            is_temp_local=False,
        )
        if await apply_busy_playback_decision(
            query,
            chat_id,
            stream_url,
            media_type,
            busy_decision,
            title=url,
            requester_id=query.from_user.id if query.from_user else None,
            lang=_LANG,
        ):
            if busy_decision.message_key:
                await _safe_edit_search_playback_result(
                    client,
                    query,
                    t(_LANG, busy_decision.message_key),
                )
            return

        ok = await CallService.join_voice_chat(
            call_py,
            chat_id,
            stream_url,
            media_type,
            user_id=query.from_user.id if query.from_user else None,
            event_source=url,
        )
        if ok:
            active = CallService.get_active_calls().get(chat_id) or {}
            display_media_type = str(
                active.get("playback_feature")
                or active.get("media_type")
                or media_type
            )
            lang = await resolve_lang(
                chat_id=chat_id,
                user_id=query.from_user.id if query.from_user else None,
            )
            text = await render_now_playing_text(
                chat_id,
                NowPlayingContext(
                    title=active.get("title"),
                    media_type=display_media_type,
                    requester_name=requester_display_name(query.from_user),
                    requester_id=query.from_user.id if query.from_user else None,
                    track_id=video_id,
                ),
                lang=lang,
                user_id=query.from_user.id if query.from_user else None,
            )
            await _safe_edit_search_playback_result(
                client,
                query,
                text,
                reply_markup=await build_now_playing_controls(lang, chat_id),
            )
        else:
            await _safe_edit_search_playback_result(
                client,
                query,
                playback_failure_text(_LANG),
            )

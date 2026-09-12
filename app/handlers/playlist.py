from __future__ import annotations

import logging
import re

from pyrogram import Client, filters
from pyrogram.types import Message

from app.config.settings import settings
from app.repositories import admin_repo, playlist_repo, user_repo
from app.utils.sudo_permissions import can_use_sudo_admin_bypass
from app.services import CallService
from app.services import group_runtime_state_service
from app.services.media_capability_service import (
    deny_free_mode_media,
    deny_video_playback,
    feature_from_media_type,
)
from app.services.playback_dispatch_service import is_chat_playing
from app.utils.cache import acquire_lock, release_lock
from app.utils.helpers import format_duration
from app.utils.i18n import t
from app.utils.playback_errors import playback_failure_text
from app.utils.media_sources import (
    download_trusted_telegram_media,
    is_http_url,
    normalize_media_source,
    validate_safe_url_with_redirects,
)

from app.services import named_playlist_service

logger = logging.getLogger(__name__)

_LANG = "fa"

_ADD_CMDS = ["افزودن به لیست", "addtoplaylist"]
_PLAY_LIST_CMDS = ["پخش لیست", "playlist"]
_STOP_LIST_CMDS = ["توقف لیست", "stoplist"]
_LIST_CMDS = ["لیست پخش", "listplaylist"]
_DEL_FROM_CMDS = ["حذف از لیست", "delfromplaylist"]
_CLEAN_CMDS = ["پاکسازی لیست پخش", "cleanplaylist"]
# TRANSPORT-09/10: named playlists. Registered before the shorter queue
# commands so "ساخت لیست"/"لیست ها" never fall through to the queue handlers.
_NP_CREATE_CMDS = ["ساخت لیست", "ایجاد لیست", "createplaylist", "Create Playlist"]
_NP_RENAME_CMDS = ["تغییر نام لیست", "renameplaylist", "Rename Playlist"]
_NP_DELETE_CMDS = ["حذف لیست", "deleteplaylist", "Delete Playlist"]
_NP_LIST_ALL_CMDS = ["لیست ها", "لیست‌ها", "allplaylists", "All Playlists"]
_NP_PAGE_PREFIX = "pl:page:"


def _command_argument(text: str, cmds: list[str]) -> str:
    """Return the free-text argument following whichever alias matched."""
    stripped = (text or "").strip()
    for cmd in sorted(cmds, key=len, reverse=True):
        if stripped.lower().startswith(cmd.lower()):
            return stripped[len(cmd):].strip()
    return ""


def _split_rename_argument(rest: str) -> tuple[str, str]:
    """Split ``<old> | <new>`` (or ``<old> => <new>``) for a rename."""
    for separator in ("|", "=>", "->"):
        if separator in rest:
            old, _, new = rest.partition(separator)
            return old.strip(), new.strip()
    return rest.strip(), ""


def _build_filter(cmds: list[str]):
    pattern = "|".join(re.escape(c) for c in cmds)
    return filters.regex(rf"^(?:{pattern})(?:\s|$)", flags=re.IGNORECASE)


async def _has_permission(client: Client, message: Message) -> bool:
    chat_id = message.chat.id
    user_id = message.from_user.id if message.from_user else 0
    runtime_state = await group_runtime_state_service.get_runtime_credit_state(chat_id, "group")
    if not runtime_state.has_runtime_credit:
        await message.reply(t(_LANG, "playback_cmd.no_credit"))
        return False
    from app.utils.bot_guards import is_developer

    if is_developer(user_id):
        return True
    if await can_use_sudo_admin_bypass(user_id):
        return True
    if await admin_repo.is_music_admin_or_above(user_id, chat_id):
        return True
    await message.reply(t(_LANG, "playback_cmd.no_permission"))
    return False


_NP_PER_PAGE = 10


async def _render_named_playlist_page(chat_id: int, page: int):
    """Build the (text, keyboard) pair for one page of named playlists."""
    from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup

    result = await named_playlist_service.list_page(
        chat_id, page=page, per_page=_NP_PER_PAGE
    )
    if not result.total:
        return t(_LANG, "named_playlist.none"), None

    total_pages = max(1, (result.total + _NP_PER_PAGE - 1) // _NP_PER_PAGE)
    page = max(0, min(page, total_pages - 1))
    lines = [t(_LANG, "named_playlist.list_title", total=result.total)]
    lines.extend(f"▪️ {row.name}" for row in result.rows)

    nav: list[InlineKeyboardButton] = []
    if page > 0:
        nav.append(
            InlineKeyboardButton(
                t(_LANG, "common.buttons.prev"), callback_data=f"{_NP_PAGE_PREFIX}{page - 1}"
            )
        )
    if page < total_pages - 1:
        nav.append(
            InlineKeyboardButton(
                t(_LANG, "common.buttons.next"), callback_data=f"{_NP_PAGE_PREFIX}{page + 1}"
            )
        )
    markup = InlineKeyboardMarkup([nav]) if nav else None
    return "\n".join(lines), markup


_NP_ERROR_KEYS = {
    "missing_name": "named_playlist.missing_name",
    "name_too_long": "named_playlist.name_too_long",
    "already_exists": "named_playlist.already_exists",
    "too_many_playlists": "named_playlist.too_many",
    "not_found": "named_playlist.not_found",
    "playlist_full": "named_playlist.full",
    "playlist_empty": "named_playlist.empty",
    "missing_source": "named_playlist.missing_source",
}


def register(bot: Client, call_py) -> None:

    # ── Add to playlist ───────────────────────────────────────────────────
    @bot.on_message(_build_filter(_NP_CREATE_CMDS) & filters.group)
    async def np_create(client: Client, message: Message):
        """TRANSPORT-09: create a named playlist."""
        if not await _has_permission(client, message):
            return
        name = _command_argument(message.text or "", _NP_CREATE_CMDS)
        result = await named_playlist_service.create(
            int(message.chat.id),
            name,
            created_by=message.from_user.id if message.from_user else None,
        )
        if not result.ok:
            await message.reply(t(_LANG, _NP_ERROR_KEYS.get(result.reason, "named_playlist.failed")))
            return
        await message.reply(t(_LANG, "named_playlist.created", name=result.name))

    @bot.on_message(_build_filter(_NP_RENAME_CMDS) & filters.group)
    async def np_rename(client: Client, message: Message):
        """TRANSPORT-09: rename a named playlist (`<old> | <new>`)."""
        if not await _has_permission(client, message):
            return
        old, new = _split_rename_argument(
            _command_argument(message.text or "", _NP_RENAME_CMDS)
        )
        if not old or not new:
            await message.reply(t(_LANG, "named_playlist.rename_usage"))
            return
        result = await named_playlist_service.rename(int(message.chat.id), old, new)
        if not result.ok:
            await message.reply(t(_LANG, _NP_ERROR_KEYS.get(result.reason, "named_playlist.failed")))
            return
        await message.reply(t(_LANG, "named_playlist.renamed", name=result.name))

    @bot.on_message(_build_filter(_NP_DELETE_CMDS) & filters.group)
    async def np_delete(client: Client, message: Message):
        """TRANSPORT-09: delete a named playlist and its items."""
        if not await _has_permission(client, message):
            return
        name = _command_argument(message.text or "", _NP_DELETE_CMDS)
        result = await named_playlist_service.delete(int(message.chat.id), name)
        if not result.ok:
            await message.reply(t(_LANG, _NP_ERROR_KEYS.get(result.reason, "named_playlist.failed")))
            return
        await message.reply(t(_LANG, "named_playlist.deleted", name=result.name))

    @bot.on_message(_build_filter(_NP_LIST_ALL_CMDS) & filters.group)
    async def np_list_all(client: Client, message: Message):
        """TRANSPORT-10: paginated list of every named playlist in the chat."""
        if not await _has_permission(client, message):
            return
        await message.reply(
            *await _render_named_playlist_page(int(message.chat.id), 0)
        )

    @bot.on_callback_query(filters.regex(rf"^{_NP_PAGE_PREFIX}\d{{1,4}}$"))
    async def np_list_page(client: Client, query):
        """TRANSPORT-10: prev/next navigation over the playlist list."""
        from app.utils.callback_trace import safe_answer_callback

        chat = getattr(getattr(query, "message", None), "chat", None)
        chat_id = getattr(chat, "id", None)
        if chat_id is None:
            await safe_answer_callback(
                query, t(_LANG, "common.errors.unknown_callback"), show_alert=True
            )
            return
        page = int(str(query.data)[len(_NP_PAGE_PREFIX):])
        text, markup = await _render_named_playlist_page(int(chat_id), page)
        await safe_answer_callback(query)
        edit = getattr(query, "edit_message_text", None)
        if callable(edit):
            await edit(text, reply_markup=markup)

    @bot.on_message(_build_filter(_ADD_CMDS) & filters.group)
    async def add_to_playlist(client: Client, message: Message):
        if not await _has_permission(client, message):
            return

        from app.utils.redis_keys import playlist_lock_key
        lock_key = playlist_lock_key(message.chat.id)
        token = await acquire_lock(lock_key, ttl_ms=15_000)
        if token is None:
            await message.reply(t(_LANG, "common.errors.try_later"))
            return
        try:
            source = None
            title = t(_LANG, "common.labels.unknown")
            parts = message.text.split(maxsplit=1) if message.text else []
            if len(parts) > 1:
                source = parts[1].strip()
                title = source[:60]
            elif message.reply_to_message:
                reply = message.reply_to_message
                if reply.audio:
                    source = await download_trusted_telegram_media(client, reply.audio, message.chat.id)
                    title = getattr(reply.audio, "title", None) or t(_LANG, "common.labels.audio_title")
                elif reply.voice:
                    source = await download_trusted_telegram_media(client, reply.voice, message.chat.id)
                    title = t(_LANG, "common.labels.voice_title")
                elif reply.text:
                    source = reply.text.strip()
                    title = source[:60]

            if not source:
                await message.reply(t(_LANG, "playlist_cmd.provide_source"))
                return

            if is_http_url(source):
                source = await validate_safe_url_with_redirects(source)
                if source is None:
                    await message.reply(t(_LANG, "playback_cmd.blocked_url"))
                    return

            source = normalize_media_source(source)
            if source is None:
                await message.reply(t(_LANG, "playback_cmd.invalid_source"))
                return

            # TRANSPORT-11: "reply to media + افزودن به لیست <name>" targets a
            # NAMED playlist. Without a reply the trailing text is the media
            # source itself, which is the pre-existing queue behaviour and is
            # left untouched.
            target_name = (
                _command_argument(message.text or "", _ADD_CMDS)
                if message.reply_to_message
                else ""
            )
            if target_name:
                result = await named_playlist_service.add_media(
                    int(message.chat.id),
                    target_name,
                    source=source,
                    title=title,
                    media_type="audio",
                    added_by=message.from_user.id if message.from_user else None,
                )
                if not result.ok:
                    await message.reply(
                        t(_LANG, _NP_ERROR_KEYS.get(result.reason, "named_playlist.failed"))
                    )
                    return
                await message.reply(t(_LANG, "named_playlist.added", name=result.name))
                return

            await playlist_repo.add_to_queue(
                chat_id=message.chat.id,
                stream_url=source if is_http_url(source) else None,
                file_path=source if not is_http_url(source) else None,
                title=title,
                media_type="audio",
                added_by=message.from_user.id if message.from_user else None,
            )
            await message.reply(t(_LANG, "playlist_cmd.added_to_list", title=title))
        finally:
            await release_lock(lock_key, token)

    # ── Play playlist ─────────────────────────────────────────────────────
    @bot.on_message(_build_filter(_PLAY_LIST_CMDS) & filters.group)
    async def play_playlist(client: Client, message: Message):
        if not await _has_permission(client, message):
            return

        from app.utils.redis_keys import chat_lock_key
        lock_key = chat_lock_key(message.chat.id)
        token = await acquire_lock(lock_key)
        if token is None:
            await message.reply(t(_LANG, "common.errors.try_later"))
            return
        try:
            current = await playlist_repo.get_current(message.chat.id)
            if current is None:
                await message.reply(t(_LANG, "playback.queue.empty"))
                return

            source = current.stream_url or current.file_path
            if not source:
                await message.reply(t(_LANG, "playback_cmd.failed"))
                return
            if is_http_url(source):
                source = await validate_safe_url_with_redirects(source)
                if source is None:
                    await message.reply(t(_LANG, "playback_cmd.blocked_url"))
                    return

            source = normalize_media_source(source)
            if source is None:
                await message.reply(t(_LANG, "playback_cmd.invalid_source"))
                return

            queue_media_type = current.media_type or "audio"
            if queue_media_type == "video":
                if await deny_video_playback(message, lang=_LANG):
                    return
            elif await deny_free_mode_media(
                message,
                feature_from_media_type(queue_media_type),
                lang=_LANG,
            ):
                return

            if is_chat_playing(message.chat.id):
                await message.reply(t(_LANG, "playback_cmd.already_playing"))
                return

            ok = await CallService.join_voice_chat(
                call_py,
                message.chat.id,
                source,
                queue_media_type,
                user_id=message.from_user.id if message.from_user else None,
                event_source=current.stream_url or current.file_path,
                title=current.title,
            )
            if ok:
                await message.reply(t(_LANG, "playlist_cmd.playing_list"))
            else:
                await message.reply(playback_failure_text(_LANG))
        finally:
            await release_lock(lock_key, token)

    # ── Stop playlist ─────────────────────────────────────────────────────
    @bot.on_message(_build_filter(_STOP_LIST_CMDS) & filters.group)
    async def stop_playlist(client: Client, message: Message):
        if not await _has_permission(client, message):
            return
        await CallService.leave_voice_chat(call_py, message.chat.id)
        await message.reply(t(_LANG, "playlist_cmd.stopped_list"))

    # ── List playlist ─────────────────────────────────────────────────────
    @bot.on_message(_build_filter(_LIST_CMDS) & filters.group)
    async def list_playlist(client: Client, message: Message):
        queue = await playlist_repo.get_queue(message.chat.id)
        if not queue:
            await message.reply(t(_LANG, "playback.queue.empty"))
            return

        lines = [t(_LANG, "playlist_cmd.list_title")]
        for item in queue[:20]:
            dur = format_duration(item.duration_seconds)
            lines.append(
                t(
                    _LANG,
                    "playlist_cmd.list_item",
                    pos=item.position + 1,
                    title=item.title or "?",
                    duration=dur,
                )
            )
        await message.reply("\n".join(lines))

    # ── Delete from playlist ──────────────────────────────────────────────
    @bot.on_message(_build_filter(_DEL_FROM_CMDS) & filters.group)
    async def del_from_playlist(client: Client, message: Message):
        if not await _has_permission(client, message):
            return

        parts = message.text.split() if message.text else []
        pos = None
        for p in parts:
            try:
                pos = int(p) - 1
                break
            except ValueError:
                continue

        if pos is None:
            await message.reply(t(_LANG, "playlist_cmd.provide_position"))
            return

        from app.utils.redis_keys import chat_lock_key
        lock_key = chat_lock_key(message.chat.id)
        token = await acquire_lock(lock_key)
        if token is None:
            await message.reply(t(_LANG, "common.errors.try_later"))
            return
        try:
            await playlist_repo.remove_from_queue(message.chat.id, pos)
            await message.reply(t(_LANG, "playlist_cmd.removed_from_list"))
        finally:
            await release_lock(lock_key, token)

    # ── Clean playlist ────────────────────────────────────────────────────
    @bot.on_message(_build_filter(_CLEAN_CMDS) & filters.group)
    async def clean_playlist(client: Client, message: Message):
        if not await _has_permission(client, message):
            return

        from app.utils.redis_keys import chat_lock_key
        lock_key = chat_lock_key(message.chat.id)
        token = await acquire_lock(lock_key)
        if token is None:
            await message.reply(t(_LANG, "common.errors.try_later"))
            return
        try:
            await playlist_repo.clear_queue(message.chat.id)
            await message.reply(t(_LANG, "playlist_cmd.cleaned"))
        finally:
            await release_lock(lock_key, token)

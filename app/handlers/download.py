from __future__ import annotations

import logging
import json
import re

from pyrogram import Client, filters
from pyrogram.types import CallbackQuery, Message

from app.services import MediaService
from app.services.download_policy_service import get_download_denial_key
from app.services.media_event_service import track_media_download
from app.services.youtube_session_service import (
    YoutubePlaylistUnsupported,
    YoutubeSessionsUnavailable,
    is_playlist_without_video,
    is_youtube_url,
    new_download_choice_token,
)
from app.utils.cache import get_redis
from app.utils.redis_keys import TTL_DOWNLOAD_CHOICE, download_choice_state_key
from app.handlers.priority import PANEL_CALLBACK_GROUP
from app.utils.i18n import t
from app.utils.media_sources import (
    is_soundcloud_track_url,
    is_soundcloud_url,
    validate_safe_url_with_redirects,
)
from app.utils.ui import CB, KeyboardFactory

logger = logging.getLogger(__name__)

_LANG = "fa"

_DOWNLOAD_CMDS = ["دانلود", "download"]
# SEARCH-06/07: name-based download. These must be registered BEFORE the generic
# URL command, whose filter (`^دانلود(\s|$)`) would otherwise swallow them.
_DOWNLOAD_MUSIC_CMDS = ["دانلود موزیک", "دانلود آهنگ", "Download Music"]
_DOWNLOAD_VIDEO_CMDS = ["دانلود ویدیو", "دانلود ویدئو", "Download Video"]


def _build_filter(cmds: list[str]):
    pattern = "|".join(re.escape(c) for c in cmds)
    return filters.regex(rf"^(?:{pattern})(?:\s|$)", flags=re.IGNORECASE)


async def _download_allowed(client: Client, *, chat_id: int, user_id: int) -> str | None:
    """Return a translated denial key, or None when the action is still allowed."""
    del client
    return await get_download_denial_key(chat_id=chat_id, user_id=user_id)


async def _store_download_choice(*, user_id: int, chat_id: int, url: str) -> str:
    token = new_download_choice_token()
    r = await get_redis()
    await r.set(
        download_choice_state_key(user_id, token),
        json.dumps({"v": 1, "chat_id": chat_id, "url": url}),
        ex=TTL_DOWNLOAD_CHOICE,
    )
    return token


async def _claim_download_choice(query: CallbackQuery, token: str) -> dict | None:
    if query.from_user is None or query.message is None:
        return None
    r = await get_redis()
    raw = await r.getdel(download_choice_state_key(query.from_user.id, token))
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return None
    if (
        data.get("v") != 1
        or data.get("chat_id") != query.message.chat.id
        or not isinstance(data.get("url"), str)
    ):
        return None
    return data


def _strip_command_prefix(text: str, cmds: list[str]) -> str:
    """Return the free-text argument after whichever alias matched."""
    stripped = (text or "").strip()
    for cmd in sorted(cmds, key=len, reverse=True):
        if stripped.lower().startswith(cmd.lower()):
            return stripped[len(cmd):].strip()
    return ""


async def _download_by_name(
    client: Client, message: Message, *, query_text: str, media_type: str,
) -> None:
    """SEARCH-06/07: resolve a free-text name to a YouTube result and deliver it.

    Format is implied by the command, so this skips the audio/video chooser that
    the URL command shows. Permissions and size caps are the shared ones.
    """
    from app.handlers.search import resolve_youtube_first_result

    chat_id = message.chat.id
    user_id = message.from_user.id if message.from_user else 0

    denial_key = await _download_allowed(client, chat_id=chat_id, user_id=user_id)
    if denial_key is not None:
        await message.reply(t(_LANG, denial_key))
        return

    query_text = (query_text or "").strip()
    if not query_text:
        await message.reply(t(_LANG, "download_cmd.name_required"))
        return

    status = await message.reply(t(_LANG, "download_cmd.searching"))
    result = await resolve_youtube_first_result(query_text)
    if result is None:
        await status.edit_text(t(_LANG, "search.no_results"))
        return

    url = result["url"]
    await status.edit_text(t(_LANG, "youtube_sessions.download_started"))
    try:
        path = (
            await MediaService.download_audio(url, chat_id)
            if media_type == "audio"
            else await MediaService.download_video(url, chat_id)
        )
    except YoutubeSessionsUnavailable:
        await message.reply(t(_LANG, "youtube_sessions.unavailable"))
        return
    except YoutubePlaylistUnsupported:
        await message.reply(t(_LANG, "youtube_sessions.playlist_requires_video"))
        return

    if not path:
        await message.reply(t(_LANG, "download_cmd.failed"))
        return
    try:
        if media_type == "audio":
            await message.reply_document(path)
        else:
            await message.reply_video(path, supports_streaming=True)
        await track_media_download(
            url, media_type=media_type, chat_id=chat_id, user_id=user_id,
        )
        await message.reply(t(_LANG, "download_cmd.completed"))
    except Exception:
        await message.reply(t(_LANG, "download_cmd.failed"))


def register(bot: Client, call_py) -> None:  # noqa: ARG001

    @bot.on_message(_build_filter(_DOWNLOAD_MUSIC_CMDS) & filters.group)
    async def download_music_by_name(client: Client, message: Message):
        """SEARCH-06: 'دانلود موزیک <name>' — name-based audio download."""
        text = message.text or message.caption or ""
        await _download_by_name(
            client,
            message,
            query_text=_strip_command_prefix(text, _DOWNLOAD_MUSIC_CMDS),
            media_type="audio",
        )

    @bot.on_message(_build_filter(_DOWNLOAD_VIDEO_CMDS) & filters.group)
    async def download_video_by_name(client: Client, message: Message):
        """SEARCH-07: 'دانلود ویدیو <name>' — name-based video download."""
        text = message.text or message.caption or ""
        await _download_by_name(
            client,
            message,
            query_text=_strip_command_prefix(text, _DOWNLOAD_VIDEO_CMDS),
            media_type="video",
        )

    @bot.on_message(_build_filter(_DOWNLOAD_CMDS) & filters.group)
    async def download_media(client: Client, message: Message):
        chat_id = message.chat.id
        user_id = message.from_user.id if message.from_user else 0

        denial_key = await _download_allowed(client, chat_id=chat_id, user_id=user_id)
        if denial_key is not None:
            await message.reply(t(_LANG, denial_key))
            return

        reply = message.reply_to_message
        if reply is None:
            parts = message.text.split(maxsplit=1) if message.text else []
            if len(parts) < 2:
                await message.reply(t(_LANG, "download_cmd.no_reply"))
                return
            url = parts[1].strip()
        else:
            if reply.audio:
                path = await client.download_media(reply.audio)
                if path:
                    await message.reply_document(path)
                    await track_media_download(
                        None,
                        media_type="audio",
                        chat_id=chat_id,
                        user_id=user_id,
                        telegram_file_unique_id=reply.audio.file_unique_id,
                    )
                    await message.reply(t(_LANG, "download_cmd.completed"))
                else:
                    await message.reply(t(_LANG, "download_cmd.failed"))
                return
            elif reply.video:
                path = await client.download_media(reply.video)
                if path:
                    await message.reply_document(path)
                    await track_media_download(
                        None,
                        media_type="video",
                        chat_id=chat_id,
                        user_id=user_id,
                        telegram_file_unique_id=reply.video.file_unique_id,
                    )
                    await message.reply(t(_LANG, "download_cmd.completed"))
                else:
                    await message.reply(t(_LANG, "download_cmd.failed"))
                return
            elif reply.text:
                url = reply.text.strip()
            else:
                await message.reply(t(_LANG, "download_cmd.no_reply"))
                return

        url = await validate_safe_url_with_redirects(url)
        if url is None:
            await message.reply(t(_LANG, "download_cmd.blocked_url"))
            return

        if is_playlist_without_video(url):
            await message.reply(t(_LANG, "youtube_sessions.playlist_requires_video"))
            return

        if is_soundcloud_url(url) and not is_soundcloud_track_url(url):
            await message.reply(t(_LANG, "download_cmd.soundcloud_track_only"))
            return

        # SEARCH-08: TikTok/Instagram (and Spotify) links are resolved by the
        # fast-creat integration, which already delivers them as files. Reusing
        # that path here means the download command accepts the same link set
        # the play command does, instead of rejecting them as unsafe URLs.
        from app.handlers.playback import _try_fast_creat_group_download

        if await _try_fast_creat_group_download(message, url):
            return

        if is_youtube_url(url):
            token = await _store_download_choice(user_id=user_id, chat_id=chat_id, url=url)
            await message.reply(
                t(_LANG, "youtube_sessions.choose_format"),
                reply_markup=KeyboardFactory.youtube_download_format(_LANG, token),
            )
            return

        status_key = (
            "download_cmd.soundcloud_downloading"
            if is_soundcloud_track_url(url)
            else "download_cmd.downloading"
        )
        status_msg = await message.reply(t(_LANG, status_key))

        path = await MediaService.download_audio(url, chat_id)
        if path:
            try:
                await message.reply_document(path)
                await status_msg.edit_text(t(_LANG, "download_cmd.completed"))
                await track_media_download(
                    url,
                    media_type="audio",
                    chat_id=chat_id,
                    user_id=user_id,
                )
            except Exception:
                await status_msg.edit_text(t(_LANG, "download_cmd.failed"))
        else:
            await status_msg.edit_text(t(_LANG, "download_cmd.failed"))

    @bot.on_callback_query(
        filters.regex(r"^dl:fmt:[avx]:[A-Za-z0-9_-]{4,32}$"),
        group=PANEL_CALLBACK_GROUP,
    )
    async def youtube_download_format_choice(client: Client, query: CallbackQuery):
        if query.from_user is None or query.message is None:
            await query.answer(t(_LANG, "common.errors.invalid_callback"), show_alert=True)
            return
        parts = query.data.removeprefix(CB["DOWNLOAD_FORMAT_PREFIX"]).split(":", 1)
        if len(parts) != 2:
            await query.answer(t(_LANG, "common.errors.invalid_callback"), show_alert=True)
            return
        choice, token = parts
        state = await _claim_download_choice(query, token)
        if state is None:
            await query.answer(t(_LANG, "youtube_sessions.download_expired"), show_alert=True)
            return
        if choice == "x":
            await query.answer(t(_LANG, "youtube_sessions.download_cancelled"))
            await query.message.edit_text(t(_LANG, "youtube_sessions.download_cancelled"))
            return

        denial_key = await _download_allowed(
            client,
            chat_id=query.message.chat.id,
            user_id=query.from_user.id,
        )
        if denial_key is not None:
            await query.answer(t(_LANG, denial_key), show_alert=True)
            return

        url = state["url"]
        await query.answer(t(_LANG, "youtube_sessions.download_started"))
        await query.message.edit_text(t(_LANG, "youtube_sessions.download_started"))
        try:
            path = (
                await MediaService.download_audio(url, query.message.chat.id)
                if choice == "a"
                else await MediaService.download_video(url, query.message.chat.id)
            )
        except YoutubeSessionsUnavailable:
            await query.message.reply(t(_LANG, "youtube_sessions.unavailable"))
            return
        except YoutubePlaylistUnsupported:
            await query.message.reply(t(_LANG, "youtube_sessions.playlist_requires_video"))
            return

        if not path:
            await query.message.reply(t(_LANG, "download_cmd.failed"))
            return
        try:
            if choice == "a":
                await query.message.reply_document(path)
                media_type = "audio"
            else:
                await query.message.reply_video(path, supports_streaming=True)
                media_type = "video"
            await track_media_download(
                url,
                media_type=media_type,
                chat_id=query.message.chat.id,
                user_id=query.from_user.id,
            )
            await query.message.reply(t(_LANG, "download_cmd.completed"))
        except Exception:
            await query.message.reply(t(_LANG, "download_cmd.failed"))

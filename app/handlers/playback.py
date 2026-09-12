from __future__ import annotations

import logging
import random
import time

from pyrogram import Client, filters
from pyrogram.types import Message

from app.handlers.priority import PRIORITY_COMMAND_GROUP
from app.repositories import admin_repo
from app.services import CallService, MediaService
from app.services.download_policy_service import get_download_denial_key
from app.services.fast_creat_media_service import (
    FastCreatMediaError,
    is_fast_creat_candidate,
    resolve_fast_creat_media,
    resolve_spotify_fallback_query,
)
from app.services.media_event_service import track_media_download
from app.services.youtube_session_service import (
    YoutubePlaylistUnsupported,
    YoutubeSessionsUnavailable,
)
from app.services.analytics_service import track_event
from app.utils.i18n import t
from app.utils.media_sources import (
    cleanup_temp_playback_local_source,
    download_trusted_telegram_media,
    is_http_url,
    is_soundcloud_track_url,
    is_soundcloud_url,
    normalize_media_source,
    validate_safe_url_with_redirects,
)
from app.services.media_capability_service import (
    build_now_playing_controls,
    deny_free_mode_media,
    deny_video_playback,
    get_chat_default_media_type,
    is_bot_media_feature_enabled,
)
from app.services.language_service import resolve_lang
from app.services.cover_art_service import reply_now_playing_with_optional_cover
from app.services.now_playing_renderer import (
    NowPlayingContext,
    extract_youtube_video_id,
    render_now_playing_text,
    requester_display_name,
    title_from_telegram_reply,
)
from app.services.playback_dispatch_service import (
    BusyPlaybackAction,
    apply_busy_playback_decision,
    decide_busy_playback,
)
from app.repositories import settings_repo
from app.utils.playback_auth import authorize_playback_action
from app.utils.playback_commands import (
    ParsedPlaybackCommand,
    PlaybackCommandKind,
    infer_reply_media,
    log_playback_command,
    normalize_playback_text,
    parse_playback_command,
    parse_slash_play_command,
    playback_command_filter,
    reply_media_kind_label,
)
from app.utils.playback_errors import reply_playback_join_failure
from app.utils.stop_notice import reply_stop_notice

logger = logging.getLogger(__name__)

_LANG = "fa"

_PLAY_AUDIO_CMDS = ["پخش", "play"]
_AUTO_MUSIC_CMDS = ["پخش خودکار موزیک", "Play Auto Music"]
_AUTO_VIDEO_CMDS = ["پخش خودکار ویدئو", "Play Auto Video"]
_PLAY_VIDEO_CMDS = ["پخش ویدیو", "playvideo"]
_SERIAL_CMDS = ["پخش سریال", "Serial Play"]
_STOP_AUDIO_CMDS = ["توقف پخش", "stopmusic", "Stop Play"]
_STOP_VIDEO_CMDS = ["توقف ویدیو", "stopvideo"]
_PAUSE_CMDS = ["مکث", "مکث پلیر", "pause", "Pause Play"]
_RESUME_CMDS = ["ازسرگیری", "resume", "Resume Play"]
_MUTE_CMDS = ["بیصدا", "پخش بیصدا", "silent", "Mute Play"]
_UNMUTE_CMDS = ["باصدا", "پخش باصدا", "unsilent", "UnMute Play"]
_SPEED_DOWN_CMDS = ["کاهش سرعت", "Speed Down"]
_SPEED_UP_CMDS = ["افزایش سرعت", "Speed Up"]
_VOLUME_DOWN_CMDS = ["کاهش صدا", "Volume-"]
_VOLUME_UP_CMDS = ["افزایش صدا", "Volume+"]
_SEEK_FRONT_CMDS = ["جلو", "Front"]
_SEEK_BACK_CMDS = ["عقب", "Back"]
_MUSIC_VOL_CMDS = ["صدای موزیک", "تنظیم صدا", "musicsound", "Set Volume"]
_VIDEO_VOL_CMDS = ["صدای ویدیو", "videosound"]
_PLAY_TV_CMDS = ["پخش تیوی", "پخش تلویزیون", "playtv", "Tv Play"]
_STOP_TV_CMDS = ["توقف تیوی", "stoptv"]
_PING_CMDS = ["پینگ", "ping"]
_BOT_CMDS = ["ربات", "bot", "robot"]
_BOT_EXACT_FA_REPLIES = ("جونم؟", "بفرمایید", "من فعالم", "در خدمتم", "آنلاینم")
_DIGIT_TRANSLATION = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")

# MISC-01: the 14 evidenced bare-emoji playback shortcuts. Each maps to an
# action already reachable by a text command, so permissions, credit checks and
# error replies are exactly the ones the equivalent command already enforces.
# The 'play' emoji is special-cased so an already-playing chat gets an explicit
# reply instead of a silent restart.
_EMOJI_PLAYBACK_ACTIONS: dict[str, str] = {
    "▶": "play",
    "⏸": "pause",
    "⏯": "toggle",
    "⏹": "stop",
    "⏮": "previous",
    "⏭": "next",
    "🔇": "mute",
    "🔈": "unmute",
    "🔉": "volume_down",
    "🔊": "volume_up",
    "📻": "radio",
    "📺": "tv",
    "📡": "satellite",
    "🎞": "panel",
}
# Telegram clients may or may not append VARIATION SELECTOR-16.
_VARIATION_SELECTOR = "️"


def normalize_playback_emoji(text: str | None) -> str | None:
    """Return the bare emoji shortcut in ``text``, or None when it is not one."""
    stripped = str(text or "").strip().replace(_VARIATION_SELECTOR, "")
    return stripped if stripped in _EMOJI_PLAYBACK_ACTIONS else None


def _is_chat_paused(chat_id: int) -> bool:
    """True when the chat has an active call that is currently paused."""
    active = CallService.get_active_calls().get(chat_id) or {}
    return bool(active.get("is_paused"))


def playback_emoji_filter():
    """Filter matching a message whose whole text is one playback emoji."""

    async def func(_flt, _client, message) -> bool:
        raw = getattr(message, "text", None) or getattr(message, "caption", None)
        return normalize_playback_emoji(raw) is not None

    return filters.create(func, name="PlaybackEmojiFilter")


def _build_filter(cmds: list[str]):
    """Build a regex filter matching any of the given command texts at start of line."""
    import re

    pattern = "|".join(re.escape(c) for c in cmds)
    return filters.regex(rf"^(?:{pattern})(?:\s|$)", flags=re.IGNORECASE)


def _build_exact_filter(cmds: list[str]):
    """Build a regex filter matching exact no-argument command texts."""
    import re

    pattern = "|".join(re.escape(c) for c in cmds)
    return filters.regex(rf"^\s*(?:{pattern})\s*$", flags=re.IGNORECASE)


def _mark_temp_playback_download(path: str | None) -> str | None:
    """Return trusted local path when downloaded only for immediate playback."""
    if not path or is_http_url(path):
        return None
    normalized = normalize_media_source(path)
    return normalized if normalized and not is_http_url(normalized) else None


async def _check_prerequisites(client: Client, message: Message) -> bool:
    """Check that the bot is admin, group has credit, and user has permission.

    Also enforces call security: when security_call_enabled is True,
    only admins, VIPs, player owners, and sudo+ can use playback (spec §13).
    """
    return await authorize_playback_action(client, message, lang=_LANG)


async def _send_spotify_youtube_fallback(
    message: Message,
    status: Message,
    *,
    source: str,
    query: str,
    title: str | None,
    user_id: int,
) -> None:
    from app.handlers.search import resolve_youtube_first_result

    await status.edit_text(t(_LANG, "fast_creat.spotify_youtube_fallback"))
    result = await resolve_youtube_first_result(query)
    if result is None:
        await status.edit_text(t(_LANG, "fast_creat.spotify_fallback_failed"))
        return
    try:
        path = await MediaService.download_audio(result["url"], message.chat.id)
    except Exception:
        logger.debug("Spotify YouTube fallback download failed", exc_info=True)
        path = None
    if path is None:
        await status.edit_text(t(_LANG, "fast_creat.failed"))
        return
    try:
        await message.reply_document(path)
        await track_media_download(
            source,
            media_type="audio",
            chat_id=message.chat.id,
            user_id=user_id,
            title=title or query,
        )
    except Exception:
        await status.edit_text(t(_LANG, "fast_creat.failed"))
        return
    await status.edit_text(t(_LANG, "fast_creat.completed", count=1))


async def _try_fast_creat_group_download(message: Message, source: str) -> bool:
    """Consume supported social links as group file downloads before Voice Chat flow."""
    if not is_fast_creat_candidate(source):
        return False

    user_id = message.from_user.id if message.from_user else 0
    denial_key = await get_download_denial_key(chat_id=message.chat.id, user_id=user_id)
    if denial_key is not None:
        await message.reply(t(_LANG, denial_key))
        return True

    status = await message.reply(t(_LANG, "fast_creat.downloading"))
    try:
        resolution = await resolve_fast_creat_media(source)
    except FastCreatMediaError as exc:
        key = {
            "blocked_url": "playback_cmd.blocked_url",
            "unsupported_link": "fast_creat.unsupported_link",
            "tokens_unavailable": "fast_creat.tokens_unavailable",
            "no_active_token": "fast_creat.no_active_token",
            "rate_limited": "fast_creat.rate_limited",
            "spotify_metadata_unavailable": "fast_creat.spotify_fallback_failed",
        }.get(exc.code, "fast_creat.failed")
        await status.edit_text(t(_LANG, key))
        return True

    if resolution.youtube_fallback_query:
        await _send_spotify_youtube_fallback(
            message,
            status,
            source=source,
            query=resolution.youtube_fallback_query,
            title=resolution.title,
            user_id=user_id,
        )
        return True

    sent = 0
    for item in resolution.items:
        path = await MediaService.download_direct_media(
            item.url,
            message.chat.id,
            media_type=item.media_type,
        )
        if path is None:
            continue
        caption = resolution.title if sent == 0 else None
        try:
            if item.media_type == "video":
                await message.reply_video(path, caption=caption, supports_streaming=True)
            elif item.media_type == "photo":
                await message.reply_photo(path, caption=caption)
            else:
                await message.reply_document(path, caption=caption)
            sent += 1
            await track_media_download(
                source,
                media_type=item.media_type,
                chat_id=message.chat.id,
                user_id=user_id,
                title=resolution.title,
            )
        except Exception:
            logger.debug("Fast-Creat media send failed", exc_info=True)

    if sent:
        await status.edit_text(t(_LANG, "fast_creat.completed", count=sent))
    elif resolution.provider == "spotify":
        fallback_query = await resolve_spotify_fallback_query(resolution.source_url)
        if fallback_query:
            await _send_spotify_youtube_fallback(
                message,
                status,
                source=source,
                query=fallback_query,
                title=resolution.title,
                user_id=user_id,
            )
        else:
            await status.edit_text(t(_LANG, "fast_creat.spotify_fallback_failed"))
    else:
        await status.edit_text(t(_LANG, "fast_creat.failed"))
    return True


async def _apply_volume_step(call_py, chat_id: int, delta: int) -> tuple[str, dict]:
    """Apply the same bounded volume-step behavior used by playback callbacks."""
    import importlib

    playback_callbacks = importlib.import_module("app.handlers.callbacks")
    if not playback_callbacks._is_playback_active(chat_id):
        return "playback_cmd.no_voice_chat", {}

    current = playback_callbacks._current_volume(chat_id)
    target = playback_callbacks._clamp(
        current + delta,
        playback_callbacks._PB_VOLUME_MIN,
        playback_callbacks._PB_VOLUME_MAX,
    )
    if target == current:
        key = "playback.controls.vol_up" if delta > 0 else "playback.controls.vol_down"
        return key, {}

    ok = await CallService.set_volume(call_py, chat_id, target)
    if ok:
        playback_callbacks._pb_volume_state[chat_id] = target
        return "playback_cmd.volume_set", {"volume": target}
    return "playback_cmd.failed", {}


def _parse_seek_seconds(text: str | None, cmds: list[str]) -> tuple[int | None, str | None]:
    """Parse optional positive seek seconds after a Help seek command."""
    remainder = _extract_prefixed_query(text, cmds)
    if remainder is None:
        return None, "playback.controls.seek_invalid_seconds"
    if not remainder:
        return CallService.PLAYBACK_SEEK_DEFAULT_SECONDS, None
    normalized = remainder.translate(_DIGIT_TRANSLATION).strip()
    if not normalized.isdigit():
        return None, "playback.controls.seek_invalid_seconds"
    seconds = int(normalized)
    if seconds <= 0:
        return None, "playback.controls.seek_invalid_seconds"
    return seconds, None


def _parse_absolute_volume(text: str | None, cmds: list[str]) -> tuple[int | None, str | None]:
    """Parse required 1..200 volume value after an absolute volume command."""
    remainder = _extract_prefixed_query(text, cmds)
    if remainder is None or not remainder.strip():
        return None, "playback_cmd.volume_usage"
    normalized = remainder.translate(_DIGIT_TRANSLATION).strip()
    if not normalized.isdigit():
        return None, "playback_cmd.volume_invalid"
    volume = int(normalized)
    if volume < 1 or volume > 200:
        return None, "playback_cmd.volume_invalid"
    return volume, None


_REPLAY_CMDS = ["پخش ریپلای", "replayreply"]
_CREATORS_LIST_CMDS = ["لیست مالکان", "creatorslist"]


def _parse_dedication(text: str, cmd_prefix: str) -> tuple[str | None, str | None]:
    """Parse optional dedication target from play command text (G3).

    Supported forms:
        پخش @username <query>
        پخش 123456789 <query>
        پخش <query> برای @username

    Returns (source_or_query, dedication_target).
    """
    parsed = parse_playback_command(text)
    if parsed is not None:
        return parsed.remainder or None, parsed.dedication
    remainder = text[len(cmd_prefix):].strip() if text else ""
    if not remainder:
        return None, None
    if "برای" in remainder:
        parts = remainder.rsplit("برای", 1)
        return parts[0].strip() or None, parts[1].strip() or None
    tokens = remainder.split(maxsplit=1)
    first = tokens[0]
    if first.startswith("@") or first.isdigit():
        target = first
        source = tokens[1].strip() if len(tokens) > 1 else None
        return source, target
    return remainder, None


def _extract_prefixed_query(text: str | None, cmds: list[str]) -> str | None:
    """Return the query after one of the exact command prefixes."""
    normalized = normalize_playback_text(text or "")
    lowered = normalized.lower()
    for prefix in sorted(cmds, key=len, reverse=True):
        normalized_prefix = normalize_playback_text(prefix)
        prefix_lower = normalized_prefix.lower()
        if lowered == prefix_lower:
            return ""
        if lowered.startswith(prefix_lower + " "):
            return normalized[len(normalized_prefix):].strip()
    return None


async def _check_media_setting_gates(
    message: Message,
    *,
    media_type: str,
    from_telegram_file: bool,
) -> bool:
    """Return False after sending a visible denial when chat media gates block playback."""
    if media_type == "audio" and not await is_bot_media_feature_enabled("audio"):
        await message.reply(t(_LANG, "playback_cmd.audio_disabled_in_group"))
        log_playback_command(
            "playback_command_ignored",
            chat_id=message.chat.id,
            user_id=message.from_user.id if message.from_user else None,
            reason="bot_audio_disabled",
        )
        return False
    if from_telegram_file and not await is_bot_media_feature_enabled("file"):
        await message.reply(t(_LANG, "playback_cmd.file_disabled_in_group"))
        log_playback_command(
            "playback_command_ignored",
            chat_id=message.chat.id,
            user_id=message.from_user.id if message.from_user else None,
            reason="bot_file_disabled",
        )
        return False
    cs = await settings_repo.get_chat_settings(message.chat.id)
    if cs is None:
        return True
    if media_type == "audio" and not getattr(cs, "audio_enabled", True):
        await message.reply(t(_LANG, "playback_cmd.audio_disabled_in_group"))
        log_playback_command(
            "playback_command_ignored",
            chat_id=message.chat.id,
            user_id=message.from_user.id if message.from_user else None,
            reason="audio_disabled",
        )
        return False
    if from_telegram_file and not getattr(cs, "file_enabled", True):
        await message.reply(t(_LANG, "playback_cmd.file_disabled_in_group"))
        log_playback_command(
            "playback_command_ignored",
            chat_id=message.chat.id,
            user_id=message.from_user.id if message.from_user else None,
            reason="file_disabled",
        )
        return False
    return True


async def _handle_play_command(
    client: Client,
    call_py,
    message: Message,
    parsed: ParsedPlaybackCommand,
    *,
    forced_media_type: str | None = None,
    prerequisites_checked: bool = False,
    require_stream_resolution: bool = False,
) -> None:
    """Shared پخش / play / /play command pipeline."""
    chat_id = message.chat.id
    user_id = message.from_user.id if message.from_user else None
    reply = message.reply_to_message
    log_playback_command(
        "playback_command_received",
        chat_id=chat_id,
        user_id=user_id,
        has_reply=reply is not None,
        reply_media=reply_media_kind_label(reply),
        command=parsed.kind.value,
    )

    source: str | None = parsed.remainder or None
    if source and is_http_url(source) and await _try_fast_creat_group_download(message, source):
        return

    if not prerequisites_checked and not await _check_prerequisites(client, message):
        log_playback_command(
            "playback_command_ignored",
            chat_id=chat_id,
            user_id=user_id,
            reason="auth_denied",
        )
        return

    dedication = parsed.dedication
    temp_cleanup_path: str | None = None
    media_type: str | None = forced_media_type
    from_telegram_file = False
    telegram_hint: str | None = None

    if not source and reply is not None:
        resolution = infer_reply_media(reply)
        if resolution is None:
            log_playback_command(
                "playback_reply_media_rejected",
                chat_id=chat_id,
                user_id=user_id,
                reason="no_playable_media",
            )
            await message.reply(t(_LANG, "playback_cmd.replay_no_media"))
            return
        media_type = forced_media_type or resolution.media_type
        log_playback_command(
            "playback_reply_media_detected",
            chat_id=chat_id,
            user_id=user_id,
            media_type=media_type,
            reply_media=reply_media_kind_label(reply),
        )
        if resolution.source_kind == "url":
            source = str(resolution.media_object)
        else:
            from_telegram_file = True
            telegram_hint = "telegram"
            source = await download_trusted_telegram_media(
                client,
                resolution.media_object,
                chat_id,
            )
            temp_cleanup_path = _mark_temp_playback_download(source)

    if not source:
        await message.reply(t(_LANG, "playback_cmd.provide_source"))
        return

    if media_type is None:
        media_type = await get_chat_default_media_type(chat_id)

    if not await _check_media_setting_gates(
        message,
        media_type=media_type,
        from_telegram_file=from_telegram_file,
    ):
        if temp_cleanup_path:
            cleanup_temp_playback_local_source(temp_cleanup_path, reason="media_gate")
        return

    if not from_telegram_file and not is_http_url(source):
        from app.handlers.search import resolve_youtube_first_result

        result = await resolve_youtube_first_result(source)
        if result is None:
            await message.reply(t(_LANG, "search.no_results"))
            return
        source = result["url"]

    if is_http_url(source):
        tracking_source = source
        source = await validate_safe_url_with_redirects(source)
        if source is None:
            if temp_cleanup_path:
                cleanup_temp_playback_local_source(temp_cleanup_path, reason="blocked_url")
            await message.reply(t(_LANG, "playback_cmd.blocked_url"))
            return
        if is_soundcloud_url(source) and not is_soundcloud_track_url(source):
            if temp_cleanup_path:
                cleanup_temp_playback_local_source(
                    temp_cleanup_path,
                    reason="soundcloud_collection",
                )
            await message.reply(t(_LANG, "playback_cmd.soundcloud_track_only"))
            return
        try:
            stream_url = await MediaService.get_stream_url(source, media_type=media_type)
        except YoutubeSessionsUnavailable:
            if temp_cleanup_path:
                cleanup_temp_playback_local_source(temp_cleanup_path, reason="youtube_sessions_unavailable")
            await message.reply(t(_LANG, "youtube_sessions.unavailable"))
            return
        except YoutubePlaylistUnsupported:
            if temp_cleanup_path:
                cleanup_temp_playback_local_source(temp_cleanup_path, reason="youtube_playlist")
            await message.reply(t(_LANG, "youtube_sessions.playlist_requires_video"))
            return
        if stream_url:
            source = stream_url
        elif require_stream_resolution:
            if temp_cleanup_path:
                cleanup_temp_playback_local_source(temp_cleanup_path, reason="stream_unavailable")
            await message.reply(t(_LANG, "playback_cmd.failed"))
            return
    else:
        tracking_source = source

    source = normalize_media_source(source)
    if source is None:
        if temp_cleanup_path:
            cleanup_temp_playback_local_source(temp_cleanup_path, reason="invalid_source")
        await message.reply(t(_LANG, "playback_cmd.invalid_source"))
        return

    if media_type == "video" and await deny_video_playback(message, lang=_LANG):
        if temp_cleanup_path:
            cleanup_temp_playback_local_source(temp_cleanup_path, reason="video_denied")
        return

    busy_decision = await decide_busy_playback(
        chat_id,
        source,
        media_type,
        is_temp_local=temp_cleanup_path is not None,
    )
    if await apply_busy_playback_decision(
        message,
        chat_id,
        source,
        media_type,
        busy_decision,
        requester_id=user_id,
        lang=_LANG,
    ):
        if temp_cleanup_path and busy_decision.action != BusyPlaybackAction.QUEUED:
            cleanup_temp_playback_local_source(temp_cleanup_path, reason="busy_playback")
        return

    if telegram_hint is None:
        telegram_hint = (
            "telegram"
            if tracking_source == source and not is_http_url(tracking_source or "")
            else None
        )

    ok = await CallService.join_voice_chat(
        call_py,
        chat_id,
        source,
        media_type,
        user_id=user_id,
        event_source=tracking_source,
        source_kind_hint=telegram_hint,
        cleanup_local_source_on_failure=temp_cleanup_path is not None,
    )
    if ok:
        temp_cleanup_path = None
        event_name = (
            "playback.play_video" if media_type == "video" else "playback.play_audio"
        )
        await track_event(event_name, chat_type="group", feature="playback")
        lang = await resolve_lang(chat_id=chat_id, user_id=user_id)
        text = await render_now_playing_text(
            chat_id,
            NowPlayingContext(
                title=title_from_telegram_reply(reply),
                media_type=media_type,
                requester_name=requester_display_name(message.from_user),
                requester_id=user_id,
                track_id=extract_youtube_video_id(tracking_source),
            ),
            lang=lang,
            dedication=dedication,
            user_id=user_id,
        )
        await reply_now_playing_with_optional_cover(
            message,
            chat_id=chat_id,
            text=text,
            reply_markup=await build_now_playing_controls(lang, chat_id),
            reply_to_message=reply,
        )
    else:
        await reply_playback_join_failure(message, lang=_LANG)


def register(bot: Client, call_py) -> None:

    async def _dispatch_play_command(client: Client, message: Message) -> None:
        parsed = parse_playback_command(normalize_playback_text(message))
        if parsed is None:
            log_playback_command(
                "playback_command_ignored",
                chat_id=message.chat.id,
                user_id=message.from_user.id if message.from_user else None,
                reason="parse_miss",
                level="debug",
            )
            return
        await _handle_play_command(client, call_py, message, parsed)

    async def _handle_auto_youtube_play(
        client: Client,
        message: Message,
        *,
        cmds: list[str],
        persian_prefix: str,
        english_prefix: str,
        media_type: str,
    ) -> None:
        raw_text = message.text or message.caption
        query = _extract_prefixed_query(raw_text, cmds)
        if query is None:
            return
        if not query:
            await message.reply(t(_LANG, "ask.search_query"))
            return

        if not await _check_prerequisites(client, message):
            return
        if media_type == "video" and await deny_video_playback(message, lang=_LANG):
            return

        from app.handlers.search import resolve_youtube_first_result

        result = await resolve_youtube_first_result(query)
        if result is None:
            await message.reply(t(_LANG, "search.no_results"))
            return

        normalized_text = normalize_playback_text(raw_text)
        is_english = normalized_text.lower().startswith(english_prefix.lower())
        parsed = ParsedPlaybackCommand(
            kind=PlaybackCommandKind.ENGLISH_PLAY
            if is_english
            else PlaybackCommandKind.PERSIAN_PLAY,
            prefix=english_prefix if is_english else persian_prefix,
            remainder=result["url"],
        )
        play_kwargs = {
            "forced_media_type": media_type,
            "prerequisites_checked": True,
        }
        if media_type == "video":
            play_kwargs["require_stream_resolution"] = True
        await _handle_play_command(client, call_py, message, parsed, **play_kwargs)

    @bot.on_message(
        _build_filter(_AUTO_MUSIC_CMDS) & filters.group,
        group=PRIORITY_COMMAND_GROUP,
    )
    async def play_auto_music(client: Client, message: Message):
        await _handle_auto_youtube_play(
            client,
            message,
            cmds=_AUTO_MUSIC_CMDS,
            persian_prefix="پخش خودکار موزیک",
            english_prefix="Play Auto Music",
            media_type="audio",
        )

    @bot.on_message(
        _build_filter(_AUTO_VIDEO_CMDS) & filters.group,
        group=PRIORITY_COMMAND_GROUP,
    )
    async def play_auto_video(client: Client, message: Message):
        await _handle_auto_youtube_play(
            client,
            message,
            cmds=_AUTO_VIDEO_CMDS,
            persian_prefix="پخش خودکار ویدئو",
            english_prefix="Play Auto Video",
            media_type="video",
        )

    @bot.on_message(
        _build_exact_filter(_SERIAL_CMDS) & filters.group,
        group=PRIORITY_COMMAND_GROUP,
    )
    async def play_serial_placeholder(client: Client, message: Message):
        await message.reply(t(_LANG, "playback.controls.serial_coming_soon"))

    async def _reply_speed_change(
        client: Client,
        message: Message,
        *,
        delta: int,
    ) -> None:
        if not await _check_prerequisites(client, message):
            return
        result = await CallService.change_playback_speed(call_py, message.chat.id, delta)
        await message.reply(
            t(_LANG, result.message_key, speed=result.speed_label)
        )

    async def _reply_volume_step(
        client: Client,
        message: Message,
        *,
        delta: int,
    ) -> None:
        if not await _check_prerequisites(client, message):
            return
        key, kwargs = await _apply_volume_step(call_py, message.chat.id, delta)
        await message.reply(t(_LANG, key, **kwargs))

    async def _reply_seek_change(
        client: Client,
        message: Message,
        *,
        cmds: list[str],
        direction: int,
    ) -> None:
        seconds, error_key = _parse_seek_seconds(message.text or message.caption, cmds)
        if error_key is not None or seconds is None:
            await message.reply(t(_LANG, error_key or "playback.controls.seek_invalid_seconds"))
            return
        if not await _check_prerequisites(client, message):
            return
        result = await CallService.seek_playback(
            call_py,
            message.chat.id,
            direction * seconds,
        )
        await message.reply(
            t(_LANG, result.message_key, position=result.position_label)
        )

    @bot.on_message(
        _build_exact_filter(_SPEED_DOWN_CMDS) & filters.group,
        group=PRIORITY_COMMAND_GROUP,
    )
    async def speed_down_control(client: Client, message: Message):
        await _reply_speed_change(
            client,
            message,
            delta=-CallService.PLAYBACK_SPEED_STEP,
        )

    @bot.on_message(
        _build_exact_filter(_SPEED_UP_CMDS) & filters.group,
        group=PRIORITY_COMMAND_GROUP,
    )
    async def speed_up_control(client: Client, message: Message):
        await _reply_speed_change(
            client,
            message,
            delta=CallService.PLAYBACK_SPEED_STEP,
        )

    @bot.on_message(
        _build_exact_filter(_VOLUME_DOWN_CMDS) & filters.group,
        group=PRIORITY_COMMAND_GROUP,
    )
    async def volume_down_step(client: Client, message: Message):
        await _reply_volume_step(client, message, delta=-20)

    @bot.on_message(
        _build_exact_filter(_VOLUME_UP_CMDS) & filters.group,
        group=PRIORITY_COMMAND_GROUP,
    )
    async def volume_up_step(client: Client, message: Message):
        await _reply_volume_step(client, message, delta=20)

    @bot.on_message(
        _build_filter(_SEEK_FRONT_CMDS) & filters.group,
        group=PRIORITY_COMMAND_GROUP,
    )
    async def seek_front_control(client: Client, message: Message):
        await _reply_seek_change(
            client,
            message,
            cmds=_SEEK_FRONT_CMDS,
            direction=1,
        )

    @bot.on_message(
        _build_filter(_SEEK_BACK_CMDS) & filters.group,
        group=PRIORITY_COMMAND_GROUP,
    )
    async def seek_back_control(client: Client, message: Message):
        await _reply_seek_change(
            client,
            message,
            cmds=_SEEK_BACK_CMDS,
            direction=-1,
        )

    # ── Play audio (with dedication support, G3 §15.15) ────────────────
    @bot.on_message(
        playback_command_filter() & filters.group,
        group=PRIORITY_COMMAND_GROUP,
    )
    async def play_audio(client: Client, message: Message):
        await _dispatch_play_command(client, message)

    @bot.on_message(
        filters.command("play") & filters.group,
        group=PRIORITY_COMMAND_GROUP,
    )
    async def play_slash_command(client: Client, message: Message):
        log_playback_command(
            "playback_slash_handler_entered",
            chat_id=message.chat.id,
            user_id=message.from_user.id if message.from_user else None,
            has_reply=message.reply_to_message is not None,
            command="slash_play",
        )
        parsed = parse_slash_play_command(message)
        if parsed is None:
            log_playback_command(
                "playback_command_ignored",
                chat_id=message.chat.id,
                user_id=message.from_user.id if message.from_user else None,
                reason="slash_parse_miss",
            )
            await message.reply(t(_LANG, "playback_cmd.provide_source"))
            return
        await _handle_play_command(client, call_py, message, parsed)

    # ── Play video (with dedication support, G3 §15.16) ────────────────
    @bot.on_message(_build_filter(_PLAY_VIDEO_CMDS) & filters.group)
    async def play_video(client: Client, message: Message):
        if not await _check_prerequisites(client, message):
            return
        if await deny_video_playback(message, lang=_LANG):
            return

        source = None
        dedication = None
        temp_cleanup_path: str | None = None
        raw = message.text or ""

        for prefix in _PLAY_VIDEO_CMDS:
            if raw.lower().startswith(prefix.lower()):
                source, dedication = _parse_dedication(raw, prefix)
                break

        if source is None and message.reply_to_message:
            reply = message.reply_to_message
            if reply.video:
                source = await download_trusted_telegram_media(client, reply.video, message.chat.id)
                temp_cleanup_path = _mark_temp_playback_download(source)
            elif reply.text:
                source = reply.text.strip()

        if not source:
            await message.reply(t(_LANG, "playback_cmd.provide_source"))
            return

        if is_http_url(source):
            tracking_source = source
            source = await validate_safe_url_with_redirects(source)
            if source is None:
                await message.reply(t(_LANG, "playback_cmd.blocked_url"))
                return
            if is_soundcloud_url(source) and not is_soundcloud_track_url(source):
                await message.reply(t(_LANG, "playback_cmd.soundcloud_track_only"))
                return
            try:
                stream_url = await MediaService.get_stream_url(source, media_type="video")
            except YoutubeSessionsUnavailable:
                await message.reply(t(_LANG, "youtube_sessions.unavailable"))
                return
            except YoutubePlaylistUnsupported:
                await message.reply(t(_LANG, "youtube_sessions.playlist_requires_video"))
                return
            if stream_url:
                source = stream_url
        else:
            tracking_source = source
        source = normalize_media_source(source)
        if source is None:
            if temp_cleanup_path:
                cleanup_temp_playback_local_source(
                    temp_cleanup_path,
                    reason="invalid_source",
                )
            await message.reply(t(_LANG, "playback_cmd.invalid_source"))
            return

        busy_decision = await decide_busy_playback(
            message.chat.id,
            source,
            "video",
            is_temp_local=temp_cleanup_path is not None,
        )
        if await apply_busy_playback_decision(
            message,
            message.chat.id,
            source,
            "video",
            busy_decision,
            requester_id=message.from_user.id if message.from_user else None,
            lang=_LANG,
        ):
            if temp_cleanup_path and busy_decision.action != BusyPlaybackAction.QUEUED:
                cleanup_temp_playback_local_source(
                    temp_cleanup_path,
                    reason="busy_playback",
                )
            return

        telegram_hint = "telegram" if tracking_source == source and not is_http_url(tracking_source or "") else None
        ok = await CallService.join_voice_chat(
            call_py,
            message.chat.id,
            source,
            "video",
            user_id=message.from_user.id if message.from_user else None,
            event_source=tracking_source,
            source_kind_hint=telegram_hint,
            cleanup_local_source_on_failure=temp_cleanup_path is not None,
        )
        if ok:
            temp_cleanup_path = None
        if ok:
            await track_event("playback.play_video", chat_type="group", feature="playback")
            lang = await resolve_lang(
                chat_id=message.chat.id,
                user_id=message.from_user.id if message.from_user else None,
            )
            text = await render_now_playing_text(
                message.chat.id,
                NowPlayingContext(
                    title=title_from_telegram_reply(message.reply_to_message),
                    media_type="video",
                    requester_name=requester_display_name(message.from_user),
                    requester_id=message.from_user.id if message.from_user else None,
                    track_id=extract_youtube_video_id(tracking_source),
                ),
                lang=lang,
                dedication=dedication,
                user_id=message.from_user.id if message.from_user else None,
            )
            await reply_now_playing_with_optional_cover(
                message,
                chat_id=message.chat.id,
                text=text,
                reply_markup=await build_now_playing_controls(lang, message.chat.id),
                reply_to_message=message.reply_to_message,
            )
        else:
            await reply_playback_join_failure(message, lang=_LANG)

    # ── Replay-on-reply (G4, §17.9) ──────────────────────────────────────
    @bot.on_message(_build_filter(_REPLAY_CMDS) & filters.group)
    async def replay_on_reply(client: Client, message: Message):
        if not await _check_prerequisites(client, message):
            return

        reply = message.reply_to_message
        if reply is None:
            await message.reply(t(_LANG, "playback_cmd.replay_no_reply"))
            return

        source = None
        media_type = "audio"
        invalid_source = False
        blocked_url = False
        temp_cleanup_path: str | None = None

        if reply.audio:
            source = await download_trusted_telegram_media(client, reply.audio, message.chat.id)
            temp_cleanup_path = _mark_temp_playback_download(source)
        elif reply.video:
            source = await download_trusted_telegram_media(client, reply.video, message.chat.id)
            temp_cleanup_path = _mark_temp_playback_download(source)
            media_type = "video"
        elif reply.voice:
            source = await download_trusted_telegram_media(client, reply.voice, message.chat.id)
            temp_cleanup_path = _mark_temp_playback_download(source)
        elif reply.document and reply.document.mime_type and (
            reply.document.mime_type.startswith("audio/")
            or reply.document.mime_type.startswith("video/")
        ):
            source = await download_trusted_telegram_media(client, reply.document, message.chat.id)
            temp_cleanup_path = _mark_temp_playback_download(source)
            if reply.document.mime_type.startswith("video/"):
                media_type = "video"
        elif reply.text:
            text_content = reply.text.strip()
            if is_http_url(text_content):
                safe_url = await validate_safe_url_with_redirects(text_content)
                if safe_url is None:
                    blocked_url = True
                else:
                    try:
                        stream_url = await MediaService.get_stream_url(
                            safe_url,
                            media_type=media_type,
                        )
                    except YoutubeSessionsUnavailable:
                        await message.reply(t(_LANG, "youtube_sessions.unavailable"))
                        return
                    except YoutubePlaylistUnsupported:
                        await message.reply(t(_LANG, "youtube_sessions.playlist_requires_video"))
                        return
                    source = stream_url or safe_url
            else:
                invalid_source = bool(text_content)

        tracking_source = source
        telegram_hint = None
        if reply.text:
            text_content = reply.text.strip()
            if is_http_url(text_content):
                tracking_source = text_content
            else:
                invalid_source = bool(text_content)
        else:
            telegram_hint = "telegram"

        if source is not None:
            source = normalize_media_source(source)
            if source is None:
                invalid_source = True

        if blocked_url:
            if temp_cleanup_path:
                cleanup_temp_playback_local_source(
                    temp_cleanup_path,
                    reason="blocked_url",
                )
            await message.reply(t(_LANG, "playback_cmd.blocked_url"))
            return

        if invalid_source:
            if temp_cleanup_path:
                cleanup_temp_playback_local_source(
                    temp_cleanup_path,
                    reason="invalid_source",
                )
            await message.reply(t(_LANG, "playback_cmd.invalid_source"))
            return

        if not source:
            await message.reply(t(_LANG, "playback_cmd.replay_no_media"))
            return

        if (
            media_type == "audio"
            and reply.text
            and is_http_url(reply.text.strip())
        ):
            media_type = await get_chat_default_media_type(message.chat.id)

        if media_type == "video" and await deny_video_playback(message, lang=_LANG):
            if temp_cleanup_path:
                cleanup_temp_playback_local_source(
                    temp_cleanup_path,
                    reason="video_denied",
                )
            return

        busy_decision = await decide_busy_playback(
            message.chat.id,
            source,
            media_type,
            is_temp_local=temp_cleanup_path is not None,
        )
        if await apply_busy_playback_decision(
            message,
            message.chat.id,
            source,
            media_type,
            busy_decision,
            requester_id=message.from_user.id if message.from_user else None,
            lang=_LANG,
        ):
            if temp_cleanup_path and busy_decision.action != BusyPlaybackAction.QUEUED:
                cleanup_temp_playback_local_source(
                    temp_cleanup_path,
                    reason="busy_playback",
                )
            return

        ok = await CallService.join_voice_chat(
            call_py,
            message.chat.id,
            source,
            media_type,
            user_id=message.from_user.id if message.from_user else None,
            event_source=tracking_source,
            source_kind_hint=telegram_hint,
            cleanup_local_source_on_failure=temp_cleanup_path is not None,
        )
        if ok:
            temp_cleanup_path = None
            lang = await resolve_lang(
                chat_id=message.chat.id,
                user_id=message.from_user.id if message.from_user else None,
            )
            text = await render_now_playing_text(
                message.chat.id,
                NowPlayingContext(
                    title=title_from_telegram_reply(reply),
                    media_type=media_type,
                    requester_name=requester_display_name(message.from_user),
                    requester_id=message.from_user.id if message.from_user else None,
                    track_id=extract_youtube_video_id(tracking_source),
                ),
                lang=lang,
                user_id=message.from_user.id if message.from_user else None,
            )
            await reply_now_playing_with_optional_cover(
                message,
                chat_id=message.chat.id,
                text=text,
                reply_markup=await build_now_playing_controls(lang, message.chat.id),
                reply_to_message=reply,
            )
        else:
            await reply_playback_join_failure(message, lang=_LANG, track=False)

    # ── CreatorsList (G5, §20.13) ─────────────────────────────────────
    @bot.on_message(_build_filter(_CREATORS_LIST_CMDS) & filters.group)
    async def creators_list(client: Client, message: Message):
        chat_id = message.chat.id
        owners = await admin_repo.get_player_owners(chat_id)
        if not owners:
            await message.reply(t(_LANG, "promotion.creators_list_empty"))
            return
        header = t(_LANG, "promotion.creators_list_title")
        lines = [t(_LANG, "list_fmt.user_item", user_id=o.user_id, username=o.username or o.display_name or "-") for o in owners]
        await message.reply(header + "\n" + "\n".join(lines))

    # ── Stop audio ────────────────────────────────────────────────────────
    @bot.on_message(_build_exact_filter(_STOP_AUDIO_CMDS) & filters.group)
    async def stop_audio(client: Client, message: Message):
        if not await _check_prerequisites(client, message):
            return
        try:
            ok = await CallService.leave_voice_chat(call_py, message.chat.id)
        except Exception:
            logger.exception("stop_audio failed chat_id=%s", message.chat.id)
            await message.reply(t(_LANG, "playback_cmd.failed"))
            return
        if not ok:
            await message.reply(
                t(
                    _LANG,
                    CallService.pop_leave_failure_key()
                    or "playback_cmd.no_voice_chat",
                )
            )
            return
        await track_event("playback.stop", chat_type="group", feature="playback")
        await reply_stop_notice(
            message, t(_LANG, "playback_cmd.stopped_audio"), int(message.chat.id)
        )

    # ── Stop video ────────────────────────────────────────────────────────
    @bot.on_message(_build_filter(_STOP_VIDEO_CMDS) & filters.group)
    async def stop_video(client: Client, message: Message):
        if not await _check_prerequisites(client, message):
            return
        await CallService.leave_voice_chat(call_py, message.chat.id)
        await track_event("playback.stop", chat_type="group", feature="playback")
        await reply_stop_notice(
            message, t(_LANG, "playback_cmd.stopped_video"), int(message.chat.id)
        )

    # ── Pause ─────────────────────────────────────────────────────────────
    @bot.on_message(_build_exact_filter(_PAUSE_CMDS) & filters.group)
    async def pause_playback(client: Client, message: Message):
        if not await _check_prerequisites(client, message):
            return
        ok = await CallService.pause(call_py, message.chat.id)
        if ok:
            await track_event("playback.pause", chat_type="group", feature="playback")
            await message.reply(t(_LANG, "playback_cmd.paused"))
        else:
            await message.reply(t(_LANG, "playback_cmd.failed"))

    # ── Resume ────────────────────────────────────────────────────────────
    @bot.on_message(_build_exact_filter(_RESUME_CMDS) & filters.group)
    async def resume_playback(client: Client, message: Message):
        if not await _check_prerequisites(client, message):
            return
        ok = await CallService.resume(call_py, message.chat.id)
        if ok:
            await track_event("playback.resume", chat_type="group", feature="playback")
            await message.reply(t(_LANG, "playback_cmd.resumed"))
        else:
            await message.reply(t(_LANG, "playback_cmd.failed"))

    # ── Mute ──────────────────────────────────────────────────────────────
    @bot.on_message(_build_exact_filter(_MUTE_CMDS) & filters.group)
    async def mute(client: Client, message: Message):
        if not await _check_prerequisites(client, message):
            return
        if not CallService.is_chat_playing(message.chat.id):
            await message.reply(t(_LANG, "playback_cmd.no_voice_chat"))
            return
        ok = await CallService.set_volume(call_py, message.chat.id, 1)
        if ok:
            await message.reply(t(_LANG, "playback_cmd.muted"))
        else:
            await message.reply(t(_LANG, "playback_cmd.failed"))

    # ── Unmute ────────────────────────────────────────────────────────────
    @bot.on_message(_build_exact_filter(_UNMUTE_CMDS) & filters.group)
    async def unmute(client: Client, message: Message):
        if not await _check_prerequisites(client, message):
            return
        if not CallService.is_chat_playing(message.chat.id):
            await message.reply(t(_LANG, "playback_cmd.no_voice_chat"))
            return
        ok = await CallService.set_volume(call_py, message.chat.id, 100)
        if ok:
            await message.reply(t(_LANG, "playback_cmd.unmuted"))
        else:
            await message.reply(t(_LANG, "playback_cmd.failed"))

    # ── Music volume ──────────────────────────────────────────────────────
    @bot.on_message(_build_filter(_MUSIC_VOL_CMDS) & filters.group)
    async def music_volume(client: Client, message: Message):
        vol, error_key = _parse_absolute_volume(message.text or message.caption, _MUSIC_VOL_CMDS)
        if error_key is not None or vol is None:
            await message.reply(t(_LANG, error_key or "playback_cmd.volume_usage"))
            return
        if not await _check_prerequisites(client, message):
            return
        ok = await CallService.set_volume(call_py, message.chat.id, vol)
        if ok:
            await message.reply(t(_LANG, "playback_cmd.volume_set", volume=vol))
        else:
            await message.reply(t(_LANG, "playback_cmd.failed"))

    # ── Video volume ──────────────────────────────────────────────────────
    @bot.on_message(_build_filter(_VIDEO_VOL_CMDS) & filters.group)
    async def video_volume(client: Client, message: Message):
        vol, error_key = _parse_absolute_volume(message.text or message.caption, _VIDEO_VOL_CMDS)
        if error_key is not None or vol is None:
            await message.reply(t(_LANG, error_key or "playback_cmd.volume_usage"))
            return
        if not await _check_prerequisites(client, message):
            return
        ok = await CallService.set_volume(call_py, message.chat.id, vol)
        if ok:
            await message.reply(t(_LANG, "playback_cmd.volume_set", volume=vol))
        else:
            await message.reply(t(_LANG, "playback_cmd.failed"))

    # ── Play TV ───────────────────────────────────────────────────────────
    @bot.on_message(_build_filter(_PLAY_TV_CMDS) & filters.group)
    async def play_tv(client: Client, message: Message):
        if not await _check_prerequisites(client, message):
            return
        if await deny_free_mode_media(message, "tv", lang=_LANG):
            return
        from app.handlers.tv_radio import show_tv_menu

        await show_tv_menu(client, message)

    # ── Stop TV ───────────────────────────────────────────────────────────
    @bot.on_message(playback_emoji_filter() & filters.group)
    async def emoji_playback_shortcut(client: Client, message: Message):
        """Route a bare playback emoji to its equivalent playback action."""
        emoji = normalize_playback_emoji(
            getattr(message, "text", None) or getattr(message, "caption", None)
        )
        if emoji is None:
            return
        action = _EMOJI_PLAYBACK_ACTIONS[emoji]
        if not await _check_prerequisites(client, message):
            return

        chat_id = int(message.chat.id)
        if action in {"radio", "tv", "satellite"}:
            from app.handlers import tv_radio

            opener = {
                "radio": tv_radio.show_radio_menu,
                "tv": tv_radio.show_tv_menu,
                "satellite": tv_radio.show_satellite_menu,
            }[action]
            await opener(client, message)
            return

        if action == "panel":
            from app.handlers.group_text_call_commands import _handle_get_panel

            await _handle_get_panel(message)
            return

        if action == "play":
            if CallService.is_chat_playing(chat_id) and not _is_chat_paused(chat_id):
                await message.reply(t(_LANG, "playback_cmd.already_playing"))
                return
            ok = await CallService.resume(call_py, chat_id)
            await message.reply(
                t(_LANG, "playback_cmd.resumed" if ok else "playback_cmd.no_voice_chat")
            )
            return

        if action == "toggle":
            action = "pause" if not _is_chat_paused(chat_id) else "play_resume"

        if action == "pause":
            ok = await CallService.pause(call_py, chat_id)
            await message.reply(
                t(_LANG, "playback_cmd.paused" if ok else "playback_cmd.failed")
            )
            return
        if action == "play_resume":
            ok = await CallService.resume(call_py, chat_id)
            await message.reply(
                t(_LANG, "playback_cmd.resumed" if ok else "playback_cmd.failed")
            )
            return
        if action == "stop":
            ok = await CallService.leave_voice_chat(call_py, chat_id)
            if not ok:
                await message.reply(
                    t(_LANG, CallService.pop_leave_failure_key() or "playback_cmd.no_voice_chat")
                )
                return
            await track_event("playback.stop", chat_type="group", feature="playback")
            await reply_stop_notice(message, t(_LANG, "playback_cmd.stopped_audio"), chat_id)
            return
        if action in {"next", "previous"}:
            mover = CallService.play_next if action == "next" else CallService.play_previous
            ok = await mover(call_py, chat_id)
            await message.reply(
                t(_LANG, "playback_cmd.resumed" if ok else "playback_cmd.no_voice_chat")
            )
            return
        if action in {"mute", "unmute"}:
            ok = await CallService.set_volume(call_py, chat_id, 0 if action == "mute" else 100)
            await message.reply(
                t(_LANG, "playback_cmd.muted" if action == "mute" else "playback_cmd.unmuted")
                if ok
                else t(_LANG, "playback_cmd.no_voice_chat")
            )
            return
        if action in {"volume_up", "volume_down"}:
            key, kwargs = await _apply_volume_step(
                call_py, chat_id, 10 if action == "volume_up" else -10
            )
            await message.reply(t(_LANG, key, **kwargs))
            return

    @bot.on_message(_build_filter(_STOP_TV_CMDS) & filters.group)
    async def stop_tv(client: Client, message: Message):
        if not await _check_prerequisites(client, message):
            return
        await CallService.leave_voice_chat(call_py, message.chat.id)
        await reply_stop_notice(
            message, t(_LANG, "playback_cmd.stopped_tv"), int(message.chat.id)
        )

    # ── Ping ──────────────────────────────────────────────────────────────
    @bot.on_message(_build_filter(_PING_CMDS) & filters.group)
    async def ping(client: Client, message: Message):
        start = time.perf_counter()
        sent = await message.reply("⏳")
        ms = int((time.perf_counter() - start) * 1000)
        await sent.edit_text(t(_LANG, "playback_cmd.ping_response", ms=ms))

    # ── Bot / Robot ───────────────────────────────────────────────────────
    @bot.on_message(_build_exact_filter(_BOT_CMDS) & filters.group)
    async def bot_info(client: Client, message: Message):
        if (message.text or "").strip() == "ربات":
            await message.reply(random.choice(_BOT_EXACT_FA_REPLIES))
            return
        await message.reply(t(_LANG, "playback_cmd.bot_info_response"))

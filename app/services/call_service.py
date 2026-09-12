from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.database.engine import async_session
from app.database.models import HelperChatBinding, PlaybackState
from app.repositories import playlist_repo
from app.utils.diagnostic_logging import create_logged_task, redact_freeform_text
from app.utils.media_sources import (
    cleanup_temp_playback_local_source,
    ensure_trusted_local_media_path,
    is_http_url,
    normalize_media_source,
    resolve_media_source_for_playback,
)

logger = logging.getLogger(__name__)

_MAX_UNSAFE_QUEUE_SKIPS = 64
_last_join_failure_key: str | None = None
_last_leave_failure_key: str | None = None

PLAYBACK_SPEED_DEFAULT = 100
PLAYBACK_SPEED_MIN = 50
PLAYBACK_SPEED_MAX = 200
PLAYBACK_SPEED_STEP = 25
PLAYBACK_SEEK_DEFAULT_SECONDS = 10
_LIVE_PLAYBACK_FEATURES = {"radio", "satellite", "tv"}


async def _equalizer_kwargs(chat_id: int) -> dict[str, str]:
    """Return the equalizer kwarg only when the chat left the default preset.

    Keeps the transcode call signature byte-identical for the overwhelmingly
    common `normal` case, so the derivative cache key is unchanged.
    """
    preset = await _chat_equalizer_preset(chat_id)
    return {"equalizer": preset} if preset != "normal" else {}


async def _chat_equalizer_preset(chat_id: int) -> str:
    """Return the chat's audio-filter preset, defaulting to unfiltered."""
    try:
        from app.repositories import settings_repo
        from app.services.transcode_pool import normalize_equalizer

        row = await settings_repo.get_chat_settings(chat_id)
        return normalize_equalizer(getattr(row, "equalizer_preset", None))
    except Exception:
        logger.debug("equalizer lookup failed chat_id=%s", chat_id, exc_info=True)
        return "normal"
_LIVE_URL_HINTS = (".m3u8", "m3u8?", "icecast", "shoutcast")

_NO_VOICE_CHAT_SIGNATURES = (
    "NO_ACTIVE_GROUP_CALL",
    "GROUPCALL_INVALID",
    "GROUP_CALL_INVALID",
    "NOT_JOINED",
    "GROUPCALL_FORBIDDEN",
    "GROUP_CALL_NOT_MODIFIED",
    "no active group call",
    "not joined",
    "voice chat is not active",
)


def _set_join_failure(key: str) -> bool:
    """Record an i18n key for the last join failure and return False."""
    global _last_join_failure_key
    _last_join_failure_key = key
    return False


def _clear_join_failure() -> None:
    """Clear the last join failure key after a successful join."""
    global _last_join_failure_key
    _last_join_failure_key = None


def _set_leave_failure(key: str) -> bool:
    """Record an i18n key for the last leave failure and return False."""
    global _last_leave_failure_key
    _last_leave_failure_key = key
    return False


def _clear_leave_failure() -> None:
    """Clear the last leave failure key after a successful leave."""
    global _last_leave_failure_key
    _last_leave_failure_key = None


def _join_failure_key_for_exception(exc: Exception) -> str:
    """Map a join_group_call exception to a user-facing i18n key when possible."""
    err_str = f"{type(exc).__name__} {exc}".upper()
    if isinstance(exc, FileNotFoundError) or "FILENOTFOUNDERROR" in err_str:
        return "playback_cmd.source_missing"
    if type(exc).__name__ == "TelegramServerError" or "TELEGRAMSERVERERROR" in err_str:
        return "playback_cmd.voice_chat_connect_failed"
    if any(sig.upper() in err_str for sig in _NO_VOICE_CHAT_SIGNATURES):
        return "playback_cmd.no_voice_chat"
    if "BOT_METHOD_INVALID" in err_str or "BOTMETHODINVALID" in err_str:
        return "playback_cmd.voice_chat_unavailable"
    return "playback_cmd.failed"


async def _resolve_call_py(
    chat_id: int,
    *,
    helper_id: int | None = None,
    fallback: Any | None = None,
) -> Any | None:
    """Return the helper-bound PyTgCalls instance for a chat when available."""
    from app.services.helper_pytgcalls_pool import (
        HelperPyTgCallsPool,
        call_py_is_bot_session,
    )

    resolved = await HelperPyTgCallsPool.get_for_chat(chat_id, helper_id=helper_id)
    if resolved is not None:
        return resolved
    if fallback is not None and not call_py_is_bot_session(fallback):
        return fallback
    return None

_active_calls: dict[int, dict[str, Any]] = {}
_ffmpeg_processes: dict[int, asyncio.subprocess.Process] = {}
_repeat_states: dict[int, bool] = {}
_playback_history: dict[int, list[dict[str, Any]]] = {}
_recycle_pending: set[int] = set()
_prefetch_cache: dict[int, dict[str, Any]] = {}
_prefetch_tasks: dict[int, asyncio.Task] = {}
_playback_speed_states: dict[int, int] = {}
_playback_speed_locks: dict[int, asyncio.Lock] = {}
_join_progress_guard = asyncio.Lock()
_join_in_progress: set[int] = set()


@dataclass(frozen=True)
class PlaybackSpeedChangeResult:
    status: str
    message_key: str
    speed_percent: int
    speed_label: str
    show_alert: bool = False


@dataclass(frozen=True)
class PlaybackSeekResult:
    status: str
    message_key: str
    position_seconds: int
    position_label: str
    show_alert: bool = False


def _format_speed_label(speed_percent: int) -> str:
    value = speed_percent / 100
    label = f"{value:.2f}".rstrip("0").rstrip(".")
    if "." not in label:
        label += ".0"
    return label


def _format_position_label(position_seconds: int) -> str:
    seconds = max(0, int(position_seconds or 0))
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def _speed_result(
    status: str,
    message_key: str,
    *,
    speed_percent: int = PLAYBACK_SPEED_DEFAULT,
    show_alert: bool = False,
) -> PlaybackSpeedChangeResult:
    return PlaybackSpeedChangeResult(
        status=status,
        message_key=message_key,
        speed_percent=speed_percent,
        speed_label=_format_speed_label(speed_percent),
        show_alert=show_alert,
    )


def _seek_result(
    status: str,
    message_key: str,
    *,
    position_seconds: int = 0,
    show_alert: bool = False,
) -> PlaybackSeekResult:
    return PlaybackSeekResult(
        status=status,
        message_key=message_key,
        position_seconds=max(0, int(position_seconds or 0)),
        position_label=_format_position_label(position_seconds),
        show_alert=show_alert,
    )


def _speed_lock_for_chat(chat_id: int) -> asyncio.Lock:
    lock = _playback_speed_locks.get(chat_id)
    if lock is None:
        lock = asyncio.Lock()
        _playback_speed_locks[chat_id] = lock
    return lock


def _speed_now() -> datetime:
    return datetime.now(timezone.utc)


def _source_url_for_speed(*sources: str | None) -> str | None:
    for raw in sources:
        if raw is None:
            continue
        source = str(raw).strip()
        if source and is_http_url(source):
            return source
    return None


def _is_live_stream_url(source: str | None) -> bool:
    if source is None:
        return False
    source = str(source).strip()
    if not source or not is_http_url(source):
        return False
    lower = source.lower()
    return any(hint in lower for hint in _LIVE_URL_HINTS)


def _playback_position_seconds(chat_id: int, *, now: datetime | None = None) -> int:
    active = _active_calls.get(chat_id) or {}
    base = int(active.get("position_base_seconds") or 0)
    if active.get("is_paused"):
        return max(0, base)
    started_at = active.get("position_started_at") or active.get("started_at")
    if started_at is None:
        return max(0, base)
    if getattr(started_at, "tzinfo", None) is None:
        started_at = started_at.replace(tzinfo=timezone.utc)
    current_time = now or _speed_now()
    elapsed = max(0.0, (current_time - started_at).total_seconds())
    speed_percent = _playback_speed_states.get(
        chat_id,
        int(active.get("speed_percent") or PLAYBACK_SPEED_DEFAULT),
    )
    return max(0, int(base + (elapsed * speed_percent / 100)))


def _set_position_anchor(
    chat_id: int,
    *,
    position_seconds: int = 0,
    now: datetime | None = None,
) -> None:
    if chat_id not in _active_calls:
        return
    _active_calls[chat_id]["position_base_seconds"] = max(0, int(position_seconds or 0))
    _active_calls[chat_id]["position_started_at"] = now or _speed_now()


def _trusted_local_speed_source(source: str | None) -> str | None:
    source = normalize_media_source(source)
    if not source or is_http_url(source):
        return None
    trusted = ensure_trusted_local_media_path(source)
    return str(trusted) if trusted is not None else None


def _cached_speed_source_for_url(url: str | None, media_type: str) -> str | None:
    if not url or _is_live_stream_url(url):
        return None
    try:
        from app.services.media_cache import cache_lookup

        cached = cache_lookup(url, media_type)
    except Exception:
        logger.debug("speed media-cache lookup failed", exc_info=True)
        return None
    return _trusted_local_speed_source(cached)


def _speed_original_source_for_active(active: dict[str, Any], media_type: str) -> str | None:
    local_source = _trusted_local_speed_source(active.get("speed_original_source"))
    if local_source is not None:
        return local_source

    local_source = _trusted_local_speed_source(active.get("source"))
    if local_source is not None:
        return local_source

    source_url = _source_url_for_speed(
        active.get("source_url"),
        active.get("event_source"),
        active.get("speed_original_source"),
        active.get("source"),
    )
    cached = _cached_speed_source_for_url(source_url, media_type)
    if cached is not None:
        return cached
    return None


async def _acquire_join_slot(chat_id: int) -> bool:
    """Reserve a per-chat join slot; return False when another join is in-flight."""
    async with _join_progress_guard:
        if chat_id in _join_in_progress:
            return False
        _join_in_progress.add(chat_id)
        return True


async def _release_join_slot(chat_id: int) -> None:
    """Release a per-chat join slot."""
    async with _join_progress_guard:
        _join_in_progress.discard(chat_id)


async def _ensure_group_call_before_play(
    chat_id: int,
    helper_id: int,
    call_py: Any,
) -> bool:
    """Ensure an active group call exists on the pooled helper session before ``play()``."""
    from app.config.settings import settings

    app = getattr(call_py, "_app", None)
    if app is None:
        logger.warning(
            "playback group call preflight missing _app chat_id=%s helper_id=%s preflight=failed",
            chat_id,
            helper_id,
        )
        return False

    get_input_call = getattr(app, "get_input_call", None)
    if not callable(get_input_call):
        logger.warning(
            "playback group call preflight get_input_call unavailable chat_id=%s helper_id=%s preflight=failed",
            chat_id,
            helper_id,
        )
        return False

    try:
        input_call = await get_input_call(chat_id)
    except Exception:
        logger.debug(
            "get_input_call failed chat_id=%s helper_id=%s",
            chat_id,
            helper_id,
            exc_info=True,
        )
        input_call = None

    if input_call is not None:
        logger.info(
            "playback group call preflight chat_id=%s helper_id=%s preflight=existing",
            chat_id,
            helper_id,
        )
        settle = max(0.0, float(settings.VC_JOIN_SETTLE_SECONDS))
        if settle > 0:
            await asyncio.sleep(settle)
        return True

    create_group_call = getattr(app, "create_group_call", None)
    if not callable(create_group_call):
        logger.warning(
            "playback group call create unavailable chat_id=%s helper_id=%s preflight=failed",
            chat_id,
            helper_id,
        )
        return False

    try:
        await create_group_call(chat_id)
    except Exception as exc:
        logger.warning(
            "playback group call create failed chat_id=%s helper_id=%s exc=%s preflight=failed",
            chat_id,
            helper_id,
            type(exc).__name__,
        )
        return False

    settle = max(0.0, float(settings.GROUP_CALL_CREATE_SETTLE_SECONDS))
    if settle > 0:
        await asyncio.sleep(settle)

    try:
        input_call = await get_input_call(chat_id)
    except Exception:
        logger.debug(
            "get_input_call verify failed chat_id=%s helper_id=%s",
            chat_id,
            helper_id,
            exc_info=True,
        )
        input_call = None

    if input_call is None:
        logger.warning(
            "playback group call cache empty after create chat_id=%s helper_id=%s preflight=failed",
            chat_id,
            helper_id,
        )
        return False

    logger.info(
        "playback group call preflight chat_id=%s helper_id=%s preflight=created",
        chat_id,
        helper_id,
    )
    return True


def _add_call_local_paths(paths: set[str], raw: str | None) -> None:
    """Add a trusted local path and its transcode siblings to a protection set."""
    source = normalize_media_source(raw)
    if not source or is_http_url(source):
        return
    paths.add(source)
    try:
        base = Path(source)
    except (TypeError, ValueError):
        return
    for suffix in (".tc.ogg", ".tc.mp4"):
        sibling = base.with_suffix(suffix)
        trusted = ensure_trusted_local_media_path(sibling)
        if trusted is not None:
            paths.add(str(trusted))


def _same_normalized_source(left: str | None, right: str | None) -> bool:
    return normalize_media_source(left) == normalize_media_source(right)


def _cleanup_speed_derivatives_if_unused(
    source: str | None,
    *,
    except_chat_id: int | None = None,
) -> None:
    source = normalize_media_source(source)
    if not source or is_http_url(source):
        return
    for chat_id, call_data in _active_calls.items():
        if chat_id == except_chat_id:
            continue
        other_source = call_data.get("speed_original_source") or call_data.get("source")
        if _same_normalized_source(other_source, source):
            return
    try:
        from app.services.transcode_pool import cleanup_speed_derivatives

        cleanup_speed_derivatives(source)
    except Exception:
        logger.debug("speed derivative cleanup failed", exc_info=True)


def _reset_playback_speed_state(
    chat_id: int,
    *,
    source: str | None = None,
    cleanup_previous: bool = False,
) -> None:
    previous = _active_calls.get(chat_id) or {}
    previous_source = previous.get("speed_original_source") or previous.get("source")
    _playback_speed_states[chat_id] = PLAYBACK_SPEED_DEFAULT
    if cleanup_previous and previous_source and not _same_normalized_source(previous_source, source):
        _cleanup_speed_derivatives_if_unused(previous_source, except_chat_id=chat_id)


def _clear_playback_speed_state(chat_id: int, *, cleanup_derivatives: bool = False) -> None:
    call_data = _active_calls.get(chat_id) or {}
    source = call_data.get("speed_original_source") or call_data.get("source")
    _playback_speed_states.pop(chat_id, None)
    _playback_speed_locks.pop(chat_id, None)
    if cleanup_derivatives:
        _cleanup_speed_derivatives_if_unused(source, except_chat_id=chat_id)


def _cleanup_source_file(source: str | None) -> None:
    """Delete downloaded media file from disk immediately after use."""
    source = normalize_media_source(source)
    if not source or is_http_url(source):
        return
    try:
        from pathlib import Path
        p = Path(source)
        if p.is_file():
            p.unlink(missing_ok=True)
            logger.debug("Cleaned up source file: %s", p.name)
    except Exception:
        pass


class CallService:
    PLAYBACK_SPEED_DEFAULT = PLAYBACK_SPEED_DEFAULT
    PLAYBACK_SPEED_MIN = PLAYBACK_SPEED_MIN
    PLAYBACK_SPEED_MAX = PLAYBACK_SPEED_MAX
    PLAYBACK_SPEED_STEP = PLAYBACK_SPEED_STEP
    PLAYBACK_SEEK_DEFAULT_SECONDS = PLAYBACK_SEEK_DEFAULT_SECONDS

    @staticmethod
    def pop_join_failure_key() -> str | None:
        """Return and clear the i18n key for the most recent join_voice_chat failure."""
        global _last_join_failure_key
        key = _last_join_failure_key
        _last_join_failure_key = None
        return key

    @staticmethod
    def pop_leave_failure_key() -> str | None:
        """Return and clear the i18n key for the most recent leave_voice_chat failure."""
        global _last_leave_failure_key
        key = _last_leave_failure_key
        _last_leave_failure_key = None
        return key

    @staticmethod
    async def join_voice_chat(
        call_py,
        chat_id: int,
        source: str,
        media_type: str = "audio",
        *,
        user_id: int | None = None,
        title: str | None = None,
        event_source: str | None = None,
        source_kind_hint: str | None = None,
        skip_event_tracking: bool = False,
        playback_feature: str | None = None,
        cleanup_local_source_on_failure: bool = False,
        resume_from_seconds: int = 0,
        resume_paused: bool = False,
        duration_seconds: int = 0,
    ) -> bool:
        helper_id: int | None = None
        call_active = False
        active_call_py: Any | None = None
        temp_cleanup_path: str | None = None
        original_url = _source_url_for_speed(event_source, source)
        if cleanup_local_source_on_failure:
            normalized = normalize_media_source(source)
            if normalized and not is_http_url(normalized):
                temp_cleanup_path = normalized
        if not await _acquire_join_slot(chat_id):
            return _set_join_failure("playback_cmd.already_playing")
        try:
            _clear_join_failure()

            from app.services.helper_pytgcalls_pool import pytgcalls_available

            if not pytgcalls_available():
                logger.warning("PyTgCalls unavailable — cannot join voice chat %s", chat_id)
                return _set_join_failure("playback_cmd.voice_chat_unavailable")

            from app.services.media_capability_service import (
                denial_message_key,
                feature_from_media_type,
                is_chat_video_enabled,
                is_media_feature_allowed,
            )

            if media_type == "video" and not await is_chat_video_enabled(chat_id):
                logger.info(
                    "Blocked video playback for chat %s (group video disabled)",
                    chat_id,
                )
                return _set_join_failure("playback_cmd.video_disabled_in_group")

            feature = playback_feature or feature_from_media_type(media_type)
            if not await is_media_feature_allowed(chat_id, feature):
                logger.info(
                    "Blocked %s playback for chat %s (free-mode restriction)",
                    feature,
                    chat_id,
                )
                return _set_join_failure(denial_message_key(feature))

            source = await resolve_media_source_for_playback(source, chat_id=chat_id)
            if source is None:
                logger.warning("Rejected unsafe media source for chat %s", chat_id)
                return _set_join_failure("playback_cmd.invalid_source")

            ensure_result = await _ensure_helper_in_chat(chat_id)
            helper_id = (
                ensure_result.helper_id
                if isinstance(ensure_result, HelperChatEnsureResult)
                else ensure_result
            )
            if helper_id is None:
                return _set_join_failure("playback_cmd.helper_unavailable")

            from app.services.transcode_pool import pre_transcode, pre_transcode_speed

            applied_seek = 0
            resume_target = max(0, int(resume_from_seconds or 0))
            tc_source: str | None = None
            if resume_target > 0:
                seeked_source = normalize_media_source(
                    await pre_transcode_speed(
                        source,
                        PLAYBACK_SPEED_DEFAULT,
                        media_type=media_type,
                        start_at_seconds=resume_target,
                    )
                )
                if seeked_source is not None:
                    tc_source = seeked_source
                    applied_seek = resume_target
                    logger.info(
                        "Recovery resume: chat %s stream starting at %ds",
                        chat_id,
                        applied_seek,
                    )
                else:
                    logger.warning(
                        "Recovery resume: chat %s source non-seekable/remote — "
                        "restarting from 0 instead of %ds",
                        chat_id,
                        resume_target,
                    )
            if tc_source is None:
                equalizer = await _chat_equalizer_preset(chat_id)
                if equalizer != "normal":
                    # MISC-07: route through the filter-capable path so the chat's
                    # equalizer preset applies to newly started playback too.
                    tc_source = normalize_media_source(
                        await pre_transcode_speed(
                            source,
                            PLAYBACK_SPEED_DEFAULT,
                            media_type=media_type,
                            equalizer=equalizer,
                        )
                    )
                if tc_source is None:
                    tc_source = normalize_media_source(await pre_transcode(source, media_type))
            if tc_source is None:
                logger.warning("Rejected unsafe transcoded media source for chat %s", chat_id)
                return _set_join_failure("playback_cmd.invalid_source")

            active_call_py = await _resolve_call_py(
                chat_id,
                helper_id=helper_id,
                fallback=call_py,
            )
            if active_call_py is None:
                logger.warning(
                    "No helper PyTgCalls available for chat %s helper_id=%s",
                    chat_id,
                    helper_id,
                )
                return _set_join_failure("playback_cmd.voice_chat_unavailable")

            if not await _ensure_group_call_before_play(
                chat_id,
                helper_id,
                active_call_py,
            ):
                return _set_join_failure("playback_cmd.voice_chat_connect_failed")

            from app.utils.voice_stack import build_audio_stream, build_video_stream, vc_join

            if media_type == "video":
                stream = build_video_stream(tc_source, log_failures=True)
            else:
                stream = build_audio_stream(tc_source, log_failures=True)

            if stream is None:
                logger.error("Could not build stream for %s", source)
                return _set_join_failure("playback_cmd.stream_unavailable")

            await vc_join(active_call_py, chat_id, stream)

            now = _speed_now()
            _reset_playback_speed_state(
                chat_id,
                source=source,
                cleanup_previous=True,
            )
            _active_calls[chat_id] = {
                "source": source,
                "speed_original_source": source,
                "speed_derivative_source": None,
                "speed_percent": PLAYBACK_SPEED_DEFAULT,
                "source_url": original_url,
                "stream_source": source if is_http_url(source) else None,
                "media_type": media_type,
                "title": title,
                "requester_id": user_id,
                "playback_feature": playback_feature,
                "helper_id": helper_id,
                "started_at": now,
                "position_base_seconds": applied_seek,
                "position_started_at": now,
                "duration_seconds": max(0, int(duration_seconds or 0)),
                "last_activity": now,
                "is_paused": False,
                "download_task": None,
            }
            call_active = True

            await _save_playback_state(
                chat_id, media_type, source, is_paused=resume_paused,
            )

            from app.services.seek_tracker import start_seek_tracker
            await start_seek_tracker(chat_id, initial_seek=applied_seek)

            if resume_paused:
                await CallService.pause(active_call_py, chat_id)

            _trigger_prefetch(chat_id)

            if not skip_event_tracking:
                from app.services.media_event_service import track_media_play

                await track_media_play(
                    event_source or source,
                    media_type=media_type,
                    chat_id=chat_id,
                    user_id=user_id,
                    title=title,
                    source_kind_hint=source_kind_hint,
                )

            _clear_join_failure()
            try:
                from app.handlers.call_security_runtime import on_call_started

                await on_call_started(chat_id)
            except Exception:
                logger.debug("call_security on_call_started hook failed chat_id=%s", chat_id)
            return True
        except Exception as exc:
            logger.exception("Failed to join voice chat %s", chat_id)
            if active_call_py is not None:
                try:
                    from app.utils.voice_stack import vc_leave

                    await vc_leave(active_call_py, chat_id)
                except Exception:
                    logger.debug(
                        "vc_leave after join failure ignored chat_id=%s",
                        chat_id,
                        exc_info=True,
                    )
            await _maybe_quarantine_helper(chat_id, exc)
            return _set_join_failure(_join_failure_key_for_exception(exc))
        finally:
            if helper_id is not None and not call_active:
                from app.services.helper_pool_service import HelperPoolService

                await HelperPoolService.release_helper_reservation(helper_id)
            if temp_cleanup_path and not call_active:
                cleanup_temp_playback_local_source(
                    temp_cleanup_path,
                    reason="join_voice_chat_failed",
                )
            await _release_join_slot(chat_id)

    @staticmethod
    async def leave_voice_chat(call_py, chat_id: int) -> bool:
        _clear_leave_failure()

        from app.services.seek_tracker import stop_seek_tracker
        stop_seek_tracker(chat_id)

        _cancel_prefetch(chat_id)

        from app.utils.voice_stack import vc_leave

        call_data_before_leave = _active_calls.get(chat_id)
        active_call_py = await _resolve_call_py(chat_id, fallback=call_py)
        if active_call_py is None:
            if call_data_before_leave is not None:
                logger.warning(
                    "leave_voice_chat could not resolve active call object chat_id=%s",
                    chat_id,
                )
                return _set_leave_failure("playback_cmd.failed")
            return _set_leave_failure("playback_cmd.no_voice_chat")

        try:
            await vc_leave(active_call_py, chat_id)
        except Exception:
            logger.exception("leave_voice_chat failed for %s", chat_id)
            return _set_leave_failure("playback_cmd.failed")

        call_data = _active_calls.pop(chat_id, None)
        source = call_data.get("source") if call_data else None
        speed_source = (
            call_data.get("speed_original_source")
            if call_data
            else None
        )
        _clear_playback_speed_state(chat_id, cleanup_derivatives=False)

        if call_data is not None:
            try:
                from app.handlers.call_security_runtime import on_call_ended

                await on_call_ended(chat_id)
            except Exception:
                logger.debug("call_security on_call_ended hook failed chat_id=%s", chat_id)

        proc = _ffmpeg_processes.pop(chat_id, None)
        if proc is not None:
            try:
                proc.kill()
            except ProcessLookupError:
                pass

        from app.services.transcode_pool import cleanup_transcoded
        if source:
            cleanup_transcoded(source)
        if speed_source:
            _cleanup_speed_derivatives_if_unused(speed_source, except_chat_id=chat_id)
        _cleanup_source_file(source)

        pf = _prefetch_cache.pop(chat_id, None)
        if pf and pf.get("path"):
            cleanup_transcoded(pf["path"])

        from app.services.helper_pool_service import HelperPoolService
        await HelperPoolService.decrement_active_calls(chat_id)

        await _remove_playback_state(chat_id)
        _playback_history.pop(chat_id, None)
        _clear_leave_failure()
        return True

    @staticmethod
    async def pause(call_py, chat_id: int) -> bool:
        from app.utils.voice_stack import vc_pause

        active_call_py = await _resolve_call_py(chat_id, fallback=call_py)
        if active_call_py is None:
            return False

        try:
            await vc_pause(active_call_py, chat_id)
            if chat_id in _active_calls:
                position = _playback_position_seconds(chat_id)
                _active_calls[chat_id]["is_paused"] = True
                _set_position_anchor(chat_id, position_seconds=position)
            await _update_playback_pause(chat_id, True)
            return True
        except Exception:
            logger.exception("Failed to pause %s", chat_id)
            return False

    @staticmethod
    async def resume(call_py, chat_id: int) -> bool:
        from app.utils.voice_stack import vc_resume

        active_call_py = await _resolve_call_py(chat_id, fallback=call_py)
        if active_call_py is None:
            return False

        try:
            await vc_resume(active_call_py, chat_id)
            if chat_id in _active_calls:
                _active_calls[chat_id]["is_paused"] = False
                _set_position_anchor(
                    chat_id,
                    position_seconds=int(_active_calls[chat_id].get("position_base_seconds") or 0),
                )
            await _update_playback_pause(chat_id, False)
            return True
        except Exception:
            logger.exception("Failed to resume %s", chat_id)
            return False

    @staticmethod
    async def set_volume(call_py, chat_id: int, volume: int) -> bool:
        volume = max(1, min(200, volume))
        active_call_py = await _resolve_call_py(chat_id, fallback=call_py)
        if active_call_py is None:
            return False

        try:
            await active_call_py.change_volume_call(chat_id, volume)
            return True
        except AttributeError:
            try:
                await active_call_py.change_stream(chat_id, volume=volume)
                return True
            except Exception:
                logger.exception("Failed to set volume for %s", chat_id)
                return False
        except Exception:
            logger.exception("Failed to set volume for %s", chat_id)
            return False

    @staticmethod
    def get_playback_speed(chat_id: int) -> int:
        return _playback_speed_states.get(chat_id, PLAYBACK_SPEED_DEFAULT)

    @staticmethod
    async def seek_playback(
        call_py,
        chat_id: int,
        delta_seconds: int,
    ) -> PlaybackSeekResult:
        """Seek active finite media by replacing the stream from a new offset.

        The operation uses the same source safety model as speed control:
        trusted local finite media or an already-cached finite URL original only.
        State is updated only after ``vc_change_stream`` succeeds.
        """
        lock = _speed_lock_for_chat(chat_id)
        async with lock:
            active = _active_calls.get(chat_id)
            if active is None:
                return _seek_result(
                    "no_active",
                    "playback_cmd.no_voice_chat",
                    show_alert=True,
                )

            delta_seconds = int(delta_seconds or 0)
            if delta_seconds == 0:
                return _seek_result(
                    "invalid",
                    "playback.controls.seek_invalid_seconds",
                    position_seconds=_playback_position_seconds(chat_id),
                    show_alert=True,
                )

            media_type = str(active.get("media_type") or "audio")
            playback_feature = str(active.get("playback_feature") or "")
            source_url = _source_url_for_speed(
                active.get("source_url"),
                active.get("event_source"),
                active.get("source"),
            )
            current_position = _playback_position_seconds(chat_id)
            if playback_feature in _LIVE_PLAYBACK_FEATURES or _is_live_stream_url(source_url):
                return _seek_result(
                    "unsupported",
                    "playback.controls.seek_live_unsupported",
                    position_seconds=current_position,
                    show_alert=True,
                )
            if media_type not in {"audio", "video"}:
                return _seek_result(
                    "unsupported",
                    "playback.controls.seek_unsupported_media",
                    position_seconds=current_position,
                    show_alert=True,
                )

            original_source = _speed_original_source_for_active(active, media_type)
            if original_source is None:
                return _seek_result(
                    "unsupported",
                    "playback.controls.seek_source_unavailable",
                    position_seconds=current_position,
                    show_alert=True,
                )

            duration_seconds = int(active.get("duration_seconds") or 0)
            target_position = max(0, current_position + delta_seconds)
            if duration_seconds > 0:
                target_position = min(target_position, max(0, duration_seconds - 1))
            if target_position == current_position:
                key = (
                    "playback.controls.seek_end"
                    if delta_seconds > 0
                    else "playback.controls.seek_start"
                )
                return _seek_result(
                    "boundary",
                    key,
                    position_seconds=current_position,
                )

            active_call_py = await _resolve_call_py(
                chat_id,
                helper_id=active.get("helper_id"),
                fallback=call_py,
            )
            if active_call_py is None:
                return _seek_result(
                    "no_active",
                    "playback_cmd.no_voice_chat",
                    position_seconds=current_position,
                    show_alert=True,
                )

            current_speed = _playback_speed_states.get(
                chat_id,
                int(active.get("speed_percent") or PLAYBACK_SPEED_DEFAULT),
            )
            try:
                from app.services.transcode_pool import pre_transcode_speed
                from app.utils.voice_stack import build_audio_stream, build_video_stream, vc_change_stream

                seek_source = await pre_transcode_speed(
                    original_source,
                    current_speed,
                    media_type=media_type,
                    start_at_seconds=target_position,
                    **await _equalizer_kwargs(chat_id),
                )
                seek_source = normalize_media_source(seek_source)
                if seek_source is None:
                    return _seek_result(
                        "failed",
                        "playback.controls.seek_failed",
                        position_seconds=current_position,
                        show_alert=True,
                    )

                stream = (
                    build_video_stream(seek_source, log_failures=True)
                    if media_type == "video"
                    else build_audio_stream(seek_source, log_failures=True)
                )
                if stream is None:
                    return _seek_result(
                        "failed",
                        "playback.controls.seek_failed",
                        position_seconds=current_position,
                        show_alert=True,
                    )

                await vc_change_stream(active_call_py, chat_id, stream)
            except Exception:
                logger.exception("Failed to seek playback for %s", chat_id)
                return _seek_result(
                    "failed",
                    "playback.controls.seek_failed",
                    position_seconds=current_position,
                    show_alert=True,
                )

            if chat_id in _active_calls:
                _active_calls[chat_id]["speed_original_source"] = original_source
                _active_calls[chat_id]["speed_derivative_source"] = (
                    seek_source if seek_source != original_source else None
                )
                _active_calls[chat_id]["speed_percent"] = current_speed
                now = _speed_now()
                _active_calls[chat_id]["last_activity"] = now
                _set_position_anchor(
                    chat_id,
                    position_seconds=target_position,
                    now=now,
                )
            return _seek_result(
                "changed",
                "playback.controls.seek_set_position",
                position_seconds=target_position,
            )

    @staticmethod
    async def change_playback_speed(
        call_py,
        chat_id: int,
        delta: int,
    ) -> PlaybackSpeedChangeResult:
        """Change active finite media speed by replacing the stream.

        Trusted local finite audio/video and already-cached URL media are
        supported.  State is updated only after ``vc_change_stream`` succeeds.
        """
        lock = _speed_lock_for_chat(chat_id)
        async with lock:
            active = _active_calls.get(chat_id)
            if active is None:
                return _speed_result(
                    "no_active",
                    "playback_cmd.no_voice_chat",
                    show_alert=True,
                )

            current = _playback_speed_states.get(
                chat_id,
                int(active.get("speed_percent") or PLAYBACK_SPEED_DEFAULT),
            )
            target = max(
                PLAYBACK_SPEED_MIN,
                min(PLAYBACK_SPEED_MAX, current + delta),
            )
            if target == current:
                key = (
                    "playback.controls.speed_max"
                    if delta > 0
                    else "playback.controls.speed_min"
                )
                return _speed_result("boundary", key, speed_percent=current)

            media_type = str(active.get("media_type") or "audio")
            playback_feature = str(active.get("playback_feature") or "")
            source_url = _source_url_for_speed(
                active.get("source_url"),
                active.get("event_source"),
                active.get("source"),
            )
            if playback_feature in _LIVE_PLAYBACK_FEATURES or _is_live_stream_url(source_url):
                return _speed_result(
                    "unsupported",
                    "playback.controls.speed_live_unsupported",
                    speed_percent=current,
                    show_alert=True,
                )
            if media_type not in {"audio", "video"}:
                return _speed_result(
                    "unsupported",
                    "playback.controls.speed_unsupported_media",
                    speed_percent=current,
                    show_alert=True,
                )

            original_source = _speed_original_source_for_active(active, media_type)
            if original_source is None:
                return _speed_result(
                    "unsupported",
                    "playback.controls.speed_source_unavailable",
                    speed_percent=current,
                    show_alert=True,
                )

            active_call_py = await _resolve_call_py(
                chat_id,
                helper_id=active.get("helper_id"),
                fallback=call_py,
            )
            if active_call_py is None:
                return _speed_result(
                    "no_active",
                    "playback_cmd.no_voice_chat",
                    speed_percent=current,
                    show_alert=True,
                )

            try:
                from app.services.transcode_pool import pre_transcode_speed
                from app.utils.voice_stack import build_audio_stream, build_video_stream, vc_change_stream

                position_seconds = _playback_position_seconds(chat_id)
                speed_source = await pre_transcode_speed(
                    original_source,
                    target,
                    media_type=media_type,
                    start_at_seconds=position_seconds,
                    **await _equalizer_kwargs(chat_id),
                )
                speed_source = normalize_media_source(speed_source)
                if speed_source is None:
                    return _speed_result(
                        "failed",
                        "playback.controls.speed_change_failed",
                        speed_percent=current,
                        show_alert=True,
                    )

                stream = (
                    build_video_stream(speed_source, log_failures=True)
                    if media_type == "video"
                    else build_audio_stream(speed_source, log_failures=True)
                )
                if stream is None:
                    return _speed_result(
                        "failed",
                        "playback.controls.speed_change_failed",
                        speed_percent=current,
                        show_alert=True,
                    )

                await vc_change_stream(active_call_py, chat_id, stream)
            except Exception:
                logger.exception("Failed to change playback speed for %s", chat_id)
                return _speed_result(
                    "failed",
                    "playback.controls.speed_change_failed",
                    speed_percent=current,
                    show_alert=True,
                )

            _playback_speed_states[chat_id] = target
            if chat_id in _active_calls:
                _active_calls[chat_id]["speed_original_source"] = original_source
                _active_calls[chat_id]["speed_derivative_source"] = (
                    speed_source if speed_source != original_source else None
                )
                _active_calls[chat_id]["speed_percent"] = target
                now = _speed_now()
                _active_calls[chat_id]["last_activity"] = now
                _set_position_anchor(
                    chat_id,
                    position_seconds=position_seconds,
                    now=now,
                )
            return _speed_result(
                "changed",
                "playback.controls.speed_set_position",
                speed_percent=target,
            )

    @staticmethod
    async def play_next(call_py, chat_id: int) -> bool:
        active_call_py = await _resolve_call_py(chat_id, fallback=call_py)
        if active_call_py is None:
            return False

        if _repeat_states.get(chat_id, False) and chat_id in _active_calls:
            source = _active_calls[chat_id].get("source")
            media_type = _active_calls[chat_id].get("media_type", "audio")
            if source:
                from app.services.media_capability_service import (
                    feature_from_media_type,
                    is_media_feature_allowed,
                )

                if not await is_media_feature_allowed(
                    chat_id,
                    feature_from_media_type(media_type),
                ):
                    await CallService.leave_voice_chat(active_call_py, chat_id)
                    return False
                try:
                    source = await resolve_media_source_for_playback(source, chat_id=chat_id)
                    if source is None:
                        raise ValueError("unsafe media source")
                    from app.utils.voice_stack import build_audio_stream, build_video_stream, vc_change_stream

                    stream = (
                        build_video_stream(source, log_failures=True)
                        if media_type == "video"
                        else build_audio_stream(source, log_failures=True)
                    )
                    if stream:
                        await vc_change_stream(active_call_py, chat_id, stream)
                        _reset_playback_speed_state(chat_id, source=source, cleanup_previous=True)
                        if chat_id in _active_calls:
                            _active_calls[chat_id]["speed_original_source"] = source
                            _active_calls[chat_id]["speed_derivative_source"] = None
                            _active_calls[chat_id]["speed_percent"] = PLAYBACK_SPEED_DEFAULT
                            _set_position_anchor(chat_id, position_seconds=0)
                        return True
                except Exception:
                    logger.debug("Repeat replay failed for %s, falling through", chat_id)

        current_call = _active_calls.get(chat_id)
        if current_call:
            current_source = normalize_media_source(current_call.get("source"))
            current_media_type = str(current_call.get("media_type", "audio"))
            if current_source:
                history = _playback_history.setdefault(chat_id, [])
                history.append(
                    {
                        "source": current_source,
                        "media_type": current_media_type,
                        "title": current_call.get("title"),
                        "requester_id": current_call.get("requester_id"),
                        "duration_seconds": current_call.get("duration_seconds"),
                        "playback_feature": current_call.get("playback_feature"),
                    }
                )
                if len(history) > 20:
                    del history[:-20]

        _cancel_prefetch(chat_id)

        next_item = None
        source = None
        existing_helper_id = (_active_calls.get(chat_id) or {}).get("helper_id")
        for _ in range(_MAX_UNSAFE_QUEUE_SKIPS):
            next_item = await playlist_repo.advance_queue(chat_id)
            if next_item is None:
                await CallService.leave_voice_chat(active_call_py, chat_id)
                return False

            stream_url = getattr(next_item, "stream_url", None)
            file_path = getattr(next_item, "file_path", None)
            title = getattr(next_item, "title", None)
            raw_source = stream_url or file_path
            if raw_source is None:
                logger.warning(
                    "Skipping queued item with no source for chat %s (title=%s)",
                    chat_id,
                    (title or "unknown")[:40],
                )
                continue

            source = await resolve_media_source_for_playback(raw_source, chat_id=chat_id)
            if source is None:
                logger.warning(
                    "Skipping unsafe queued item for chat %s (title=%s)",
                    chat_id,
                    (title or "unknown")[:40],
                )
                continue

            from app.services.media_capability_service import (
                feature_from_media_type,
                is_media_feature_allowed,
            )

            queue_media_type = getattr(next_item, "media_type", None) or "audio"
            if not await is_media_feature_allowed(
                chat_id,
                feature_from_media_type(queue_media_type),
            ):
                logger.info(
                    "Skipping queued %s item for chat %s (free-mode restriction)",
                    queue_media_type,
                    chat_id,
                )
                continue
            break
        else:
            logger.warning(
                "Exceeded unsafe queue skip limit (%d) for chat %s",
                _MAX_UNSAFE_QUEUE_SKIPS,
                chat_id,
            )
            await CallService.leave_voice_chat(active_call_py, chat_id)
            return False

        proc = _ffmpeg_processes.pop(chat_id, None)
        if proc is not None:
            try:
                proc.kill()
            except ProcessLookupError:
                pass

        stream_url = getattr(next_item, "stream_url", None)
        file_path = getattr(next_item, "file_path", None)
        title = getattr(next_item, "title", None)
        duration_seconds = int(getattr(next_item, "duration_seconds", 0) or 0)
        requester_id = getattr(next_item, "added_by", None)
        media_type = getattr(next_item, "media_type", None) or "audio"

        pf = _prefetch_cache.pop(chat_id, None)
        pf_path = normalize_media_source(pf.get("path")) if pf and pf.get("path") else None
        if pf and pf_path and pf.get("source") == source:
            tc_source = pf_path
            logger.debug("Prefetch HIT for chat %d", chat_id)
        else:
            from app.services.transcode_pool import pre_transcode
            tc_source = await pre_transcode(source, media_type)
            tc_source = normalize_media_source(tc_source)
            if tc_source is None:
                logger.warning("Rejected unsafe transcoded queued media source for chat %s", chat_id)
                await CallService.leave_voice_chat(active_call_py, chat_id)
                return False

        try:
            from app.utils.voice_stack import build_audio_stream, build_video_stream, vc_change_stream

            if media_type == "video":
                stream = build_video_stream(tc_source, log_failures=True)
            else:
                stream = build_audio_stream(tc_source, log_failures=True)

            if stream is None:
                await CallService.leave_voice_chat(active_call_py, chat_id)
                return False

            await vc_change_stream(active_call_py, chat_id, stream)

            _reset_playback_speed_state(chat_id, source=source, cleanup_previous=True)
            source_url = _source_url_for_speed(
                stream_url,
                file_path,
                source,
            )
            now = _speed_now()
            _active_calls[chat_id] = {
                "source": source,
                "speed_original_source": source,
                "speed_derivative_source": None,
                "speed_percent": PLAYBACK_SPEED_DEFAULT,
                "source_url": source_url,
                "stream_source": source if is_http_url(source) else None,
                "media_type": media_type,
                "title": title,
                "duration_seconds": duration_seconds,
                "requester_id": requester_id,
                "helper_id": existing_helper_id,
                "started_at": now,
                "position_base_seconds": 0,
                "position_started_at": now,
                "last_activity": now,
                "is_paused": False,
            }

            await _save_playback_state(
                chat_id, media_type, source,
                is_paused=False, title=title,
            )

            _trigger_prefetch(chat_id)

            from app.services.media_event_service import track_media_play

            await track_media_play(
                stream_url or file_path or source,
                media_type=media_type,
                chat_id=chat_id,
                title=title,
            )

            return True
        except Exception as exc:
            logger.exception("Failed to play next for %s", chat_id)
            await _maybe_quarantine_helper(chat_id, exc)
            await CallService.leave_voice_chat(active_call_py, chat_id)
            return False

    @staticmethod
    async def play_previous(call_py, chat_id: int) -> bool:
        """Play the previous item from in-memory playback history.

        History is populated when ``play_next`` advances from an active item.
        """
        active_call_py = await _resolve_call_py(chat_id, fallback=call_py)
        if active_call_py is None:
            return False

        history = _playback_history.get(chat_id) or []
        if not history:
            return False

        item = history.pop()
        source = await resolve_media_source_for_playback(item.get("source"), chat_id=chat_id)
        if source is None:
            return False

        media_type = item.get("media_type", "audio")
        from app.services.media_capability_service import (
            feature_from_media_type,
            is_media_feature_allowed,
        )

        if not await is_media_feature_allowed(chat_id, feature_from_media_type(media_type)):
            return False

        from app.services.transcode_pool import pre_transcode
        tc_source = await pre_transcode(source, media_type)
        tc_source = normalize_media_source(tc_source)
        if tc_source is None:
            return False

        try:
            from app.utils.voice_stack import build_audio_stream, build_video_stream, vc_change_stream

            if media_type == "video":
                stream = build_video_stream(tc_source, log_failures=True)
            else:
                stream = build_audio_stream(tc_source, log_failures=True)

            if stream is None:
                return False

            await vc_change_stream(active_call_py, chat_id, stream)
            existing_helper_id = (_active_calls.get(chat_id) or {}).get("helper_id")
            _reset_playback_speed_state(chat_id, source=source, cleanup_previous=True)
            source_url = _source_url_for_speed(item.get("source"), source)
            now = _speed_now()
            _active_calls[chat_id] = {
                "source": source,
                "speed_original_source": source,
                "speed_derivative_source": None,
                "speed_percent": PLAYBACK_SPEED_DEFAULT,
                "source_url": source_url,
                "stream_source": source if is_http_url(source) else None,
                "media_type": media_type,
                "title": item.get("title"),
                "duration_seconds": int(item.get("duration_seconds") or 0),
                "requester_id": item.get("requester_id"),
                "playback_feature": item.get("playback_feature"),
                "helper_id": existing_helper_id,
                "started_at": now,
                "position_base_seconds": 0,
                "position_started_at": now,
                "last_activity": now,
                "is_paused": False,
            }
            await _save_playback_state(
                chat_id,
                media_type,
                source,
                is_paused=False,
                title=item.get("title"),
            )
            _trigger_prefetch(chat_id)
            return True
        except Exception:
            logger.exception("Failed to play previous for %s", chat_id)
            return False

    @staticmethod
    def get_active_calls() -> dict[int, dict[str, Any]]:
        return dict(_active_calls)

    @staticmethod
    def is_chat_playing(chat_id: int) -> bool:
        """Return True when this chat has an active in-process voice stream."""
        return chat_id in _active_calls

    @staticmethod
    def is_chat_joining(chat_id: int) -> bool:
        """Return True when a voice-chat join is in progress for this chat."""
        return chat_id in _join_in_progress

    @staticmethod
    def collect_active_local_paths() -> frozenset[str]:
        """Return resolved local paths for active calls and prefetch (not URLs)."""
        paths: set[str] = set()
        for call_data in _active_calls.values():
            _add_call_local_paths(paths, call_data.get("source"))
            _add_call_local_paths(paths, call_data.get("speed_original_source"))
            _add_call_local_paths(paths, call_data.get("speed_derivative_source"))
        for prefetch in _prefetch_cache.values():
            _add_call_local_paths(paths, prefetch.get("path"))
            _add_call_local_paths(paths, prefetch.get("source"))
        return frozenset(paths)

    @staticmethod
    def get_repeat_state(chat_id: int) -> bool:
        return _repeat_states.get(chat_id, False)

    @staticmethod
    def set_repeat_state(chat_id: int, enabled: bool) -> None:
        _repeat_states[chat_id] = enabled

    @staticmethod
    def register_ffmpeg_process(chat_id: int, process: asyncio.subprocess.Process) -> None:
        _ffmpeg_processes[chat_id] = process

    @staticmethod
    def unregister_ffmpeg_process(chat_id: int) -> None:
        _ffmpeg_processes.pop(chat_id, None)

    @staticmethod
    async def clear_persisted_playback_state(chat_id: int) -> None:
        """Remove persisted playback state for a chat (e.g. unsafe recovery source)."""
        await _remove_playback_state(chat_id)


async def _save_playback_state(
    chat_id: int,
    media_type: str,
    source: str,
    is_paused: bool,
    title: str | None = None,
) -> None:
    try:
        from sqlalchemy import select as sa_select

        async with async_session() as session:
            async with session.begin():
                stmt = sa_select(PlaybackState).where(PlaybackState.chat_id == chat_id)
                result = await session.execute(stmt)
                state = result.scalar_one_or_none()

                if state is None:
                    state = PlaybackState(
                        chat_id=chat_id,
                        media_type=media_type,
                        source=source,
                        title=title,
                        is_paused=is_paused,
                    )
                    session.add(state)
                else:
                    state.media_type = media_type
                    state.source = source
                    state.is_paused = is_paused
                    if title is not None:
                        state.title = title
    except Exception as exc:
        logger.warning(
            "Failed to save playback state for chat %s (%s)",
            chat_id,
            type(exc).__name__,
        )


async def _update_playback_pause(chat_id: int, is_paused: bool) -> None:
    try:
        from sqlalchemy import update

        async with async_session() as session:
            async with session.begin():
                stmt = (
                    update(PlaybackState)
                    .where(PlaybackState.chat_id == chat_id)
                    .values(is_paused=is_paused)
                )
                await session.execute(stmt)
    except Exception as exc:
        logger.warning(
            "Failed to update playback pause state for chat %s (%s)",
            chat_id,
            type(exc).__name__,
        )


async def _remove_playback_state(chat_id: int) -> None:
    try:
        from sqlalchemy import delete

        async with async_session() as session:
            async with session.begin():
                stmt = delete(PlaybackState).where(PlaybackState.chat_id == chat_id)
                await session.execute(stmt)
    except Exception as exc:
        logger.warning(
            "Failed to remove playback state for chat %s (%s)",
            chat_id,
            type(exc).__name__,
        )


# ── Prefetch pipeline ────────────────────────────────────────────────────

def _trigger_prefetch(chat_id: int) -> None:
    """Kick off background download + transcode of the next playlist item."""
    old = _prefetch_tasks.pop(chat_id, None)
    if old and not old.done():
        old.cancel()

    try:
        loop = asyncio.get_running_loop()
        task = create_logged_task(_prefetch_next(chat_id), name=f"prefetch_next_{chat_id}")
        _prefetch_tasks[chat_id] = task
    except RuntimeError:
        pass


async def _prefetch_next(chat_id: int) -> None:
    """Download and transcode the next queue item in the background."""
    try:
        items = await playlist_repo.get_queue(chat_id)
        if len(items) < 2:
            return
        next_item = items[1]
        raw_source = next_item.stream_url or next_item.file_path
        if raw_source is None:
            return

        source = await resolve_media_source_for_playback(raw_source, chat_id=chat_id)
        if source is None:
            return

        if is_http_url(source):
            from app.services.media_service import MediaService
            media_type = next_item.media_type or "audio"
            if media_type == "video":
                downloaded = await MediaService.download_video(source, chat_id)
            else:
                downloaded = await MediaService.download_audio(source, chat_id)
            if downloaded is None:
                return
            source = downloaded
            source = normalize_media_source(source)
            if source is None:
                logger.warning("Rejected unsafe downloaded prefetch media source for chat %s", chat_id)
                return

        from app.services.transcode_pool import pre_transcode
        media_type = next_item.media_type or "audio"
        tc_path = await pre_transcode(source, media_type)
        tc_path = normalize_media_source(tc_path)
        if tc_path is None:
            logger.warning("Rejected unsafe prefetch transcode result for chat %s", chat_id)
            return

        _prefetch_cache[chat_id] = {
            "source": next_item.stream_url or next_item.file_path,
            "path": tc_path,
            "media_type": media_type,
        }
        logger.debug("Prefetch ready for chat %d: %s", chat_id,
                      (next_item.title or "unknown")[:40])
    except asyncio.CancelledError:
        pass
    except Exception:
        logger.debug("Prefetch failed for chat %d", chat_id)


def _cancel_prefetch(chat_id: int) -> None:
    """Cancel any in-progress prefetch for a chat."""
    task = _prefetch_tasks.pop(chat_id, None)
    if task and not task.done():
        task.cancel()
    _prefetch_cache.pop(chat_id, None)


_QUARANTINE_ERRORS = (
    "UserRestricted", "UserDeactivatedBan", "PeerFlood", "FloodWait",
    "ChatAdminRequired", "UserBannedInChannel", "PEER_FLOOD",
    "FLOOD_WAIT", "USER_RESTRICTED",
)


def _as_aware_utc(value):
    if value is None:
        return None
    tzinfo = getattr(value, "tzinfo", None)
    if tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _helper_available_for_runtime_log(helper, now: datetime, *, exclude_id: int | None = None) -> bool:
    if exclude_id is not None and getattr(helper, "id", None) == exclude_id:
        return False
    if getattr(helper, "status", None) != "active":
        return False
    current = int(getattr(helper, "current_active_calls", 0) or 0)
    max_calls = int(getattr(helper, "max_concurrent_calls", 0) or 0)
    if max_calls <= 0 or current >= max_calls:
        return False
    cooldown_until = _as_aware_utc(getattr(helper, "cooldown_until", None))
    if cooldown_until is not None and cooldown_until > now:
        return False
    banned_until = _as_aware_utc(getattr(helper, "banned_until", None))
    if banned_until is not None and banned_until > now:
        return False
    return True


async def _remaining_helper_availability_count(exclude_id: int | None = None) -> int:
    try:
        from app.services.helper_pool_service import HelperPoolService

        helpers = await HelperPoolService.get_all_helpers()
        now = datetime.now(timezone.utc)
        return sum(
            1 for helper in helpers
            if _helper_available_for_runtime_log(helper, now, exclude_id=exclude_id)
        )
    except Exception:
        logger.debug("helper availability count failed", exc_info=True)
        return -1


@dataclass(frozen=True)
class HelperChatEnsureResult:
    """Outcome of reserving a helper and attempting a group join."""

    helper_id: int | None = None
    last_join_result: Any | None = None


_NO_QUARANTINE_JOIN_REASONS = frozenset({
    "invite_link_failed",
    "bot_unavailable",
    "channel_invalid",
})


def _should_quarantine_join_failure(join_result) -> bool:
    """Return True when a failed join should put the helper in quarantine."""
    if join_result.ok:
        return False
    if join_result.reason in _NO_QUARANTINE_JOIN_REASONS:
        return False
    if join_result.exception_type == "UserAlreadyParticipant":
        return False
    return True


def _public_present_result_from_join(join_result) -> str:
    """Map a detailed join failure to a public ensure-present status string."""
    mapping = {
        "invite_link_failed": "invite_failed",
        "bot_unavailable": "bot_unavailable",
        "channel_invalid": "channel_invalid",
    }
    return mapping.get(join_result.reason, "failed")


async def _ensure_helper_in_chat(
    chat_id: int,
    max_retries: int = 2,
    *,
    bot_client=None,
) -> HelperChatEnsureResult:
    """Pre-stream check: reserve a helper and ensure it is joined to the chat.

    Returns ``HelperChatEnsureResult`` with the bound helper id on success.
    Active-call count is reserved in DB before Telegram work; failed attempts
    release the reservation before optional quarantine/retry.
    """
    from app.services.helper_pool_service import HelperJoinResult, HelperPoolService
    from app.repositories import helper_event_repo
    from app.services.helper_pytgcalls_pool import HelperPyTgCallsPool

    last_join_result: HelperJoinResult | None = None

    for attempt in range(max_retries):
        reserved = await HelperPoolService.reserve_best_helper(chat_id)
        if reserved is None:
            logger.warning("No available helper for chat %d", chat_id)
            return HelperChatEnsureResult(helper_id=None, last_join_result=last_join_result)

        pooled_call_py = await HelperPyTgCallsPool.get_for_helper(reserved.id)
        if pooled_call_py is None:
            logger.warning(
                "Helper PyTgCalls pool unavailable helper_id=%s chat_id=%s",
                reserved.id,
                chat_id,
            )
            await HelperPoolService.release_helper_reservation(reserved.id)
            return HelperChatEnsureResult(helper_id=None, last_join_result=last_join_result)

        pooled_client = await HelperPyTgCallsPool.get_client_for_helper(reserved.id)
        if pooled_client is None:
            logger.warning(
                "Helper pooled client unavailable helper_id=%s chat_id=%s",
                reserved.id,
                chat_id,
            )
            await HelperPoolService.release_helper_reservation(reserved.id)
            return HelperChatEnsureResult(helper_id=None, last_join_result=last_join_result)

        join_result = await HelperPoolService.ensure_helper_joined_with_client(
            reserved.id,
            chat_id,
            pooled_client,
            bot_client=bot_client,
        )
        last_join_result = join_result
        if join_result.ok:
            await HelperPoolService.bind_chat_to_helper(chat_id, reserved.id)
            await helper_event_repo.log_event(
                "helper.join_chat", actor="runtime",
                helper_account_id=reserved.id, chat_id=chat_id,
            )
            from app.services.bot_update_service import get_runtime_bot
            from app.services.helper_admin_service import ensure_helper_call_admin

            admin_result = await ensure_helper_call_admin(
                bot_client or get_runtime_bot(),
                chat_id,
                reserved.id,
            )
            if not admin_result.ok:
                logger.warning(
                    "Helper %d joined chat %d but promote failed reason=%s",
                    reserved.id,
                    chat_id,
                    admin_result.reason,
                )
            return HelperChatEnsureResult(helper_id=reserved.id, last_join_result=join_result)

        remaining_available = await _remaining_helper_availability_count(exclude_id=reserved.id)
        cooldown_until = None
        quarantine_count = None
        try:
            helper_row = await HelperPoolService._get_helper_row(reserved.id)
            cooldown_until = getattr(helper_row, "cooldown_until", None)
            quarantine_count = getattr(helper_row, "quarantine_count", None)
        except Exception:
            logger.debug("helper join failure row refresh failed helper_id=%s", reserved.id)

        logger.warning(
            "Helper %d failed to join chat %d (attempt %d/%d) "
            "reason=%s exception_type=%s safe_message=%s remaining_available=%s "
            "cooldown_until=%s quarantine_count=%s quarantine=%s",
            reserved.id,
            chat_id,
            attempt + 1,
            max_retries,
            join_result.reason,
            join_result.exception_type,
            redact_freeform_text(join_result.safe_message or ""),
            remaining_available,
            cooldown_until,
            quarantine_count,
            _should_quarantine_join_failure(join_result),
        )
        await HelperPoolService.release_helper_reservation(reserved.id)
        if _should_quarantine_join_failure(join_result):
            await HelperPoolService.quarantine_helper(
                reserved.id, "pre_stream_join_failed", 1800,
            )
            await helper_event_repo.log_event(
                "helper.quarantine", actor="runtime",
                helper_account_id=reserved.id, chat_id=chat_id,
                metadata={
                    "reason": "pre_stream_join_failed",
                    "join_reason": join_result.reason,
                    "exception_type": join_result.exception_type,
                    "safe_message": redact_freeform_text(join_result.safe_message or ""),
                    "attempt": attempt + 1,
                    "chat_id": chat_id,
                    "remaining_available": remaining_available,
                },
            )
        elif join_result.reason in _NO_QUARANTINE_JOIN_REASONS:
            logger.info(
                "Skipping helper quarantine for infra join failure helper_id=%s chat_id=%s reason=%s",
                reserved.id,
                chat_id,
                join_result.reason,
            )
            break

    logger.error("All helper join attempts failed for chat %d", chat_id)
    return HelperChatEnsureResult(helper_id=None, last_join_result=last_join_result)


def _public_present_result_from_promote(admin_result) -> str | None:
    """Map a failed admin promotion to a public ensure-present status string."""
    if admin_result.ok:
        return None
    if admin_result.reason == "helper_user_unknown":
        return "helper_user_unknown"
    return "promote_failed"


async def _finalize_helper_present(
    chat_id: int,
    helper_id: int,
    join_status: str,
    *,
    reason: str,
    bot_client=None,
) -> str:
    """Log ensure-present, promote helper admin rights, and map final status."""
    from app.repositories import helper_event_repo
    from app.services.bot_update_service import get_runtime_bot
    from app.services.helper_admin_service import ensure_helper_call_admin

    admin_result = await ensure_helper_call_admin(
        bot_client or get_runtime_bot(),
        chat_id,
        helper_id,
    )
    await helper_event_repo.log_event(
        f"helper.ensure_present.{reason}",
        actor="command",
        helper_account_id=helper_id,
        chat_id=chat_id,
        metadata={"admin_reason": admin_result.reason},
    )
    promote_status = _public_present_result_from_promote(admin_result)
    if promote_status is not None:
        return promote_status
    return join_status


async def ensure_helper_present_for_group(
    chat_id: int,
    reason: str = "manual",
    *,
    bot_client=None,
) -> str:
    """Public service: ensure a helper is joined to a group chat.

    Returns one of: ``success``, ``already_present``, ``unavailable``,
    ``failed``, ``invite_failed``, ``bot_unavailable``, ``channel_invalid``,
    ``promote_failed``, ``helper_user_unknown``.
    Idempotent — safe to call when helper may already be present.
    """
    from app.services.helper_pool_service import HelperPoolService
    from sqlalchemy import select as _sa_select

    async with async_session() as session:
        bound_stmt = _sa_select(HelperChatBinding).where(HelperChatBinding.chat_id == chat_id)
        binding = (await session.execute(bound_stmt)).scalar_one_or_none()

    if binding is not None:
        if bot_client is None:
            joined = await HelperPoolService.ensure_helper_joined(
                binding.helper_account_id,
                chat_id,
            )
        else:
            joined = await HelperPoolService.ensure_helper_joined(
                binding.helper_account_id,
                chat_id,
                bot_client=bot_client,
            )
        if joined:
            return await _finalize_helper_present(
                chat_id,
                binding.helper_account_id,
                "already_present",
                reason=reason,
                bot_client=bot_client,
            )

    reserved = await HelperPoolService.reserve_best_helper(chat_id)
    if reserved is None:
        return "unavailable"

    if bot_client is None:
        join_result = await HelperPoolService.ensure_helper_joined_detailed(
            reserved.id,
            chat_id,
        )
    else:
        join_result = await HelperPoolService.ensure_helper_joined_detailed(
            reserved.id,
            chat_id,
            bot_client=bot_client,
        )
    if not join_result.ok:
        await HelperPoolService.release_helper_reservation(reserved.id)
        if _should_quarantine_join_failure(join_result):
            await HelperPoolService.quarantine_helper(
                reserved.id,
                "manual_join_failed",
                1800,
            )
        return _public_present_result_from_join(join_result)

    await HelperPoolService.bind_chat_to_helper(chat_id, reserved.id)
    return await _finalize_helper_present(
        chat_id,
        reserved.id,
        "success",
        reason=reason,
        bot_client=bot_client,
    )


async def _maybe_quarantine_helper(chat_id: int, exc: Exception) -> None:
    """Check if the exception indicates a helper ban/flood and quarantine it."""
    err_str = str(type(exc).__name__) + " " + str(exc)

    should_quarantine = any(sig in err_str for sig in _QUARANTINE_ERRORS)
    if not should_quarantine:
        return

    try:
        from sqlalchemy import select as sa_select

        async with async_session() as session:
            stmt = sa_select(HelperChatBinding).where(HelperChatBinding.chat_id == chat_id)
            result = await session.execute(stmt)
            binding = result.scalar_one_or_none()

        if binding is None:
            return

        from app.services.helper_pool_service import HelperPoolService

        wait_secs = 600
        if "FloodWait" in err_str or "FLOOD_WAIT" in err_str:
            import re
            match = re.search(r"(\d+)", err_str)
            if match:
                wait_secs = int(match.group(1)) + 10

        await HelperPoolService.quarantine_helper(
            binding.helper_account_id,
            reason=err_str[:200],
            duration_seconds=wait_secs,
        )
        logger.warning(
            "Quarantined helper %d for chat %s: %s (cooldown %ds)",
            binding.helper_account_id, chat_id, err_str[:100], wait_secs,
        )
    except Exception:
        logger.debug("Failed to quarantine helper for chat %s", chat_id)

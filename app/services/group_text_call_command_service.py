"""Service layer for slash-free group call text commands."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import inspect
import logging
import os
from typing import Any, Iterable

from pyrogram import filters

from app.database.models import PlaybackState
from app.repositories import group_text_call_command_repo as repo
from app.services import CallService
from app.config.settings import settings
from app.services import call_security_service
from app.services import group_call_moderation_service
from app.utils.i18n import AUTO_LANG, t

logger = logging.getLogger(__name__)

# Live sources never reach an end-of-track boundary, so repeat cannot apply.
_LIVE_REPEAT_BLOCKED_FEATURES = frozenset({"radio", "satellite", "tv"})


@dataclass(frozen=True)
class CommandActionResult:
    ok: bool
    reason: str = "ok"
    count: int = 0
    link: str | None = None
    title: str | None = None
    end_at: datetime | None = None
    persisted: bool = False
    applied_live: bool = False


@dataclass(frozen=True)
class FutureCallSettings:
    auto_stats_enabled: bool
    call_mute_enabled: bool
    call_comment_enabled: bool
    title: str | None


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


async def is_call_active(chat_id: int) -> bool:
    if CallService.is_chat_playing(chat_id):
        return True
    if await repo.has_persisted_playback_state(chat_id):
        return True
    try:
        from app.utils.cache import get_redis
        from app.utils.redis_keys import callsec_active_chat_key

        redis = await get_redis()
        return await redis.get(callsec_active_chat_key(chat_id)) is not None
    except Exception:
        return False


async def get_current_playback(chat_id: int) -> PlaybackState | None:
    state = await repo.get_playback_state(chat_id)
    if state is not None:
        return state
    active = CallService.get_active_calls().get(chat_id)
    if not active:
        return None
    return PlaybackState(
        chat_id=chat_id,
        media_type=str(active.get("media_type") or "audio"),
        source=active.get("source"),
        title=active.get("title"),
        is_paused=bool(active.get("is_paused", False)),
    )


async def _run_scheduled_call_end(call_py: Any, chat_id: int) -> None:
    result = await group_call_moderation_service.try_discard_group_call(
        chat_id,
        reason="scheduled_group_text_command",
    )
    if not result.ok:
        logger.info(
            "scheduled group call discard failed chat_id=%s reason=%s",
            chat_id,
            result.reason,
        )
    await CallService.leave_voice_chat(call_py, chat_id)
    if result.ok or result.reason == "no_active_call":
        await repo.clear_scheduled_end(chat_id)


def _schedule_runtime_call_end(
    call_py: Any,
    chat_id: int,
    end_at: datetime,
    *,
    scheduler_obj: Any | None = None,
) -> None:
    if scheduler_obj is None and os.getenv("TEST_MODE", "").strip().lower() in {"1", "true", "yes"}:
        return
    try:
        if scheduler_obj is None:
            from app.scheduler import scheduler as scheduler_obj

        scheduler_obj.add_job(
            _run_scheduled_call_end,
            "date",
            run_date=end_at,
            args=[call_py, chat_id],
            id=f"{settings.INSTANCE_ID}:group_text_call_end_{chat_id}",
            replace_existing=True,
        )
    except Exception:
        return


async def restore_scheduled_call_end_jobs(
    call_py: Any,
    *,
    scheduler_obj: Any | None = None,
) -> int:
    restored = 0
    for cfg in await repo.list_pending_scheduled_ends():
        _schedule_runtime_call_end(
            call_py,
            cfg.chat_id,
            cfg.end_at,
            scheduler_obj=scheduler_obj,
        )
        restored += 1
    return restored


async def schedule_call_end(
    call_py: Any,
    chat_id: int,
    minutes: int,
    *,
    requested_by: int | None,
    scheduler_obj: Any | None = None,
) -> CommandActionResult:
    if not await is_call_active(chat_id):
        return CommandActionResult(False, "no_active_call")
    if not group_call_moderation_service.raw_discard_group_call_available():
        return CommandActionResult(False, "raw_api_unavailable")
    resolution = await group_call_moderation_service.resolve_group_call_helper(
        chat_id,
        require_admin=True,
        reason="group_text_command_schedule_end",
    )
    await group_call_moderation_service.close_group_call_helper(resolution)
    if not resolution.ok:
        reason = {
            "no_helper": "no_helper_available",
            "session_unavailable": "helper_session_unavailable",
            "join_failed": "helper_join_failed",
            "not_in_group": "helper_not_in_group",
            "not_admin": "helper_not_admin",
            "api_failed": "api_error",
        }.get(resolution.status, "api_error")
        return CommandActionResult(False, reason)
    end_at = datetime.now(timezone.utc) + timedelta(minutes=minutes)
    try:
        await repo.set_scheduled_end(
            chat_id,
            minutes,
            end_at,
            requested_by=requested_by,
        )
    except Exception:
        logger.exception("failed to persist scheduled call end chat_id=%s", chat_id)
        return CommandActionResult(False, "db_error")
    _schedule_runtime_call_end(call_py, chat_id, end_at, scheduler_obj=scheduler_obj)
    return CommandActionResult(True, "scheduled", end_at=end_at, persisted=True)


async def end_call_now(call_py: Any, chat_id: int) -> CommandActionResult:
    if not await is_call_active(chat_id):
        return CommandActionResult(False, "no_active_call")

    discard_ok = False
    if group_call_moderation_service.raw_discard_group_call_available():
        result = await group_call_moderation_service.try_discard_group_call(
            chat_id,
            reason="group_text_command_immediate_end",
        )
        if result.ok or result.reason == "no_active_call":
            discard_ok = True
        else:
            return CommandActionResult(False, result.reason)

    leave_ok = await CallService.leave_voice_chat(call_py, chat_id)
    if leave_ok or discard_ok:
        try:
            await repo.clear_scheduled_end(chat_id)
        except Exception:
            logger.debug("failed to clear scheduled end after immediate end chat_id=%s", chat_id, exc_info=True)
        return CommandActionResult(True, "ended", applied_live=True)

    failure_key = CallService.pop_leave_failure_key()
    if failure_key == "playback_cmd.no_voice_chat":
        return CommandActionResult(False, "no_active_call")
    return CommandActionResult(False, "api_error")


async def start_call(client: Any, call_py: Any, chat_id: int) -> CommandActionResult:
    if await is_call_active(chat_id):
        return CommandActionResult(True, "already_active")
    if not group_call_moderation_service.raw_create_group_call_available():
        return CommandActionResult(False, "raw_api_unavailable")
    settings = await get_future_call_settings(chat_id)
    result = await group_call_moderation_service.try_create_group_call(
        chat_id,
        title=settings.title,
        reason="group_text_command",
    )
    if result.ok:
        await _apply_future_settings_live(chat_id, settings)
        return CommandActionResult(True, "started", applied_live=True)
    if result.reason == "already_active":
        return CommandActionResult(True, "already_active")
    return CommandActionResult(False, result.reason)


async def mute_participant(call_py: Any, chat_id: int, user_id: int) -> CommandActionResult:
    if not await is_call_active(chat_id):
        return CommandActionResult(False, "no_active_call")
    result = await call_security_service.try_mute_participant(call_py, chat_id, user_id)
    return CommandActionResult(bool(result.ok), result.reason)


async def unmute_participant(call_py: Any, chat_id: int, user_id: int) -> CommandActionResult:
    if not await is_call_active(chat_id):
        return CommandActionResult(False, "no_active_call")
    result = await call_security_service.try_unmute_participant(
        call_py,
        chat_id,
        user_id,
        reason="group_text_command",
    )
    return CommandActionResult(bool(result.ok), result.reason)


async def _invite_targets(client: Any, call_py: Any, chat_id: int, user_ids: Iterable[int]) -> CommandActionResult:
    unique_ids = list(dict.fromkeys(int(uid) for uid in user_ids if uid is not None))
    if not await is_call_active(chat_id):
        return CommandActionResult(False, "no_active_call")
    if not unique_ids:
        return CommandActionResult(False, "no_users")
    if not group_call_moderation_service.raw_invite_group_call_available():
        return CommandActionResult(False, "raw_api_unavailable")
    result = await group_call_moderation_service.try_invite_to_group_call(
        chat_id,
        unique_ids,
        reason="group_text_command",
    )
    if result.ok:
        return CommandActionResult(True, "invited", count=len(unique_ids), applied_live=True)
    return CommandActionResult(False, result.reason)


async def invite_user(client: Any, call_py: Any, chat_id: int, user_id: int) -> CommandActionResult:
    return await _invite_targets(client, call_py, chat_id, [user_id])


async def invite_admins(client: Any, call_py: Any, chat_id: int) -> CommandActionResult:
    user_ids: list[int] = []
    try:
        async for member in client.get_chat_members(
            chat_id,
            filter=filters.ChatMembersFilter.ADMINISTRATORS,
        ):
            user = getattr(member, "user", None)
            if user is not None and not getattr(user, "is_bot", False):
                user_ids.append(int(user.id))
    except Exception:
        return CommandActionResult(False, "api_error")
    return await _invite_targets(client, call_py, chat_id, user_ids)


async def invite_recent(client: Any, call_py: Any, chat_id: int) -> CommandActionResult:
    user_ids = await repo.get_recent_user_ids(chat_id)
    return await _invite_targets(client, call_py, chat_id, user_ids)


async def invite_special(client: Any, call_py: Any, chat_id: int) -> CommandActionResult:
    if not await repo.is_auto_stats_enabled(chat_id):
        return CommandActionResult(False, "special_stats_disabled")
    rows = await repo.get_call_stats(chat_id)
    return await _invite_targets(client, call_py, chat_id, [row.user_id for row in rows])


async def set_auto_stats(chat_id: int, enabled: bool, *, updated_by: int | None) -> CommandActionResult:
    try:
        await repo.set_auto_stats_enabled(chat_id, enabled, updated_by=updated_by)
    except Exception:
        logger.exception("failed to persist auto stats setting chat_id=%s", chat_id)
        return CommandActionResult(False, "db_error")
    return CommandActionResult(True, "enabled" if enabled else "disabled", persisted=True)


async def set_call_mute(chat_id: int, enabled: bool, *, updated_by: int | None) -> CommandActionResult:
    try:
        await repo.set_call_mute_enabled(chat_id, enabled, updated_by=updated_by)
    except Exception:
        logger.exception("failed to persist call mute setting chat_id=%s", chat_id)
        return CommandActionResult(False, "db_error")
    if await is_call_active(chat_id) and group_call_moderation_service.raw_toggle_group_call_settings_available():
        live = await group_call_moderation_service.try_toggle_group_call_settings(
            chat_id,
            join_muted=enabled,
            reason="group_text_command",
        )
        if live.ok:
            return CommandActionResult(
                True,
                "enabled_live" if enabled else "disabled_live",
                persisted=True,
                applied_live=True,
            )
        return CommandActionResult(
            True,
            "enabled_saved_live_failed" if enabled else "disabled_saved_live_failed",
            persisted=True,
        )
    return CommandActionResult(
        True,
        "enabled_saved" if enabled else "disabled_saved",
        persisted=True,
    )


async def set_call_comment(chat_id: int, enabled: bool, *, updated_by: int | None) -> CommandActionResult:
    try:
        await repo.set_call_comment_enabled(chat_id, enabled, updated_by=updated_by)
    except Exception:
        logger.exception("failed to persist call comment setting chat_id=%s", chat_id)
        return CommandActionResult(False, "db_error")
    if await is_call_active(chat_id) and group_call_moderation_service.raw_toggle_group_call_settings_available():
        live = await group_call_moderation_service.try_toggle_group_call_settings(
            chat_id,
            messages_enabled=enabled,
            reason="group_text_command",
        )
        if live.ok:
            return CommandActionResult(
                True,
                "enabled_live" if enabled else "disabled_live",
                persisted=True,
                applied_live=True,
            )
        return CommandActionResult(
            True,
            "enabled_saved_live_failed" if enabled else "disabled_saved_live_failed",
            persisted=True,
        )
    return CommandActionResult(
        True,
        "enabled_saved" if enabled else "disabled_saved",
        persisted=True,
    )


EQUALIZER_PRESETS: tuple[str, ...] = (
    "normal",
    "bassboost",
    "amplifier",
    "soft",
    "treble",
)


async def set_repeat(chat_id: int, enabled: bool) -> CommandActionResult:
    """Toggle end-of-track repeat for the chat's currently playing media.

    Repeat is session state by design: it only makes sense while a finite track
    is playing, and live streams (radio/TV/satellite) never end, so they are
    rejected explicitly instead of silently accepting a no-op toggle.
    """
    if not CallService.is_chat_playing(chat_id):
        return CommandActionResult(False, "no_active_call")
    active = CallService.get_active_calls().get(chat_id) or {}
    feature = str(active.get("playback_feature") or "")
    if feature in _LIVE_REPEAT_BLOCKED_FEATURES:
        return CommandActionResult(False, "repeat_live_unsupported")
    CallService.set_repeat_state(chat_id, enabled)
    return CommandActionResult(True, "enabled" if enabled else "disabled", applied_live=True)


async def set_auto_clear_stopped(
    chat_id: int, enabled: bool, *, updated_by: int | None = None,  # noqa: ARG001
) -> CommandActionResult:
    """Persist whether the bot's own 'playback stopped' notice self-deletes."""
    try:
        await _persist_chat_setting(chat_id, "auto_clear_stopped_enabled", enabled)
    except Exception:
        logger.exception("failed to persist auto-clear setting chat_id=%s", chat_id)
        return CommandActionResult(False, "db_error")
    return CommandActionResult(True, "enabled" if enabled else "disabled", persisted=True)


async def set_equalizer_preset(chat_id: int, preset: str) -> CommandActionResult:
    """Persist the per-chat audio-filter preset applied at transcode time."""
    if preset not in EQUALIZER_PRESETS:
        return CommandActionResult(False, "invalid_preset")
    try:
        await _persist_chat_setting(chat_id, "equalizer_preset", preset)
    except Exception:
        logger.exception("failed to persist equalizer preset chat_id=%s", chat_id)
        return CommandActionResult(False, "db_error")
    return CommandActionResult(True, preset, persisted=True)


async def get_equalizer_preset(chat_id: int) -> str:
    from app.repositories import settings_repo

    row = await settings_repo.get_chat_settings(chat_id)
    return str(getattr(row, "equalizer_preset", None) or "normal")


async def _persist_chat_setting(chat_id: int, key: str, value: object) -> None:
    from app.repositories import settings_repo
    from app.utils.cache import invalidate_chat_settings

    if await settings_repo.get_chat_settings(chat_id) is None:
        await settings_repo.create_defaults(chat_id)
    await settings_repo.update_setting(chat_id, key, value)
    await invalidate_chat_settings(chat_id, "group")


async def set_call_report_dm(chat_id: int, enabled: bool) -> CommandActionResult:
    """CALLSEC-01: toggle owner DMs for voice-chat moderation actions."""
    try:
        await _persist_chat_setting(chat_id, "call_report_dm_enabled", enabled)
    except Exception:
        logger.exception("failed to persist call-report DM setting chat_id=%s", chat_id)
        return CommandActionResult(False, "db_error")
    return CommandActionResult(True, "enabled" if enabled else "disabled", persisted=True)


async def notify_owners_of_moderation(
    client: Any,
    chat_id: int,
    *,
    action: str,
    target_user_id: int,
    actor_user_id: int | None,
    chat_title: str | None = None,
) -> int:
    """DM the group's player owners about a voice-chat moderation action.

    Returns the number of owners successfully notified. Never raises: a failed
    DM (owner never started the bot, blocked it, …) must not break the
    moderation action that triggered it.
    """
    from app.repositories import admin_repo, settings_repo

    try:
        row = await settings_repo.get_chat_settings(chat_id)
        if row is None or not getattr(row, "call_report_dm_enabled", False):
            return 0
        owners = await admin_repo.get_player_owners(chat_id)
    except Exception:
        logger.debug("call-report owner lookup failed chat_id=%s", chat_id, exc_info=True)
        return 0

    send_message = getattr(client, "send_message", None)
    if not callable(send_message) or not owners:
        return 0

    text = t(
        AUTO_LANG,
        "group_text_call.call_report_dm",
        action=t(AUTO_LANG, f"group_text_call.call_report_action_{action}"),
        chat=chat_title or str(chat_id),
        chat_id=str(chat_id),
        target=str(target_user_id),
        actor=str(actor_user_id) if actor_user_id is not None else "-",
    )

    delivered = 0
    for owner in owners:
        owner_id = getattr(owner, "user_id", None)
        if owner_id is None:
            continue
        try:
            await send_message(int(owner_id), text)
            delivered += 1
        except Exception:
            logger.debug(
                "call-report DM failed chat_id=%s owner_id=%s", chat_id, owner_id, exc_info=True
            )
    return delivered


async def _bot_is_full_admin(client: Any, chat_id: int) -> bool:
    """True when the bot can manage video chats in ``chat_id``."""
    get_me = getattr(client, "get_me", None)
    get_member = getattr(client, "get_chat_member", None)
    if not callable(get_me) or not callable(get_member):
        return False
    try:
        me = await get_me()
        member = await get_member(chat_id, me.id)
    except Exception:
        return False
    status = str(
        getattr(getattr(member, "status", None), "value", getattr(member, "status", ""))
        or ""
    ).lower()
    if status in {"creator", "owner"}:
        return True
    if status != "administrator":
        return False
    privileges = getattr(member, "privileges", None) or member
    return bool(
        getattr(privileges, "can_manage_video_chats", None)
        or getattr(privileges, "can_manage_voice_chats", None)
    )


async def link_playback_channel(
    client: Any, chat_id: int, channel_id: int,
) -> CommandActionResult:
    """CALLMGMT-01: bind a channel as this group's playback target.

    The bot must be a video-chat-managing admin in BOTH chats; a missing right
    in either one is rejected explicitly instead of silently half-linking.
    """
    if not await _bot_is_full_admin(client, chat_id):
        return CommandActionResult(False, "not_admin_here")

    get_chat = getattr(client, "get_chat", None)
    if not callable(get_chat):
        return CommandActionResult(False, "api_unavailable")
    try:
        channel = await get_chat(channel_id)
    except Exception:
        return CommandActionResult(False, "channel_not_found")

    resolved_id = int(getattr(channel, "id", channel_id) or channel_id)
    if not await _bot_is_full_admin(client, resolved_id):
        return CommandActionResult(False, "not_admin_channel")

    title = str(getattr(channel, "title", None) or resolved_id)
    try:
        await _persist_chat_setting(chat_id, "linked_channel_id", resolved_id)
        await _persist_chat_setting(chat_id, "linked_channel_title", title)
    except Exception:
        logger.exception("failed to persist channel link chat_id=%s", chat_id)
        return CommandActionResult(False, "db_error")
    return CommandActionResult(True, "linked", title=title, count=resolved_id, persisted=True)


async def set_channel_playback(chat_id: int, enabled: bool) -> CommandActionResult:
    """CALLMGMT-03: route playback to the linked channel, or back to the group."""
    from app.repositories import settings_repo

    row = await settings_repo.get_chat_settings(chat_id)
    if enabled and not getattr(row, "linked_channel_id", None):
        # The prerequisite link is missing; say so instead of enabling a
        # routing flag that would silently do nothing.
        return CommandActionResult(False, "no_channel_link")
    try:
        await _persist_chat_setting(chat_id, "channel_playback_enabled", enabled)
    except Exception:
        logger.exception("failed to persist channel playback chat_id=%s", chat_id)
        return CommandActionResult(False, "db_error")
    return CommandActionResult(
        True,
        "enabled" if enabled else "disabled",
        title=str(getattr(row, "linked_channel_title", None) or ""),
        persisted=True,
    )


async def resolve_playback_target_chat(chat_id: int) -> int:
    """Return the chat whose voice chat playback should join (CALLMGMT-03).

    Falls back to the originating group whenever routing is off or the link was
    removed, so playback can never be stranded on a stale channel id.
    """
    from app.repositories import settings_repo

    try:
        row = await settings_repo.get_chat_settings(chat_id)
    except Exception:
        logger.debug("playback target lookup failed chat_id=%s", chat_id, exc_info=True)
        return chat_id
    if not getattr(row, "channel_playback_enabled", False):
        return chat_id
    linked = getattr(row, "linked_channel_id", None)
    return int(linked) if linked else chat_id


async def set_call_stats_reset_cadence(
    chat_id: int, cadence: str, *, updated_by: int | None = None,
) -> CommandActionResult:
    """Persist the daily/monthly cadence used to roll over call-stats aggregates."""
    from app.repositories import call_stats_settings_repo

    if cadence not in {"daily", "monthly"}:
        return CommandActionResult(False, "invalid_cadence")
    try:
        await call_stats_settings_repo.set_reset_cadence(
            chat_id, cadence, updated_by=updated_by
        )
    except Exception:
        logger.exception("failed to persist call-stats cadence chat_id=%s", chat_id)
        return CommandActionResult(False, "db_error")
    return CommandActionResult(True, cadence, persisted=True)


async def set_call_title(
    call_py: Any,
    chat_id: int,
    title: str,
    *,
    updated_by: int | None,
) -> CommandActionResult:
    title = title.strip()
    if not title:
        return CommandActionResult(False, "missing_title")
    if not await is_call_active(chat_id):
        return CommandActionResult(False, "no_active_call", title=title)
    if not group_call_moderation_service.raw_edit_group_call_title_available():
        return CommandActionResult(False, "raw_api_unavailable", title=title)
    result = await group_call_moderation_service.try_edit_group_call_title(
        chat_id,
        title,
        reason="group_text_command",
    )
    if not result.ok:
        return CommandActionResult(False, result.reason, title=title)
    try:
        await repo.set_call_title(chat_id, title, updated_by=updated_by)
    except Exception:
        logger.exception("failed to persist live-applied call title chat_id=%s", chat_id)
        return CommandActionResult(False, "db_error", title=title, applied_live=True)
    return CommandActionResult(
        True,
        "title_applied",
        title=title,
        persisted=True,
        applied_live=True,
    )


async def get_call_link(client: Any, call_py: Any, chat: Any) -> CommandActionResult:
    chat_id = int(chat.id)
    if not await is_call_active(chat_id):
        return CommandActionResult(False, "no_active_call")
    if not group_call_moderation_service.raw_export_group_call_invite_available():
        return CommandActionResult(False, "raw_api_unavailable")
    result, link = await group_call_moderation_service.try_export_group_call_invite_link(
        chat_id,
        reason="group_text_command",
    )
    if result.ok and link:
        return CommandActionResult(True, "ok", link=link, applied_live=True)
    return CommandActionResult(False, result.reason)


async def get_future_call_settings(chat_id: int) -> FutureCallSettings:
    return FutureCallSettings(
        auto_stats_enabled=await repo.is_auto_stats_enabled(chat_id),
        call_mute_enabled=await repo.is_call_mute_enabled(chat_id),
        call_comment_enabled=await repo.is_call_comment_enabled(chat_id),
        title=await repo.get_call_title(chat_id),
    )


async def _apply_future_settings_live(
    chat_id: int,
    settings: FutureCallSettings,
) -> None:
    join_muted = settings.call_mute_enabled
    messages_enabled = settings.call_comment_enabled
    if not (join_muted or messages_enabled):
        return
    if not group_call_moderation_service.raw_toggle_group_call_settings_available():
        return
    result = await group_call_moderation_service.try_toggle_group_call_settings(
        chat_id,
        join_muted=join_muted,
        messages_enabled=messages_enabled,
        reason="group_text_command_start",
    )
    if not result.ok:
        logger.info(
            "future call settings not applied live chat_id=%s reason=%s",
            chat_id,
            result.reason,
        )


async def build_call_stats_message(
    chat_id: int,
    *,
    day: str | None = None,
    days: int | None = None,
    lang: str = AUTO_LANG,
) -> str | None:
    if days is not None:
        end = datetime.now(timezone.utc)
        start = end - timedelta(days=max(1, int(days)))
        rows = await repo.get_call_stats_between(chat_id, start=start, end=end)
    else:
        rows = await repo.get_call_stats(chat_id, day=day)
    if not rows:
        return None
    lines = [t(lang, "group_text_call.stats_title")]
    for index, row in enumerate(rows, start=1):
        lines.append(
            t(
                lang,
                "group_text_call.stats_item",
                rank=str(index),
                user=row.display_name,
                duration=format_duration(row.total_seconds),
                count=str(row.report_count),
            )
        )
    return "\n".join(lines)


async def send_auto_call_stats(
    bot: Any,
    chat_id: int,
    *,
    day: str | None = None,
    days: int | None = None,
    lang: str = AUTO_LANG,
) -> CommandActionResult:
    if not await repo.is_auto_stats_enabled(chat_id):
        return CommandActionResult(False, "auto_stats_disabled")
    body = await build_call_stats_message(chat_id, day=day, days=days, lang=lang)
    if not body:
        return CommandActionResult(False, "no_stats")
    send_message = getattr(bot, "send_message", None)
    if not callable(send_message):
        return CommandActionResult(False, "api_unavailable")
    try:
        await _maybe_await(send_message(chat_id, body))
    except Exception:
        logger.exception("failed to send automatic call stats chat_id=%s", chat_id)
        return CommandActionResult(False, "api_error")
    return CommandActionResult(True, "sent")


async def run_auto_call_stats_report(
    bot: Any,
    *,
    day: str | None = None,
    days: int | None = None,
    lang: str = AUTO_LANG,
) -> int:
    sent = 0
    for chat_id in await repo.list_auto_stats_enabled_chat_ids():
        result = await send_auto_call_stats(bot, chat_id, day=day, days=days, lang=lang)
        if result.ok:
            sent += 1
    return sent


def format_duration(seconds: int) -> str:
    seconds = max(0, int(seconds))
    hours, rem = divmod(seconds, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}h {minutes}m {secs}s"
    if minutes:
        return f"{minutes}m {secs}s"
    return f"{secs}s"

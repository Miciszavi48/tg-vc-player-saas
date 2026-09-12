"""Call Security runtime: participant tracking, live reports, summaries."""

from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timezone
from typing import Any

from pyrogram import Client, raw

from app.handlers.priority import CALL_SECURITY_RAW_GROUP
from app.repositories import call_security_repo
from app.services import call_security_service
from app.services import group_membership_age_service
from app.services.language_service import resolve_lang
from app.utils.cache import get_redis
from app.utils.i18n import t
from app.utils.redis_keys import (
    TTL_CALLSEC_REPORT_COOLDOWN,
    TTL_CALLSEC_STATE,
    callsec_active_chat_key,
    callsec_callmap_key,
    callsec_report_cooldown_key,
    callsec_state_key,
    callsec_user_key,
)
from app.utils.safe_sender import safe_send_message

logger = logging.getLogger(__name__)

_IDLE_EXPIRE_SECONDS = 600
_bot_client: Client | None = None


def _peer_to_chat_id(peer: Any) -> int | None:
    """Convert a raw Peer to bot chat_id."""
    if peer is None:
        return None
    cls = type(peer).__name__
    if cls == "PeerChannel":
        return int(f"-100{peer.channel_id}")
    if cls == "PeerChat":
        return -int(peer.chat_id)
    return None


def _get_call_id(call: Any) -> int | None:
    """Extract group call id from a raw call object."""
    call_id = getattr(call, "id", None)
    return int(call_id) if call_id is not None else None


async def register_call_mapping(chat_id: int, call_id: int) -> None:
    """Map a Telegram group call id to a chat id in Redis."""
    redis = await get_redis()
    await redis.set(callsec_callmap_key(call_id), str(chat_id), ex=TTL_CALLSEC_STATE)
    await redis.set(callsec_active_chat_key(chat_id), str(call_id), ex=TTL_CALLSEC_STATE)


async def resolve_chat_id_for_call(call_id: int) -> int | None:
    """Resolve chat id from group call id."""
    redis = await get_redis()
    raw_val = await redis.get(callsec_callmap_key(call_id))
    if raw_val is None:
        return None
    try:
        return int(raw_val)
    except (TypeError, ValueError):
        return None


async def _load_runtime_state(chat_id: int) -> dict[str, Any]:
    redis = await get_redis()
    raw_state = await redis.get(callsec_state_key(chat_id))
    state = call_security_service.deserialize_runtime_state(raw_state)
    state.setdefault("events", [])
    state.setdefault("users", {})
    return state


async def _save_runtime_state(chat_id: int, state: dict[str, Any]) -> None:
    redis = await get_redis()
    await redis.set(
        callsec_state_key(chat_id),
        call_security_service.serialize_runtime_state(state),
        ex=TTL_CALLSEC_STATE,
    )


async def _load_user_state(chat_id: int, user_id: int) -> dict[str, Any]:
    redis = await get_redis()
    raw_user = await redis.get(callsec_user_key(chat_id, user_id))
    return call_security_service.deserialize_runtime_state(raw_user)


async def _save_user_state(chat_id: int, user_id: int, user_state: dict[str, Any]) -> None:
    redis = await get_redis()
    await redis.set(
        callsec_user_key(chat_id, user_id),
        call_security_service.serialize_runtime_state(user_state),
        ex=TTL_CALLSEC_STATE,
    )


async def _append_event(chat_id: int, event: call_security_service.SuspiciousEvent) -> None:
    state = await _load_runtime_state(chat_id)
    events: list[dict[str, Any]] = state.setdefault("events", [])
    events.append(
        {
            "reason": event.reason,
            "user_id": event.user_id,
            "detail": event.detail,
            "timestamp": event.timestamp,
        }
    )
    state["last_activity"] = time.time()
    await _save_runtime_state(chat_id, state)


async def _report_cooldown_active(chat_id: int, user_id: int, reason: str) -> bool:
    redis = await get_redis()
    bucket = datetime.now(timezone.utc).strftime("%Y%m%d%H")
    key = callsec_report_cooldown_key(chat_id, user_id, reason, bucket)
    inserted = await redis.set(key, "1", ex=TTL_CALLSEC_REPORT_COOLDOWN, nx=True)
    return not bool(inserted)


async def _send_live_report(
    bot: Client,
    chat_id: int,
    event: call_security_service.SuspiciousEvent,
) -> None:
    if event.user_id is None:
        return
    if await _report_cooldown_active(chat_id, event.user_id, event.reason):
        return
    lang = await resolve_lang(chat_id=chat_id)
    reason_text = t(lang, f"call_security.reason_{event.reason}")
    text = t(
        lang,
        "call_security.live_report",
        user_id=event.user_id,
        reason=reason_text,
    )
    await safe_send_message(bot, chat_id, text)


async def _record_moderation_action(
    bot: Client,
    chat_id: int,
    user_id: int,
    *,
    action_reason: str,
    detail: str | None,
    report_enabled: bool,
    report_failure: bool = False,
) -> None:
    event = call_security_service.SuspiciousEvent(
        reason=action_reason,
        user_id=user_id,
        detail=detail,
    )
    await _append_event(chat_id, event)
    if report_enabled and report_failure:
        await _send_live_report(bot, chat_id, event)


async def _maybe_emit_summary(
    bot: Client,
    chat_id: int,
    *,
    chat_title: str | None = None,
) -> None:
    settings = await call_security_repo.get_call_security_settings(chat_id)
    if settings is None or not settings.summary_enabled:
        return
    state = await _load_runtime_state(chat_id)
    raw_events = state.get("events") or []
    if not raw_events:
        return
    events = [
        call_security_service.SuspiciousEvent(
            reason=item.get("reason", "unknown"),
            user_id=item.get("user_id"),
            detail=item.get("detail"),
            timestamp=item.get("timestamp") or datetime.now(timezone.utc).isoformat(),
        )
        for item in raw_events
    ]
    path, filename = call_security_service.write_summary_tempfile(
        chat_id,
        chat_title,
        events,
        started_at=state.get("started_at"),
        ended_at=datetime.now(timezone.utc).isoformat(),
    )
    try:
        lang = await resolve_lang(chat_id=chat_id)
        caption = t(lang, "call_security.summary_caption")
        await bot.send_document(chat_id, document=str(path), caption=caption, file_name=filename)
    except Exception as exc:
        logger.warning(
            "call_security summary send failed chat_id=%s err=%s",
            chat_id,
            type(exc).__name__,
        )
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass
    redis = await get_redis()
    await redis.delete(callsec_state_key(chat_id))
    await redis.delete(callsec_active_chat_key(chat_id))


async def on_call_started(chat_id: int, call_id: int | None = None) -> None:
    """Mark a voice chat as active for Call Security tracking."""
    state = {
        "started_at": datetime.now(timezone.utc).isoformat(),
        "last_activity": time.time(),
        "events": [],
        "users": {},
    }
    await _save_runtime_state(chat_id, state)
    if call_id is not None:
        await register_call_mapping(chat_id, call_id)


async def on_call_ended(
    chat_id: int,
    *,
    chat_title: str | None = None,
) -> None:
    """Finalize Call Security state when the bot leaves a voice chat."""
    if _bot_client is not None:
        await _maybe_emit_summary(_bot_client, chat_id, chat_title=chat_title)
    redis = await get_redis()
    call_raw = await redis.get(callsec_active_chat_key(chat_id))
    if call_raw is not None:
        try:
            await redis.delete(callsec_callmap_key(int(call_raw)))
        except (TypeError, ValueError):
            pass
    await redis.delete(callsec_active_chat_key(chat_id))


async def _process_participant(
    bot: Client,
    call_py: Any,
    chat_id: int,
    participant: Any,
) -> None:
    settings = await call_security_repo.get_call_security_settings(chat_id)
    if settings is None or not settings.enabled:
        return

    now_ts = time.time()
    normalized = call_security_service.normalize_participant_update(
        participant,
        timestamp=now_ts,
    )
    user_id = normalized.get("user_id")
    if user_id is None:
        return
    user_id = int(user_id)
    try:
        await group_membership_age_service.record_member_seen(
            chat_id,
            user_id,
            datetime.now(timezone.utc),
            source="call_event",
        )
    except Exception as exc:
        logger.debug(
            "call_security membership seen record failed chat_id=%s user_id=%s err=%s",
            chat_id,
            user_id,
            type(exc).__name__,
        )

    prior = await _load_user_state(chat_id, user_id)

    privileged = await call_security_service.is_privileged_member(
        user_id,
        chat_id,
        settings,
    )
    events = call_security_service.detect_suspicious_events(
        chat_id,
        user_id,
        normalized,
        prior or None,
        privileged=privileged,
        mute_incoming=settings.mute_incoming_enabled,
    )

    merged = dict(prior)
    if normalized.get("just_joined"):
        merged["join_count"] = int(prior.get("join_count", 0)) + 1
        merged["left_since_last_join"] = bool(prior.get("left"))
    if normalized.get("video_active") and not prior.get("video_active"):
        merged["video_join_count"] = int(prior.get("video_join_count", 0)) + 1
    if normalized.get("left"):
        merged["left"] = True
        merged["left_since_last_join"] = True
    else:
        merged["left"] = False
    merged.update(normalized)
    await _save_user_state(chat_id, user_id, merged)

    caps = call_security_service.get_capabilities(call_py)
    if (
        settings.mute_incoming_enabled
        and caps.raw_mute_api_available
        and normalized.get("just_joined")
        and not normalized.get("left")
    ):
        auto_unmute = await call_security_service.should_auto_unmute(
            user_id,
            chat_id,
            settings,
            privileged=privileged,
        )
        if auto_unmute:
            reason = "call_security_privileged" if privileged else "call_security_membership_age"
            result = await call_security_service.try_unmute_participant(
                call_py,
                chat_id,
                user_id,
                reason=reason,
            )
            await _record_moderation_action(
                bot,
                chat_id,
                user_id,
                action_reason=(
                    "unmute_privileged" if privileged else "unmute_membership_age"
                )
                if result.ok
                else "mute_failed",
                detail=result.reason,
                report_enabled=settings.report_enabled,
                report_failure=not result.ok,
            )
        else:
            result = await call_security_service.try_mute_participant(
                call_py,
                chat_id,
                user_id,
            )
            await _record_moderation_action(
                bot,
                chat_id,
                user_id,
                action_reason="mute_enforced" if result.ok else "mute_failed",
                detail=result.reason,
                report_enabled=settings.report_enabled,
                report_failure=not result.ok,
            )

    for event in events:
        await _append_event(chat_id, event)
        if settings.report_enabled:
            await _send_live_report(bot, chat_id, event)


async def _handle_group_call_update(bot: Client, call_py: Any, update: Any) -> None:
    call_id = _get_call_id(update.call)
    peer_chat = _peer_to_chat_id(update.peer)
    if call_id is not None and peer_chat is not None:
        await register_call_mapping(peer_chat, call_id)

    participants_count = getattr(update.call, "participants_count", None)
    if peer_chat is not None and participants_count == 0:
        await on_call_ended(peer_chat)


async def _handle_participants_update(bot: Client, call_py: Any, update: Any) -> None:
    call_id = _get_call_id(update.call)
    if call_id is None:
        return
    chat_id = await resolve_chat_id_for_call(call_id)
    if chat_id is None:
        from app.services.call_service import CallService

        for active_chat_id in CallService.get_active_calls():
            await register_call_mapping(active_chat_id, call_id)
            chat_id = active_chat_id
            break
    if chat_id is None:
        return

    for participant in update.participants or []:
        try:
            await _process_participant(bot, call_py, chat_id, participant)
        except Exception as exc:
            logger.debug(
                "call_security participant processing failed chat_id=%s err=%s",
                chat_id,
                type(exc).__name__,
            )


def register(bot: Client, call_py) -> None:
    """Register raw update handler for Call Security runtime."""
    global _bot_client
    _bot_client = bot

    @bot.on_raw_update(group=CALL_SECURITY_RAW_GROUP)
    async def call_security_raw_handler(
        client: Client,
        update: Any,
        users: dict,
        chats: dict,
    ):  # noqa: ARG001
        try:
            if isinstance(update, raw.types.UpdateGroupCall):
                await _handle_group_call_update(client, call_py, update)
            elif isinstance(update, raw.types.UpdateGroupCallParticipants):
                await _handle_participants_update(client, call_py, update)
        except Exception as exc:
            logger.debug(
                "call_security raw update skipped err=%s",
                type(exc).__name__,
            )

    logger.info(
        "call_security raw observer registered group=%s update_types=%s",
        CALL_SECURITY_RAW_GROUP,
        "UpdateGroupCall,UpdateGroupCallParticipants",
    )

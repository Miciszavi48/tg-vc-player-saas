"""Call Security (امنیت کال) business logic."""

from __future__ import annotations

import json
import logging
import os
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.database.models import CallSecuritySettings
from app.config.settings import settings
from app.repositories import admin_repo, call_security_repo, user_repo
from app.repositories.admin_report_repo import get_install_row
from app.services import (
    group_call_moderation_service,
    group_membership_age_service,
    group_runtime_state_service,
)
from app.utils.bot_guards import is_developer
from app.utils.i18n import t
from app.utils.ui import CB, _btn, toggle_label

logger = logging.getLogger(__name__)

ABNORMAL_GAP_SECONDS = 120


@dataclass
class CallSecurityCapabilities:
    """Runtime capability flags for Call Security."""

    mute_enforcement: bool = False
    participant_updates: bool = True
    raw_mute_api_available: bool = False
    active_call_known: bool = False
    helper_available: bool = False
    mute_status_reason: str = "raw_api_unavailable"


@dataclass
class SuspiciousEvent:
    """One detected suspicious Call Security event."""

    reason: str
    user_id: int | None = None
    detail: str | None = None
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


def probe_mute_support(call_py: Any | None = None) -> bool:
    """Return True when raw MTProto participant mute APIs are locally available."""
    if call_py is not None and (
        not callable(getattr(call_py, "invoke", None))
        or not callable(getattr(call_py, "resolve_peer", None))
    ):
        return False
    return group_call_moderation_service.raw_mute_api_available()


def get_capabilities(call_py: Any | None = None) -> CallSecurityCapabilities:
    """Return current Call Security runtime capabilities."""
    raw_ok = probe_mute_support(None)
    return CallSecurityCapabilities(
        mute_enforcement=raw_ok,
        participant_updates=True,
        raw_mute_api_available=raw_ok,
        active_call_known=False,
        helper_available=False,
        mute_status_reason="no_active_call" if raw_ok else "raw_api_unavailable",
    )


async def get_panel_capabilities(
    chat_id: int,
    call_py: Any | None = None,  # noqa: ARG001 - retained for caller compatibility
) -> CallSecurityCapabilities:
    """Return panel-safe mute readiness without making live Telegram calls."""
    readiness = await group_call_moderation_service.get_call_mute_readiness(chat_id)
    return CallSecurityCapabilities(
        mute_enforcement=(
            readiness.raw_mute_api_available
            and readiness.active_call_known
            and readiness.helper_available
        ),
        participant_updates=True,
        raw_mute_api_available=readiness.raw_mute_api_available,
        active_call_known=readiness.active_call_known,
        helper_available=readiness.helper_available,
        mute_status_reason=readiness.reason,
    )


async def get_installer_user_id(chat_id: int) -> int | None:
    """Return the user id that installed the bot in this chat."""
    row = await get_install_row(chat_id)
    if row is None or row.installed_by is None:
        return None
    return int(row.installed_by)


async def is_installer(user_id: int, chat_id: int) -> bool:
    """Return True when user_id is the install trigger for the chat."""
    installer_id = await get_installer_user_id(chat_id)
    return installer_id is not None and int(user_id) == installer_id


async def can_toggle_owner_access(user_id: int, chat_id: int, *, client: Any | None = None) -> bool:
    """Only Developer or Deputy+ may toggle owner access."""
    if is_developer(user_id):
        return True
    if client is not None:
        from app.utils.player_permissions import can_manage_call_security

        if await can_manage_call_security(client, chat_id, user_id):
            return True
        return False
    if await user_repo.is_owner(user_id):
        return True
    if await user_repo.is_sudo(user_id):
        return True
    return await admin_repo.is_player_deputy_or_above(user_id, chat_id)


async def can_manage(
    user_id: int,
    chat_id: int,
    settings: CallSecuritySettings,  # noqa: ARG001 - retained for caller compatibility
    *,
    client: Any | None = None,
) -> bool:
    """Return whether user may manage Call Security options."""
    if is_developer(user_id):
        return True
    if client is not None:
        from app.utils.player_permissions import can_manage_call_security

        if await can_manage_call_security(client, chat_id, user_id):
            return True
        return False
    if await user_repo.is_owner(user_id):
        return True
    if await user_repo.is_sudo(user_id):
        return True
    return await admin_repo.is_player_deputy_or_above(user_id, chat_id)


async def is_privileged_member(user_id: int, chat_id: int, settings: CallSecuritySettings) -> bool:
    """Members that may speak without mute-incoming restrictions."""
    if is_developer(user_id):
        return True
    if await is_installer(user_id, chat_id):
        return True
    if await admin_repo.is_music_admin_or_above(user_id, chat_id):
        return True
    if await admin_repo.is_vip(user_id, chat_id):
        return True
    if settings.owner_access_enabled and await admin_repo.is_player_owner(user_id, chat_id):
        return True
    return False


async def has_active_credit(chat_id: int) -> bool:
    """Return True when the installed chat has usable runtime credit."""
    state = await group_runtime_state_service.get_runtime_credit_state(chat_id, "group")
    return state.has_runtime_credit


def get_membership_age_threshold_days(settings: CallSecuritySettings) -> int:
    """Return the configured group-membership-age threshold."""
    value = getattr(settings, "membership_age_days", 7)
    return call_security_repo.clamp_membership_age_days(int(value))


def build_panel_text(
    lang: str,
    settings: CallSecuritySettings,
    caps: CallSecurityCapabilities,
) -> str:
    """Render Call Security panel body text."""
    lines = [
        t(lang, "call_security.panel_title"),
        "",
        t(lang, "call_security.panel_status_header"),
        t(
            lang,
            "call_security.panel_row",
            feature=t(lang, "call_security.feature_enabled"),
            state=t(lang, "common.labels.on" if settings.enabled else "common.labels.off"),
        ),
        t(
            lang,
            "call_security.panel_row",
            feature=t(lang, "call_security.feature_mute_incoming"),
            state=t(lang, "common.labels.on" if settings.mute_incoming_enabled else "common.labels.off"),
        ),
        t(
            lang,
            "call_security.panel_row",
            feature=t(lang, "call_security.feature_summary"),
            state=t(lang, "common.labels.on" if settings.summary_enabled else "common.labels.off"),
        ),
        t(
            lang,
            "call_security.panel_row",
            feature=t(lang, "call_security.feature_report"),
            state=t(lang, "common.labels.on" if settings.report_enabled else "common.labels.off"),
        ),
        t(
            lang,
            "call_security.panel_row",
            feature=t(lang, "call_security.feature_membership_age"),
            state=str(get_membership_age_threshold_days(settings)),
        ),
    ]
    if settings.mute_incoming_enabled:
        status_key = {
            "ready": "call_security.mute_status_ready",
            "no_active_call": "call_security.mute_status_no_active_call",
            "no_helper_available": "call_security.mute_status_no_helper",
            "no_helper_session": "call_security.mute_status_no_helper",
            "raw_api_unavailable": "call_security.mute_status_unsupported",
        }.get(caps.mute_status_reason, "call_security.mute_status_needs_permission")
        lines.extend(
            [
                "",
                t(lang, "call_security.mute_status_line", status=t(lang, status_key)),
            ]
        )
        if caps.raw_mute_api_available and caps.active_call_known:
            lines.append(t(lang, "call_security.mute_helper_permission_note"))
    return "\n".join(lines)


def build_panel_keyboard(
    lang: str,
    settings: CallSecuritySettings,
    *,
    show_owner_access: bool,
) -> InlineKeyboardMarkup:
    """Build Call Security inline keyboard."""
    rows: list[list[InlineKeyboardButton]] = []
    if show_owner_access:
        rows.append(
            [
                _btn(
                    toggle_label(
                        lang,
                        t(lang, "call_security.btn_owner_access"),
                        settings.owner_access_enabled,
                    ),
                    CB["GRP_CALLSEC_OWNERS"],
                    toggle_state=settings.owner_access_enabled,
                )
            ]
        )
    rows.extend(
        [
            [
                _btn(
                    toggle_label(lang, t(lang, "call_security.btn_enabled"), settings.enabled),
                    CB["GRP_CALLSEC_TOGGLE"],
                    toggle_state=settings.enabled,
                ),
                _btn(
                    toggle_label(
                        lang,
                        t(lang, "call_security.btn_mute_incoming"),
                        settings.mute_incoming_enabled,
                    ),
                    CB["GRP_CALLSEC_MUTE_IN"],
                    toggle_state=settings.mute_incoming_enabled,
                ),
            ],
            [
                _btn(
                    toggle_label(
                        lang,
                        t(lang, "call_security.btn_summary"),
                        settings.summary_enabled,
                    ),
                    CB["GRP_CALLSEC_SUMMARY"],
                    toggle_state=settings.summary_enabled,
                ),
                _btn(
                    toggle_label(
                        lang,
                        t(lang, "call_security.btn_report"),
                        settings.report_enabled,
                    ),
                    CB["GRP_CALLSEC_REPORT"],
                    toggle_state=settings.report_enabled,
                ),
            ],
            [
                _btn(
                    t(
                        lang,
                        "call_security.btn_membership_age",
                        days=get_membership_age_threshold_days(settings),
                    ),
                    CB["GRP_CALLSEC_AGE"],
                )
            ],
            [_btn(t(lang, "common.buttons.back"), CB["GRP_CALLSEC_BACK"])],
        ]
    )
    return InlineKeyboardMarkup(rows)


async def toggle_field(
    chat_id: int,
    field: str,
    user_id: int,
    *,
    call_py: Any | None = None,
    client: Any | None = None,
) -> tuple[CallSecuritySettings | None, str | None]:
    """Toggle a boolean Call Security field with permission and credit checks.

    Returns:
        Tuple of (updated settings, error i18n key or None on success).
    """
    settings = await call_security_repo.get_or_create_call_security_settings(chat_id)
    if field == "owner_access_enabled":
        if not await can_toggle_owner_access(user_id, chat_id, client=client):
            return None, "call_security.no_permission"
    elif not await can_manage(user_id, chat_id, settings, client=client):
        return None, "call_security.no_permission"

    current = bool(getattr(settings, field))
    new_value = not current
    if field == "enabled" and new_value and not await has_active_credit(chat_id):
        return None, "call_security.no_credit"

    updated = await call_security_repo.update_call_security_setting(
        chat_id,
        field,
        new_value,
        updated_by=user_id,
    )
    if field == "mute_incoming_enabled" and new_value:
        caps = get_capabilities(call_py)
        if not caps.mute_enforcement:
            logger.info(
                "call_security mute_incoming enabled chat_id=%s mute_enforcement_unsupported",
                chat_id,
            )
    return updated, None


async def try_mute_participant(
    call_py: Any | None,
    chat_id: int,
    user_id: int,
) -> group_call_moderation_service.GroupCallModerationResult:
    """Attempt to mute a participant via raw MTProto; never fake success."""
    return await group_call_moderation_service.try_mute_participant(
        chat_id,
        user_id,
        muted=True,
        reason="call_security",
    )


async def try_unmute_participant(
    call_py: Any | None,
    chat_id: int,
    user_id: int,
    *,
    reason: str = "call_security",
) -> group_call_moderation_service.GroupCallModerationResult:
    """Attempt to unmute a participant via raw MTProto; never fake success."""
    return await group_call_moderation_service.try_unmute_participant(
        chat_id,
        user_id,
        reason=reason,
    )


def detect_suspicious_events(
    chat_id: int,
    user_id: int,
    participant: dict[str, Any],
    prior: dict[str, Any] | None,
    *,
    privileged: bool,
    mute_incoming: bool,
) -> list[SuspiciousEvent]:
    """Pure detection from normalized participant state."""
    events: list[SuspiciousEvent] = []
    now_ts = participant.get("timestamp")
    if prior and now_ts and prior.get("timestamp"):
        gap = float(now_ts) - float(prior["timestamp"])
        if gap > ABNORMAL_GAP_SECONDS:
            events.append(
                SuspiciousEvent(
                    reason="abnormal_time_gap",
                    user_id=user_id,
                    detail=str(int(gap)),
                )
            )

    if participant.get("just_joined") and not participant.get("left"):
        prior_join_count = int(prior.get("join_count", 0) if prior else 0)
        if prior_join_count >= 1 and not prior.get("left_since_last_join", True):
            events.append(SuspiciousEvent(reason="multiple_join", user_id=user_id))

        if (
            mute_incoming
            and not privileged
            and participant.get("muted") is False
            and participant.get("mic_known", True)
        ):
            events.append(SuspiciousEvent(reason="mic_active_on_entry", user_id=user_id))

    video_count = int(participant.get("video_join_count", 0))
    if participant.get("video_active") and not (prior or {}).get("video_active"):
        prior_video_count = int(prior.get("video_join_count", 0) if prior else 0)
        video_count = max(video_count, prior_video_count + 1)
    if video_count > 1 and not participant.get("left"):
        events.append(SuspiciousEvent(reason="multiple_video_joins", user_id=user_id))

    sources = int(participant.get("source_count", 0))
    if sources > 1:
        events.append(SuspiciousEvent(reason="multiple_sources", user_id=user_id))

    endpoints = int(participant.get("endpoint_count", 0))
    if endpoints > 1:
        events.append(SuspiciousEvent(reason="multiple_endpoints", user_id=user_id))

    return events


def normalize_participant_update(
    participant: Any,
    *,
    timestamp: float,
) -> dict[str, Any]:
    """Normalize raw GroupCallParticipant into a plain dict."""
    peer = getattr(participant, "peer", None)
    user_id = getattr(peer, "user_id", None) if peer is not None else None
    video = getattr(participant, "video", None)
    endpoint_count = 0
    source_count = 1 if getattr(participant, "source", None) else 0
    if video is not None:
        endpoint = getattr(video, "endpoint", None)
        if endpoint:
            endpoint_count = 1
        groups = getattr(video, "source_groups", None) or []
        source_count = max(source_count, sum(len(getattr(g, "sources", []) or []) for g in groups))

    return {
        "user_id": user_id,
        "muted": getattr(participant, "muted", None),
        "mic_known": getattr(participant, "muted", None) is not None,
        "left": bool(getattr(participant, "left", False)),
        "just_joined": bool(getattr(participant, "just_joined", False)),
        "source_count": source_count,
        "endpoint_count": endpoint_count,
        "video_active": video is not None,
        "timestamp": timestamp,
    }


async def should_auto_unmute(
    user_id: int,
    chat_id: int,
    settings: CallSecuritySettings,
    *,
    privileged: bool,
) -> bool:
    """Decide auto-unmute eligibility from role or observed group membership age."""
    if privileged:
        return True
    threshold = get_membership_age_threshold_days(settings)
    return await group_membership_age_service.is_membership_old_enough(
        chat_id,
        user_id,
        threshold,
    )


def write_summary_tempfile(
    chat_id: int,
    chat_title: str | None,
    events: list[SuspiciousEvent],
    *,
    started_at: str | None,
    ended_at: str | None,
) -> tuple[Path, str]:
    """Write Call Security summary to a temp text file."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    filename = f"call_security_summary_{chat_id}_{stamp}.txt"
    lines = [
        f"chat_id={chat_id}",
        f"chat_title={chat_title or '-'}",
        f"started_at={started_at or '-'}",
        f"ended_at={ended_at or '-'}",
        "",
    ]
    grouped: dict[str, list[SuspiciousEvent]] = {}
    for ev in events:
        grouped.setdefault(ev.reason, []).append(ev)
    for reason, items in grouped.items():
        lines.append(f"[{reason}] count={len(items)}")
        for ev in items:
            lines.append(f"  user_id={ev.user_id} at={ev.timestamp} detail={ev.detail or '-'}")
        lines.append("")
    Path(settings.TEMP_PATH).mkdir(parents=True, exist_ok=True)
    fd, raw_path = tempfile.mkstemp(
        prefix="callsec_summary_",
        suffix=".txt",
        dir=settings.TEMP_PATH,
    )
    os.close(fd)
    path = Path(raw_path)
    path.write_text("\n".join(lines), encoding="utf-8")
    return path, filename


def serialize_runtime_state(state: dict[str, Any]) -> str:
    """JSON-serialize runtime call state for Redis."""
    return json.dumps(state, ensure_ascii=False)


def deserialize_runtime_state(raw: str | None) -> dict[str, Any]:
    """Parse runtime call state from Redis."""
    if not raw:
        return {}
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}

"""Business logic for slash-free manager/group text commands."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import logging
from typing import Any

from pyrogram.enums import ChatMembersFilter, ChatMemberStatus

from app.config.settings import settings
from app.repositories import admin_repo, manager_command_repo as repo, settings_repo, user_repo
from app.services import group_runtime_state_service as runtime_state
from app.utils.bot_guards import is_developer
from app.utils.cache import invalidate_chat_settings
from app.utils.i18n import normalize_lang
from app.utils.sudo_permissions import (
    allow_group_chat_settings_change,
    can_use_sudo_admin_bypass,
    sudo_has_permission,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ManagerCommandResult:
    ok: bool
    reason: str
    params: dict[str, Any] = field(default_factory=dict)
    rows: list[Any] = field(default_factory=list)


@dataclass(frozen=True)
class InstallPlayerSetupState:
    chat_id: int
    title: str
    managed: bool
    settings_exists: bool
    credit_exists: bool
    credit_days: int
    credit_status: str
    credit_is_trial: bool
    language: str
    audio_enabled: bool
    video_enabled: bool
    helper_id: int | None
    helper_state: str | None
    helper_account_status: str | None
    helper_failed: bool
    music_admin_count: int
    video_admin_count: int
    player_owner_count: int
    player_deputy_count: int
    player_vip_count: int

    @property
    def config_done(self) -> bool:
        return (
            self.music_admin_count
            + self.video_admin_count
            + self.player_owner_count
            + self.player_deputy_count
        ) > 0

    @property
    def helper_installed(self) -> bool:
        return self.helper_id is not None and not self.helper_failed


def _role_row_label(row: Any) -> str:
    username = getattr(row, "username", None)
    if username:
        return f"@{username}"
    display_name = getattr(row, "display_name", None)
    user_id = getattr(row, "user_id", None)
    if display_name and user_id is not None:
        return f"{display_name} ({user_id})"
    return str(user_id or "-")


def _format_role_section(rows: list[Any]) -> str:
    if not rows:
        return "-"
    return "\n".join(f"- {_role_row_label(row)}" for row in rows)


async def can_manage_install_setup(user_id: int) -> bool:
    if is_developer(user_id) or await user_repo.is_owner(user_id):
        return True
    return await sudo_has_permission(user_id, "can_manage_groups", owner_bypass=False)


async def can_manage_setup_credit(user_id: int) -> bool:
    return is_developer(user_id)


async def can_manage_setup_helper(user_id: int, chat_id: int) -> bool:
    if is_developer(user_id) or await user_repo.is_owner(user_id):
        return True
    if await sudo_has_permission(user_id, "can_manage_chat_settings", owner_bypass=False):
        return True
    return await admin_repo.is_music_admin_or_above(user_id, chat_id)


async def can_manage_setup_config(user_id: int, chat_id: int) -> bool:
    if is_developer(user_id) or await user_repo.is_owner(user_id):
        return True
    if await can_use_sudo_admin_bypass(user_id):
        return True
    return await admin_repo.is_player_owner(user_id, chat_id)


async def can_manage_setup_access(user_id: int, chat_id: int) -> bool:
    return await allow_group_chat_settings_change(user_id, chat_id)


async def build_install_player_setup_state(chat_id: int) -> InstallPlayerSetupState:
    snapshot = await repo.get_install_setup_snapshot(chat_id)
    group = snapshot.group
    settings_row = snapshot.settings
    credit = snapshot.credit
    helper_failed = bool(
        snapshot.helper_id is not None
        and (
            (snapshot.helper_binding_state or "bound") != "bound"
            or snapshot.helper_last_error
            or (snapshot.helper_account_status not in (None, "active"))
        )
    )
    return InstallPlayerSetupState(
        chat_id=chat_id,
        title=(group.chat_title if group and group.chat_title else str(chat_id)),
        managed=group is not None,
        settings_exists=settings_row is not None,
        credit_exists=credit is not None,
        credit_days=int(getattr(credit, "credit_days", 0) or 0),
        credit_status=str(getattr(credit, "status", "") or ""),
        credit_is_trial=bool(getattr(credit, "is_trial", False)),
        language=normalize_lang(getattr(settings_row, "language", None)),
        audio_enabled=bool(getattr(settings_row, "audio_enabled", True)),
        video_enabled=bool(getattr(settings_row, "video_enabled", True)),
        helper_id=snapshot.helper_id,
        helper_state=snapshot.helper_binding_state,
        helper_account_status=snapshot.helper_account_status,
        helper_failed=helper_failed,
        music_admin_count=snapshot.music_admin_count,
        video_admin_count=snapshot.video_admin_count,
        player_owner_count=snapshot.player_owner_count,
        player_deputy_count=snapshot.player_deputy_count,
        player_vip_count=snapshot.player_vip_count,
    )


async def install_group(
    chat_id: int,
    chat_title: str | None,
    user_id: int | None,
) -> ManagerCommandResult:
    group, changed = await repo.install_group(chat_id, chat_title, user_id)
    trial_days = max(int(getattr(settings, "TRIAL_DAYS", 0) or 0), 0)
    await repo.ensure_credit_row(chat_id, days=trial_days, operated_by=user_id)
    if changed:
        await repo.add_install_log(chat_id, group.chat_title, user_id, "install")
        return ManagerCommandResult(
            True,
            "installed",
            {"chat_id": chat_id, "title": group.chat_title or str(chat_id), "days": trial_days},
        )
    return ManagerCommandResult(
        True,
        "already_installed",
        {"chat_id": chat_id, "title": group.chat_title or str(chat_id)},
    )


async def uninstall_group(
    chat_id: int,
    chat_title: str | None,
    user_id: int | None,
) -> ManagerCommandResult:
    group = await repo.get_active_group(chat_id)
    if group is None:
        return ManagerCommandResult(False, "not_managed", {"chat_id": chat_id})
    await repo.cleanup_group_management(chat_id)
    await repo.add_install_log(chat_id, chat_title or group.chat_title, user_id, "uninstall")
    return ManagerCommandResult(
        True,
        "uninstalled",
        {"chat_id": chat_id, "title": chat_title or group.chat_title or str(chat_id)},
    )


async def leave_group(
    client: Any,
    chat_id: int,
    chat_title: str | None,
    user_id: int | None,
) -> ManagerCommandResult:
    group = await repo.get_active_group(chat_id)
    if group is None:
        return ManagerCommandResult(False, "not_managed", {"chat_id": chat_id})

    helper_id = await repo.cleanup_group_management(chat_id)
    await repo.add_install_log(chat_id, chat_title or group.chat_title, user_id, "leave")

    helper_ok: bool | None = None
    if helper_id is not None:
        from app.services.helper_pool_service import HelperPoolService

        helper_ok = await HelperPoolService.leave_chat_as_helper(helper_id, chat_id)

    bot_ok = True
    try:
        leave = getattr(client, "leave_chat", None)
        if callable(leave):
            await leave(chat_id)
        else:
            bot_ok = False
    except Exception as exc:
        logger.info("manager text leave failed chat_id=%s err=%s", chat_id, type(exc).__name__)
        bot_ok = False

    if not bot_ok:
        return ManagerCommandResult(
            False,
            "bot_leave_failed",
            {"chat_id": chat_id, "helper_ok": helper_ok},
        )
    if helper_ok is False:
        return ManagerCommandResult(
            True,
            "left_helper_failed",
            {"chat_id": chat_id, "helper_id": helper_id},
        )
    return ManagerCommandResult(
        True,
        "left",
        {"chat_id": chat_id, "helper_id": helper_id},
    )


async def charge_group(
    chat_id: int,
    *,
    mode: str,
    amount: int | None,
    user_id: int | None,
) -> ManagerCommandResult:
    if not await runtime_state.require_active_group(chat_id, "group"):
        return ManagerCommandResult(False, "not_managed", {"chat_id": chat_id})
    update = await repo.apply_credit_update(
        chat_id,
        mode=mode,
        amount=amount,
        operated_by=user_id,
    )
    return ManagerCommandResult(
        True,
        "charge_updated",
        {
            "chat_id": chat_id,
            "mode": mode,
            "amount": amount or 0,
            "before": update.before,
            "after": update.after,
            "status": update.status,
        },
    )


async def add_helper(chat_id: int, *, bot_client: Any | None = None) -> ManagerCommandResult:
    if await repo.get_active_group(chat_id) is None:
        return ManagerCommandResult(False, "not_managed", {"chat_id": chat_id})
    from app.services.call_service import ensure_helper_present_for_group

    if bot_client is None:
        result = await ensure_helper_present_for_group(
            chat_id,
            reason="manager_text_addhelper",
        )
    else:
        result = await ensure_helper_present_for_group(
            chat_id,
            reason="manager_text_addhelper",
            bot_client=bot_client,
        )
    if result == "success":
        helper_id = await repo.get_bound_helper_id(chat_id)
        return ManagerCommandResult(True, "helper_added", {"helper_id": helper_id})
    if result == "already_present":
        helper_id = await repo.get_bound_helper_id(chat_id)
        return ManagerCommandResult(True, "helper_already_present", {"helper_id": helper_id})
    if result == "unavailable":
        return ManagerCommandResult(False, "helper_unavailable")
    if result == "promote_failed":
        return ManagerCommandResult(False, "helper_promote_failed")
    if result == "helper_user_unknown":
        return ManagerCommandResult(False, "helper_user_unknown")
    return ManagerCommandResult(False, "helper_join_failed")


async def apply_setup_charge(
    chat_id: int,
    *,
    duration_days: int,
    user_id: int | None,
) -> ManagerCommandResult:
    if not is_developer(user_id):
        return ManagerCommandResult(False, "no_access", {"chat_id": chat_id})
    if duration_days == 2:
        if not await runtime_state.require_active_group(chat_id, "group"):
            return ManagerCommandResult(False, "not_managed", {"chat_id": chat_id})
        state = await build_install_player_setup_state(chat_id)
        if state.credit_exists:
            if state.credit_status == "unlimited" or (
                state.credit_status == "active"
                and state.credit_days > 0
                and not state.credit_is_trial
            ):
                return ManagerCommandResult(
                    False,
                    "trial_active_credit",
                    {"chat_id": chat_id, "days": state.credit_days},
                )
            if state.credit_is_trial:
                return ManagerCommandResult(
                    False,
                    "trial_already_used",
                    {"chat_id": chat_id, "days": state.credit_days},
                )
        from app.services.credit_service import CreditService

        try:
            credit = await CreditService.activate_trial(chat_id, "group")
        except ValueError:
            return ManagerCommandResult(False, "trial_already_used", {"chat_id": chat_id})
        return ManagerCommandResult(
            True,
            "trial_started",
            {"chat_id": chat_id, "days": int(credit.credit_days or 0)},
        )
    try:
        if duration_days == 0:
            return await charge_group(chat_id, mode="unlimited", amount=None, user_id=user_id)
        return await charge_group(
            chat_id,
            mode="increase",
            amount=duration_days,
            user_id=user_id,
        )
    except ValueError as exc:
        if str(exc) == "only_developer_can_mutate_credit":
            return ManagerCommandResult(False, "no_access", {"chat_id": chat_id})
        raise


async def set_setup_language(chat_id: int, lang: str) -> ManagerCommandResult:
    if not await runtime_state.require_active_group(chat_id, "group"):
        return ManagerCommandResult(False, "not_managed", {"chat_id": chat_id})
    normalized = normalize_lang(lang)
    if normalized not in {"fa", "en"}:
        return ManagerCommandResult(False, "invalid_language", {"chat_id": chat_id})
    if await settings_repo.get_chat_settings(chat_id) is None:
        await settings_repo.create_defaults(chat_id)
    await settings_repo.update_setting(chat_id, "language", normalized)
    await invalidate_chat_settings(chat_id, "group")
    return ManagerCommandResult(True, "language_updated", {"chat_id": chat_id, "language": normalized})


async def toggle_setup_access(chat_id: int, access: str) -> ManagerCommandResult:
    if not await runtime_state.require_active_group(chat_id, "group"):
        return ManagerCommandResult(False, "not_managed", {"chat_id": chat_id})
    field_name = {
        "music": "audio_enabled",
        "video": "video_enabled",
    }.get(access)
    if field_name is None:
        return ManagerCommandResult(False, "invalid_access", {"chat_id": chat_id})
    cs = await settings_repo.get_chat_settings(chat_id)
    if cs is None:
        cs = await settings_repo.create_defaults(chat_id)
    new_value = not bool(getattr(cs, field_name, True))
    await settings_repo.update_setting(chat_id, field_name, new_value)
    await invalidate_chat_settings(chat_id, "group")
    return ManagerCommandResult(
        True,
        "access_updated",
        {"chat_id": chat_id, "access": access, "enabled": new_value},
    )


def _is_chat_creator(member: Any) -> bool:
    """True when a chat member row represents the Telegram group creator.

    Kurigram exposes ``ChatMemberStatus.OWNER``; older payloads and test doubles
    may carry the plain ``"creator"``/``"owner"`` string instead.
    """
    status = getattr(member, "status", None)
    if status is ChatMemberStatus.OWNER:
        return True
    value = str(getattr(status, "value", status) or "").lower()
    return value in {"creator", "owner"}


async def config_group_admins(client: Any, chat_id: int, user_id: int | None) -> ManagerCommandResult:
    if await repo.get_active_group(chat_id) is None:
        return ManagerCommandResult(False, "not_managed", {"chat_id": chat_id})
    get_chat_members = getattr(client, "get_chat_members", None)
    if not callable(get_chat_members):
        return ManagerCommandResult(False, "telegram_admins_unavailable")

    rows: list[dict[str, object]] = []
    creator: dict[str, Any] | None = None
    skipped = 0
    failures = 0
    try:
        async for member in get_chat_members(chat_id, filter=ChatMembersFilter.ADMINISTRATORS):
            user = getattr(member, "user", None)
            if user is None or getattr(user, "is_bot", False):
                skipped += 1
                continue
            uid = getattr(user, "id", None)
            if uid is None:
                skipped += 1
                continue
            if _is_chat_creator(member):
                creator = {
                    "user_id": int(uid),
                    "username": getattr(user, "username", None),
                    "display_name": getattr(user, "first_name", None),
                }
            rows.append(
                {
                    "user_id": int(uid),
                    "username": getattr(user, "username", None),
                    "display_name": getattr(user, "first_name", None),
                    "promoted_by": user_id,
                }
            )
    except Exception as exc:
        logger.info("config admin import failed chat_id=%s err=%s", chat_id, type(exc).__name__)
        failures += 1

    if failures:
        return ManagerCommandResult(False, "telegram_admins_unavailable", {"failures": failures})
    if not rows:
        return ManagerCommandResult(False, "telegram_admins_empty", {"skipped": skipped})

    imported = await repo.replace_music_admins(chat_id, rows)
    if creator is not None:
        try:
            await repo.promote_role(
                "owner",
                chat_id,
                int(creator["user_id"]),
                username=creator.get("username"),
                display_name=creator.get("display_name"),
                promoted_by=user_id,
            )
        except Exception as exc:  # pragma: no cover - defensive, import already succeeded
            logger.info(
                "config creator owner grant failed chat_id=%s err=%s",
                chat_id,
                type(exc).__name__,
            )
    owners = await admin_repo.get_player_owners(chat_id)
    deputies = await admin_repo.get_player_deputies(chat_id)
    admins = await admin_repo.get_music_admins(chat_id)
    vips = (await admin_repo.get_player_vips_page(chat_id, 0, 1000))[0]
    return ManagerCommandResult(
        True,
        "config_imported",
        {
            "imported": imported,
            "skipped": skipped,
            "failures": failures,
            "owners_section": _format_role_section(owners),
            "deputies_section": _format_role_section(deputies),
            "admins_section": _format_role_section(admins),
            "vips_section": _format_role_section(vips),
        },
    )


async def expire_status(chat_id: int) -> ManagerCommandResult:
    state = await runtime_state.get_runtime_credit_state(chat_id, "group")
    if not state.is_active:
        return ManagerCommandResult(False, "not_managed", {"chat_id": chat_id})
    credit = state.credit
    if credit is None:
        return ManagerCommandResult(
            False,
            "credit_missing",
            {"chat_id": chat_id},
        )
    status = str(credit.status or "")
    return ManagerCommandResult(
        True,
        "expire_status",
        {
            "chat_id": chat_id,
            "days": int(credit.credit_days or 0),
            "status": status,
            "unlimited": status == "unlimited",
            "expire_at": getattr(credit, "expire_at", None),
        },
    )


def duration_to_expires_at(duration: str | None) -> datetime | None:
    """Convert a parsed ``<n>d`` / ``<n>h`` duration token to an expiry instant."""
    if not duration:
        return None
    value, unit = duration[:-1], duration[-1]
    if not value.isdigit():
        return None
    amount = int(value)
    if amount <= 0:
        return None
    delta = timedelta(days=amount) if unit == "d" else timedelta(hours=amount)
    return datetime.now(timezone.utc) + delta


async def add_role(
    role: str,
    chat_id: int,
    target: dict[str, Any],
    promoted_by: int | None,
    *,
    expires_at: datetime | None = None,
) -> ManagerCommandResult:
    if await repo.get_active_group(chat_id) is None:
        return ManagerCommandResult(False, "not_managed", {"chat_id": chat_id})
    user_id = int(target["user_id"])
    added = await repo.promote_role(
        role,
        chat_id,
        user_id,
        username=target.get("username"),
        display_name=target.get("display_name"),
        promoted_by=promoted_by,
        expires_at=expires_at,
    )
    return ManagerCommandResult(
        True,
        "role_added" if added else "role_exists",
        {
            "role": role,
            "user_id": user_id,
            "expires_at": expires_at,
        },
    )


async def remove_role(role: str, chat_id: int, user_id: int) -> ManagerCommandResult:
    if await repo.get_active_group(chat_id) is None:
        return ManagerCommandResult(False, "not_managed", {"chat_id": chat_id})
    protected = await repo.protected_user_ids(
        chat_id,
        include_player_owners=role != "owner",
        include_player_deputies=role != "deputy",
        include_player_vips=role == "mod",
    )
    if int(user_id) in protected:
        return ManagerCommandResult(False, "protected_role", {"role": role, "user_id": user_id})
    removed = await repo.demote_role(role, chat_id, int(user_id))
    return ManagerCommandResult(
        True,
        "role_removed" if removed else "role_not_found",
        {"role": role, "user_id": user_id},
    )


async def list_role(role: str, chat_id: int) -> ManagerCommandResult:
    if await repo.get_active_group(chat_id) is None:
        return ManagerCommandResult(False, "not_managed", {"chat_id": chat_id})
    rows = await repo.list_roles(role, chat_id)
    return ManagerCommandResult(True, "role_list", {"role": role}, rows=rows)


async def clear_role(role: str, chat_id: int) -> ManagerCommandResult:
    if await repo.get_active_group(chat_id) is None:
        return ManagerCommandResult(False, "not_managed", {"chat_id": chat_id})
    removed = await repo.clear_roles(
        role,
        chat_id,
        protect_player_owners=role != "owner",
        protect_player_deputies=role != "deputy",
        protect_player_vips=role == "mod",
    )
    return ManagerCommandResult(True, "role_cleared", {"role": role, "count": removed})

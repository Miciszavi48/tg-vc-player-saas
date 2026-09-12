"""Slash-free manager/admin and group management command handlers."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import logging
import re
from typing import Any

from pyrogram import Client, filters
from pyrogram.types import Message

from app.handlers.priority import PRIORITY_COMMAND_GROUP
from app.repositories import admin_repo, user_repo
from app.services import manager_command_service as svc
from app.utils.bot_guards import is_developer
from app.utils.i18n import AUTO_LANG, t
from app.utils.manager_text_commands import (
    ManagerTextCommand,
    manager_text_command_filter,
    parse_manager_text_command,
)
from app.utils.sudo_permissions import can_use_sudo_admin_bypass, sudo_has_permission

logger = logging.getLogger(__name__)

_LANG = AUTO_LANG
_USERNAME_RE = re.compile(r"^@[A-Za-z0-9_]{5,32}$")

_INSTALL_FAMILIES = {"install", "uninstall", "leave"}
_ROLE_MAP = {
    "owner": {"owner_add", "owner_remove", "owner_list", "owner_clear"},
    "deputy": {"deputy_add", "deputy_remove", "deputy_list", "deputy_clear"},
    "mod": {"mod_add", "mod_remove", "mod_list", "mod_clear"},
    "vip": {"vip_add", "vip_remove", "vip_list", "vip_clear"},
}
_ROLE_LABEL_KEYS = {
    "owner": "manager_text.role_owner",
    "deputy": "manager_text.role_deputy",
    "mod": "manager_text.role_mod",
    "vip": "manager_text.role_vip",
}
_CHARGE_MODE_KEYS = {
    "increase": "manager_text.charge_mode_increase",
    "decrease": "manager_text.charge_mode_decrease",
    "unlimited": "manager_text.charge_mode_unlimited",
}


def _chat_type_value(message: Message) -> str:
    chat_type = getattr(getattr(message, "chat", None), "type", None)
    return str(getattr(chat_type, "value", chat_type) or "")


def _is_group_message(message: Message) -> bool:
    return _chat_type_value(message) in {"group", "supergroup"}


def _is_private_message(message: Message) -> bool:
    return _chat_type_value(message) == "private"


async def _reply(message: Message, text: str, **kwargs: Any) -> None:
    reply = getattr(message, "reply", None)
    if callable(reply):
        await reply(text, **kwargs)
        return
    reply_text = getattr(message, "reply_text", None)
    if callable(reply_text):
        await reply_text(text, **kwargs)


async def _can_manage_install_family(user_id: int, family: str) -> bool:
    if is_developer(user_id) or await user_repo.is_owner(user_id):
        return True
    if family == "install":
        return await sudo_has_permission(user_id, "can_manage_groups", owner_bypass=False)
    if family in {"uninstall", "leave"}:
        return await sudo_has_permission(user_id, "can_remove_bot", owner_bypass=False)
    return False


async def _can_manage_credit(user_id: int) -> bool:
    return is_developer(user_id)


async def _can_manage_helper(user_id: int, chat_id: int) -> bool:
    if is_developer(user_id) or await user_repo.is_owner(user_id):
        return True
    if await sudo_has_permission(user_id, "can_manage_chat_settings", owner_bypass=False):
        return True
    return await admin_repo.is_music_admin_or_above(user_id, chat_id)


async def _can_manage_config(client: Client, user_id: int, chat_id: int) -> bool:
    if is_developer(user_id) or await user_repo.is_owner(user_id):
        return True
    if await can_use_sudo_admin_bypass(user_id):
        return True
    from app.utils.player_permissions import can_manage_deputy

    return await can_manage_deputy(client, chat_id, user_id)


async def _can_manage_owner(client: Client, user_id: int, chat_id: int) -> bool:
    from app.utils.player_permissions import can_manage_player_owner

    return await can_manage_player_owner(client, chat_id, user_id)


async def _can_manage_deputy(client: Client, user_id: int, chat_id: int) -> bool:
    from app.utils.player_permissions import can_manage_deputy

    return await can_manage_deputy(client, chat_id, user_id)


async def _can_manage_mod(client: Client, user_id: int, chat_id: int) -> bool:
    from app.utils.player_permissions import can_manage_admin

    return await can_manage_admin(client, chat_id, user_id)


async def _can_manage_vip(client: Client, user_id: int, chat_id: int) -> bool:
    from app.utils.player_permissions import can_manage_vip

    return await can_manage_vip(client, chat_id, user_id)


async def _has_permission(client: Client, parsed: ManagerTextCommand, message: Message) -> bool:
    user_id = getattr(getattr(message, "from_user", None), "id", None)
    chat_id = getattr(getattr(message, "chat", None), "id", None)
    if user_id is None:
        return False
    user_id = int(user_id)
    if parsed.family in _INSTALL_FAMILIES:
        return await _can_manage_install_family(user_id, parsed.family)
    if parsed.family == "charge":
        return await _can_manage_credit(user_id)
    if parsed.family == "expire":
        return True
    if chat_id is None:
        return False
    chat_id = int(chat_id)
    if parsed.family == "add_helper":
        return await _can_manage_helper(user_id, chat_id)
    if parsed.family == "config":
        return await _can_manage_config(client, user_id, chat_id)
    if parsed.family.startswith("owner_"):
        return await _can_manage_owner(client, user_id, chat_id)
    if parsed.family.startswith("deputy_"):
        return await _can_manage_deputy(client, user_id, chat_id)
    if parsed.family.startswith("mod_"):
        return await _can_manage_mod(client, user_id, chat_id)
    if parsed.family.startswith("vip_"):
        return await _can_manage_vip(client, user_id, chat_id)
    return False


def _parser_error_key(error: str | None) -> str | None:
    return {
        "missing_amount": "manager_text.missing_amount",
        "invalid_amount": "manager_text.invalid_amount",
        "invalid_chat_id": "manager_text.invalid_chat_id",
    }.get(error or "")


def _role_from_family(family: str) -> str:
    for role, families in _ROLE_MAP.items():
        if family in families:
            return role
    raise ValueError(f"unknown role family {family}")


def _role_label(role: str) -> str:
    return t(_LANG, _ROLE_LABEL_KEYS.get(role, "manager_text.role_mod"))


def _stop_message_propagation(message: Message) -> None:
    stop_propagation = getattr(message, "stop_propagation", None)
    if callable(stop_propagation):
        stop_propagation()


def _result_text(result: svc.ManagerCommandResult) -> str:
    p = result.params
    key = {
        "installed": "manager_text.install_success",
        "already_installed": "manager_text.install_already",
        "uninstalled": "manager_text.uninstall_success",
        "left": "manager_text.leave_success",
        "left_helper_failed": "manager_text.leave_helper_failed",
        "bot_leave_failed": "manager_text.leave_bot_failed",
        "not_managed": "manager_text.group_not_managed",
        "helper_added": "manager_text.helper_added",
        "helper_already_present": "manager_text.helper_already_present",
        "helper_unavailable": "manager_text.helper_unavailable",
        "helper_join_failed": "manager_text.helper_join_failed",
        "helper_promote_failed": "manager_text.helper_promote_failed",
        "helper_user_unknown": "manager_text.helper_user_unknown",
        "telegram_admins_unavailable": "manager_text.config_failed",
        "telegram_admins_empty": "manager_text.config_empty",
        "config_imported": "manager_text.config_imported",
        "credit_missing": "manager_text.credit_missing",
        "protected_role": "manager_text.role_protected",
        "role_exists": "manager_text.role_exists",
        "role_added": "manager_text.role_added",
        "role_not_found": "manager_text.role_not_found",
        "role_removed": "manager_text.role_removed",
        "role_cleared": "manager_text.role_cleared",
    }.get(result.reason)
    if key is not None:
        role = str(p.get("role", ""))
        return t(
            _LANG,
            key,
            chat_id=p.get("chat_id", ""),
            title=p.get("title", ""),
            days=p.get("days", ""),
            imported=p.get("imported", 0),
            skipped=p.get("skipped", 0),
            failures=p.get("failures", 0),
            owners_section=p.get("owners_section", "-"),
            deputies_section=p.get("deputies_section", "-"),
            admins_section=p.get("admins_section", "-"),
            vips_section=p.get("vips_section", "-"),
            user_id=p.get("user_id", ""),
            role=_role_label(role) if role else "",
            count=p.get("count", 0),
        )
    if result.reason == "charge_updated":
        if p.get("status") == "unlimited":
            return t(
                _LANG,
                "manager_text.charge_unlimited",
                chat_id=p.get("chat_id", ""),
                before=p.get("before", 0),
                after=p.get("after", 0),
            )
        return t(
            _LANG,
            "manager_text.charge_updated",
            chat_id=p.get("chat_id", ""),
            mode=t(_LANG, _CHARGE_MODE_KEYS.get(str(p.get("mode")), "manager_text.charge_mode_increase")),
            amount=p.get("amount", 0),
            before=p.get("before", 0),
            after=p.get("after", 0),
        )
    if result.reason == "expire_status":
        if p.get("unlimited"):
            return t(
                _LANG,
                "manager_text.expire_unlimited",
                chat_id=p.get("chat_id", ""),
                status=p.get("status", ""),
            )
        return t(
            _LANG,
            "manager_text.expire_status",
            chat_id=p.get("chat_id", ""),
            days=p.get("days", 0),
            status=p.get("status", ""),
        )
    return t(_LANG, "manager_text.db_error")


async def _resolve_target_user(
    client: Client,
    message: Message,
    target_text: str | None,
) -> dict[str, Any] | None:
    if target_text:
        token = target_text.strip().split(maxsplit=1)[0]
        if re.fullmatch(r"\d+", token):
            return {"user_id": int(token), "username": None, "display_name": None}
        if _USERNAME_RE.fullmatch(token):
            get_users = getattr(client, "get_users", None)
            if callable(get_users):
                try:
                    user = await get_users(token)
                    uid = getattr(user, "id", None)
                    if uid is not None:
                        return {
                            "user_id": int(uid),
                            "username": getattr(user, "username", None),
                            "display_name": getattr(user, "first_name", None),
                        }
                except Exception:
                    return None

    reply = getattr(message, "reply_to_message", None)
    reply_user = getattr(reply, "from_user", None)
    if reply_user is not None and getattr(reply_user, "id", None) is not None:
        return {
            "user_id": int(reply_user.id),
            "username": getattr(reply_user, "username", None),
            "display_name": getattr(reply_user, "first_name", None),
        }

    raw_text = getattr(message, "text", None) or getattr(message, "caption", None) or ""
    for entity in getattr(message, "entities", None) or []:
        entity_type = getattr(getattr(entity, "type", None), "value", None) or getattr(
            entity,
            "type",
            None,
        )
        if str(entity_type) == "text_mention":
            user = getattr(entity, "user", None)
            if user is not None and getattr(user, "id", None) is not None:
                return {
                    "user_id": int(user.id),
                    "username": getattr(user, "username", None),
                    "display_name": getattr(user, "first_name", None),
                }
        if str(entity_type) == "mention":
            offset = int(getattr(entity, "offset", 0))
            length = int(getattr(entity, "length", 0))
            username = raw_text[offset : offset + length]
            if _USERNAME_RE.fullmatch(username):
                get_users = getattr(client, "get_users", None)
                if callable(get_users):
                    try:
                        user = await get_users(username)
                        uid = getattr(user, "id", None)
                        if uid is not None:
                            return {
                                "user_id": int(uid),
                                "username": getattr(user, "username", None),
                                "display_name": getattr(user, "first_name", None),
                            }
                    except Exception:
                        return None
    return None


def _format_role_list(role: str, rows: list[Any]) -> str:
    if not rows:
        return t(_LANG, "manager_text.role_list_empty", role=_role_label(role))
    lines = [t(_LANG, "manager_text.role_list_title", role=_role_label(role))]
    for row in rows:
        lines.append(
            t(
                _LANG,
                "list_fmt.user_item",
                user_id=getattr(row, "user_id", ""),
                username=getattr(row, "username", None) or "-",
            )
        )
    return "\n".join(lines)


async def _dispatch(
    client: Client,
    message: Message,
    parsed: ManagerTextCommand,
) -> svc.ManagerCommandResult:
    chat_id = int(message.chat.id)
    user_id = int(message.from_user.id) if message.from_user else None
    title = getattr(message.chat, "title", None) or str(chat_id)

    if parsed.family == "install":
        return await svc.install_group(chat_id, title, user_id)
    if parsed.family == "uninstall":
        return await svc.uninstall_group(chat_id, title, user_id)
    if parsed.family == "leave":
        return await svc.leave_group(client, chat_id, title, user_id)
    if parsed.family == "charge":
        target_chat_id = parsed.target_chat_id if parsed.target_chat_id is not None else chat_id
        return await svc.charge_group(
            target_chat_id,
            mode=parsed.charge_mode or "increase",
            amount=parsed.amount,
            user_id=user_id,
        )
    if parsed.family == "add_helper":
        return await svc.add_helper(chat_id)
    if parsed.family == "config":
        return await svc.config_group_admins(client, chat_id, user_id)
    if parsed.family == "expire":
        return await svc.expire_status(chat_id)

    role = _role_from_family(parsed.family)
    if parsed.family.endswith("_add"):
        target = await _resolve_target_user(client, message, parsed.target_user)
        if target is None:
            return svc.ManagerCommandResult(False, "target_missing", {"role": role})
        return await svc.add_role(
            role,
            chat_id,
            target,
            user_id,
            expires_at=svc.duration_to_expires_at(parsed.duration),
        )
    if parsed.family.endswith("_remove"):
        target = await _resolve_target_user(client, message, parsed.target_user)
        if target is None:
            return svc.ManagerCommandResult(False, "target_missing", {"role": role})
        return await svc.remove_role(role, chat_id, int(target["user_id"]))
    if parsed.family.endswith("_list"):
        return await svc.list_role(role, chat_id)
    if parsed.family.endswith("_clear"):
        return await svc.clear_role(role, chat_id)

    return svc.ManagerCommandResult(False, "db_error")


def _format_expiry(value: datetime | None) -> str:
    if value is None:
        return "-"
    return value.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M")


async def _handle_result(message: Message, parsed: ManagerTextCommand, result: svc.ManagerCommandResult) -> None:
    if result.reason == "target_missing":
        await _reply(message, t(_LANG, "manager_text.target_missing"))
        return
    if result.reason == "role_list":
        await _reply(message, _format_role_list(str(result.params.get("role")), result.rows))
        return
    if parsed.family == "install" and result.reason in {"installed", "already_installed"}:
        from app.handlers.group_panel import reply_install_player_setup_panel

        await reply_install_player_setup_panel(message)
        return
    if parsed.family == "vip_add" and result.reason in {"role_added", "role_exists"}:
        await _reply_vip_grant(message, result)
        return
    await _reply(message, _result_text(result))


async def _reply_vip_grant(message: Message, result: svc.ManagerCommandResult) -> None:
    """Confirm a VIP grant and offer the evidenced day/hour expiry picker."""
    from app.utils.ui import KeyboardFactory

    expires_at = result.params.get("expires_at")
    target_id = int(result.params.get("user_id") or 0)
    if expires_at is not None:
        text = t(
            _LANG,
            "manager_text.role_added_until",
            role=_role_label("vip"),
            user_id=target_id,
            expires_at=_format_expiry(expires_at),
        )
    else:
        text = _result_text(result) + "\n" + t(_LANG, "manager_text.vip_dur_prompt")
    await _reply(
        message,
        text,
        reply_markup=KeyboardFactory.vip_duration_picker(_LANG, target_id),
    )


_VIP_DURATION_RE = re.compile(
    r"^vipd:(?P<act>dp|dm|hp|hm|ok|x):(?P<uid>\d{1,20}):(?P<days>\d{1,4}):(?P<hours>\d{1,4})$"
)
_VIP_DURATION_MAX_DAYS = 3650
_VIP_DURATION_MAX_HOURS = 23


def register(bot: Client, call_py) -> None:  # noqa: ARG001
    """Register slash-free manager/group text commands."""

    @bot.on_callback_query(filters.regex(_VIP_DURATION_RE))
    async def vip_duration_picker(client: Client, query):
        """Day/hour stepper that sets the expiry of an already granted VIP role."""
        from app.repositories import manager_command_repo as role_repo
        from app.utils.callback_trace import safe_answer_callback
        from app.utils.ui import KeyboardFactory

        match = _VIP_DURATION_RE.match(str(getattr(query, "data", "") or ""))
        if match is None:
            await safe_answer_callback(
                query, t(_LANG, "common.errors.unknown_callback"), show_alert=True
            )
            return

        chat = getattr(getattr(query, "message", None), "chat", None)
        chat_id = getattr(chat, "id", None)
        actor_id = getattr(getattr(query, "from_user", None), "id", None)
        if chat_id is None:
            # Stale/edited panel: the origin chat is unknowable, so reject explicitly.
            await safe_answer_callback(
                query, t(_LANG, "common.errors.unknown_callback"), show_alert=True
            )
            return
        if actor_id is None or not await _can_manage_vip(client, int(actor_id), int(chat_id)):
            await safe_answer_callback(
                query, t(_LANG, "manager_text.no_permission"), show_alert=True
            )
            return

        act = match.group("act")
        target_id = int(match.group("uid"))
        days = int(match.group("days"))
        hours = int(match.group("hours"))

        if act == "x":
            await safe_answer_callback(query, t(_LANG, "manager_text.vip_dur_closed"))
            edit = getattr(query, "edit_message_reply_markup", None)
            if callable(edit):
                await edit(reply_markup=None)
            return

        if act == "ok":
            if days == 0 and hours == 0:
                await safe_answer_callback(
                    query, t(_LANG, "manager_text.vip_dur_zero"), show_alert=True
                )
                return
            if not await admin_repo.is_vip(target_id, int(chat_id)):
                await safe_answer_callback(
                    query, t(_LANG, "manager_text.vip_dur_not_found"), show_alert=True
                )
                return
            expires_at = datetime.now(timezone.utc) + timedelta(days=days, hours=hours)
            await role_repo.promote_role(
                "vip",
                int(chat_id),
                target_id,
                username=None,
                display_name=None,
                promoted_by=int(actor_id),
                expires_at=expires_at,
            )
            await safe_answer_callback(query, t(_LANG, "manager_text.vip_dur_closed"))
            edit_text = getattr(query, "edit_message_text", None)
            if callable(edit_text):
                await edit_text(
                    t(
                        _LANG,
                        "manager_text.vip_dur_set",
                        user_id=target_id,
                        expires_at=_format_expiry(expires_at),
                    )
                )
            return

        if act == "dp":
            days = min(days + 1, _VIP_DURATION_MAX_DAYS)
        elif act == "dm":
            days = max(days - 1, 0)
        elif act == "hp":
            hours = min(hours + 1, _VIP_DURATION_MAX_HOURS)
        elif act == "hm":
            hours = max(hours - 1, 0)

        await safe_answer_callback(query)
        edit_markup = getattr(query, "edit_message_reply_markup", None)
        if callable(edit_markup):
            await edit_markup(
                reply_markup=KeyboardFactory.vip_duration_picker(
                    _LANG, target_id, days, hours
                )
            )

    @bot.on_message(filters.text & manager_text_command_filter(), group=PRIORITY_COMMAND_GROUP)
    async def manager_text_command(client: Client, message: Message):
        parsed = parse_manager_text_command(message)
        if parsed is None:
            continue_propagation = getattr(message, "continue_propagation", None)
            if callable(continue_propagation):
                continue_propagation()
            return

        if parsed.family != "charge" and not _is_group_message(message):
            await _reply(message, t(_LANG, "manager_text.group_only"))
            _stop_message_propagation(message)
            return
        if parsed.family == "charge":
            if _is_private_message(message) and parsed.target_chat_id is None:
                await _reply(message, t(_LANG, "manager_text.private_target_required"))
                _stop_message_propagation(message)
                return
            if _is_group_message(message) and parsed.target_chat_id is not None:
                await _reply(message, t(_LANG, "manager_text.private_target_only"))
                _stop_message_propagation(message)
                return
            if not (_is_group_message(message) or _is_private_message(message)):
                await _reply(message, t(_LANG, "manager_text.wrong_scope"))
                _stop_message_propagation(message)
                return

        error_key = _parser_error_key(parsed.error)
        if error_key is not None:
            await _reply(message, t(_LANG, error_key))
            _stop_message_propagation(message)
            return

        if not await _has_permission(client, parsed, message):
            await _reply(message, t(_LANG, "manager_text.no_permission"))
            _stop_message_propagation(message)
            return

        try:
            result = await _dispatch(client, message, parsed)
        except ValueError:
            await _reply(message, t(_LANG, "manager_text.invalid_amount"))
            _stop_message_propagation(message)
            return
        except Exception as exc:
            logger.exception(
                "manager text command failed family=%s chat_id=%s err=%s",
                parsed.family,
                getattr(getattr(message, "chat", None), "id", None),
                type(exc).__name__,
            )
            await _reply(message, t(_LANG, "manager_text.db_error"))
            _stop_message_propagation(message)
            return

        await _handle_result(message, parsed, result)
        _stop_message_propagation(message)

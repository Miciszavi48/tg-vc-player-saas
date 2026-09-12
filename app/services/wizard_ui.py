from __future__ import annotations

import json
import logging

from pyrogram import Client
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.repositories import user_repo
from app.utils.bot_guards import is_developer
from app.services.admin_dashboard_service import AdminDashboardService
from app.services.helper_pool_service import HelperPoolService
from app.services.install_policy_service import InstallPolicyService
from app.services.panel_router import build_private_root_payload
from app.services.texts_links_ui import build_texts_hub_payload
from app.utils.button_style import compatible_inline_button
from app.utils.cache import get_redis
from app.utils.i18n import label, t
from app.utils.redis_keys import (
    TTL_WIZARD_RETURN,
    bcw_state_key,
    helper_otp_state_key,
    helper_proxy_state_key,
    wizard_return_key,
)
from app.utils.ui import CB, KeyboardFactory

logger = logging.getLogger(__name__)

TOKEN_ROLE_ROOT = "role_root"
TOKEN_DEV_ROOT = "dev_root"
TOKEN_DEV_CREDIT = "dev_credit"
TOKEN_DEV_RATES = "dev_rates"
TOKEN_DEV_BROADCAST = "dev_broadcast"
TOKEN_DEV_LISTS = "dev_lists"
TOKEN_DEV_SETTINGS = "dev_settings"
TOKEN_DEV_USERS = "dev_users"
TOKEN_DEV_TEXTS = "dev_texts"
TOKEN_DEV_INSTALL_POLICY = "dev_install_policy"
TOKEN_DEV_MONTHLY_INVOICE = "dev_monthly_invoice"
TOKEN_DEV_FORCE_JOIN = "dev_force_join"
TOKEN_DEV_MODERATION = "dev_moderation"
TOKEN_OWNER_ROOT = "owner_root"
TOKEN_OWNER_TEXTS = "owner_texts"
TOKEN_GROUP_ROOT = "group_root"
TOKEN_HELPER_HOME = "helper_home"
WIZARD_HELPER_PROXY = "helper_proxy"
WIZARD_HELPER_OTP = "helper_otp"
WIZARD_BROADCAST = "broadcast_wizard"

_WIZARD_PRIORITY = (
    WIZARD_HELPER_PROXY,
    WIZARD_HELPER_OTP,
    WIZARD_BROADCAST,
)

_KNOWN_TOKENS = {
    TOKEN_ROLE_ROOT,
    TOKEN_DEV_ROOT,
    TOKEN_DEV_CREDIT,
    TOKEN_DEV_RATES,
    TOKEN_DEV_BROADCAST,
    TOKEN_DEV_LISTS,
    TOKEN_DEV_SETTINGS,
    TOKEN_DEV_USERS,
    TOKEN_DEV_TEXTS,
    TOKEN_DEV_INSTALL_POLICY,
    TOKEN_DEV_MONTHLY_INVOICE,
    TOKEN_DEV_FORCE_JOIN,
    TOKEN_DEV_MODERATION,
    TOKEN_OWNER_ROOT,
    TOKEN_OWNER_TEXTS,
    TOKEN_GROUP_ROOT,
    TOKEN_HELPER_HOME,
}

_DEVELOPER_NAV_TOKENS = {
    TOKEN_DEV_CREDIT,
    TOKEN_DEV_RATES,
    TOKEN_DEV_BROADCAST,
    TOKEN_DEV_LISTS,
    TOKEN_DEV_SETTINGS,
    TOKEN_DEV_USERS,
    TOKEN_DEV_TEXTS,
    TOKEN_DEV_INSTALL_POLICY,
    TOKEN_DEV_MONTHLY_INVOICE,
    TOKEN_DEV_FORCE_JOIN,
    TOKEN_DEV_MODERATION,
    TOKEN_HELPER_HOME,
}

_OWNER_NAV_TOKENS = {
    TOKEN_OWNER_TEXTS,
}


def _btn(
    label: str,
    callback_data: str,
) -> InlineKeyboardButton:
    return compatible_inline_button(label, callback_data=callback_data)


def _wizard_state_keys(user_id: int) -> dict[str, str]:
    return {
        WIZARD_HELPER_PROXY: helper_proxy_state_key(user_id),
        WIZARD_HELPER_OTP: helper_otp_state_key(user_id),
        WIZARD_BROADCAST: bcw_state_key(user_id),
    }


def _safe_token(token: str | None) -> str:
    if token in _KNOWN_TOKENS:
        return token
    return TOKEN_ROLE_ROOT


async def _can_resolve_navigation_token(user_id: int, token: str) -> bool:
    if token in _DEVELOPER_NAV_TOKENS:
        return is_developer(user_id)
    if token in _OWNER_NAV_TOKENS:
        if is_developer(user_id):
            return True
        try:
            return await user_repo.is_owner(user_id)
        except Exception:
            return False
    return True


async def _navigation_denied_payload(
    client: Client,
    user_id: int,
    lang: str,
) -> tuple[str, InlineKeyboardMarkup]:
    text, kb = await build_private_root_payload(
        client,
        user_id,
        str(user_id),
        lang,
        include_welcome=False,
    )
    return f"{t(lang, 'common.errors.no_access')}\n\n{text}", kb


def resolve_role_root_callback(role: str) -> str:  # noqa: ARG001
    return CB["WZ_HOME"]


def wz_cancel_callback(return_to: str) -> str:
    return f"{CB['WZ_CANCEL_PREFIX']}{_safe_token(return_to)}"


def wz_back_callback(return_to: str) -> str:
    return f"{CB['WZ_BACK_PREFIX']}{_safe_token(return_to)}"


def build_cancel_kb(
    lang: str,
    return_to: str,
    *,
    include_home: bool = True,
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = [
        [
            _btn(
                t(lang, "common.buttons.cancel_inline"),
                wz_cancel_callback(return_to),
            )
        ],
    ]
    return InlineKeyboardMarkup(rows)


def build_done_kb(
    lang: str,
    return_to: str,
    *,
    include_home: bool = True,
) -> InlineKeyboardMarkup:
    nav = [_btn(t(lang, "common.buttons.back"), wz_back_callback(return_to))]
    if include_home:
        nav.append(_btn(t(lang, "common.buttons.home"), CB["WZ_HOME"]))
    return InlineKeyboardMarkup([nav])


def _bool_label(lang: str, enabled: bool) -> str:
    return t(lang, "common.labels.on") if enabled else t(lang, "common.labels.off")


def _summary_row(lang: str, label_key: str, value: int | str) -> str:
    template = t(lang, "list_fmt.setting_entry")
    return template.format(
        key=t(lang, label_key),
        value=str(value),
    )


async def _build_dev_credit_payload(lang: str) -> tuple[str, InlineKeyboardMarkup]:
    summary = await AdminDashboardService.get_credit_summary()
    lines = [
        t(lang, "panels.developer.cat_credit_title"),
        "",
        t(lang, "panels.developer.cat_credit_desc"),
        "",
        _summary_row(lang, "panels.developer.summary_active_installs", summary["active_installs"]),
        _summary_row(lang, "panels.developer.summary_expiring_24h", summary["expiring_24h"]),
        _summary_row(lang, "panels.developer.summary_no_credit", summary["no_credit"]),
        _summary_row(lang, "panels.developer.summary_invoices_24h", summary["invoices_24h"]),
    ]
    return "\n".join(lines), KeyboardFactory.dev_sub_credit(lang)


async def _build_dev_lists_payload(lang: str) -> tuple[str, InlineKeyboardMarkup]:
    summary = await AdminDashboardService.get_lists_summary()
    lines = [
        t(lang, "panels.developer.cat_lists_title"),
        "",
        t(lang, "panels.developer.cat_lists_desc"),
        "",
        _summary_row(lang, "panels.developer.summary_groups", summary["groups"]),
        _summary_row(lang, "panels.developer.summary_channels", summary["channels"]),
        _summary_row(lang, "panels.developer.summary_no_credit", summary["no_credit"]),
        _summary_row(lang, "panels.developer.summary_expiring_24h", summary["expiring_24h"]),
    ]
    return "\n".join(lines), KeyboardFactory.dev_sub_lists(lang)


async def _build_dev_settings_payload(lang: str) -> tuple[str, InlineKeyboardMarkup]:
    summary = await AdminDashboardService.get_general_summary()
    lines = [
        t(lang, "panels.developer.cat_settings_title"),
        "",
        t(lang, "panels.developer.cat_settings_desc"),
        "",
        t(
            lang,
            "status.setting_value",
            feature=t(lang, "status.force_join_label"),
            value=_bool_label(lang, bool(summary["force_join_enabled"])),
        ),
        t(
            lang,
            "status.setting_value",
            feature=t(lang, "status.auto_leave_label"),
            value=_bool_label(lang, bool(summary["auto_leave_enabled"])),
        ),
        t(
            lang,
            "status.setting_value",
            feature=t(lang, "status.trial_label"),
            value=_bool_label(lang, bool(summary["trial_enabled"])),
        ),
        t(
            lang,
            "status.setting_value",
            feature=t(lang, "status.bot_enabled_label"),
            value=_bool_label(lang, bool(summary.get("bot_enabled", True))),
        ),
        t(
            lang,
            "status.setting_value",
            feature=t(lang, "status.sudo_panel_enabled_label"),
            value=_bool_label(lang, bool(summary.get("sudo_panel_enabled", True))),
        ),
        _summary_row(lang, "panels.developer.summary_required_channels", int(summary["required_channels"])),
    ]
    return "\n".join(lines), KeyboardFactory.dev_sub_settings(
        lang,
        bot_enabled=bool(summary.get("bot_enabled", True)),
        sudo_panel_enabled=bool(summary.get("sudo_panel_enabled", True)),
        force_join_enabled=bool(summary.get("force_join_enabled", False)),
        auto_leave_enabled=bool(summary.get("auto_leave_enabled", False)),
        trial_enabled=bool(summary.get("trial_enabled", False)),
    )


async def _build_dev_users_payload(lang: str) -> tuple[str, InlineKeyboardMarkup]:
    summary = await AdminDashboardService.get_users_summary()
    lines = [
        t(lang, "panels.developer.cat_users_title"),
        "",
        t(lang, "panels.developer.cat_users_desc"),
        "",
        _summary_row(lang, "panels.developer.summary_owners", summary["owners"]),
        _summary_row(lang, "panels.developer.summary_sudos", summary["sudos"]),
    ]
    return "\n".join(lines), KeyboardFactory.dev_sub_users(lang)


async def _build_dev_force_join_payload(lang: str) -> tuple[str, InlineKeyboardMarkup]:
    summary = await AdminDashboardService.get_general_summary()
    lines = [
        t(lang, "panels.developer.cat_force_join_title"),
        "",
        t(lang, "panels.developer.cat_force_join_desc"),
        "",
        _summary_row(lang, "status.force_join_label", _bool_label(lang, bool(summary["force_join_enabled"]))),
        _summary_row(lang, "panels.developer.summary_required_channels", int(summary["required_channels"])),
    ]
    return "\n".join(lines), KeyboardFactory.dev_sub_force_join(
        lang,
        force_join_enabled=bool(summary.get("force_join_enabled", False)),
    )


async def _build_dev_moderation_payload(lang: str) -> tuple[str, InlineKeyboardMarkup]:
    lines = [
        t(lang, "panels.developer.cat_moderation_title"),
        "",
        t(lang, "panels.developer.cat_moderation_desc"),
    ]
    return "\n".join(lines), KeyboardFactory.dev_sub_moderation(lang)


async def _build_dev_install_policy_payload(lang: str) -> tuple[str, InlineKeyboardMarkup]:
    policy = await InstallPolicyService.get_policy()
    mode = policy.policy_mode if policy else "open"
    text = (
        t(lang, "admin.install_policy.title")
        + "\n"
        + t(
            lang,
            "admin.install_policy.current_mode",
            mode=label(lang, "install_policy_mode", mode),
        )
    )
    return text, KeyboardFactory.install_policy_panel(lang)


async def remember_return_token(user_id: int, token: str) -> None:
    try:
        r = await get_redis()
        await r.set(
            wizard_return_key(user_id),
            _safe_token(token),
            ex=TTL_WIZARD_RETURN,
        )
    except Exception:
        logger.warning(
            "remember_return_token unavailable user_id=%s token=%s",
            user_id,
            token,
            exc_info=True,
        )


async def get_return_token(user_id: int) -> str | None:
    try:
        r = await get_redis()
        token = await r.get(wizard_return_key(user_id))
        return _safe_token(token) if token else None
    except Exception:
        logger.warning("get_return_token unavailable user_id=%s", user_id, exc_info=True)
        return None


async def pop_return_token(user_id: int) -> str | None:
    try:
        r = await get_redis()
        key = wizard_return_key(user_id)
        token = await r.get(key)
        await r.delete(key)
        return _safe_token(token) if token else None
    except Exception:
        logger.warning("pop_return_token unavailable user_id=%s", user_id, exc_info=True)
        return None


async def clear_all_wizard_state_keys(user_id: int) -> None:
    from app.services import helper_otp_pre_auth_registry

    try:
        await helper_otp_pre_auth_registry.evict(user_id, phase="clear_all_wizard_state")
    except Exception:
        logger.warning(
            "pre-auth wizard cleanup failed user_id=%s",
            user_id,
            exc_info=True,
        )
    try:
        r = await get_redis()
        state_keys = _wizard_state_keys(user_id)
        await r.delete(*state_keys.values(), wizard_return_key(user_id))
    except Exception:
        logger.warning(
            "Redis wizard cleanup unavailable user_id=%s",
            user_id,
            exc_info=True,
        )


async def detect_active_wizard_state(
    user_id: int,
    *,
    heal_conflicts: bool = True,
) -> tuple[str | None, dict | None]:
    """Return the currently active wizard state for a user.

    If multiple wizard states exist unexpectedly, chooses deterministically by
    priority and optionally clears the stale conflicting states.
    """
    r = await get_redis()
    return await detect_active_wizard_state_in_redis(
        r,
        user_id,
        heal_conflicts=heal_conflicts,
    )


async def detect_active_wizard_state_in_redis(
    redis_client,
    user_id: int,
    *,
    heal_conflicts: bool = True,
) -> tuple[str | None, dict | None]:
    """Redis-client variant used by handlers that already fetched Redis."""
    state_keys = _wizard_state_keys(user_id)
    loaded: dict[str, dict] = {}

    for name, key in state_keys.items():
        raw = await redis_client.get(key)
        if not raw:
            continue
        try:
            loaded[name] = json.loads(raw)
        except Exception:
            # Corrupted state should never block routing.
            await redis_client.delete(key)

    if not loaded:
        return None, None

    chosen = next((name for name in _WIZARD_PRIORITY if name in loaded), None)
    if chosen is None:
        return None, None

    if heal_conflicts:
        stale_names = [name for name in loaded if name != chosen]
        stale_keys = [state_keys[name] for name in stale_names]
        if stale_keys:
            await redis_client.delete(*stale_keys)
        if WIZARD_HELPER_OTP in stale_names:
            from app.services import helper_otp_pre_auth_registry

            await helper_otp_pre_auth_registry.evict(user_id, phase="wizard_conflict_heal")

    return chosen, loaded.get(chosen)


async def is_wizard_active(user_id: int, wizard_name: str) -> bool:
    active_name, _ = await detect_active_wizard_state(user_id, heal_conflicts=True)
    return active_name == wizard_name


async def safe_edit_navigation_message(
    client: Client,
    message,
    text: str,
    *,
    reply_markup: InlineKeyboardMarkup | None = None,
    user_id: int | None = None,
) -> bool:
    """Render navigation text safely across text/caption/non-editable messages.

    Returns:
        True when the navigation UI was updated or already matched the target.
        False when every edit/send fallback failed.
    """
    from app.services.panel_message_service import remember_panel_message
    from app.utils.telegram_message import safe_edit_message, safe_edit_or_send

    chat = getattr(message, "chat", None)
    chat_id = getattr(chat, "id", None)

    message_id = getattr(message, "id", None) or getattr(message, "message_id", None)
    if await safe_edit_message(message, text, reply_markup=reply_markup):
        if chat_id is not None and user_id is not None and message_id is not None:
            await remember_panel_message(chat_id, user_id, message_id)
        elif chat_id is not None and message_id is not None:
            from_user = getattr(getattr(message, "from_user", None), "id", None)
            if from_user is not None:
                await remember_panel_message(chat_id, from_user, message_id)
        return True

    sent = await safe_edit_or_send(
        client,
        message,
        text,
        reply_markup=reply_markup,
        fallback_chat_id=chat_id,
        attempt_edit=False,
    )
    if sent is not None and chat_id is not None:
        anchor_user = user_id
        if anchor_user is None:
            anchor_user = getattr(getattr(message, "from_user", None), "id", None)
        sent_id = getattr(sent, "id", None) or getattr(sent, "message_id", None)
        if anchor_user is not None and sent_id is not None:
            await remember_panel_message(chat_id, anchor_user, sent_id)
        logger.debug("navigation send fallback used chat_id=%s", chat_id)
        return True

    logger.warning("navigation edit failed chat_id=%s", chat_id)
    return False


async def clear_runtime_state(
    client: Client,
    user_id: int,
    chat_id: int | None = None,
) -> None:
    if chat_id is not None:
        from app.utils.ask_result import safe_stop_listening

        await safe_stop_listening(client, chat_id, user_id=user_id)

    await clear_all_wizard_state_keys(user_id)


async def clear_wizard_and_allow_command(
    client: Client,
    message,
    *,
    lang: str = "fa",
    notify_key: str = "common.cancelled",  # retained for call compatibility
) -> bool:
    """Clear wizard state on escape commands without blocking their handlers.

    Returns True when *message* is a global escape (``/help``, ``/panel``, etc.).
    """
    from app.utils.text_commands import is_escape_command

    text = message.text or getattr(message, "caption", None) or ""
    if not is_escape_command(text):
        return False
    user = getattr(message, "from_user", None)
    user_id = user.id if user is not None else 0
    chat = getattr(message, "chat", None)
    chat_id = chat.id if chat is not None else None
    await clear_runtime_state(client, user_id, chat_id)
    # The command handler itself owns the single visible outcome. Sending a
    # separate "cancelled" reply here creates duplicate notices/panels.
    return True


async def resolve_navigation_payload(
    client: Client,
    user_id: int,
    chat_type: str,
    return_to: str | None,
    lang: str = "fa",
    *,
    chat_id: int | None = None,
) -> tuple[str, InlineKeyboardMarkup]:
    token = _safe_token(return_to)

    if chat_type in ("group", "supergroup"):
        if chat_id is None:
            return t(lang, "common.errors.no_access"), KeyboardFactory.back_button(lang)
        from app.utils.player_permissions import can_open_group_panel

        if not await can_open_group_panel(client, chat_id, user_id):
            return t(lang, "common.errors.no_access"), KeyboardFactory.back_button(lang)
        return t(lang, "panels.group.title"), KeyboardFactory.group_panel(lang)

    if not await _can_resolve_navigation_token(user_id, token):
        return await _navigation_denied_payload(client, user_id, lang)

    if token == TOKEN_DEV_CREDIT:
        return await _build_dev_credit_payload(lang)
    if token in {TOKEN_DEV_RATES, TOKEN_DEV_MONTHLY_INVOICE}:
        return t(lang, "credit.financial_surface_removed"), KeyboardFactory.developer_panel(lang)
    if token == TOKEN_DEV_BROADCAST:
        return t(lang, "panels.developer.cat_broadcast_title"), KeyboardFactory.dev_sub_broadcast(lang)
    if token == TOKEN_DEV_LISTS:
        return await _build_dev_lists_payload(lang)
    if token == TOKEN_DEV_SETTINGS:
        return await _build_dev_settings_payload(lang)
    if token == TOKEN_DEV_USERS:
        return await _build_dev_users_payload(lang)
    if token == TOKEN_DEV_FORCE_JOIN:
        return await _build_dev_force_join_payload(lang)
    if token == TOKEN_DEV_MODERATION:
        return await _build_dev_moderation_payload(lang)
    if token == TOKEN_DEV_TEXTS:
        return await build_texts_hub_payload(lang, "dev")
    if token == TOKEN_DEV_INSTALL_POLICY:
        return await _build_dev_install_policy_payload(lang)
    if token == TOKEN_OWNER_TEXTS:
        return await build_texts_hub_payload(lang, "owner", owner_user_id=user_id)
    if token == TOKEN_HELPER_HOME:
        helpers = await HelperPoolService.get_all_helpers()
        active = sum(1 for h in helpers if h.status == "active")
        disabled = sum(1 for h in helpers if h.status == "disabled")
        quarantined = sum(1 for h in helpers if h.status == "quarantined")
        return (
            t(lang, "admin.helpers.title"),
            KeyboardFactory.helper_home(lang, active, disabled, quarantined),
        )

    text, kb = await build_private_root_payload(
        client,
        user_id,
        str(user_id),
        lang,
        include_welcome=False,
    )
    return text, kb


async def cancel_and_resolve(
    client: Client,
    user_id: int,
    chat_id: int,
    chat_type: str,
    *,
    lang: str = "fa",
    return_to: str | None = None,
) -> tuple[str, InlineKeyboardMarkup]:
    token = _safe_token(return_to) if return_to else await pop_return_token(user_id)
    await clear_runtime_state(client, user_id, chat_id)
    return await resolve_navigation_payload(
        client,
        user_id,
        chat_type,
        token,
        lang,
        chat_id=chat_id,
    )

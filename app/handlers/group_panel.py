from __future__ import annotations

from dataclasses import dataclass
import logging
import re
import time

import pyrogram
from pyrogram import Client, filters
from pyrogram.types import CallbackQuery, Message

from app.repositories import admin_repo, call_security_repo, call_stats_settings_repo, id_command_settings_repo, settings_repo
from app.services import user_info_formatter_service
from app.services import owner_scope_service
from app.services import group_runtime_state_service
from app.services import manager_command_service
from app.services.bot_settings_service import (
    get_developer_link,
    get_guide_channel_link,
    get_support_group_link,
)
from app.handlers.priority import (
    GROUP_FAMILY_FALLBACK_GROUP,
    GROUP_NAV_CALLBACK_GROUP,
    PRIORITY_COMMAND_GROUP,
)
from app.utils.bot_guards import is_developer
from app.utils.cache import invalidate_chat_settings
from app.utils.decorators import group_music_admin, music_admin_or_above, player_deputy_or_above
from app.utils.sudo_permissions import allow_group_chat_settings_change, notify_sudo_permission_denied
from app.utils.filters import group_chat_filter, install_chat_filter, music_admin_filter
from app.utils.i18n import AUTO_LANG, normalize_lang, t
from app.services.media_capability_service import (
    build_playback_type_menu,
    normalize_default_media_type,
)
from app.handlers.help_center import render_help_home_panel
from app.services.panel_message_service import panel_callback_edit, remember_panel_from_message
from app.services.wizard_ui import clear_runtime_state, safe_edit_navigation_message
from app.utils.callback_trace import trace_callback_event, trace_edit
from app.utils.ui import CB, KeyboardFactory

logger = logging.getLogger(__name__)

_LANG = AUTO_LANG
_grp_admin = music_admin_filter() & group_chat_filter()
_grp_any = group_chat_filter()


async def _resolve_owner_for_group_query(query: CallbackQuery) -> int | None:
    """Resolve install-lineage owner id from a group/channel callback query."""
    if query.message is None or query.message.chat is None:
        return None
    chat = query.message.chat
    chat_type = "channel" if chat.type.value == "channel" else "group"
    return await owner_scope_service.resolve_owner_user_id_for_chat(chat.id, chat_type)

# Legacy grp:set:repeat -> vote_skip_enabled (no vote-skip runtime; toggle hidden in UI).
# Session repeat uses pb:repeat -> CallService._repeat_states (B6-1). B6-2: repeat_enabled.
_SETTING_TOGGLE_MAP: dict[str, str] = {
    CB["GRP_MUSIC_VIDEO"]: "video_enabled",
    CB["GRP_SECURITY_CALL"]: "security_call_enabled",
    CB["GRP_REPEAT"]: "vote_skip_enabled",
    CB["GRP_DOWNLOAD_USERS"]: "download_enabled",
    CB["GRP_CALL_MESSAGE"]: "announce_enabled",
    CB["GRP_AUTO_CLEAN"]: "filter_enabled",
    CB["GRP_SERVICE_CLEAN"]: "service_clean_enabled",
    CB["GRP_QUEUE"]: "smart_radio_enabled",
    CB["GRP_AUTO_READY_CALL"]: "auto_leave_enabled",
    CB["GRP_CALL_REPORT"]: "buttons_enabled",
    CB["GRP_RECORD_CALL"]: "lyrics_enabled",
    CB["GRP_SHOW_ID"]: "show_track_id",
    CB["GRP_SHOW_PHOTO"]: "show_cover",
    CB["GRP_SHOW_TEXT"]: "show_now_playing_text",
}

_GROUP_SETTING_TOGGLE_REGEX = "^(" + "|".join(re.escape(cb) for cb in _SETTING_TOGGLE_MAP) + ")$"
_GROUP_CLEAR_CONFIRM_TTL_SECONDS = 300
_INSTALL_SETUP_CALLBACK_TTL_SECONDS = 300
_GROUP_SETTINGS_TEXT_CMDS = ["تنظیمات", "settings"]
_PLAYER_PANEL_TEXT_CMDS = ["پنل پلیر", "player panel"]
_PANEL_TEXT_CMDS = ["پنل"]
_GROUP_ID_TEXT_CMDS = ["شناسه گروه", "Group id"]
_CHANNEL_ID_TEXT_CMDS = ["شناسه کانال", "Channel id"]
_PLAYER_STATUS_TEXT_CMDS = ["وضعیت پلیر", "Status Player"]
_USER_ID_TEXT_CMDS = ["آیدی", "Id"]
_SHOW_ID_PHOTO_TEXT_CMDS = ["وضعیت نمایش شناسه عکس", "Show Id Status Photo"]
_SHOW_ID_SIMPLE_TEXT_CMDS = ["وضعیت نمایش شناسه ساده", "Show Id Status Simple"]
_SHOW_ID_INACTIVE_TEXT_CMDS = ["وضعیت نمایش شناسه غیرفعال", "Show Id Status Inactive"]
_VIP_LIST_TEXT_CMDS = ["لیست ویژه پلیر", "ListVip Player", "VIP List"]
_VIP_CLEAR_TEXT_CMDS = ["پاکسازی لیست ویژه پلیر", "ClearListVip Player", "Clear VIP List"]
_INSTALL_SETUP_ACTION_PATTERN = (
    r"(?:Home|ConfigAdmin|Lang|SetLangFa|SetLangEn|ShowSetCharge|"
    r"SetCharge(?:3|5|10|15|20|30|60|90|120|150|180|360|0|2)|"
    r"AddCli|Access(?::(?:Music|Video))?|Exit)"
)
_INSTALL_SETUP_CALLBACK_PATTERN = (
    rf"^{re.escape(CB['INSTALL_SETUP_PREFIX'])}"
    rf"(?P<action>{_INSTALL_SETUP_ACTION_PATTERN}):G(?P<chat_id>-?\d+):"
    rf"U(?P<user_id>\d+):T(?P<issued_at>\d+)$"
)
_INSTALL_SETUP_MALFORMED_PATTERN = (
    rf"^{re.escape(CB['INSTALL_SETUP_PREFIX'])}"
    rf"(?!{_INSTALL_SETUP_ACTION_PATTERN}:G-?\d+:U\d+:T\d+$)"
)
_INSTALL_SETUP_CHARGE_DURATIONS = {
    "SetCharge3": 3,
    "SetCharge5": 5,
    "SetCharge10": 10,
    "SetCharge15": 15,
    "SetCharge20": 20,
    "SetCharge30": 30,
    "SetCharge60": 60,
    "SetCharge90": 90,
    "SetCharge120": 120,
    "SetCharge150": 150,
    "SetCharge180": 180,
    "SetCharge360": 360,
    "SetCharge0": 0,
    "SetCharge2": 2,
}


@dataclass(frozen=True)
class _InstallSetupPayload:
    action: str
    chat_id: int
    user_id: int
    issued_at: int


def _normalized_alias_filter(aliases: list[str], *, name: str):
    """Match slash-free aliases after NFKC/ZW normalization."""
    pattern = re.compile(
        rf"^(?:{'|'.join(re.escape(c) for c in aliases)})\s*$",
        flags=re.IGNORECASE,
    )

    async def func(_flt, _client, message: Message) -> bool:
        from app.utils.text_commands import normalize_command_text

        text = normalize_command_text(message)
        return bool(text) and pattern.fullmatch(text) is not None

    return filters.create(func, name=name)


def _group_settings_message_filter():
    """Match /settings and Persian/English text aliases in groups only."""
    return (
        filters.command("settings")
        | _normalized_alias_filter(_GROUP_SETTINGS_TEXT_CMDS, name="GroupSettingsText")
    ) & group_chat_filter()


def _player_panel_message_filter():
    """Match پنل پلیر / player panel in installed chat contexts."""
    return (
        _normalized_alias_filter(_PLAYER_PANEL_TEXT_CMDS, name="PlayerPanelText")
        & install_chat_filter()
    )


def _panel_command_filter():
    """Match /panel and پنل text command in group chats."""
    return (
        filters.command("panel")
        | _normalized_alias_filter(_PANEL_TEXT_CMDS, name="PanelText")
    ) & group_chat_filter()


def _parse_group_clear_token(data: str, prefix: str) -> tuple[int, int, int] | None:
    if not data.startswith(prefix):
        return None
    parts = data[len(prefix):].split(":")
    if len(parts) != 3:
        return None
    try:
        chat_id = int(parts[0])
        user_id = int(parts[1])
        issued_at = int(parts[2])
    except ValueError:
        return None
    if user_id <= 0 or issued_at <= 0:
        return None
    return chat_id, user_id, issued_at


def _parse_prefixed_nonnegative_int(data: str | None, prefix: str) -> int | None:
    if not data or not data.startswith(prefix):
        return None
    raw = data[len(prefix):]
    if not raw or ":" in raw:
        return None
    try:
        value = int(raw)
    except ValueError:
        return None
    if value < 0:
        return None
    return value


def _parse_vip_demote_payload(data: str | None) -> tuple[int, int] | None:
    prefix = CB["GRP_VIP_DEMOTE_PREFIX"]
    if not data or not data.startswith(prefix):
        return None
    parts = data[len(prefix):].split(":")
    if len(parts) not in (1, 2):
        return None
    try:
        user_id = int(parts[0])
        page = int(parts[1]) if len(parts) == 2 else 0
    except ValueError:
        return None
    if user_id <= 0 or page < 0:
        return None
    return user_id, page


def _is_stale_group_clear_token(issued_at: int) -> bool:
    now = int(time.time())
    return issued_at > now + 60 or now - issued_at > _GROUP_CLEAR_CONFIRM_TTL_SECONDS


def _is_stale_install_setup_token(issued_at: int) -> bool:
    now = int(time.time())
    return issued_at > now + 60 or now - issued_at > _INSTALL_SETUP_CALLBACK_TTL_SECONDS


def _parse_install_setup_callback(data: str | None) -> _InstallSetupPayload | None:
    if not data:
        return None
    match = re.fullmatch(_INSTALL_SETUP_CALLBACK_PATTERN, data)
    if match is None:
        return None
    try:
        payload = _InstallSetupPayload(
            action=match.group("action"),
            chat_id=int(match.group("chat_id")),
            user_id=int(match.group("user_id")),
            issued_at=int(match.group("issued_at")),
        )
    except (TypeError, ValueError):
        return None
    if payload.user_id <= 0 or payload.issued_at <= 0:
        return None
    return payload


async def _install_setup_permission_allowed(
    kind: str,
    user_id: int,
    chat_id: int,
    client: Client | None = None,
) -> bool:
    if kind == "credit":
        return await manager_command_service.can_manage_setup_credit(user_id)
    if kind == "helper":
        return await manager_command_service.can_manage_setup_helper(user_id, chat_id)
    if kind == "config":
        if await manager_command_service.can_manage_setup_config(user_id, chat_id):
            return True
        if client is not None:
            from app.utils.player_permissions import can_manage_deputy

            return await can_manage_deputy(client, chat_id, user_id)
        return await manager_command_service.can_manage_setup_config(user_id, chat_id)
    if kind == "settings":
        if await manager_command_service.can_manage_setup_access(user_id, chat_id):
            return True
        if client is not None:
            from app.utils.player_permissions import can_open_group_settings

            return await can_open_group_settings(client, chat_id, user_id)
        return await manager_command_service.can_manage_setup_access(user_id, chat_id)
    return await manager_command_service.can_manage_install_setup(user_id)


async def _validate_install_setup_callback(
    client: Client,
    query: CallbackQuery,
    *,
    permission: str = "install",
) -> _InstallSetupPayload | None:
    payload = _parse_install_setup_callback(query.data)
    if payload is None:
        await query.answer(t(_LANG, "common.errors.invalid_callback"), show_alert=True)
        return None
    query_chat_id = getattr(getattr(query, "message", None), "chat", None)
    query_chat_id = getattr(query_chat_id, "id", None)
    query_user_id = getattr(getattr(query, "from_user", None), "id", None)
    if query_chat_id is None or query_user_id is None:
        await query.answer(t(_LANG, "common.errors.invalid_callback"), show_alert=True)
        return None
    if payload.chat_id != int(query_chat_id) or payload.user_id != int(query_user_id):
        await query.answer(t(_LANG, "common.errors.no_access"), show_alert=True)
        return None
    if _is_stale_install_setup_token(payload.issued_at):
        await query.answer(t(_LANG, "common.errors.invalid_callback"), show_alert=True)
        return None
    if not await group_runtime_state_service.require_active_group(payload.chat_id, "group"):
        await query.answer(t(_LANG, "panels.group.not_managed"), show_alert=True)
        return None
    if not await _install_setup_permission_allowed(permission, payload.user_id, payload.chat_id, client):
        await query.answer(t(_LANG, "manager_text.no_permission"), show_alert=True)
        return None
    return payload


async def _guard_group_chat_settings(
    update: Message | CallbackQuery,
    user_id: int,
    chat_id: int,
    client: Client | None = None,
) -> bool:
    """Require active installed group state and chat-settings permission."""
    if not await group_runtime_state_service.require_active_group(chat_id, "group"):
        text = t(_LANG, "panels.group.not_managed")
        if isinstance(update, CallbackQuery):
            await update.answer(text, show_alert=True)
        else:
            reply = getattr(update, "reply", None) or getattr(update, "reply_text", None)
            if callable(reply):
                await reply(text)
        return False
    if client is not None:
        from app.utils.player_permissions import can_open_group_settings

        if await can_open_group_settings(client, chat_id, user_id):
            return True
    if await allow_group_chat_settings_change(user_id, chat_id):
        return True
    await notify_sudo_permission_denied(update, "can_manage_chat_settings")
    return False


async def _get_settings_dict(chat_id: int) -> dict:
    cs = await settings_repo.get_chat_settings(chat_id)
    if cs is None:
        cs = await settings_repo.create_defaults(chat_id)
    return {
        "music_video": cs.video_enabled,
        "security_call": cs.security_call_enabled,
        "repeat": cs.vote_skip_enabled,
        "download_users": cs.download_enabled,
        "call_message": cs.announce_enabled,
        "auto_clean": cs.filter_enabled,
        "queue": cs.smart_radio_enabled,
        "auto_ready_call": cs.auto_leave_enabled,
        "call_report": cs.buttons_enabled,
        "record_call": cs.lyrics_enabled,
        "show_id": cs.show_track_id,
        "show_photo": cs.show_cover,
        "show_text": cs.show_now_playing_text,
        "call_stats": await call_stats_settings_repo.get_enabled(chat_id),
        "id_call_stats": await id_command_settings_repo.get_show_call_stats(chat_id),
        "default_media_type": normalize_default_media_type(
            getattr(cs, "default_media_type", None)
        ),
        "language": normalize_lang(getattr(cs, "language", None)),
    }


def _state_label(enabled: bool) -> str:
    return t(_LANG, "common.labels.on") if enabled else t(_LANG, "common.labels.off")


def _setting_entry(label: str, value: str) -> str:
    template = t(_LANG, "list_fmt.setting_entry")
    return template.format(key=label, value=value)


def _install_setup_state_label(enabled: bool) -> str:
    return t(_LANG, "panels.group.install_setup.state_on" if enabled else "panels.group.install_setup.state_off")


def _install_setup_charge_text(state: manager_command_service.InstallPlayerSetupState) -> str:
    if not state.credit_exists:
        return t(_LANG, "panels.group.install_setup.charge_missing")
    if state.credit_status == "unlimited":
        return t(_LANG, "panels.group.install_setup.charge_unlimited")
    if state.credit_status == "expired" or state.credit_days <= 0:
        return t(_LANG, "panels.group.install_setup.charge_expired")
    if state.credit_is_trial:
        return t(_LANG, "panels.group.install_setup.charge_trial", days=state.credit_days)
    return t(_LANG, "panels.group.install_setup.charge_days", days=state.credit_days)


def _install_setup_helper_text(state: manager_command_service.InstallPlayerSetupState) -> str:
    if state.helper_installed:
        return t(_LANG, "panels.group.install_setup.helper_installed", helper_id=state.helper_id)
    if state.helper_failed:
        return t(_LANG, "panels.group.install_setup.helper_failed")
    return t(_LANG, "panels.group.install_setup.helper_missing")


async def _build_install_setup_text(
    chat_id: int,
    *,
    section_key: str | None = None,
) -> str:
    state = await manager_command_service.build_install_player_setup_state(chat_id)
    config_count = (
        state.music_admin_count
        + state.video_admin_count
        + state.player_owner_count
        + state.player_deputy_count
    )
    lines = [
        t(_LANG, "panels.group.install_setup.title"),
        "",
        t(_LANG, "panels.group.install_setup.group_line", title=state.title),
        _setting_entry(
            t(_LANG, "panels.group.install_setup.managed_label"),
            t(
                _LANG,
                "panels.group.install_setup.managed_active"
                if state.managed
                else "panels.group.install_setup.managed_missing",
            ),
        ),
        _setting_entry(
            t(_LANG, "panels.group.install_setup.config_label"),
            t(
                _LANG,
                "panels.group.install_setup.config_done"
                if state.config_done
                else "panels.group.install_setup.config_needed",
                count=config_count,
            ),
        ),
        _setting_entry(
            t(_LANG, "panels.group.install_setup.charge_label"),
            _install_setup_charge_text(state),
        ),
        _setting_entry(
            t(_LANG, "panels.group.install_setup.helper_label"),
            _install_setup_helper_text(state),
        ),
        _setting_entry(
            t(_LANG, "panels.group.install_setup.language_label"),
            t(_LANG, f"panels.group.settings.language_name_{state.language}"),
        ),
        _setting_entry(
            t(_LANG, "panels.group.install_setup.access_label"),
            t(
                _LANG,
                "panels.group.install_setup.access_status",
                music=_install_setup_state_label(state.audio_enabled),
                video=_install_setup_state_label(state.video_enabled),
            ),
        ),
    ]
    if section_key is not None:
        lines.extend(["", t(_LANG, f"panels.group.install_setup.{section_key}")])
    return "\n".join(lines)


async def build_install_player_setup_payload(
    chat_id: int,
    user_id: int,
    issued_at: int,
    *,
    view: str = "home",
) -> tuple[str, object]:
    from app.utils.bot_guards import is_developer

    section_key: str | None = None
    show_charge = is_developer(user_id)
    state = await manager_command_service.build_install_player_setup_state(chat_id)
    if view == "charge":
        section_key = "charge_menu_title"
        reply_markup = KeyboardFactory.install_player_charge_menu(_LANG, chat_id, user_id, issued_at)
    elif view == "access":
        section_key = "access_menu_title"
        reply_markup = KeyboardFactory.install_player_access_menu(
            _LANG,
            chat_id,
            user_id,
            issued_at,
            audio_enabled=state.audio_enabled,
            video_enabled=state.video_enabled,
        )
    elif view == "language":
        section_key = "language_menu_title"
        reply_markup = KeyboardFactory.install_player_language_menu(_LANG, chat_id, user_id, issued_at)
    else:
        reply_markup = KeyboardFactory.install_player_setup_panel(
            _LANG,
            chat_id,
            user_id,
            issued_at,
            show_charge=show_charge,
        )
    return await _build_install_setup_text(chat_id, section_key=section_key), reply_markup


async def reply_install_player_setup_panel(message: Message) -> None:
    user_id = int(message.from_user.id) if message.from_user else 0
    issued_at = int(time.time())
    text, reply_markup = await build_install_player_setup_payload(message.chat.id, user_id, issued_at)
    sent = await message.reply(text, reply_markup=reply_markup)
    if sent is not None:
        await remember_panel_from_message(sent, user_id)


async def _edit_install_setup_panel(
    query: CallbackQuery,
    payload: _InstallSetupPayload,
    *,
    view: str = "home",
) -> None:
    text, reply_markup = await build_install_player_setup_payload(
        payload.chat_id,
        payload.user_id,
        payload.issued_at,
        view=view,
    )
    await query.message.edit_text(text, reply_markup=reply_markup)


def _install_setup_permission_for_action(action: str) -> str:
    if action == "ShowSetCharge" or action.startswith("SetCharge"):
        return "credit"
    if action == "AddCli":
        return "helper"
    if action == "ConfigAdmin":
        return "config"
    if action in {"Lang", "SetLangFa", "SetLangEn", "Access", "Access:Music", "Access:Video"}:
        return "settings"
    return "install"


def _install_setup_result_text(result: manager_command_service.ManagerCommandResult) -> str:
    params = result.params
    if result.reason == "no_access":
        return t(_LANG, "common.errors.no_access")
    if result.reason == "charge_updated":
        if params.get("status") == "unlimited":
            return t(_LANG, "install.setup_charge_unlimited_done")
        return t(_LANG, "install.setup_charge_done", days=params.get("amount", 0))
    if result.reason == "trial_started":
        return t(_LANG, "install.setup_trial_started", days=params.get("days", 0))
    if result.reason == "trial_already_used":
        return t(_LANG, "install.setup_trial_already_used")
    if result.reason == "trial_active_credit":
        return t(_LANG, "install.setup_trial_active_credit")
    if result.reason == "language_updated":
        lang = str(params.get("language", "fa"))
        return t(_LANG, "install.setup_language_updated", language=t(_LANG, f"panels.group.settings.language_name_{lang}"))
    if result.reason == "access_updated":
        access = str(params.get("access", "music"))
        label_key = "install.setup_access_music" if access == "music" else "install.setup_access_video"
        state = _install_setup_state_label(bool(params.get("enabled", False)))
        return t(_LANG, "install.setup_access_updated", feature=t(_LANG, label_key), state=state)
    if result.reason == "config_imported":
        return t(
            _LANG,
            "install.setup_config_done",
            imported=params.get("imported", 0),
            skipped=params.get("skipped", 0),
            failures=params.get("failures", 0),
            owners_section=params.get("owners_section", "-"),
            deputies_section=params.get("deputies_section", "-"),
            admins_section=params.get("admins_section", "-"),
            vips_section=params.get("vips_section", "-"),
        )
    if result.reason == "telegram_admins_empty":
        return t(_LANG, "install.setup_config_empty")
    if result.reason == "telegram_admins_unavailable":
        return t(_LANG, "install.setup_config_failed")
    if result.reason == "helper_added":
        return t(_LANG, "install.setup_helper_added")
    if result.reason == "helper_already_present":
        return t(_LANG, "install.setup_helper_already")
    if result.reason == "helper_unavailable":
        return t(_LANG, "install.setup_helper_unavailable")
    if result.reason == "helper_promote_failed":
        return t(_LANG, "install.setup_helper_promote_failed")
    if result.reason == "helper_user_unknown":
        return t(_LANG, "install.setup_helper_unknown")
    if result.reason == "helper_join_failed":
        return t(_LANG, "install.setup_helper_failed")
    if result.reason == "not_managed":
        return t(_LANG, "panels.group.not_managed")
    return t(_LANG, "manager_text.db_error")


async def _build_vip_page_view(chat_id: int, page: int):
    from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup

    vips, total_pages, page = await admin_repo.get_player_vips_page(chat_id, page)
    if not vips:
        return (
            t(_LANG, "panels.group.management.vip_list_title")
            + "\n"
            + t(_LANG, "filter_mgmt.list_empty"),
            KeyboardFactory.back_button(_LANG),
        )

    rows = []
    for v in vips:
        label = t(_LANG, "list_fmt.user_item", user_id=v.user_id, username=v.username or "-")
        demote_label = t(_LANG, "panels.group.management.vip_demote_btn")
        rows.append([
            InlineKeyboardButton(label, callback_data=CB["GRP_VIP_LIST"]),
            InlineKeyboardButton(
                demote_label,
                callback_data=f"{CB['GRP_VIP_DEMOTE_PREFIX']}{v.user_id}:{page}",
            ),
        ])

    nav_row = []
    if page > 0:
        nav_row.append(
            InlineKeyboardButton(
                t(_LANG, "common.buttons.prev"),
                callback_data=f"{CB['PAGE_VIP']}{page - 1}",
            )
        )
    if total_pages > 1:
        nav_row.append(
            InlineKeyboardButton(f"{page + 1}/{total_pages}", callback_data=CB["NOOP"])
        )
    if page < total_pages - 1:
        nav_row.append(
            InlineKeyboardButton(
                t(_LANG, "common.buttons.next"),
                callback_data=f"{CB['PAGE_VIP']}{page + 1}",
            )
        )
    if nav_row:
        rows.append(nav_row)
    rows.append([InlineKeyboardButton(t(_LANG, "common.buttons.back"), callback_data=CB["GRP_MANAGEMENT"])])
    return (
        t(_LANG, "panels.group.management.vip_list_title"),
        InlineKeyboardMarkup(rows),
    )


async def _render_vip_page(query: CallbackQuery, chat_id: int, page: int) -> None:
    text, reply_markup = await _build_vip_page_view(chat_id, page)
    await query.message.edit_text(
        text,
        reply_markup=reply_markup,
    )


def _safe_status_title(value: object) -> str:
    if value is None:
        return t(_LANG, "common.labels.unknown")
    text = " ".join(str(value).split())
    if not text:
        return t(_LANG, "common.labels.unknown")
    if "://" in text or "\\" in text or "/" in text:
        return t(_LANG, "common.labels.unknown")
    return text[:80]


async def _build_player_status_text(chat_id: int) -> str:
    from app.repositories import playlist_repo
    from app.services import CallService

    active = CallService.get_active_calls().get(chat_id)
    if not active:
        return t(_LANG, "public_cmd.player_status_inactive")

    media_type_key = str(active.get("media_type") or "audio")
    media_type = t(_LANG, f"playback.types.{media_type_key}")
    state = (
        t(_LANG, "public_cmd.player_state_paused")
        if bool(active.get("is_paused"))
        else t(_LANG, "public_cmd.player_state_playing")
    )
    try:
        queue_size = await playlist_repo.get_queue_length(chat_id)
    except Exception:
        logger.debug("player status queue length failed chat_id=%s", chat_id, exc_info=True)
        queue_size = 0
    try:
        from app.handlers import callbacks as playback_callbacks

        volume = playback_callbacks._current_volume(chat_id)
    except Exception:
        volume = 100
    speed = CallService.get_playback_speed(chat_id) / 100
    speed_label = f"{speed:.2f}".rstrip("0").rstrip(".")
    if "." not in speed_label:
        speed_label += ".0"
    return t(
        _LANG,
        "public_cmd.player_status_active",
        state=state,
        title=_safe_status_title(active.get("title")),
        media_type=media_type,
        speed=speed_label,
        volume=volume,
        queue=queue_size,
    )


async def _set_id_output_mode_from_text(
    client: Client,
    message: Message,
    *,
    mode: str,
) -> None:
    user_id = message.from_user.id if message.from_user else None
    if user_id is None:
        await message.reply(t(_LANG, "common.errors.no_access"))
        return
    chat_id = message.chat.id
    if not await _guard_group_chat_settings(message, user_id, chat_id, client):
        return

    await id_command_settings_repo.set_output_mode(
        chat_id,
        mode,  # type: ignore[arg-type]
        updated_by=user_id,
    )
    confirm_key = {
        "photo": "public_cmd.show_id_mode_photo_set",
        "inactive": "public_cmd.show_id_mode_inactive_set",
    }.get(mode, "public_cmd.show_id_mode_simple_set")
    await message.reply(t(_LANG, confirm_key))


async def _reply_vip_list_message(message: Message) -> None:
    text, reply_markup = await _build_vip_page_view(message.chat.id, 0)
    await message.reply(text, reply_markup=reply_markup)


async def _reply_vip_clear_confirmation(message: Message) -> None:
    issued_at = int(time.time())
    await message.reply(
        t(_LANG, "panels.group.management.vip_clear_confirm"),
        reply_markup=KeyboardFactory.group_vip_clear_confirm(
            _LANG,
            message.chat.id,
            message.from_user.id if message.from_user else 0,
            issued_at,
        ),
    )


async def _build_group_settings_summary(sd: dict, chat_id: int) -> str:
    css = await call_security_repo.get_call_security_settings(chat_id)
    call_sec_on = bool(css and css.enabled)
    return "\n".join(
        [
            t(_LANG, "panels.group.settings_title"),
            "",
            _setting_entry(
                t(_LANG, "panels.group.settings.security_call"),
                _state_label(bool(sd["security_call"])),
            ),
            _setting_entry(
                t(_LANG, "panels.group.settings.call_security"),
                _state_label(call_sec_on),
            ),
            _setting_entry(
                t(_LANG, "panels.group.settings.download_users"),
                _state_label(bool(sd["download_users"])),
            ),
            _setting_entry(
                t(_LANG, "panels.group.settings.auto_clean"),
                _state_label(bool(sd["auto_clean"])),
            ),
            _setting_entry(
                t(_LANG, "panels.group.settings.call_message"),
                _state_label(bool(sd["call_message"])),
            ),
            _setting_entry(
                t(_LANG, "panels.group.settings.auto_ready_call"),
                _state_label(bool(sd["auto_ready_call"])),
            ),
            _setting_entry(
                t(_LANG, "panels.group.settings.music_video"),
                _state_label(bool(sd["music_video"])),
            ),
            _setting_entry(
                t(_LANG, "panels.group.settings.language_label"),
                t(_LANG, f"panels.group.settings.language_name_{sd.get('language', 'fa')}"),
            ),
            _setting_entry(
                t(_LANG, "panels.group.settings.default_media_summary_label"),
                t(
                    _LANG,
                    f"panels.group.settings.default_media_summary_{sd.get('default_media_type', 'audio')}",
                ),
            ),
            _setting_entry(
                t(_LANG, "panels.group.settings.call_report"),
                _state_label(bool(sd["call_report"])),
            ),
            _setting_entry(
                t(_LANG, "panels.group.settings.queue"),
                _state_label(bool(sd["queue"])),
            ),
            _setting_entry(
                t(_LANG, "panels.group.settings.show_id_summary_label"),
                _state_label(bool(sd["show_id"])),
            ),
            _setting_entry(
                t(_LANG, "panels.group.settings.show_photo_summary_label"),
                _state_label(bool(sd["show_photo"])),
            ),
            _setting_entry(
                t(_LANG, "panels.group.settings.show_text_summary_label"),
                _state_label(bool(sd["show_text"])),
            ),
            _setting_entry(
                t(_LANG, "panels.group.settings.call_stats_summary_label"),
                _state_label(bool(sd.get("call_stats", True))),
            ),
            _setting_entry(
                t(_LANG, "panels.group.settings.id_call_stats_summary_label"),
                (
                    _state_label(bool(sd.get("id_call_stats", False)))
                    if bool(sd.get("call_stats", True))
                    else t(_LANG, "panels.group.settings.id_call_stats_off_reason")
                ),
            ),
        ]
    )


async def _build_group_management_summary(chat_id: int) -> str:
    owners = await admin_repo.get_player_owners(chat_id)
    deputies = await admin_repo.get_player_deputies(chat_id)
    music_admins = await admin_repo.get_music_admins(chat_id)
    video_admins = await admin_repo.get_video_admins(chat_id)
    vip_count = await admin_repo.count_player_vips(chat_id)

    return "\n".join(
        [
            t(_LANG, "panels.group.management_title"),
            "",
            _setting_entry(
                t(_LANG, "panels.group.management.owners_list"),
                str(len(owners)),
            ),
            _setting_entry(
                t(_LANG, "panels.group.management.deputies_list"),
                str(len(deputies)),
            ),
            _setting_entry(
                t(_LANG, "panels.group.management.admins_list"),
                str(len(music_admins) + len(video_admins)),
            ),
            _setting_entry(
                t(_LANG, "panels.group.management.vip_list"),
                str(int(vip_count)),
            ),
        ]
    )


async def _navigate_to_group_root(client: Client, query: CallbackQuery) -> bool:
    """Edit the callback message to the group root panel."""
    return await safe_edit_navigation_message(
        client,
        query.message,
        t(_LANG, "panels.group.title"),
        reply_markup=KeyboardFactory.group_panel(_LANG),
        user_id=query.from_user.id,
    )


async def _answer_navigation_result(query: CallbackQuery, ok: bool) -> None:
    """Answer the callback query after navigation, alerting on failure."""
    if ok:
        await query.answer()
        return
    await query.answer(t(_LANG, "common.errors.navigation_failed"), show_alert=True)


async def _reply_group_panel(message: Message, client: Client | None = None) -> None:
    """Send the root group settings panel (shared by /settings and text aliases)."""
    user_id = message.from_user.id if message.from_user else 0
    if not await _guard_group_chat_settings(message, user_id, message.chat.id, client):
        return
    sent = await message.reply(
        t(_LANG, "panels.group.title"),
        reply_markup=KeyboardFactory.group_panel(_LANG),
    )
    if sent is not None:
        await remember_panel_from_message(sent, user_id)


async def render_group_settings_panel(client: Client, query: CallbackQuery) -> None:
    """Refresh the group settings submenu from a callback query."""
    chat_id = query.message.chat.id
    if not await _guard_group_chat_settings(query, query.from_user.id, chat_id, client):
        return
    sd = await _get_settings_dict(chat_id)
    await panel_callback_edit(
        client,
        query,
        await _build_group_settings_summary(sd, chat_id),
        KeyboardFactory.group_settings(_LANG, sd),
    )


async def _reply_player_panel(message: Message, client: Client, call_py) -> None:
    """Route پنل پلیر to group panel or Call Security in channels."""
    user_id = message.from_user.id if message.from_user else 0
    chat = message.chat
    if chat.type.value in ("group", "supergroup"):
        await _reply_group_panel(message, client)
        return
    from app.handlers.call_security_panel import (
        _can_open_panel,
        render_call_security_panel_message,
    )

    if not await _can_open_panel(user_id, chat.id, client):
        await message.reply(t(_LANG, "call_security.no_permission"))
        return
    await render_call_security_panel_message(client, message, call_py)


def register(bot: Client, call_py) -> None:  # noqa: ARG001

    # ── Group nav: back / home (priority over global callbacks.py handlers) ─
    @bot.on_callback_query(
        filters.regex(f"^{CB['NAV_BACK']}$") & _grp_any,
        group=GROUP_NAV_CALLBACK_GROUP,
    )
    async def grp_nav_back(client: Client, query: CallbackQuery):  # noqa: ARG001
        from app.utils.player_permissions import can_open_group_panel

        chat_id = query.message.chat.id
        user_id = query.from_user.id
        if not await can_open_group_panel(client, chat_id, user_id):
            await query.answer(t(_LANG, "call_security.no_permission"), show_alert=True)
            if hasattr(query, "stop_propagation"):
                query.stop_propagation()
            raise pyrogram.StopPropagation

        trace_callback_event(
            "nav_back.received",
            query,
            handler="grp_nav_back",
            route_type="group_override",
        )
        try:
            trace_callback_event(
                "nav_back.context",
                query,
                handler="grp_nav_back",
                route_type="group_override",
            )
            trace_callback_event(
                "nav_back.route_selected",
                query,
                handler="grp_nav_back",
                route_type="group_root",
                result="group_override",
            )
            trace_callback_event(
                "nav_back.edit_attempt",
                query,
                handler="grp_nav_back",
                route_type="group_root",
            )
            ok = await _navigate_to_group_root(client, query)
            trace_edit(query, result="edited" if ok else "failed", handler="grp_nav_back")
            trace_callback_event(
                "nav_back.edit_success" if ok else "nav_back.edit_fallback",
                query,
                level="info" if ok else "warning",
                handler="grp_nav_back",
                route_type="group_root",
                result="success" if ok else "failed",
            )
            await _answer_navigation_result(query, ok)
            trace_callback_event(
                "nav_back.answer_sent",
                query,
                handler="grp_nav_back",
                route_type="group_root",
                result="success" if ok else "failed",
            )
            if hasattr(query, "stop_propagation"):
                query.stop_propagation()
        except pyrogram.StopPropagation:
            raise
        except Exception as exc:
            trace_callback_event(
                "nav_back.exception",
                query,
                level="error",
                handler="grp_nav_back",
                route_type="group_override",
                result="failed",
                error=exc,
            )
            raise

    @bot.on_callback_query(
        filters.regex(f"^{CB['WZ_HOME']}$") & _grp_any,
        group=GROUP_NAV_CALLBACK_GROUP,
    )
    async def grp_wz_home(client: Client, query: CallbackQuery):  # noqa: ARG001
        from app.utils.player_permissions import can_open_group_panel

        chat_id = query.message.chat.id
        user_id = query.from_user.id
        if not await can_open_group_panel(client, chat_id, user_id):
            await query.answer(t(_LANG, "call_security.no_permission"), show_alert=True)
            if hasattr(query, "stop_propagation"):
                query.stop_propagation()
            raise pyrogram.StopPropagation

        try:
            await clear_runtime_state(
                client,
                query.from_user.id,
                query.message.chat.id,
            )
        except Exception:
            logger.warning(
                "grp_wz_home clear_runtime_state failed user_id=%s chat_id=%s",
                query.from_user.id,
                query.message.chat.id,
                exc_info=True,
            )
        ok = await _navigate_to_group_root(client, query)
        await _answer_navigation_result(query, ok)
        if hasattr(query, "stop_propagation"):
            query.stop_propagation()

    # ── /settings + تنظیمات (+ optional plain "settings") ───────────────
    @bot.on_message(_group_settings_message_filter(), group=PRIORITY_COMMAND_GROUP)
    @group_music_admin
    async def settings_command(client: Client, message: Message):  # noqa: ARG001
        await _reply_group_panel(message, client)

    # ── /panel + پنل ─────────────────────────────────────────────────────
    @bot.on_message(_panel_command_filter(), group=PRIORITY_COMMAND_GROUP)
    async def panel_command(client: Client, message: Message):
        user_id = message.from_user.id if message.from_user else 0
        from app.utils.player_permissions import can_open_group_panel

        if not await can_open_group_panel(client, message.chat.id, user_id):
            await message.reply(t(_LANG, "call_security.no_permission"))
            return
        await _reply_group_panel(message, client)

    @bot.on_message(
        _normalized_alias_filter(_GROUP_ID_TEXT_CMDS, name="GroupIdText")
        & group_chat_filter(),
        group=PRIORITY_COMMAND_GROUP,
    )
    async def group_id_command(client: Client, message: Message):
        from app.services.user_info_formatter_service import resolve_chat_dc_id

        chat_id = message.chat.id
        dc_label = await resolve_chat_dc_id(client, int(chat_id), _LANG)
        await message.reply(
            t(_LANG, "public_cmd.group_id", chat_id=chat_id)
            + "\n"
            + t(_LANG, "public_cmd.group_id_dc", dc_id=dc_label)
        )

    @bot.on_message(
        _normalized_alias_filter(_CHANNEL_ID_TEXT_CMDS, name="ChannelIdText")
        & filters.text,
        group=PRIORITY_COMMAND_GROUP,
    )
    async def channel_id_command(client: Client, message: Message):  # noqa: ARG001
        chat = message.chat
        chat_type = getattr(getattr(chat, "type", None), "value", None)
        if chat_type == "channel":
            await message.reply(t(_LANG, "public_cmd.channel_id", chat_id=chat.id))
            return
        if chat_type in ("group", "supergroup"):
            await message.reply(t(_LANG, "public_cmd.channel_id_current_chat", chat_id=chat.id))
            return
        await message.reply(t(_LANG, "public_cmd.channel_id_group_only"))

    @bot.on_message(
        _normalized_alias_filter(_PLAYER_STATUS_TEXT_CMDS, name="PlayerStatusText")
        & group_chat_filter(),
        group=PRIORITY_COMMAND_GROUP,
    )
    async def player_status_command(client: Client, message: Message):  # noqa: ARG001
        await message.reply(await _build_player_status_text(message.chat.id))

    @bot.on_message(
        _normalized_alias_filter(_USER_ID_TEXT_CMDS, name="UserIdText") & filters.text,
        group=PRIORITY_COMMAND_GROUP,
    )
    async def user_id_command(client: Client, message: Message):
        chat = message.chat
        chat_type = getattr(getattr(chat, "type", None), "value", None)
        is_group = chat_type in ("group", "supergroup")
        if is_group:
            output_mode = await id_command_settings_repo.get_output_mode(chat.id)
            include_call_stats = await call_stats_settings_repo.is_effective_id_call_stats(
                chat.id
            )
        else:
            output_mode = "simple"
            include_call_stats = False
        await user_info_formatter_service.reply_user_info(
            client,
            message,
            output_mode=output_mode,
            include_call_stats=include_call_stats,
            lang=_LANG,
        )

    @bot.on_message(
        _normalized_alias_filter(_SHOW_ID_PHOTO_TEXT_CMDS, name="ShowIdPhotoText")
        & group_chat_filter(),
        group=PRIORITY_COMMAND_GROUP,
    )
    @group_music_admin
    async def show_id_photo_command(client: Client, message: Message):  # noqa: ARG001
        await _set_id_output_mode_from_text(client, message, mode="photo")

    @bot.on_message(
        _normalized_alias_filter(_SHOW_ID_SIMPLE_TEXT_CMDS, name="ShowIdSimpleText")
        & group_chat_filter(),
        group=PRIORITY_COMMAND_GROUP,
    )
    @group_music_admin
    async def show_id_simple_command(client: Client, message: Message):  # noqa: ARG001
        await _set_id_output_mode_from_text(client, message, mode="simple")

    @bot.on_message(
        _normalized_alias_filter(_SHOW_ID_INACTIVE_TEXT_CMDS, name="ShowIdInactiveText")
        & group_chat_filter(),
        group=PRIORITY_COMMAND_GROUP,
    )
    @group_music_admin
    async def show_id_inactive_command(client: Client, message: Message):  # noqa: ARG001
        await _set_id_output_mode_from_text(client, message, mode="inactive")

    @bot.on_message(
        _normalized_alias_filter(_VIP_LIST_TEXT_CMDS, name="VipListText")
        & group_chat_filter(),
        group=PRIORITY_COMMAND_GROUP,
    )
    @group_music_admin
    async def vip_list_command(client: Client, message: Message):  # noqa: ARG001
        await _reply_vip_list_message(message)

    @bot.on_message(
        _normalized_alias_filter(_VIP_CLEAR_TEXT_CMDS, name="VipClearText")
        & group_chat_filter(),
        group=PRIORITY_COMMAND_GROUP,
    )
    @music_admin_or_above
    async def vip_clear_command(client: Client, message: Message):  # noqa: ARG001
        await _reply_vip_clear_confirmation(message)

    # ── Show group panel (from callback) ──────────────────────────────────
    @bot.on_callback_query(filters.regex(f"^{CB['GRP_SETTINGS']}$") & _grp_admin)
    @group_music_admin
    async def grp_settings(client: Client, query: CallbackQuery):
        if not await _guard_group_chat_settings(query, query.from_user.id, query.message.chat.id, client):
            return
        await query.answer()
        chat_id = query.message.chat.id
        sd = await _get_settings_dict(chat_id)
        await query.message.edit_text(
            await _build_group_settings_summary(sd, chat_id),
            reply_markup=KeyboardFactory.group_settings(_LANG, sd),
        )

    @bot.on_message(_player_panel_message_filter())
    async def player_panel_command(client: Client, message: Message):
        chat = message.chat
        user_id = message.from_user.id if message.from_user else 0
        if chat.type.value in ("group", "supergroup"):
            from app.utils.player_permissions import can_open_group_panel

            if not await can_open_group_panel(client, chat.id, user_id):
                await message.reply(t(_LANG, "call_security.no_permission"))
                return
            if not await _guard_group_chat_settings(message, user_id, chat.id, client):
                return
        await _reply_player_panel(message, client, call_py)

    @bot.on_callback_query(filters.regex(_INSTALL_SETUP_CALLBACK_PATTERN))
    async def install_player_setup_callback(client: Client, query: CallbackQuery):
        payload = _parse_install_setup_callback(query.data)
        if payload is None:
            await query.answer(t(_LANG, "common.errors.invalid_callback"), show_alert=True)
            return
        payload = await _validate_install_setup_callback(
            client,
            query,
            permission=_install_setup_permission_for_action(payload.action),
        )
        if payload is None:
            return

        action = payload.action
        if action == "Home":
            await query.answer(t(_LANG, "install.setup_home_opened"))
            await _edit_install_setup_panel(query, payload)
            return
        if action == "ShowSetCharge":
            await query.answer(t(_LANG, "install.setup_charge_menu_opened"))
            await _edit_install_setup_panel(query, payload, view="charge")
            return
        if action == "Access":
            await query.answer(t(_LANG, "install.setup_access_menu_opened"))
            await _edit_install_setup_panel(query, payload, view="access")
            return
        if action == "Lang":
            await query.answer(t(_LANG, "install.setup_language_menu_opened"))
            await _edit_install_setup_panel(query, payload, view="language")
            return
        if action == "SetLangFa" or action == "SetLangEn":
            result = await manager_command_service.set_setup_language(
                payload.chat_id,
                "fa" if action == "SetLangFa" else "en",
            )
            await query.answer(_install_setup_result_text(result), show_alert=True)
            await _edit_install_setup_panel(query, payload)
            return
        if action in {"Access:Music", "Access:Video"}:
            result = await manager_command_service.toggle_setup_access(
                payload.chat_id,
                "music" if action == "Access:Music" else "video",
            )
            await query.answer(_install_setup_result_text(result), show_alert=True)
            await _edit_install_setup_panel(query, payload, view="access")
            return
        if action in _INSTALL_SETUP_CHARGE_DURATIONS:
            result = await manager_command_service.apply_setup_charge(
                payload.chat_id,
                duration_days=_INSTALL_SETUP_CHARGE_DURATIONS[action],
                user_id=payload.user_id,
            )
            await query.answer(_install_setup_result_text(result), show_alert=True)
            await _edit_install_setup_panel(query, payload, view="charge")
            return
        if action == "ConfigAdmin":
            result = await manager_command_service.config_group_admins(
                client,
                payload.chat_id,
                payload.user_id,
            )
            await query.answer(_install_setup_result_text(result), show_alert=True)
            await _edit_install_setup_panel(query, payload)
            return
        if action == "AddCli":
            result = await manager_command_service.add_helper(
                payload.chat_id,
                bot_client=client,
            )
            await query.answer(_install_setup_result_text(result), show_alert=True)
            await _edit_install_setup_panel(query, payload)
            return
        if action == "Exit":
            await query.answer(t(_LANG, "install.setup_closed"))
            try:
                await query.message.delete()
            except Exception:
                await query.message.edit_text(t(_LANG, "install.setup_closed"))
            return

        await query.answer(t(_LANG, "common.errors.invalid_callback"), show_alert=True)

    @bot.on_callback_query(filters.regex(_INSTALL_SETUP_MALFORMED_PATTERN))
    async def install_player_setup_malformed(client: Client, query: CallbackQuery):  # noqa: ARG001
        await query.answer(t(_LANG, "common.errors.invalid_callback"), show_alert=True)

    # ── Toggle default_media_type (G20, §13.5) ──────────────────────────
    @bot.on_callback_query(filters.regex(f"^{CB['GRP_DEFAULT_MEDIA_TYPE']}$") & _grp_admin)
    @group_music_admin
    async def grp_toggle_default_media(client: Client, query: CallbackQuery):
        chat_id = query.message.chat.id
        if not await _guard_group_chat_settings(query, query.from_user.id, chat_id, client):
            return
        cs = await settings_repo.get_chat_settings(chat_id)
        if cs is None:
            cs = await settings_repo.create_defaults(chat_id)

        current = normalize_default_media_type(getattr(cs, "default_media_type", None))
        new_value = "video" if current == "audio" else "audio"
        await settings_repo.update_setting(chat_id, "default_media_type", new_value)
        await invalidate_chat_settings(chat_id)

        label = t(_LANG, "panels.group.settings.default_media_type")
        state = t(_LANG, f"playback.types.{new_value}")
        await query.answer(t(_LANG, "status.setting_value", feature=label, value=state), show_alert=True)

        sd = await _get_settings_dict(chat_id)
        await query.message.edit_text(
            await _build_group_settings_summary(sd, chat_id),
            reply_markup=KeyboardFactory.group_settings(_LANG, sd),
        )

    # ── Toggle per-chat panel language (fa ↔ en) ─────────────────────────
    @bot.on_callback_query(filters.regex(f"^{CB['GRP_LANGUAGE']}$") & _grp_admin)
    @group_music_admin
    async def grp_toggle_language(client: Client, query: CallbackQuery):
        chat_id = query.message.chat.id
        if not await _guard_group_chat_settings(query, query.from_user.id, chat_id, client):
            return
        cs = await settings_repo.get_chat_settings(chat_id)
        if cs is None:
            cs = await settings_repo.create_defaults(chat_id)

        current = normalize_lang(getattr(cs, "language", None))
        new_lang = "en" if current == "fa" else "fa"
        await settings_repo.update_setting(chat_id, "language", new_lang)
        await invalidate_chat_settings(chat_id)

        label = t(_LANG, "panels.group.settings.language_label")
        state = t(_LANG, f"panels.group.settings.language_name_{new_lang}")
        await query.answer(t(_LANG, "status.setting_value", feature=label, value=state), show_alert=True)

        sd = await _get_settings_dict(chat_id)
        await query.message.edit_text(
            await _build_group_settings_summary(sd, chat_id),
            reply_markup=KeyboardFactory.group_settings(_LANG, sd),
        )

    @bot.on_callback_query(filters.regex(f"^{CB['GRP_CALL_STATS']}$") & _grp_admin)
    @group_music_admin
    async def grp_toggle_call_stats(client: Client, query: CallbackQuery):
        chat_id = query.message.chat.id
        if not await _guard_group_chat_settings(query, query.from_user.id, chat_id, client):
            return
        await query.answer()
        current = await call_stats_settings_repo.get_enabled(chat_id)
        await call_stats_settings_repo.set_enabled(
            chat_id,
            not current,
            updated_by=query.from_user.id,
        )
        sd = await _get_settings_dict(chat_id)
        await query.message.edit_text(
            await _build_group_settings_summary(sd, chat_id),
            reply_markup=KeyboardFactory.group_settings(_LANG, sd),
        )

    @bot.on_callback_query(filters.regex(f"^{CB['GRP_ID_CALL_STATS']}$") & _grp_admin)
    @group_music_admin
    async def grp_toggle_id_call_stats(client: Client, query: CallbackQuery):
        chat_id = query.message.chat.id
        if not await _guard_group_chat_settings(query, query.from_user.id, chat_id, client):
            return
        if not await call_stats_settings_repo.get_enabled(chat_id):
            await query.answer(
                t(_LANG, "panels.group.settings.id_call_stats_off_reason"),
                show_alert=True,
            )
            return
        await query.answer()
        current = await id_command_settings_repo.get_show_call_stats(chat_id)
        await id_command_settings_repo.set_show_call_stats(
            chat_id,
            not current,
            updated_by=query.from_user.id,
        )
        sd = await _get_settings_dict(chat_id)
        await query.message.edit_text(
            await _build_group_settings_summary(sd, chat_id),
            reply_markup=KeyboardFactory.group_settings(_LANG, sd),
        )

    # ── Toggle a specific setting ─────────────────────────────────────────
    @bot.on_callback_query(filters.regex(_GROUP_SETTING_TOGGLE_REGEX) & _grp_admin)
    @group_music_admin
    async def grp_toggle_setting(client: Client, query: CallbackQuery):
        chat_id = query.message.chat.id
        if not await _guard_group_chat_settings(query, query.from_user.id, chat_id, client):
            return
        await query.answer()
        cb_data = query.data

        db_field = _SETTING_TOGGLE_MAP.get(cb_data)
        if db_field is None:
            await query.answer(t(_LANG, "common.errors.no_access"), show_alert=True)
            return

        cs = await settings_repo.get_chat_settings(chat_id)
        if cs is None:
            cs = await settings_repo.create_defaults(chat_id)

        current_value = getattr(cs, db_field, False)
        new_value = not current_value
        await settings_repo.update_setting(chat_id, db_field, new_value)
        await invalidate_chat_settings(chat_id)

        sd = await _get_settings_dict(chat_id)
        await query.message.edit_text(
            await _build_group_settings_summary(sd, chat_id),
            reply_markup=KeyboardFactory.group_settings(_LANG, sd),
        )

    # ── Management section ────────────────────────────────────────────────
    @bot.on_callback_query(filters.regex(f"^{CB['GRP_MANAGEMENT']}$") & _grp_admin)
    @group_music_admin
    async def grp_management(client: Client, query: CallbackQuery):
        await query.answer()
        chat_id = query.message.chat.id
        await query.message.edit_text(
            await _build_group_management_summary(chat_id),
            reply_markup=KeyboardFactory.group_management_menu(_LANG),
        )

    @bot.on_callback_query(filters.regex(f"^{CB['GRP_OWNERS_LIST']}$") & _grp_admin)
    @group_music_admin
    async def grp_owners_list(client: Client, query: CallbackQuery):
        await query.answer()
        chat_id = query.message.chat.id
        owners = await admin_repo.get_player_owners(chat_id)
        if not owners:
            await query.message.edit_text(
                t(_LANG, "filter_mgmt.list_empty"),
                reply_markup=KeyboardFactory.back_button(_LANG),
            )
            return
        lines = [t(_LANG, "list_fmt.user_item", user_id=o.user_id, username=o.username or "-") for o in owners]
        await query.message.edit_text(
            t(_LANG, "panels.group.management.owners_list") + "\n" + "\n".join(lines),
            reply_markup=KeyboardFactory.back_button(_LANG),
        )

    @bot.on_callback_query(filters.regex(f"^{CB['GRP_DEPUTIES_LIST']}$") & _grp_admin)
    @group_music_admin
    async def grp_deputies_list(client: Client, query: CallbackQuery):
        await query.answer()
        chat_id = query.message.chat.id
        deputies = await admin_repo.get_player_deputies(chat_id)
        if not deputies:
            await query.message.edit_text(
                t(_LANG, "filter_mgmt.list_empty"),
                reply_markup=KeyboardFactory.back_button(_LANG),
            )
            return
        lines = [t(_LANG, "list_fmt.user_item", user_id=d.user_id, username=d.username or "-") for d in deputies]
        await query.message.edit_text(
            t(_LANG, "panels.group.management.deputies_list") + "\n" + "\n".join(lines),
            reply_markup=KeyboardFactory.back_button(_LANG),
        )

    @bot.on_callback_query(filters.regex(f"^{CB['GRP_ADMINS_LIST']}$") & _grp_admin)
    @group_music_admin
    async def grp_admins_list(client: Client, query: CallbackQuery):
        await query.answer()
        chat_id = query.message.chat.id
        m_admins = await admin_repo.get_music_admins(chat_id)
        v_admins = await admin_repo.get_video_admins(chat_id)
        lines = [t(_LANG, "list_fmt.admin_item_m", user_id=a.user_id, username=a.username or "-") for a in m_admins]
        lines += [t(_LANG, "list_fmt.admin_item_v", user_id=a.user_id, username=a.username or "-") for a in v_admins]
        if not lines:
            await query.message.edit_text(
                t(_LANG, "filter_mgmt.list_empty"),
                reply_markup=KeyboardFactory.back_button(_LANG),
            )
            return
        await query.message.edit_text(
            t(_LANG, "panels.group.management.admins_list") + "\n" + "\n".join(lines),
            reply_markup=KeyboardFactory.back_button(_LANG),
        )

    @bot.on_callback_query(filters.regex(f"^{CB['GRP_VIP_LIST']}$") & _grp_admin)
    @group_music_admin
    async def grp_vip_list(client: Client, query: CallbackQuery):
        await query.answer()
        await _render_vip_page(query, query.message.chat.id, 0)

    @bot.on_callback_query(filters.regex(r"^pg:vip:\d+$") & _grp_admin)
    @group_music_admin
    async def grp_vip_page(client: Client, query: CallbackQuery):
        page = _parse_prefixed_nonnegative_int(query.data, CB["PAGE_VIP"])
        if page is None:
            await query.answer(t(_LANG, "common.errors.invalid_callback"), show_alert=True)
            return
        await query.answer()
        await _render_vip_page(query, query.message.chat.id, page)

    @bot.on_callback_query(filters.regex(r"^grp:vip:rm:\d+(:\d+)?$") & _grp_any)
    @music_admin_or_above
    async def grp_vip_demote(client: Client, query: CallbackQuery):
        parsed = _parse_vip_demote_payload(query.data)
        if parsed is None:
            await query.answer(t(_LANG, "common.errors.invalid_callback"), show_alert=True)
            return
        user_id, page = parsed
        chat_id = query.message.chat.id
        await admin_repo.demote_vip(chat_id, user_id)
        await query.answer(t(_LANG, "panels.group.management.vip_demoted"), show_alert=True)
        total = await admin_repo.count_player_vips(chat_id)
        if total == 0:
            await query.message.edit_text(
                t(_LANG, "panels.group.management.vip_list_title")
                + "\n"
                + t(_LANG, "filter_mgmt.list_empty"),
                reply_markup=KeyboardFactory.back_button(_LANG),
            )
            return
        page = admin_repo.clamp_vip_list_page(page, total)
        await _render_vip_page(query, chat_id, page)

    @bot.on_callback_query(filters.regex(r"^grp:vip:rm:(?!\d+$)") & _grp_any)
    async def grp_vip_demote_invalid(client: Client, query: CallbackQuery):  # noqa: ARG001
        await query.answer(t(_LANG, "common.errors.invalid_number"), show_alert=True)

    @bot.on_callback_query(filters.regex(r"^grp:vip:clear_confirm:-?\d+:\d+:\d+$") & _grp_any)
    @music_admin_or_above
    async def grp_vip_clear_confirm(client: Client, query: CallbackQuery):
        payload = _parse_group_clear_token(query.data, CB["GRP_VIP_CLEAR_CONFIRM_PREFIX"])
        if payload is None:
            await query.answer(t(_LANG, "common.errors.invalid_callback"), show_alert=True)
            return
        chat_id, user_id, issued_at = payload
        if chat_id != query.message.chat.id or user_id != query.from_user.id:
            await query.answer(t(_LANG, "common.errors.no_access"), show_alert=True)
            return
        if _is_stale_group_clear_token(issued_at):
            await query.answer(t(_LANG, "common.errors.invalid_callback"), show_alert=True)
            return
        count = await admin_repo.clear_player_vips(chat_id)
        await query.answer(
            t(_LANG, "panels.group.management.vip_clear_done", count=count),
            show_alert=True,
        )
        await query.message.edit_text(
            await _build_group_management_summary(chat_id),
            reply_markup=KeyboardFactory.group_management_menu(_LANG),
        )

    @bot.on_callback_query(filters.regex(r"^grp:vip:clear_cancel:-?\d+:\d+:\d+$") & _grp_any)
    @music_admin_or_above
    async def grp_vip_clear_cancel(client: Client, query: CallbackQuery):
        payload = _parse_group_clear_token(query.data, CB["GRP_VIP_CLEAR_CANCEL_PREFIX"])
        if payload is None:
            await query.answer(t(_LANG, "common.errors.invalid_callback"), show_alert=True)
            return
        chat_id, user_id, issued_at = payload
        if chat_id != query.message.chat.id or user_id != query.from_user.id:
            await query.answer(t(_LANG, "common.errors.no_access"), show_alert=True)
            return
        if _is_stale_group_clear_token(issued_at):
            await query.answer(t(_LANG, "common.errors.invalid_callback"), show_alert=True)
            return
        await query.answer(t(_LANG, "panels.group.management.vip_clear_cancelled"), show_alert=True)
        await query.message.edit_text(
            await _build_group_management_summary(chat_id),
            reply_markup=KeyboardFactory.group_management_menu(_LANG),
        )

    @bot.on_callback_query(filters.regex(r"^grp:vip:clear_(?:confirm|cancel):(?!-?\d+:\d+:\d+$)") & _grp_any)
    async def grp_vip_clear_malformed(client: Client, query: CallbackQuery):  # noqa: ARG001
        await query.answer(t(_LANG, "common.errors.invalid_callback"), show_alert=True)

    @bot.on_callback_query(filters.regex(f"^{CB['GRP_CLEAR_ALL']}$") & _grp_any)
    @player_deputy_or_above
    async def grp_clear_all(client: Client, query: CallbackQuery):
        chat_id = query.message.chat.id
        issued_at = int(time.time())
        await query.answer()
        await query.message.edit_text(
            t(_LANG, "panels.group.management.clear_admins_confirm"),
            reply_markup=KeyboardFactory.group_clear_admins_confirm(
                _LANG,
                chat_id,
                query.from_user.id,
                issued_at,
            ),
        )

    @bot.on_callback_query(filters.regex(r"^grp:mgmt:clear_confirm:-?\d+:\d+:\d+$") & _grp_any)
    @player_deputy_or_above
    async def grp_clear_confirm(client: Client, query: CallbackQuery):
        payload = _parse_group_clear_token(query.data, CB["GRP_CLEAR_CONFIRM_PREFIX"])
        if payload is None:
            await query.answer(t(_LANG, "common.errors.invalid_callback"), show_alert=True)
            return
        chat_id, user_id, issued_at = payload
        if chat_id != query.message.chat.id or user_id != query.from_user.id:
            await query.answer(t(_LANG, "common.errors.no_access"), show_alert=True)
            return
        if _is_stale_group_clear_token(issued_at):
            await query.answer(t(_LANG, "common.errors.invalid_callback"), show_alert=True)
            return
        await admin_repo.clear_music_admins(chat_id)
        await admin_repo.clear_video_admins(chat_id)
        await query.answer(t(_LANG, "panels.group.management.clear_admins_done"), show_alert=True)
        await query.message.edit_text(
            await _build_group_management_summary(chat_id),
            reply_markup=KeyboardFactory.group_management_menu(_LANG),
        )

    @bot.on_callback_query(filters.regex(r"^grp:mgmt:clear_cancel:-?\d+:\d+:\d+$") & _grp_any)
    @player_deputy_or_above
    async def grp_clear_cancel(client: Client, query: CallbackQuery):
        payload = _parse_group_clear_token(query.data, CB["GRP_CLEAR_CANCEL_PREFIX"])
        if payload is None:
            await query.answer(t(_LANG, "common.errors.invalid_callback"), show_alert=True)
            return
        chat_id, user_id, issued_at = payload
        if chat_id != query.message.chat.id or user_id != query.from_user.id:
            await query.answer(t(_LANG, "common.errors.no_access"), show_alert=True)
            return
        if _is_stale_group_clear_token(issued_at):
            await query.answer(t(_LANG, "common.errors.invalid_callback"), show_alert=True)
            return
        await query.answer(t(_LANG, "panels.group.management.clear_admins_cancelled"), show_alert=True)
        await query.message.edit_text(
            await _build_group_management_summary(chat_id),
            reply_markup=KeyboardFactory.group_management_menu(_LANG),
        )

    @bot.on_callback_query(filters.regex(r"^grp:mgmt:clear_(?:confirm|cancel):(?!-?\d+:\d+:\d+$)") & _grp_any)
    async def grp_clear_malformed(client: Client, query: CallbackQuery):  # noqa: ARG001
        await query.answer(t(_LANG, "common.errors.invalid_callback"), show_alert=True)

    # ── Help section ──────────────────────────────────────────────────────
    @bot.on_callback_query(filters.regex(f"^{CB['GRP_HELP']}$") & _grp_any)
    async def grp_help(client: Client, query: CallbackQuery):
        await render_help_home_panel(client, query)

    @bot.on_callback_query(filters.regex(f"^{CB['GRP_PROMOTE_DEMOTE']}$") & _grp_any)
    async def grp_help_promote(client: Client, query: CallbackQuery):
        await query.answer()
        text = t(_LANG, "help_content.promote_demote")
        await query.message.edit_text(text, reply_markup=KeyboardFactory.back_button(_LANG))

    @bot.on_callback_query(filters.regex(f"^{CB['GRP_PLAY_COMMANDS']}$") & _grp_any)
    async def grp_help_play(client: Client, query: CallbackQuery):
        await query.answer()
        chat_id = query.message.chat.id
        reply_markup = await build_playback_type_menu(_LANG, chat_id)
        await query.message.edit_text(
            t(_LANG, "playback.choose_source"),
            reply_markup=reply_markup,
        )

    @bot.on_callback_query(filters.regex(f"^{CB['GRP_GENERAL_COMMANDS']}$") & _grp_any)
    async def grp_help_general(client: Client, query: CallbackQuery):
        await query.answer()
        text = t(_LANG, "help_content.general_commands")
        await query.message.edit_text(text, reply_markup=KeyboardFactory.back_button(_LANG))

    @bot.on_callback_query(filters.regex(f"^{CB['GRP_MANAGER_COMMANDS']}$") & _grp_any)
    async def grp_help_manager(client: Client, query: CallbackQuery):
        await query.answer()
        text = t(_LANG, "help_content.manager_commands")
        await query.message.edit_text(text, reply_markup=KeyboardFactory.back_button(_LANG))

    @bot.on_callback_query(filters.regex(f"^{CB['GRP_CALL_COMMANDS']}$") & _grp_any)
    async def grp_help_call(client: Client, query: CallbackQuery):
        await query.answer()
        text = t(_LANG, "help_content.call_commands")
        await query.message.edit_text(text, reply_markup=KeyboardFactory.back_button(_LANG))

    @bot.on_callback_query(filters.regex(f"^{CB['GRP_SUPPORT_REQUEST']}$") & _grp_any)
    async def grp_help_support(client: Client, query: CallbackQuery):
        await query.answer()
        text = t(_LANG, "panels.group.help.support_request")
        await query.message.edit_text(text, reply_markup=KeyboardFactory.back_button(_LANG))

    # ── Support section ───────────────────────────────────────────────────
    @bot.on_callback_query(filters.regex(f"^{CB['GRP_SUPPORT']}$") & _grp_any)
    async def grp_support(client: Client, query: CallbackQuery):
        await query.answer()
        await query.message.edit_text(
            t(_LANG, "panels.group.support_title"),
            reply_markup=KeyboardFactory.group_support_menu(_LANG),
        )

    @bot.on_callback_query(filters.regex(f"^{CB['GRP_CREATOR']}$") & _grp_any)
    async def grp_creator(client: Client, query: CallbackQuery):
        owner_id = await _resolve_owner_for_group_query(query)
        link = await get_developer_link(owner_user_id=owner_id)
        if not link:
            await query.answer(t(_LANG, "common.errors.not_configured"), show_alert=True)
            return
        await query.answer(link, show_alert=True)

    @bot.on_callback_query(filters.regex(f"^{CB['GRP_SUDO']}$") & _grp_any)
    async def grp_sudo(client: Client, query: CallbackQuery):
        player_owners = await admin_repo.get_player_owners(query.message.chat.id)
        if not player_owners:
            await query.answer(t(_LANG, "panels.group.support.player_owners_empty"), show_alert=True)
            return
        names = ", ".join(owner.username or str(owner.user_id) for owner in player_owners[:5])
        await query.answer(names, show_alert=True)

    @bot.on_callback_query(filters.regex(f"^{CB['GRP_GUIDE_CHANNEL']}$") & _grp_any)
    async def grp_guide_channel(client: Client, query: CallbackQuery):
        owner_id = await _resolve_owner_for_group_query(query)
        link = await get_guide_channel_link(owner_user_id=owner_id)
        if not link:
            await query.answer(t(_LANG, "common.errors.not_configured"), show_alert=True)
            return
        await query.answer(link, show_alert=True)

    @bot.on_callback_query(filters.regex(f"^{CB['GRP_BOT_CHANNEL']}$") & _grp_any)
    async def grp_bot_channel(client: Client, query: CallbackQuery):  # noqa: ARG001
        """PANEL-02: evidenced 'کانال ربات' support link."""
        from app.services.bot_settings_service import get_bot_channel_link

        owner_id = await _resolve_owner_for_group_query(query)
        link = await get_bot_channel_link(owner_user_id=owner_id)
        if not link:
            await query.answer(t(_LANG, "common.errors.not_configured"), show_alert=True)
            return
        await query.answer(link, show_alert=True)

    @bot.on_callback_query(filters.regex(f"^{CB['GRP_MESSENGER']}$") & _grp_any)
    async def grp_messenger(client: Client, query: CallbackQuery):  # noqa: ARG001
        """PANEL-02: evidenced 'پیامرسان' link (owner-configured custom link)."""
        from app.services.bot_settings_service import get_custom_link

        owner_id = await _resolve_owner_for_group_query(query)
        link = await get_custom_link(owner_user_id=owner_id)
        if not link:
            await query.answer(t(_LANG, "common.errors.not_configured"), show_alert=True)
            return
        await query.answer(link, show_alert=True)

    @bot.on_callback_query(filters.regex(f"^{CB['GRP_SUPPORT_GROUP']}$") & _grp_any)
    async def grp_support_group(client: Client, query: CallbackQuery):
        owner_id = await _resolve_owner_for_group_query(query)
        link = await get_support_group_link(owner_user_id=owner_id)
        if not link:
            await query.answer(t(_LANG, "common.errors.not_configured"), show_alert=True)
            return
        await query.answer(link, show_alert=True)

    @bot.on_callback_query(
        filters.regex(r"^(?:grp:|pg:vip:)"), group=GROUP_FAMILY_FALLBACK_GROUP
    )
    async def grp_permission_or_unknown_fallback(client: Client, query: CallbackQuery):
        """Explicit feedback when no grp:* route accepted a group-visible tap.

        Group-panel routes keep deny-capable admin filters (``_grp_admin``); a
        non-privileged member's tap then skips every specific handler and would
        surface as the generic ``unknown_callback``. This family fallback runs
        after all specific route groups and answers with an explicit no-access
        alert instead. Suppressed when a specific route already ran, so it
        never intercepts healthy taps and never weakens the global fallback.
        """
        if getattr(query, "_musicbot_callback_route_seen", False):
            return
        from app.utils.callback_trace import safe_answer_callback, trace_guard

        user_id = query.from_user.id if query.from_user else None
        chat = query.message.chat if query.message else None
        chat_type = getattr(getattr(chat, "type", None), "value", None)
        if (
            user_id is not None
            and chat is not None
            and chat_type in ("group", "supergroup")
            and not is_developer(user_id)
        ):
            from app.utils.player_permissions import can_open_group_settings

            try:
                allowed = await can_open_group_settings(client, chat.id, user_id)
            except Exception:
                allowed = False
            if not allowed:
                trace_guard(
                    query,
                    guard="group_family_fallback",
                    result="deny",
                    reason="not_group_admin",
                    handler="grp_permission_or_unknown_fallback",
                )
                await safe_answer_callback(
                    query,
                    t(_LANG, "common.errors.no_access"),
                    show_alert=True,
                    text_key="common.errors.no_access",
                )
                return
        await safe_answer_callback(
            query,
            t(_LANG, "common.errors.unknown_callback"),
            show_alert=True,
            text_key="common.errors.unknown_callback",
        )

from __future__ import annotations

import logging
import math
import re
import time
from collections.abc import Callable

from pyrogram import Client, filters
from pyrogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup
from pyromod.exceptions import ListenerStopped
from sqlalchemy import and_, func, or_, select

from app.database.engine import async_session
from app.database.models import Channel, Group, GroupCredit, Sudo
from app.repositories import (
    admin_report_repo,
    blacklist_repo,
    broadcast_repo,
    channel_repo,
    filter_repo,
    force_join_repo,
    global_ban_repo,
    group_repo,
    log_repo,
    settings_repo,
    user_repo,
)
from app.database.models import Broadcast
from app.repositories.global_ban_repo import GlobalBanValidationError
from app.services import CreditService
from app.services import start_customization_service as start_custom
from app.services import owner_scope_service
from app.services import sudo_permission_ui_service
from app.services.admin_dashboard_service import AdminDashboardService
from app.services.admin_title_service import (
    apply_admin_title,
    preflight_admin_title_apply,
    preflight_manual_telegram_promotion,
    promote_telegram_admin,
)
from app.services.broadcast_service_v2 import BroadcastServiceV2
from app.services.forced_membership_service import ForcedMembershipService
from app.services.global_ban_service import RemovalSummary, remove_user_from_installed_chats
from app.services.media_capability_service import BOT_MEDIA_SETTING_KEYS, get_bot_media_feature_states
from app.services.notification_service import NotificationService
from app.services.panel_router import build_private_root_payload
from app.services.bot_settings_service import resolve_single_active_owner_user_id
from app.services.panel_message_service import panel_callback_edit, replace_panel_with_photo
from app.utils.ask_result import (
    AskResult,
    deliver_ask_outcome,
    notify_ask_abort,
    prompt_for_panel_input,
    safe_delete_user_input,
    safe_stop_listening,
)
from app.services import owner_text_link_service
from app.services.texts_links_ui import (
    build_text_field_payload,
    build_texts_hub_payload,
    confirmation_key_for_clear,
    confirmation_key_for_save,
    decode_setting_value,
    encode_media_value,
    extract_media_payload,
    field_from_callback,
    field_label_key,
    get_field_spec,
    is_owner_editable_field,
    is_valid_link_value,
    normalize_link_value,
)
from app.services.wizard_ui import (
    TOKEN_OWNER_ROOT,
    TOKEN_OWNER_TEXTS,
    TOKEN_ROLE_ROOT,
    build_cancel_kb,
    build_done_kb,
    cancel_and_resolve,
    pop_return_token,
    remember_return_token,
)
from app.utils.cache import (
    get_redis,
    invalidate_filterwords,
    invalidate_ownerlist,
    invalidate_sudolist,
)
from app.utils.bot_guards import is_developer
from app.utils.button_style import mark_toggle_state
from app.utils.decorators import owner_or_above
from app.utils.diagnostic_logging import create_logged_task
from app.utils.filters import owner_filter, private_chat_filter
from app.utils.helpers import (
    MAX_CREDIT_DAYS,
    MAX_LIMIT_VALUE,
    parse_bounded_int,
    parse_user_id,
)
from app.utils.i18n import AUTO_LANG, label, t
from app.utils.redis_keys import (
    TTL_OWNER_BROADCAST_CONFIRM,
    owner_broadcast_confirm_claim_key,
)
from app.utils.ui import CB, KeyboardFactory, compatible_inline_button

logger = logging.getLogger(__name__)

_LANG = AUTO_LANG
_TIMEOUT = 60
_OWNER_CONFIRM_TTL_SECONDS = 300
_OWNER_BANALL_PAGE_SIZE = 10

_OWNER_BC_MODE_CODES = {
    ("group", "copy"): "gc",
    ("group", "forward"): "gf",
    ("private", "copy"): "pc",
    ("private", "forward"): "pf",
    ("channel", "copy"): "cc",
    ("channel", "forward"): "cf",
}
_OWNER_BC_MODE_DECODE = {v: k for k, v in _OWNER_BC_MODE_CODES.items()}
_ADMIN_TITLE_MAX_LEN = 32
_ADMIN_TITLE_URL_RE = re.compile(r"https?://|t\.me/|www\.", re.IGNORECASE)

_pm_own = owner_filter() & private_chat_filter()
async def _ask(
    client: Client,
    chat_id: int,
    key: str,
    lang: str = "fa",
    *,
    user_id: int | None = None,
    return_to: str = TOKEN_ROLE_ROOT,
    prompt_kwargs: dict[str, str] | None = None,
    prompt_text: str | None = None,
    delete_response: bool = True,
) -> AskResult:
    if user_id is not None:
        await remember_return_token(user_id, return_to)

    stopped = await safe_stop_listening(client, chat_id, user_id=user_id)
    logger.debug(
        "owner ask start key=%s chat_id=%s user_id=%s stop_listening=%s",
        key,
        chat_id,
        user_id,
        stopped,
    )

    try:
        message = await prompt_for_panel_input(
            client,
            chat_id,
            user_id,
            prompt_text or t(lang, key, **(prompt_kwargs or {})),
            build_cancel_kb(lang, return_to),
            timeout=_TIMEOUT,
        )
        if message and message.text and message.text.strip().lower() in ("/cancel", "cancel", "لغو"):
            await safe_delete_user_input(message)
            if user_id is not None:
                text, kb = await cancel_and_resolve(
                    client,
                    user_id,
                    chat_id,
                    "private",
                    lang=lang,
                    return_to=return_to,
                )
                await deliver_ask_outcome(client, chat_id, user_id, text, kb)
            else:
                await deliver_ask_outcome(
                    client,
                    chat_id,
                    None,
                    t(lang, "common.cancelled"),
                    build_done_kb(lang, return_to),
                )
            return AskResult(message=None, abort_reason="cancel_text", user_notified=True)
        if delete_response:
            await safe_delete_user_input(message)
        if user_id is not None:
            await pop_return_token(user_id)
        return AskResult(message=message)
    except ListenerStopped:
        logger.debug("owner ask listener stopped key=%s chat_id=%s user_id=%s", key, chat_id, user_id)
        # The callback/command that interrupted the listener redraws the panel.
        return AskResult(message=None, abort_reason="listener_stopped", user_notified=True)
    except Exception as exc:
        logger.debug(
            "owner ask failed key=%s chat_id=%s user_id=%s reason=%s",
            key,
            chat_id,
            user_id,
            type(exc).__name__,
        )
        await deliver_ask_outcome(
            client,
            chat_id,
            user_id,
            t(lang, "ask.timeout"),
            build_done_kb(lang, return_to),
        )
        if user_id is not None:
            await pop_return_token(user_id)
        return AskResult(message=None, abort_reason="timeout", user_notified=True)


async def _ask_value(
    client: Client,
    chat_id: int,
    key: str,
    parser: Callable[[object], object | None],
    invalid_text: str,
    *,
    user_id: int,
    return_to: str,
    prompt_kwargs: dict[str, str] | None = None,
) -> tuple[AskResult, object | None]:
    retry_prompt: str | None = None
    while True:
        result = await _ask(
            client,
            chat_id,
            key,
            user_id=user_id,
            return_to=return_to,
            prompt_kwargs=prompt_kwargs,
            prompt_text=retry_prompt,
        )
        if result.message is None:
            return result, None
        value = parser(result.message)
        if value is not None:
            return result, value
        base_prompt = t(_LANG, key, **(prompt_kwargs or {}))
        retry_prompt = f"{invalid_text}\n\n{base_prompt}"


async def _notify_texts_links_ask_abort(
    client: Client,
    chat_id: int,
    result: AskResult,
    return_to: str,
) -> bool:
    """Return True when a texts/links handler should stop after an aborted ask."""
    return await notify_ask_abort(
        client,
        chat_id,
        result,
        return_to=return_to,
        lang=_LANG,
        user_id=chat_id,
    )


async def _send_done(
    client: Client,
    chat_id: int,
    text: str,
    return_to: str,
    *,
    user_id: int | None = None,
) -> None:
    anchor_user_id = user_id if user_id is not None else chat_id
    await deliver_ask_outcome(
        client,
        chat_id,
        anchor_user_id,
        text,
        build_done_kb(_LANG, return_to),
    )


async def _send_owner_text_link_edit_denied(
    client: Client,
    query: CallbackQuery,
) -> None:
    await deliver_ask_outcome(
        client,
        query.message.chat.id,
        query.from_user.id,
        t(_LANG, "texts_links.owner_start_text_developer_only"),
        build_done_kb(_LANG, TOKEN_OWNER_TEXTS),
    )


def _setting_entry(label: str, value: str) -> str:
    template = t(_LANG, "list_fmt.setting_entry")
    return template.format(key=label, value=value)


def _nav_row(lang: str, *, back_cb: str) -> list[InlineKeyboardButton]:
    return [
        InlineKeyboardButton(t(lang, "common.buttons.back"), callback_data=back_cb),
        InlineKeyboardButton(t(lang, "common.buttons.home"), callback_data=CB["WZ_HOME"]),
    ]


_OWNER_NO_CREDIT_PAGE_SIZE = 50
_OWNER_INSTALL_LIST_LIMIT = 50


async def _deny_owner_global_scope(query: CallbackQuery) -> None:
    await query.message.edit_text(
        t(_LANG, "owner_mgmt.global_scope_unavailable"),
        reply_markup=build_done_kb(_LANG, TOKEN_OWNER_ROOT),
    )


async def _require_developer_for_global_owner_route(query: CallbackQuery) -> bool:
    if is_developer(getattr(query.from_user, "id", None)):
        return True
    await _deny_owner_global_scope(query)
    return False


async def _require_active_owner_for_text_link_route(query: CallbackQuery) -> bool:
    """Allow active owners; redirect pure developers to the global dev panel."""
    user_id = getattr(query.from_user, "id", None)
    if user_id and await user_repo.is_owner(user_id):
        return True
    if is_developer(user_id):
        await query.message.edit_text(
            t(_LANG, "texts_links.owner_dev_use_global_panel"),
            reply_markup=build_done_kb(_LANG, TOKEN_OWNER_ROOT),
        )
        return False
    await _deny_owner_global_scope(query)
    return False


async def _deny_owner_install_not_in_scope(
    query: CallbackQuery,
    owner_user_id: int,
    chat_id: int,
    chat_type: str,
) -> bool:
    """Answer and return True when the install is outside owner lineage scope."""
    if await owner_scope_service.assert_owner_install_access(
        owner_user_id,
        chat_id,
        chat_type,
        actor_user_id=query.from_user.id,
    ):
        return False
    await query.answer(t(_LANG, "owner_mgmt.not_in_scope"), show_alert=True)
    return True


async def _deny_owner_sudo_not_in_scope(query: CallbackQuery, sudo: Sudo | None) -> bool:
    """Answer and return True when the sudo is outside owner scope."""
    if await owner_scope_service.assert_sudo_belongs_to_actor(sudo, query.from_user.id):
        return False
    await query.answer(t(_LANG, "owner_mgmt.sudo_not_in_scope"), show_alert=True)
    return True


async def _owner_add_sudo(actor_id: int, uid: int) -> tuple[bool, str]:
    """Add or reactivate a sudo for an owner-scoped actor.

    Returns:
        Tuple of success flag and i18n result key.
    """
    existing = await user_repo.get_sudo_record(uid)
    if existing is not None and existing.is_active:
        if not owner_scope_service.sudo_belongs_to_actor(existing, actor_id):
            return False, "owner_mgmt.sudo_not_in_scope"
        return False, "owner_mgmt.sudo_already_active"
    if (
        existing is not None
        and existing.added_by is not None
        and not owner_scope_service.sudo_belongs_to_actor(existing, actor_id)
    ):
        return False, "owner_mgmt.sudo_not_in_scope"
    await user_repo.add_sudo(uid, added_by=actor_id)
    await invalidate_sudolist()
    return True, "owner_mgmt.sudo_added"


def _chat_model(chat_type: str):
    return Channel if chat_type == "channel" else Group


async def _get_owner_install_rows(
    owner_id: int,
    chat_type: str,
    *,
    limit: int = _OWNER_INSTALL_LIST_LIMIT,
) -> list[admin_report_repo.ChatInstallRow]:
    model = _chat_model(chat_type)
    sudo_user_ids = await owner_scope_service.get_sudo_user_ids_for_owner(owner_id)
    async with async_session() as session:
        stmt = (
            select(
                model.chat_id,
                model.chat_title,
                model.invite_link,
                model.status,
                model.installed_by,
                GroupCredit.credit_days,
                GroupCredit.expire_at,
            )
            .select_from(model)
            .outerjoin(
                GroupCredit,
                and_(
                    GroupCredit.chat_id == model.chat_id,
                    GroupCredit.chat_type == chat_type,
                ),
            )
            .where(
                model.status == "active",
                owner_scope_service.install_scope_clause(model, owner_id, sudo_user_ids),
            )
            .order_by(model.id.desc())
            .limit(limit)
        )
        result = await session.execute(stmt)

    return [
        admin_report_repo.ChatInstallRow(
            chat_id=row.chat_id,
            chat_type=chat_type,
            title=row.chat_title,
            invite_link=row.invite_link,
            credit_days=int(row.credit_days or 0),
            expire_at=row.expire_at,
            status=row.status,
            installed_by=row.installed_by,
        )
        for row in result.all()
    ]


async def _get_owner_no_credit_rows(
    owner_id: int,
    *,
    limit: int = _OWNER_NO_CREDIT_PAGE_SIZE,
) -> list[admin_report_repo.ChatInstallRow]:
    sudo_user_ids = await owner_scope_service.get_sudo_user_ids_for_owner(owner_id)
    group_scope = owner_scope_service.install_scope_clause(Group, owner_id, sudo_user_ids)
    channel_scope = owner_scope_service.install_scope_clause(Channel, owner_id, sudo_user_ids)
    async with async_session() as session:
        stmt = (
            select(
                GroupCredit.chat_id,
                GroupCredit.chat_type,
                GroupCredit.credit_days,
                GroupCredit.expire_at,
                func.coalesce(Group.chat_title, Channel.chat_title).label("chat_title"),
                func.coalesce(Group.invite_link, Channel.invite_link).label("invite_link"),
                Group.status.label("group_status"),
                Channel.status.label("channel_status"),
                func.coalesce(Group.installed_by, Channel.installed_by).label("installed_by"),
            )
            .select_from(GroupCredit)
            .outerjoin(
                Group,
                and_(
                    GroupCredit.chat_type == "group",
                    Group.chat_id == GroupCredit.chat_id,
                ),
            )
            .outerjoin(
                Channel,
                and_(
                    GroupCredit.chat_type == "channel",
                    Channel.chat_id == GroupCredit.chat_id,
                ),
            )
            .where(
                GroupCredit.chat_type.in_(("group", "channel")),
                GroupCredit.credit_days <= 0,
                or_(
                    and_(
                        GroupCredit.chat_type == "group",
                        Group.status == "active",
                        group_scope,
                    ),
                    and_(
                        GroupCredit.chat_type == "channel",
                        Channel.status == "active",
                        channel_scope,
                    ),
                ),
            )
            .order_by(GroupCredit.id.desc())
            .limit(limit)
        )
        result = await session.execute(stmt)

    return [
        admin_report_repo.ChatInstallRow(
            chat_id=row.chat_id,
            chat_type=row.chat_type,
            title=row.chat_title,
            invite_link=row.invite_link,
            credit_days=int(row.credit_days or 0),
            expire_at=row.expire_at,
            status=row.group_status if row.chat_type == "group" else row.channel_status,
            installed_by=row.installed_by,
        )
        for row in result.all()
    ]


# ── Owner Groups: paginated list / detail / row actions ────────────────────

_OWNER_GROUP_PAGE_SIZE = 3

_OWNER_KIND_ACTIVE_GROUPS = "act_g"
_OWNER_KIND_ACTIVE_CHANNELS = "act_c"
_OWNER_KIND_NO_CREDIT = "no_cr"
_OWNER_KIND_INACTIVE_GROUPS = "ina_g"
_OWNER_KIND_INACTIVE_CHANNELS = "ina_c"
_OWNER_KIND_RENEWAL = "ren"

_OWNER_GROUP_LIST_KINDS = frozenset(
    {
        _OWNER_KIND_ACTIVE_GROUPS,
        _OWNER_KIND_ACTIVE_CHANNELS,
        _OWNER_KIND_NO_CREDIT,
        _OWNER_KIND_INACTIVE_GROUPS,
        _OWNER_KIND_INACTIVE_CHANNELS,
        _OWNER_KIND_RENEWAL,
    }
)

_OWNER_GROUP_LIST_TITLE_KEYS = {
    _OWNER_KIND_ACTIVE_GROUPS: "panels.owner.list_installs_groups",
    _OWNER_KIND_ACTIVE_CHANNELS: "panels.owner.list_installs_channels",
    _OWNER_KIND_NO_CREDIT: "panels.owner.list_no_credit",
    _OWNER_KIND_INACTIVE_GROUPS: "panels.owner.list_inactive_groups",
    _OWNER_KIND_INACTIVE_CHANNELS: "panels.owner.list_inactive_channels",
    _OWNER_KIND_RENEWAL: "panels.owner.list_renewal",
}

# Kinds whose installs are still active in Telegram: credit +/- and leave apply.
_OWNER_GROUP_ACTIONABLE_KINDS = frozenset(
    {
        _OWNER_KIND_ACTIVE_GROUPS,
        _OWNER_KIND_ACTIVE_CHANNELS,
        _OWNER_KIND_NO_CREDIT,
        _OWNER_KIND_RENEWAL,
    }
)


def _owner_owns_installed_by(
    owner_id: int, sudo_user_ids: frozenset[int], installed_by: int | None
) -> bool:
    return installed_by is not None and (installed_by == owner_id or installed_by in sudo_user_ids)


async def _paged_owner_install_rows(
    model,
    chat_type: str,
    status: str,
    owner_id: int,
    sudo_user_ids: frozenset[int],
    page: int,
    page_size: int,
) -> tuple[list[admin_report_repo.ChatInstallRow], int]:
    scope = owner_scope_service.install_scope_clause(model, owner_id, sudo_user_ids)
    async with async_session() as session:
        total = (
            await session.execute(
                select(func.count()).select_from(model).where(model.status == status, scope)
            )
        ).scalar() or 0
        if total == 0:
            return [], 1
        total_pages = max(1, (total + page_size - 1) // page_size)
        page = max(0, min(page, total_pages - 1))
        stmt = (
            select(
                model.chat_id,
                model.chat_title,
                model.invite_link,
                model.status,
                model.installed_by,
                GroupCredit.credit_days,
                GroupCredit.expire_at,
            )
            .select_from(model)
            .outerjoin(
                GroupCredit,
                and_(GroupCredit.chat_id == model.chat_id, GroupCredit.chat_type == chat_type),
            )
            .where(model.status == status, scope)
            .order_by(model.id.desc())
            .offset(page * page_size)
            .limit(page_size)
        )
        result = await session.execute(stmt)
    rows = [
        admin_report_repo.ChatInstallRow(
            chat_id=row.chat_id,
            chat_type=chat_type,
            title=row.chat_title,
            invite_link=row.invite_link,
            credit_days=int(row.credit_days or 0),
            expire_at=row.expire_at,
            status=row.status,
            installed_by=row.installed_by,
        )
        for row in result.all()
    ]
    return rows, total_pages


async def _paged_owner_no_credit_rows(
    owner_id: int,
    sudo_user_ids: frozenset[int],
    page: int,
    page_size: int,
) -> tuple[list[admin_report_repo.ChatInstallRow], int]:
    group_scope = owner_scope_service.install_scope_clause(Group, owner_id, sudo_user_ids)
    channel_scope = owner_scope_service.install_scope_clause(Channel, owner_id, sudo_user_ids)
    where_clause = and_(
        GroupCredit.chat_type.in_(("group", "channel")),
        GroupCredit.credit_days <= 0,
        or_(
            and_(GroupCredit.chat_type == "group", Group.status == "active", group_scope),
            and_(GroupCredit.chat_type == "channel", Channel.status == "active", channel_scope),
        ),
    )
    base_stmt = (
        select(
            GroupCredit.id.label("row_id"),
            GroupCredit.chat_id,
            GroupCredit.chat_type,
            GroupCredit.credit_days,
            GroupCredit.expire_at,
            func.coalesce(Group.chat_title, Channel.chat_title).label("chat_title"),
            func.coalesce(Group.invite_link, Channel.invite_link).label("invite_link"),
            Group.status.label("group_status"),
            Channel.status.label("channel_status"),
            func.coalesce(Group.installed_by, Channel.installed_by).label("installed_by"),
        )
        .select_from(GroupCredit)
        .outerjoin(Group, and_(GroupCredit.chat_type == "group", Group.chat_id == GroupCredit.chat_id))
        .outerjoin(Channel, and_(GroupCredit.chat_type == "channel", Channel.chat_id == GroupCredit.chat_id))
        .where(where_clause)
    )
    async with async_session() as session:
        total = (await session.execute(select(func.count()).select_from(base_stmt.subquery()))).scalar() or 0
        if total == 0:
            return [], 1
        total_pages = max(1, (total + page_size - 1) // page_size)
        page = max(0, min(page, total_pages - 1))
        result = await session.execute(
            base_stmt.order_by(GroupCredit.id.desc()).offset(page * page_size).limit(page_size)
        )
    rows = [
        admin_report_repo.ChatInstallRow(
            chat_id=row.chat_id,
            chat_type=row.chat_type,
            title=row.chat_title,
            invite_link=row.invite_link,
            credit_days=int(row.credit_days or 0),
            expire_at=row.expire_at,
            status=row.group_status if row.chat_type == "group" else row.channel_status,
            installed_by=row.installed_by,
        )
        for row in result.all()
    ]
    return rows, total_pages


async def _paged_owner_renewal_rows(
    owner_id: int,
    sudo_user_ids: frozenset[int],
    page: int,
    page_size: int,
    *,
    hours: int = 24,
) -> tuple[list[admin_report_repo.ChatInstallRow], int]:
    threshold_days = max(1, (hours // 24) + 1)
    group_scope = owner_scope_service.install_scope_clause(Group, owner_id, sudo_user_ids)
    channel_scope = owner_scope_service.install_scope_clause(Channel, owner_id, sudo_user_ids)
    where_clause = and_(
        GroupCredit.chat_type.in_(("group", "channel")),
        GroupCredit.credit_days > 0,
        GroupCredit.credit_days <= threshold_days,
        or_(
            and_(GroupCredit.chat_type == "group", Group.status == "active", group_scope),
            and_(GroupCredit.chat_type == "channel", Channel.status == "active", channel_scope),
        ),
    )
    base_stmt = (
        select(
            GroupCredit.id.label("row_id"),
            GroupCredit.chat_id,
            GroupCredit.chat_type,
            GroupCredit.credit_days,
            GroupCredit.expire_at,
            func.coalesce(Group.chat_title, Channel.chat_title).label("chat_title"),
            func.coalesce(Group.invite_link, Channel.invite_link).label("invite_link"),
            Group.status.label("group_status"),
            Channel.status.label("channel_status"),
            func.coalesce(Group.installed_by, Channel.installed_by).label("installed_by"),
        )
        .select_from(GroupCredit)
        .outerjoin(Group, and_(GroupCredit.chat_type == "group", Group.chat_id == GroupCredit.chat_id))
        .outerjoin(Channel, and_(GroupCredit.chat_type == "channel", Channel.chat_id == GroupCredit.chat_id))
        .where(where_clause)
    )
    async with async_session() as session:
        total = (await session.execute(select(func.count()).select_from(base_stmt.subquery()))).scalar() or 0
        if total == 0:
            return [], 1
        total_pages = max(1, (total + page_size - 1) // page_size)
        page = max(0, min(page, total_pages - 1))
        result = await session.execute(
            base_stmt.order_by(GroupCredit.credit_days.asc(), GroupCredit.id.desc())
            .offset(page * page_size)
            .limit(page_size)
        )
    rows = [
        admin_report_repo.ChatInstallRow(
            chat_id=row.chat_id,
            chat_type=row.chat_type,
            title=row.chat_title,
            invite_link=row.invite_link,
            credit_days=int(row.credit_days or 0),
            expire_at=row.expire_at,
            status=row.group_status if row.chat_type == "group" else row.channel_status,
            installed_by=row.installed_by,
        )
        for row in result.all()
    ]
    return rows, total_pages


async def _fetch_owner_group_list_page(
    owner_id: int, kind: str, page: int, *, page_size: int = _OWNER_GROUP_PAGE_SIZE
) -> tuple[list[admin_report_repo.ChatInstallRow], int]:
    sudo_user_ids = await owner_scope_service.get_sudo_user_ids_for_owner(owner_id)
    if kind == _OWNER_KIND_ACTIVE_GROUPS:
        return await _paged_owner_install_rows(Group, "group", "active", owner_id, sudo_user_ids, page, page_size)
    if kind == _OWNER_KIND_ACTIVE_CHANNELS:
        return await _paged_owner_install_rows(Channel, "channel", "active", owner_id, sudo_user_ids, page, page_size)
    if kind == _OWNER_KIND_INACTIVE_GROUPS:
        return await _paged_owner_install_rows(Group, "group", "inactive", owner_id, sudo_user_ids, page, page_size)
    if kind == _OWNER_KIND_INACTIVE_CHANNELS:
        return await _paged_owner_install_rows(Channel, "channel", "inactive", owner_id, sudo_user_ids, page, page_size)
    if kind == _OWNER_KIND_NO_CREDIT:
        return await _paged_owner_no_credit_rows(owner_id, sudo_user_ids, page, page_size)
    if kind == _OWNER_KIND_RENEWAL:
        return await _paged_owner_renewal_rows(owner_id, sudo_user_ids, page, page_size)
    return [], 1


def _owner_group_row_payload(kind: str, chat_id: int, page: int) -> str:
    return f"{kind}:{chat_id}:{page}"


def _owner_group_list_kb(
    rows: list[admin_report_repo.ChatInstallRow], kind: str, page: int, total_pages: int
) -> InlineKeyboardMarkup:
    buttons: list[list[InlineKeyboardButton]] = []
    actionable = kind in _OWNER_GROUP_ACTIONABLE_KINDS
    for row in rows:
        payload = _owner_group_row_payload(kind, row.chat_id, page)
        action_row = [
            InlineKeyboardButton(
                t(_LANG, "reports.action_details"),
                callback_data=f"{CB['OWN_GRP_DETAIL_PREFIX']}{payload}",
            ),
        ]
        if row.invite_link:
            action_row.append(InlineKeyboardButton(t(_LANG, "reports.action_link"), url=row.invite_link))
        buttons.append(action_row)
        if actionable:
            buttons.append(
                [
                    InlineKeyboardButton(
                        t(_LANG, "reports.action_leave"),
                        callback_data=f"{CB['OWN_GRP_LEAVE_CONFIRM_PREFIX']}{payload}",
                    ),
                ]
            )

    nav: list[InlineKeyboardButton] = []
    page_prefix = f"{CB['OWN_GRP_LIST_PREFIX']}{kind}:"
    if page > 0:
        nav.append(InlineKeyboardButton(t(_LANG, "reports.pagination_prev"), callback_data=f"{page_prefix}{page - 1}"))
    if page + 1 < total_pages:
        nav.append(InlineKeyboardButton(t(_LANG, "reports.pagination_next"), callback_data=f"{page_prefix}{page + 1}"))
    if nav:
        buttons.append(nav)
    buttons.append(_nav_row(_LANG, back_cb=CB["NAV_BACK"]))
    return InlineKeyboardMarkup(buttons)


def _owner_group_detail_kb(
    row: admin_report_repo.ChatInstallRow, kind: str, page: int
) -> InlineKeyboardMarkup:
    payload = _owner_group_row_payload(kind, row.chat_id, page)
    buttons: list[list[InlineKeyboardButton]] = []
    if row.invite_link:
        buttons.append([InlineKeyboardButton(t(_LANG, "reports.action_link"), url=row.invite_link)])
    if kind in _OWNER_GROUP_ACTIONABLE_KINDS:
        buttons.append(
            [
                InlineKeyboardButton(
                    t(_LANG, "reports.action_leave"),
                    callback_data=f"{CB['OWN_GRP_LEAVE_CONFIRM_PREFIX']}{payload}",
                ),
            ]
        )
    buttons.append(
        [
            InlineKeyboardButton(
                t(_LANG, "reports.back_to_list"),
                callback_data=f"{CB['OWN_GRP_LIST_PREFIX']}{kind}:{page}",
            )
        ]
    )
    buttons.append([InlineKeyboardButton(t(_LANG, "common.buttons.home"), callback_data=CB["WZ_HOME"])])
    return InlineKeyboardMarkup(buttons)


def _format_owner_group_detail_text(row: admin_report_repo.ChatInstallRow) -> str:
    title_key = "reports.group_details_title" if row.chat_type == "group" else "reports.channel_details_title"
    link = row.invite_link or t(_LANG, "reports.value_unavailable")
    expire_at = (
        row.expire_at.strftime("%Y-%m-%d %H:%M")
        if row.expire_at is not None
        else t(_LANG, "reports.value_unavailable")
    )
    installed_by = (
        str(row.installed_by) if row.installed_by is not None else t(_LANG, "reports.value_unavailable")
    )
    status = label(_LANG, "install_status", row.status) if row.status else t(_LANG, "reports.value_unavailable")
    return "\n".join(
        [
            t(_LANG, title_key),
            "",
            t(
                _LANG,
                "reports.detail_body",
                title=row.title or str(row.chat_id),
                chat_id=row.chat_id,
                chat_type=label(_LANG, "chat_type", row.chat_type),
                link=link,
                credit_days=row.credit_days,
                expire_at=expire_at,
                installed_by=installed_by,
                status=status,
            ),
        ]
    )


async def _render_owner_group_list_page(
    query: CallbackQuery, kind: str, page: int, *, notice: str | None = None
) -> None:
    owner_id = query.from_user.id
    rows, total_pages = await _fetch_owner_group_list_page(owner_id, kind, page)
    if not rows:
        await query.message.edit_text(
            t(_LANG, "filter_mgmt.list_empty"),
            reply_markup=build_done_kb(_LANG, TOKEN_OWNER_ROOT),
        )
        return

    page = max(0, min(page, total_pages - 1))
    lines = [t(_LANG, _OWNER_GROUP_LIST_TITLE_KEYS[kind]), ""]
    if notice:
        lines.extend([notice, ""])

    for idx, row in enumerate(rows, start=(page * _OWNER_GROUP_PAGE_SIZE) + 1):
        installed_by = (
            str(row.installed_by) if row.installed_by is not None else t(_LANG, "reports.value_unavailable")
        )
        status = label(_LANG, "install_status", row.status) if row.status else t(_LANG, "reports.value_unavailable")
        link_status = (
            t(_LANG, "reports.list_link_available") if row.invite_link else t(_LANG, "reports.list_link_missing")
        )
        line = t(
            _LANG,
            "reports.list_item_compact",
            index=idx,
            title=row.title or str(row.chat_id),
            chat_id=row.chat_id,
            credit_days=row.credit_days,
            link_status=link_status,
            installed_by=installed_by,
            status=status,
        )
        if row.expire_at is not None:
            line += "\n" + t(
                _LANG, "reports.list_item_expire_at", expire_at=row.expire_at.strftime("%Y-%m-%d %H:%M")
            )
        lines.append(line)
        lines.append("")

    lines.append(t(_LANG, "list_fmt.page_indicator", page=page + 1, total=total_pages))
    await query.message.edit_text(
        "\n".join(lines),
        reply_markup=_owner_group_list_kb(rows, kind, page, total_pages),
        disable_web_page_preview=True,
    )


async def _render_owner_group_detail(
    query: CallbackQuery, kind: str, chat_id: int, page: int, *, notice: str | None = None
) -> None:
    owner_id = query.from_user.id
    sudo_user_ids = await owner_scope_service.get_sudo_user_ids_for_owner(owner_id)
    row = await admin_report_repo.get_install_row(chat_id)
    if row is None or not _owner_owns_installed_by(owner_id, sudo_user_ids, row.installed_by):
        await query.answer(t(_LANG, "owner_mgmt.not_in_scope"), show_alert=True)
        return
    text = _format_owner_group_detail_text(row)
    if notice:
        text = f"{notice}\n\n{text}"
    await query.message.edit_text(
        text,
        reply_markup=_owner_group_detail_kb(row, kind, page),
        disable_web_page_preview=True,
    )


def _parse_owner_group_payload(data: str, prefix: str) -> tuple[str, int, int] | None:
    """Parse ``{kind}:{chat_id}:{page}``."""
    if not data.startswith(prefix):
        return None
    parts = data[len(prefix):].split(":")
    if len(parts) != 3:
        return None
    kind, chat_id_raw, page_raw = parts
    if kind not in _OWNER_GROUP_LIST_KINDS:
        return None
    chat_id = parse_user_id(chat_id_raw)
    if chat_id is None:
        return None
    try:
        page = int(page_raw)
    except ValueError:
        return None
    return kind, chat_id, max(page, 0)


def _parse_owner_group_list_nav_payload(data: str, prefix: str) -> tuple[str, int] | None:
    """Parse ``{kind}:{page}``."""
    if not data.startswith(prefix):
        return None
    parts = data[len(prefix):].split(":")
    if len(parts) != 2:
        return None
    kind, page_raw = parts
    if kind not in _OWNER_GROUP_LIST_KINDS:
        return None
    try:
        page = int(page_raw)
    except ValueError:
        return None
    return kind, max(page, 0)


def _parse_owner_group_leave_payload(data: str, prefix: str) -> tuple[str, int, int, int, int] | None:
    """Parse ``{kind}:{chat_id}:{page}:{actor_id}:{issued_at}``."""
    if not data.startswith(prefix):
        return None
    parts = data[len(prefix):].split(":")
    if len(parts) != 5:
        return None
    kind, chat_id_raw, page_raw, actor_raw, issued_raw = parts
    if kind not in _OWNER_GROUP_LIST_KINDS:
        return None
    chat_id = parse_user_id(chat_id_raw)
    if chat_id is None:
        return None
    try:
        page = int(page_raw)
        actor_id = int(actor_raw)
        issued_at = int(issued_raw)
    except ValueError:
        return None
    return kind, chat_id, max(page, 0), actor_id, issued_at


async def _handle_owner_group_row_credit(
    client: Client, query: CallbackQuery, kind: str, chat_id: int, page: int, *, increase: bool
) -> None:
    owner_id = query.from_user.id
    from app.utils.bot_guards import is_developer
    if not is_developer(owner_id):
        await query.answer(t(_LANG, "common.errors.no_access"), show_alert=True)
        return
    chat_type = await _owner_managed_chat_type(owner_id, chat_id)
    if chat_type is None:
        await query.answer(t(_LANG, "owner_mgmt.not_in_scope"), show_alert=True)
        return
    row = await admin_report_repo.get_install_row(chat_id)
    title = row.title if row is not None else None

    pm_chat_id = query.message.chat.id
    resp, days = await _ask_value(
        client,
        pm_chat_id,
        "reports.enter_credit_days",
        lambda message: parse_bounded_int(
            getattr(message, "text", None),
            min_value=1,
            max_value=MAX_CREDIT_DAYS,
        ),
        t(_LANG, "reports.invalid_days"),
        user_id=owner_id,
        return_to=TOKEN_OWNER_ROOT,
        prompt_kwargs={"title": title or str(chat_id)},
    )
    if resp.message is None:
        return
    try:
        if increase:
            await CreditService.charge_managed_chat(
                chat_id, chat_type, days, operated_by=owner_id, note="owner_panel"
            )
            notice = t(_LANG, "reports.credit_updated", title=title or str(chat_id), amount=days)
        else:
            await CreditService.adjust_managed_credit(
                chat_id, chat_type, mode="decrease", amount=days, operated_by=owner_id, note="owner_panel"
            )
            notice = t(_LANG, "reports.credit_deducted_row", title=title or str(chat_id), amount=days)
    except ValueError as exc:
        if "not_managed" in str(exc):
            notice = t(_LANG, "reports.leave_not_found", chat_id=chat_id)
        else:
            logger.exception("owner group row credit adjustment rejected for %s", chat_id)
            notice = t(_LANG, "common.errors.try_later")
    except Exception:
        logger.exception("owner group row credit adjustment failed for %s", chat_id)
        notice = t(_LANG, "common.errors.try_later")

    await AdminDashboardService.invalidate_cache("lists")
    await AdminDashboardService.invalidate_cache("credit")
    await _render_owner_group_detail(query, kind, chat_id, page, notice=notice)


async def _render_owner_sudo_list_page(query: CallbackQuery, page: int) -> None:
    """Render paginated owner-scoped sudo list with detail buttons."""
    sudos = await owner_scope_service.get_scoped_sudos_for_actor(query.from_user.id)
    if not sudos:
        await query.message.edit_text(
            t(_LANG, "sudo_mgmt.list_empty"),
            reply_markup=build_done_kb(_LANG, TOKEN_OWNER_ROOT),
        )
        return

    page_size = 10
    total_pages = max(1, (len(sudos) + page_size - 1) // page_size)
    page = max(0, min(page, total_pages - 1))
    chunk = sudos[page * page_size : (page + 1) * page_size]

    lines = [t(_LANG, "sudo_mgmt.list_title"), t(_LANG, "sudo_permissions.owner_scope_note"), ""]
    lines.extend(_format_role_line(entry) for entry in chunk)
    lines.append("")
    lines.append(t(_LANG, "list_fmt.page_indicator", page=page + 1, total=total_pages))

    await query.message.edit_text(
        "\n".join(lines),
        reply_markup=sudo_permission_ui_service.build_owner_sudo_list_kb(
            chunk,
            page,
            total_pages,
            CB["PAGE_OWN_SUDOS"],
            TOKEN_OWNER_ROOT,
            _LANG,
        ),
    )


async def _render_owner_sudo_detail(query: CallbackQuery, user_id: int, page: int) -> None:
    """Render owner-scoped sudo detail with permission toggles."""
    sudo = await user_repo.get_sudo_record(user_id)
    if await _deny_owner_sudo_not_in_scope(query, sudo):
        return
    assert sudo is not None
    await query.message.edit_text(
        sudo_permission_ui_service.format_owner_sudo_detail_text(sudo, _LANG),
        reply_markup=sudo_permission_ui_service.build_owner_sudo_detail_kb(sudo, page, _LANG),
    )


def _format_install_rows(rows: list[admin_report_repo.ChatInstallRow]) -> str:
    lines: list[str] = []
    for row in rows:
        key = "list_fmt.channel_item" if row.chat_type == "channel" else "list_fmt.group_item"
        installed_by = (
            str(row.installed_by)
            if row.installed_by is not None
            else t(_LANG, "reports.value_unavailable")
        )
        status = (
            label(_LANG, "install_status", row.status)
            if row.status
            else t(_LANG, "reports.value_unavailable")
        )
        lines.append(
            t(
                _LANG,
                key,
                title=row.title or str(row.chat_id),
                chat_id=row.chat_id,
                chat_type=label(_LANG, "chat_type", row.chat_type),
                days=row.credit_days,
                link=row.invite_link or "-",
                status=status,
                installed_by=installed_by,
            )
        )
    return "\n".join(lines)


async def _build_owner_status_text(owner_id: int) -> str:
    groups = await _get_owner_install_rows(owner_id, "group")
    channels = await _get_owner_install_rows(owner_id, "channel")
    sudos = await owner_scope_service.get_owner_sudos(owner_id)
    total_credit = sum(row.credit_days for row in groups) + sum(row.credit_days for row in channels)
    return t(
        _LANG,
        "owner_mgmt.scoped_status",
        groups=len(groups),
        channels=len(channels),
        users=t(_LANG, "common.labels.unknown"),
        sudos=len(sudos),
        credits=total_credit,
    )


def _no_credit_list_lines(rows: list[admin_report_repo.ChatInstallRow]) -> list[str]:
    lines: list[str] = []
    for row in rows:
        status = (
            label(_LANG, "install_status", row.status)
            if row.status
            else t(_LANG, "reports.value_unavailable")
        )
        lines.append(
            t(
                _LANG,
                "list_fmt.no_credit_item",
                chat_id=row.chat_id,
                chat_type=label(_LANG, "chat_type", row.chat_type),
                days=row.credit_days,
                status=status,
            )
        )
    return lines


async def _show_owner_root(client: Client, query: CallbackQuery) -> None:
    text, kb = await build_private_root_payload(
        client,
        query.from_user.id,
        query.from_user.first_name or str(query.from_user.id),
        lang=_LANG,
        include_welcome=False,
    )
    await query.message.edit_text(text, reply_markup=kb)


def _owner_category_title(key: str) -> str:
    return t(_LANG, key)


async def _owner_settings_keyboard() -> InlineKeyboardMarkup:
    summary = await AdminDashboardService.get_general_summary()
    return KeyboardFactory.owner_sub_settings(
        _LANG,
        force_join_enabled=bool(summary.get("force_join_enabled", False)),
        auto_leave_enabled=bool(summary.get("auto_leave_enabled", False)),
        trial_enabled=bool(summary.get("trial_enabled", False)),
    )


async def _toggle_owner_setting(client: Client, query: CallbackQuery, key: str, _label_key: str) -> None:
    await query.answer()
    await settings_repo.toggle_bot_setting(
        key,
        updated_by=query.from_user.id,
    )
    if key == "force_join_enabled":
        await ForcedMembershipService.invalidate_cache()
    await AdminDashboardService.invalidate_cache("general")
    await _show_owner_root(client, query)


def _format_role_line(entry) -> str:
    name = getattr(entry, "display_name", None) or "-"
    username_raw = getattr(entry, "username", None)
    username = f"@{username_raw}" if username_raw else "-"
    return t(
        _LANG,
        "list_fmt.role_user_item",
        name=name,
        user_id=getattr(entry, "user_id", 0),
        username=username,
    )


def _role_list_kb(page: int, total_pages: int, page_prefix: str, return_to: str):
    from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup

    rows: list[list[InlineKeyboardButton]] = []
    nav: list[InlineKeyboardButton] = []
    if page > 0:
        nav.append(
            InlineKeyboardButton(
                t(_LANG, "common.buttons.prev"),
                callback_data=f"{page_prefix}{page - 1}",
            )
        )
    if page + 1 < total_pages:
        nav.append(
            InlineKeyboardButton(
                t(_LANG, "common.buttons.next"),
                callback_data=f"{page_prefix}{page + 1}",
            )
        )
    if nav:
        rows.append(nav)

    rows.append(
        [
            InlineKeyboardButton(
                t(_LANG, "common.buttons.back"),
                callback_data=f"{CB['WZ_BACK_PREFIX']}{return_to}",
            )
        ]
    )
    rows.append([InlineKeyboardButton(t(_LANG, "common.buttons.home"), callback_data=CB["WZ_HOME"])])
    return InlineKeyboardMarkup(rows)


async def _render_role_list(
    query: CallbackQuery,
    entries: list,
    title_key: str,
    empty_key: str,
    page: int,
    page_prefix: str,
    return_to: str,
) -> None:
    if not entries:
        await query.message.edit_text(
            t(_LANG, empty_key),
            reply_markup=build_done_kb(_LANG, return_to),
        )
        return

    page_size = 10
    total_pages = max(1, (len(entries) + page_size - 1) // page_size)
    page = max(0, min(page, total_pages - 1))
    chunk = entries[page * page_size : (page + 1) * page_size]

    lines = [t(_LANG, title_key), ""]
    lines.extend(_format_role_line(e) for e in chunk)
    lines.append("")
    lines.append(t(_LANG, "list_fmt.page_indicator", page=page + 1, total=total_pages))

    await query.message.edit_text(
        "\n".join(lines),
        reply_markup=_role_list_kb(page, total_pages, page_prefix, return_to),
    )




def _owner_force_join_menu_kb(
    force_join_enabled: bool,
) -> InlineKeyboardMarkup:
    toggle_button = InlineKeyboardButton(
        t(_LANG, "panels.owner.toggle_force_join"),
        callback_data=CB["OWN_FORCE_JOIN_ENABLE_TOGGLE"],
    )
    mark_toggle_state(toggle_button, force_join_enabled)
    return InlineKeyboardMarkup(
        [
            [toggle_button],
            [InlineKeyboardButton(t(_LANG, "force_join_mgmt.add_btn"), callback_data=CB["OWN_FORCE_JOIN_ADD"])],
            [InlineKeyboardButton(t(_LANG, "force_join_mgmt.list_btn"), callback_data=CB["OWN_FORCE_JOIN_LIST"])],
            [
                InlineKeyboardButton(
                    t(_LANG, "common.buttons.back"),
                    callback_data=f"{CB['WZ_BACK_PREFIX']}{TOKEN_OWNER_ROOT}",
                )
            ],
            [InlineKeyboardButton(t(_LANG, "common.buttons.home"), callback_data=CB["WZ_HOME"])],
        ]
    )


def _owner_force_join_list_kb(channels: list, page: int, total_pages: int) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []

    for channel in channels:
        rows.append(
            [
                InlineKeyboardButton(
                    t(_LANG, "force_join_mgmt.remove_item_btn", channel_id=channel.channel_id),
                    callback_data=f"{CB['OWN_FORCE_JOIN_REMOVE_PREFIX']}{channel.channel_id}:{page}",
                )
            ]
        )

    nav: list[InlineKeyboardButton] = []
    if page > 0:
        nav.append(
            InlineKeyboardButton(
                t(_LANG, "common.buttons.prev"),
                callback_data=f"{CB['OWN_FORCE_JOIN_PAGE_PREFIX']}{page - 1}",
            )
        )
    if page + 1 < total_pages:
        nav.append(
            InlineKeyboardButton(
                t(_LANG, "common.buttons.next"),
                callback_data=f"{CB['OWN_FORCE_JOIN_PAGE_PREFIX']}{page + 1}",
            )
        )
    if nav:
        rows.append(nav)

    rows.append([InlineKeyboardButton(t(_LANG, "force_join_mgmt.add_btn"), callback_data=CB["OWN_FORCE_JOIN_ADD"])])
    rows.append([InlineKeyboardButton(t(_LANG, "force_join_mgmt.list_btn"), callback_data=CB["OWN_FORCE_JOIN_LIST"])])
    rows.append(
        [
            InlineKeyboardButton(
                t(_LANG, "common.buttons.back"),
                callback_data=f"{CB['WZ_BACK_PREFIX']}{TOKEN_OWNER_ROOT}",
            )
        ]
    )
    rows.append([InlineKeyboardButton(t(_LANG, "common.buttons.home"), callback_data=CB["WZ_HOME"])])
    return InlineKeyboardMarkup(rows)


def _force_join_status_text(status: str) -> str:
    if status == "ok":
        return t(_LANG, "force_join_mgmt.status_ok")
    if status == "pending":
        return t(_LANG, "force_join_mgmt.status_pending")
    if status == "bot_not_admin":
        return t(_LANG, "force_join_mgmt.status_bot_not_admin")
    if status == "inaccessible":
        return t(_LANG, "force_join_mgmt.status_inaccessible")
    return t(_LANG, "force_join_mgmt.status_unknown")


def _force_join_badge(status: str) -> str:
    if status == "ok":
        return t(_LANG, "admin.fm.badge_ok")
    if status == "pending":
        return t(_LANG, "admin.fm.badge_pending")
    return t(_LANG, "admin.fm.badge_broken")


async def _render_owner_force_join_home(query: CallbackQuery, *, notice: str | None = None) -> None:
    summary = await AdminDashboardService.get_general_summary()
    lines = [
        t(_LANG, "force_join_mgmt.menu_title"),
        "",
        t(_LANG, "force_join_mgmt.menu_desc"),
        "",
        _setting_entry(
            t(_LANG, "status.force_join_label"),
            t(_LANG, "common.labels.on") if bool(summary["force_join_enabled"]) else t(_LANG, "common.labels.off"),
        ),
        _setting_entry(
            t(_LANG, "panels.developer.summary_required_channels"),
            str(int(summary["required_channels"])),
        ),
    ]
    if notice:
        lines.extend(["", notice])

    await query.message.edit_text(
        "\n".join(lines),
        reply_markup=_owner_force_join_menu_kb(
            bool(summary["force_join_enabled"]),
        ),
    )


async def _render_owner_force_join_list(query: CallbackQuery, page: int, *, notice: str | None = None) -> None:
    channels, total_pages = await force_join_repo.get_active_targets_page(page, page_size=10)
    if not channels:
        summary = await AdminDashboardService.get_general_summary()
        await query.message.edit_text(
            t(_LANG, "force_join_mgmt.list_empty"),
            reply_markup=_owner_force_join_menu_kb(
                bool(summary["force_join_enabled"]),
            ),
        )
        return

    page = max(0, min(page, total_pages - 1))
    lines = [t(_LANG, "force_join_mgmt.list_title"), ""]
    if notice:
        lines.extend([notice, ""])

    for idx, channel in enumerate(channels, start=(page * 10) + 1):
        lines.append(
            t(
                _LANG,
                "force_join_mgmt.list_item",
                index=idx,
                badge=_force_join_badge(channel.verify_status),
                title=channel.display_name or str(channel.channel_id),
                channel_id=channel.channel_id,
                username=(f"@{channel.channel_username}" if channel.channel_username else t(_LANG, "reports.value_unavailable")),
                status=_force_join_status_text(channel.verify_status),
            )
        )

    lines.append("")
    lines.append(t(_LANG, "list_fmt.page_indicator", page=page + 1, total=total_pages))
    await query.message.edit_text(
        "\n".join(lines),
        reply_markup=_owner_force_join_list_kb(channels, page, total_pages),
    )


def _parse_owner_force_join_remove(data: str) -> tuple[int, int] | None:
    if not data.startswith(CB["OWN_FORCE_JOIN_REMOVE_PREFIX"]):
        return None
    raw = data[len(CB["OWN_FORCE_JOIN_REMOVE_PREFIX"]):]
    parts = raw.split(":")
    if len(parts) != 2:
        return None
    channel_id = parse_user_id(parts[0])
    if channel_id is None:
        return None
    try:
        page = int(parts[1])
    except ValueError:
        return None
    return channel_id, max(page, 0)


def _parse_owner_force_join_remove_confirm(data: str, prefix: str) -> tuple[int, int, int, int] | None:
    parsed = _parse_bound_payload(data, prefix, 4)
    if parsed is None:
        return None
    channel_id, page, actor_id, issued_at = parsed
    return channel_id, max(page, 0), actor_id, issued_at


async def _can_manage_force_join(user_id: int) -> bool:
    return is_developer(user_id) or await user_repo.is_owner(user_id)


def _is_stale_owner_confirm_token(issued_at: int) -> bool:
    return int(time.time()) - int(issued_at) > _OWNER_CONFIRM_TTL_SECONDS


def _owner_confirm_kb(yes_cb: str, no_cb: str, back_cb: str = CB["WZ_HOME"]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                compatible_inline_button(
                    t(_LANG, "common.buttons.confirm"),
                    callback_data=yes_cb,
                ),
                compatible_inline_button(
                    t(_LANG, "common.buttons.cancel_inline"),
                    callback_data=no_cb,
                ),
            ],
            [InlineKeyboardButton(t(_LANG, "common.buttons.back"), callback_data=back_cb)],
        ]
    )


def _parse_bound_payload(data: str, prefix: str, parts_count: int) -> list[int] | None:
    if not data.startswith(prefix):
        return None
    raw = data[len(prefix):]
    parts = raw.split(":")
    if len(parts) != parts_count:
        return None
    parsed: list[int] = []
    for part in parts:
        value = parse_user_id(part)
        if value is None:
            return None
        parsed.append(value)
    return parsed


def _parse_owner_text_clear_bound(data: str, prefix: str) -> tuple[str, int, int] | None:
    """Parse ``{field}:{user_id}:{issued_at}``, mirroring the developer variant."""
    if not data.startswith(prefix):
        return None
    parts = data[len(prefix):].rsplit(":", 2)
    if len(parts) != 3:
        return None
    field, user_raw, issued_raw = parts
    if get_field_spec(field) is None:
        return None
    try:
        user_id = int(user_raw)
        issued_at = int(issued_raw)
    except ValueError:
        return None
    if user_id <= 0 or issued_at <= 0:
        return None
    return field, user_id, issued_at


async def _owner_installed_chat_count() -> int:
    groups = await group_repo.get_all_active_groups()
    channels = await channel_repo.get_all_active_channels()
    chat_ids = {int(row.chat_id) for row in groups}
    chat_ids.update(int(row.chat_id) for row in channels)
    return len(chat_ids)


async def _run_owner_banall_removal_and_notify(
    client: Client,
    chat_id: int,
    target_user_id: int,
    actor_user_id: int,
) -> None:
    try:
        summary = await remove_user_from_installed_chats(client, target_user_id)
        await deliver_ask_outcome(
            client,
            chat_id,
            actor_user_id,
            _format_owner_removal_summary(summary),
            build_done_kb(_LANG, TOKEN_OWNER_ROOT),
        )
    except Exception:
        logger.exception(
            "Owner ban-all removal sweep failed user_id=%s",
            target_user_id,
        )
        await deliver_ask_outcome(
            client,
            chat_id,
            actor_user_id,
            t(_LANG, "global_ban.removal_failed"),
            build_done_kb(_LANG, TOKEN_OWNER_ROOT),
        )


def _format_owner_removal_summary(summary: RemovalSummary) -> str:
    return t(
        _LANG,
        "global_ban.removal_summary",
        attempted=summary.attempted,
        removed=summary.removed,
        failed=summary.failed,
        skipped=summary.skipped,
    )


def _format_owner_ban_list_text(bans: list, *, page: int, total: int) -> str:
    if not bans:
        return t(_LANG, "global_ban.list_empty")
    lines = [t(_LANG, "global_ban.list_title"), ""]
    for ban in bans:
        created = ban.created_at.strftime("%Y-%m-%d %H:%M") if ban.created_at else "-"
        lines.append(
            t(
                _LANG,
                "global_ban.list_item",
                user_id=ban.user_id,
                reason=ban.reason or "-",
                created_by=ban.created_by or "-",
                created_at=created,
            )
        )
    lines.append("")
    lines.append(t(_LANG, "global_ban.list_page", page=page + 1, total=total))
    return "\n".join(lines)


def _owner_banall_home_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton(t(_LANG, "global_ban.add"), callback_data=CB["OWN_BANALL_ADD"])],
            [InlineKeyboardButton(t(_LANG, "global_ban.remove"), callback_data=CB["OWN_BANALL_REMOVE"])],
            [InlineKeyboardButton(t(_LANG, "global_ban.list"), callback_data=f"{CB['OWN_BANALL_LIST_PREFIX']}0")],
            _nav_row(_LANG, back_cb=CB["NAV_BACK"]),
        ]
    )


def _owner_banall_list_kb(bans: list, page: int, total_pages: int) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for ban in bans:
        rows.append(
            [
                InlineKeyboardButton(
                    t(_LANG, "global_ban.row_remove", user_id=ban.user_id),
                    callback_data=f"{CB['OWN_BANALL_RM_PREFIX']}{ban.user_id}:{page}",
                )
            ]
        )
    nav: list[InlineKeyboardButton] = []
    if page > 0:
        nav.append(InlineKeyboardButton(t(_LANG, "common.buttons.prev"), callback_data=f"{CB['OWN_BANALL_LIST_PREFIX']}{page - 1}"))
    if page + 1 < total_pages:
        nav.append(InlineKeyboardButton(t(_LANG, "common.buttons.next"), callback_data=f"{CB['OWN_BANALL_LIST_PREFIX']}{page + 1}"))
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton(t(_LANG, "common.buttons.back"), callback_data=CB["OWN_BANALL_HOME"])])
    rows.append([InlineKeyboardButton(t(_LANG, "common.buttons.home"), callback_data=CB["WZ_HOME"])])
    return InlineKeyboardMarkup(rows)


async def _show_owner_banall_list(query: CallbackQuery, page: int) -> None:
    bans, total = await global_ban_repo.list_global_bans(page=page, limit=_OWNER_BANALL_PAGE_SIZE)
    total_pages = max(1, math.ceil(total / _OWNER_BANALL_PAGE_SIZE)) if total else 1
    await query.message.edit_text(
        _format_owner_ban_list_text(bans, page=page, total=total),
        reply_markup=_owner_banall_list_kb(bans, page, total_pages),
    )


def _broadcast_is_cancelable(bc: Broadcast | None) -> bool:
    return bc is not None and bc.status in {"pending", "running"}


def _format_owner_broadcast_history(broadcasts: list[Broadcast]) -> str:
    if not broadcasts:
        return t(_LANG, "broadcast.owner_history_empty")
    lines = [t(_LANG, "broadcast.owner_history_title"), ""]
    for bc in broadcasts:
        created = bc.created_at.strftime("%Y-%m-%d %H:%M") if bc.created_at else "-"
        lines.append(
            t(
                _LANG,
                "broadcast.owner_history_item",
                id=bc.id,
                scope=label(_LANG, "broadcast_scope", bc.target_scope),
                mode=bc.mode,
                status=bc.status,
                sent=bc.sent_count or 0,
                failed=bc.fail_count or 0,
                created_at=created,
            )
        )
    return "\n".join(lines)


def _owner_broadcast_history_kb(broadcasts: list[Broadcast]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for bc in broadcasts:
        if _broadcast_is_cancelable(bc):
            rows.append(
                [
                    InlineKeyboardButton(
                        t(_LANG, "broadcast.owner_cancel_btn", id=bc.id),
                        callback_data=f"{CB['OWN_BC_CANCEL_PREFIX']}{bc.id}",
                    )
                ]
            )
    rows.append(_nav_row(_LANG, back_cb=CB["OWN_BROADCAST"]))
    return InlineKeyboardMarkup(rows)


async def _render_owner_broadcast_history(query: CallbackQuery) -> None:
    broadcasts = await broadcast_repo.get_recent_by_admin(query.from_user.id, limit=20)
    await query.message.edit_text(
        _format_owner_broadcast_history(broadcasts),
        reply_markup=_owner_broadcast_history_kb(broadcasts),
    )


def _normalize_admin_title(raw: str | None) -> tuple[str | None, str | None]:
    title = (raw or "").strip()
    if not title or "\n" in title or _ADMIN_TITLE_URL_RE.search(title):
        return None, "admin_titles.invalid"
    if len(title) > _ADMIN_TITLE_MAX_LEN:
        return None, "admin_titles.too_long"
    return title, None


def _owner_sudo_title_rows(sudos: list[Sudo], page: int, total_pages: int) -> list[list[InlineKeyboardButton]]:
    rows: list[list[InlineKeyboardButton]] = []
    for sudo in sudos:
        uid = int(sudo.user_id)
        rows.append(
            [
                InlineKeyboardButton(t(_LANG, "admin_titles.set_sudo"), callback_data=f"{CB['OWN_TITLE_SUDO_SET_PREFIX']}{uid}:{page}"),
                InlineKeyboardButton(t(_LANG, "admin_titles.clear_sudo"), callback_data=f"{CB['OWN_TITLE_SUDO_CLEAR_PREFIX']}{uid}:{page}"),
            ]
        )
        rows.append(
            [
                InlineKeyboardButton(t(_LANG, "admin_titles.apply_telegram_title"), callback_data=f"{CB['OWN_TITLE_APPLY_SUDO_PREFIX']}{uid}:{page}"),
                InlineKeyboardButton(t(_LANG, "admin_titles.promote_telegram_admin"), callback_data=f"{CB['OWN_TGPROM_SUDO_PREFIX']}{uid}:{page}"),
            ]
        )
    nav: list[InlineKeyboardButton] = []
    if page > 0:
        nav.append(InlineKeyboardButton(t(_LANG, "common.buttons.prev"), callback_data=f"{CB['OWN_TITLE_SUDO_LIST_PREFIX']}{page - 1}"))
    if page + 1 < total_pages:
        nav.append(InlineKeyboardButton(t(_LANG, "common.buttons.next"), callback_data=f"{CB['OWN_TITLE_SUDO_LIST_PREFIX']}{page + 1}"))
    if nav:
        rows.append(nav)
    rows.append(_nav_row(_LANG, back_cb=CB["NAV_BACK"]))
    return rows


async def _render_owner_sudo_title_list(query: CallbackQuery, page: int) -> None:
    sudos = await owner_scope_service.get_scoped_sudos_for_actor(query.from_user.id)
    if not sudos:
        await query.message.edit_text(
            t(_LANG, "sudo_mgmt.list_empty"),
            reply_markup=build_done_kb(_LANG, TOKEN_OWNER_ROOT),
        )
        return
    page_size = 5
    total_pages = max(1, math.ceil(len(sudos) / page_size))
    page = max(0, min(page, total_pages - 1))
    chunk = sudos[page * page_size : (page + 1) * page_size]
    lines = [t(_LANG, "admin_titles.section_title"), t(_LANG, "sudo_permissions.owner_scope_note"), ""]
    for sudo in chunk:
        title = getattr(sudo, "admin_title", None) or t(_LANG, "admin_titles.not_set")
        lines.append(
            t(
                _LANG,
                "owner_mgmt.sudo_title_item",
                user_id=sudo.user_id,
                username=sudo.username or "-",
                title=title,
            )
        )
    lines.append("")
    lines.append(t(_LANG, "list_fmt.page_indicator", page=page + 1, total=total_pages))
    await query.message.edit_text(
        "\n".join(lines),
        reply_markup=InlineKeyboardMarkup(_owner_sudo_title_rows(chunk, page, total_pages)),
    )


async def _get_owner_scoped_sudo_or_deny(query: CallbackQuery, user_id: int) -> Sudo | None:
    sudo = await user_repo.get_sudo(user_id)
    if await _deny_owner_sudo_not_in_scope(query, sudo):
        return None
    return sudo


async def _owner_managed_chat_type(actor_id: int, chat_id: int) -> str | None:
    if await owner_scope_service.assert_owner_install_access(
        actor_id,
        chat_id,
        "group",
        actor_user_id=actor_id,
    ):
        return "group"
    if await owner_scope_service.assert_owner_install_access(
        actor_id,
        chat_id,
        "channel",
        actor_user_id=actor_id,
    ):
        return "channel"
    return None


def _parse_owner_title_target(data: str, prefix: str) -> tuple[int, int] | None:
    parsed = _parse_bound_payload(data, prefix, 2)
    if parsed is None:
        return None
    target_id, page = parsed
    return target_id, max(page, 0)


def _parse_owner_title_clear(data: str, prefix: str) -> tuple[int, int, int, int] | None:
    parsed = _parse_bound_payload(data, prefix, 4)
    if parsed is None:
        return None
    target_id, page, actor_id, issued_at = parsed
    return target_id, max(page, 0), actor_id, issued_at


def _parse_owner_title_apply(data: str, prefix: str) -> tuple[int, int, int, int, int] | None:
    parsed = _parse_bound_payload(data, prefix, 5)
    if parsed is None:
        return None
    target_id, chat_id, page, actor_id, issued_at = parsed
    return target_id, chat_id, max(page, 0), actor_id, issued_at


async def _send_owner_sudo_remove_confirm(client: Client, chat_id: int, actor_id: int, uid: int) -> None:
    issued_at = int(time.time())
    await deliver_ask_outcome(
        client,
        chat_id,
        actor_id,
        t(_LANG, "owner_mgmt.sudo_remove_confirm", user=uid),
        _owner_confirm_kb(
            f"{CB['OWN_REMOVE_SUDO_DO_PREFIX']}{uid}:{actor_id}:{issued_at}",
            f"{CB['OWN_REMOVE_SUDO_NO_PREFIX']}{uid}:{actor_id}:{issued_at}",
            CB["OWN_SUDOS"],
        ),
    )


def _owner_media_text(states: dict[str, bool]) -> str:
    lines = [t(_LANG, "owner_mgmt.media_settings_title"), ""]
    for feature, key in (
        ("audio", "panels.owner.media_audio"),
        ("video", "panels.owner.media_video"),
        ("file", "panels.owner.media_file"),
        ("download", "panels.owner.media_download"),
        ("buttons", "panels.owner.media_buttons"),
    ):
        lines.append(
            _setting_entry(
                t(_LANG, key),
                t(_LANG, "common.labels.on") if states.get(feature, True) else t(_LANG, "common.labels.off"),
            )
        )
    return "\n".join(lines)


async def _toggle_owner_media_feature(query: CallbackQuery, feature: str) -> None:
    key = BOT_MEDIA_SETTING_KEYS.get(feature)
    if key is None:
        await query.answer(t(_LANG, "common.errors.invalid_callback"), show_alert=True)
        return
    await settings_repo.toggle_bot_setting(key, updated_by=query.from_user.id, default=True)
    states = await get_bot_media_feature_states()
    await query.message.edit_text(
        _owner_media_text(states),
        reply_markup=KeyboardFactory.owner_sub_media(_LANG, states),
    )




def _build_blacklist_text(entries: list) -> str:
    if not entries:
        return t(_LANG, "blacklist_mgmt.list_empty")

    lines = [t(_LANG, "blacklist_mgmt.list_title"), ""]
    for entry in entries:
        lines.append(
            t(
                _LANG,
                "blacklist_mgmt.item",
                entity_type=label(
                    _LANG,
                    "entity_type",
                    getattr(entry, "entity_type", "user"),
                ),
                entity=str(getattr(entry, "entity_id", 0)),
            )
        )
    return "\n".join(lines)

async def _deny_force_join_access(query: CallbackQuery) -> None:
    await query.message.edit_text(
        t(_LANG, "force_join_mgmt.not_allowed"),
        reply_markup=build_done_kb(_LANG, TOKEN_OWNER_ROOT),
    )


async def render_owner_home_panel(client: Client, query: CallbackQuery) -> bool:
    """Render owner panel home via panel_callback_edit."""
    owner_id = query.from_user.id if query.from_user else 0
    text = await _build_owner_status_text(owner_id)
    return await panel_callback_edit(
        client,
        query,
        text,
        KeyboardFactory.owner_panel(_LANG),
        answer=False,
    )


def register(bot: Client, call_py) -> None:  # noqa: ARG001

    # ── Stats ─────────────────────────────────────────────────────────────
    @bot.on_callback_query(filters.regex(f"^{CB['OWN_STATS']}$") & _pm_own)
    @owner_or_above
    async def own_stats(client: Client, query: CallbackQuery):
        await query.answer()
        await render_owner_home_panel(client, query)

    @bot.on_callback_query(filters.regex(f"^{CB['OWN_GROUPS']}$") & _pm_own)
    @owner_or_above
    async def own_groups(client: Client, query: CallbackQuery):  # noqa: ARG001
        await query.answer()
        await query.message.edit_text(
            _owner_category_title("panels.owner.root_groups"),
            reply_markup=KeyboardFactory.owner_sub_groups(_LANG),
        )

    @bot.on_callback_query(filters.regex(f"^{CB['OWN_CREDIT']}$") & _pm_own)
    @owner_or_above
    async def own_credit(client: Client, query: CallbackQuery):  # noqa: ARG001
        await query.answer()
        await query.message.edit_text(
            _owner_category_title("panels.owner.root_credit"),
            reply_markup=KeyboardFactory.owner_sub_credit(_LANG),
        )

    @bot.on_callback_query(filters.regex(f"^{CB['OWN_SUDOS']}$") & _pm_own)
    @owner_or_above
    async def own_sudos(client: Client, query: CallbackQuery):  # noqa: ARG001
        await query.answer()
        await query.message.edit_text(
            _owner_category_title("panels.owner.root_sudos"),
            reply_markup=KeyboardFactory.owner_sub_users(_LANG),
        )

    @bot.on_callback_query(filters.regex(f"^{CB['OWN_SUDO_TITLES']}$") & _pm_own)
    @owner_or_above
    async def own_sudo_titles(client: Client, query: CallbackQuery):  # noqa: ARG001
        await query.answer()
        await query.message.edit_text(
            _owner_category_title("panels.owner.root_sudo_titles"),
            reply_markup=KeyboardFactory.owner_sub_sudo_titles(_LANG),
        )

    @bot.on_callback_query(filters.regex(f"^{CB['OWN_START_TEXT']}$") & _pm_own)
    @owner_or_above
    async def own_start_text(client: Client, query: CallbackQuery):  # noqa: ARG001
        await query.answer()
        if not await _require_active_owner_for_text_link_route(query):
            return
        text, kb = await build_text_field_payload(
            _LANG,
            "owner",
            "start_text",
            owner_user_id=query.from_user.id,
        )
        await query.message.edit_text(text, reply_markup=kb)

    @bot.on_callback_query(filters.regex(f"^{CB['OWN_BROADCAST']}$") & _pm_own)
    @owner_or_above
    async def own_broadcast(client: Client, query: CallbackQuery):  # noqa: ARG001
        await query.answer()
        await query.message.edit_text(
            _owner_category_title("panels.owner.root_broadcast"),
            reply_markup=KeyboardFactory.owner_sub_broadcast(_LANG),
        )

    @bot.on_callback_query(filters.regex(f"^{CB['OWN_LISTS']}$") & _pm_own)
    @owner_or_above
    async def own_lists(client: Client, query: CallbackQuery):  # noqa: ARG001
        await query.answer()
        await query.message.edit_text(
            _owner_category_title("panels.owner.root_lists"),
            reply_markup=KeyboardFactory.owner_sub_lists(_LANG),
        )

    @bot.on_callback_query(filters.regex(f"^{CB['OWN_MODERATION']}$") & _pm_own)
    @owner_or_above
    async def own_moderation(client: Client, query: CallbackQuery):  # noqa: ARG001
        await query.answer()
        await query.message.edit_text(
            _owner_category_title("panels.owner.root_moderation"),
            reply_markup=KeyboardFactory.owner_sub_moderation(_LANG),
        )

    @bot.on_callback_query(filters.regex(f"^{CB['OWN_MEDIA']}$") & _pm_own)
    @owner_or_above
    async def own_media(client: Client, query: CallbackQuery):  # noqa: ARG001
        await query.answer()
        states = await get_bot_media_feature_states()
        await query.message.edit_text(
            _owner_media_text(states),
            reply_markup=KeyboardFactory.owner_sub_media(_LANG, states),
        )

    @bot.on_callback_query(filters.regex(f"^{CB['OWN_REPORTS']}$") & _pm_own)
    @owner_or_above
    async def own_reports(client: Client, query: CallbackQuery):  # noqa: ARG001
        await query.answer()
        await query.message.edit_text(
            _owner_category_title("panels.owner.root_reports"),
            reply_markup=KeyboardFactory.owner_sub_reports(_LANG),
        )

    @bot.on_callback_query(filters.regex(f"^{CB['OWN_CAT_INSTALLS']}$") & _pm_own)
    @owner_or_above
    async def own_cat_installs(client: Client, query: CallbackQuery):
        await query.answer()
        await query.message.edit_text(
            _owner_category_title("panels.owner.root_lists"),
            reply_markup=KeyboardFactory.owner_sub_lists(_LANG),
        )

    @bot.on_callback_query(filters.regex(f"^{CB['OWN_CAT_BROADCAST']}$") & _pm_own)
    @owner_or_above
    async def own_cat_broadcast(client: Client, query: CallbackQuery):
        await query.answer()
        await query.message.edit_text(
            _owner_category_title("panels.owner.root_broadcast"),
            reply_markup=KeyboardFactory.owner_sub_broadcast(_LANG),
        )

    @bot.on_callback_query(filters.regex(f"^{CB['OWN_CAT_SETTINGS']}$") & _pm_own)
    @owner_or_above
    async def own_cat_settings(client: Client, query: CallbackQuery):
        await query.answer()
        await query.message.edit_text(
            _owner_category_title("panels.owner.root_moderation"),
            reply_markup=KeyboardFactory.owner_sub_moderation(_LANG),
        )

    @bot.on_callback_query(filters.regex(f"^{CB['OWN_CAT_USERS']}$") & _pm_own)
    @owner_or_above
    async def own_cat_users(client: Client, query: CallbackQuery):
        await query.answer()
        await query.message.edit_text(
            _owner_category_title("panels.owner.root_sudos"),
            reply_markup=KeyboardFactory.owner_sub_users(_LANG),
        )

    @bot.on_callback_query(filters.regex(f"^{CB['OWN_CAT_REPORTS']}$") & _pm_own)
    @owner_or_above
    async def own_cat_reports(client: Client, query: CallbackQuery):
        await query.answer()
        await query.message.edit_text(
            _owner_category_title("panels.owner.root_reports"),
            reply_markup=KeyboardFactory.owner_sub_reports(_LANG),
        )

    @bot.on_callback_query(filters.regex(f"^{CB['OWN_CAT_BILLING']}$") & _pm_own)
    @owner_or_above
    async def own_cat_billing(client: Client, query: CallbackQuery):
        await query.answer(t(_LANG, "wallet.topup_removed_notice"), show_alert=True)

    @bot.on_callback_query(filters.regex(f"^{CB['OWN_CAT_TEXTS']}$") & _pm_own)
    @owner_or_above
    async def own_cat_texts(client: Client, query: CallbackQuery):
        await query.answer()
        if not await _require_active_owner_for_text_link_route(query):
            return
        await query.message.edit_text(
            _owner_category_title("panels.owner.cat_texts_title"),
            reply_markup=KeyboardFactory.owner_sub_texts(_LANG),
        )

    @bot.on_callback_query(filters.regex(f"^{CB['OWN_LIST_OWNERS']}$") & _pm_own)
    @owner_or_above
    async def own_list_owners(client: Client, query: CallbackQuery):
        await query.answer()
        if not await _require_developer_for_global_owner_route(query):
            return
        owners = await user_repo.get_all_owners()
        await _render_role_list(
            query,
            owners,
            "owner_mgmt.list_title",
            "owner_mgmt.list_empty",
            0,
            CB["PAGE_OWN_OWNERS"],
            TOKEN_OWNER_ROOT,
        )

    @bot.on_callback_query(filters.regex(r"^pg:own:owners:\d+$") & _pm_own)
    @owner_or_above
    async def own_list_owners_page(client: Client, query: CallbackQuery):
        await query.answer()
        if not await _require_developer_for_global_owner_route(query):
            return
        owners = await user_repo.get_all_owners()
        page = int(query.data.split(":")[3])
        await _render_role_list(
            query,
            owners,
            "owner_mgmt.list_title",
            "owner_mgmt.list_empty",
            page,
            CB["PAGE_OWN_OWNERS"],
            TOKEN_OWNER_ROOT,
        )

    @bot.on_callback_query(filters.regex(f"^{CB['OWN_LIST_SUDOS']}$") & _pm_own)
    @owner_or_above
    async def own_list_sudos(client: Client, query: CallbackQuery):
        await query.answer()
        await _render_owner_sudo_list_page(query, 0)

    @bot.on_callback_query(filters.regex(r"^pg:own:sudos:\d+$") & _pm_own)
    @owner_or_above
    async def own_list_sudos_page(client: Client, query: CallbackQuery):
        await query.answer()
        page = int(query.data.split(":")[3])
        await _render_owner_sudo_list_page(query, page)

    @bot.on_callback_query(filters.regex(r"^own:sudo:detail:-?\d+:\d+$") & _pm_own)
    @owner_or_above
    async def own_sudo_detail(client: Client, query: CallbackQuery):  # noqa: ARG001
        await query.answer()
        parsed = sudo_permission_ui_service.parse_sudo_detail_payload(
            query.data,
            CB["OWN_SUDO_DETAIL_PREFIX"],
        )
        if parsed is None:
            await query.answer(t(_LANG, "sudo_permissions.invalid_callback"), show_alert=True)
            return
        user_id, page = parsed
        await _render_owner_sudo_detail(query, user_id, page)

    @bot.on_callback_query(filters.regex(r"^own:sudo:back:\d+$") & _pm_own)
    @owner_or_above
    async def own_sudo_list_back(client: Client, query: CallbackQuery):  # noqa: ARG001
        await query.answer()
        raw = query.data[len(CB["OWN_SUDO_LIST_BACK_PREFIX"]) :]
        try:
            page = max(0, int(raw))
        except ValueError:
            await query.answer(t(_LANG, "sudo_permissions.invalid_callback"), show_alert=True)
            return
        await _render_owner_sudo_list_page(query, page)

    @bot.on_callback_query(filters.regex(r"^own:title:sudo:list:\d+$") & _pm_own)
    @owner_or_above
    async def own_title_sudo_list(client: Client, query: CallbackQuery):  # noqa: ARG001
        await query.answer()
        page = int(query.data.split(":")[-1])
        await _render_owner_sudo_title_list(query, page)

    @bot.on_callback_query(filters.regex(r"^own:title:sudo:set:-?\d+:\d+$") & _pm_own)
    @owner_or_above
    async def own_title_sudo_set(client: Client, query: CallbackQuery):
        parsed = _parse_owner_title_target(query.data, CB["OWN_TITLE_SUDO_SET_PREFIX"])
        if parsed is None:
            await query.answer(t(_LANG, "common.errors.invalid_callback"), show_alert=True)
            return
        target_id, page = parsed
        if await _get_owner_scoped_sudo_or_deny(query, target_id) is None:
            return
        await query.answer()
        def _parse_title(message) -> str | None:
            title, error_key = _normalize_admin_title(
                getattr(message, "text", None)
            )
            return title if error_key is None else None

        resp, title = await _ask_value(
            client,
            query.message.chat.id,
            "admin_titles.enter_title",
            _parse_title,
            t(_LANG, "admin_titles.invalid"),
            user_id=query.from_user.id,
            return_to=TOKEN_OWNER_ROOT,
            prompt_kwargs={"max": str(_ADMIN_TITLE_MAX_LEN)},
        )
        if await notify_ask_abort(client, query.message.chat.id, resp, return_to=TOKEN_OWNER_ROOT, lang=_LANG):
            return
        if not await user_repo.set_sudo_admin_title(target_id, title):
            await _send_done(client, query.message.chat.id, t(_LANG, "sudo_mgmt.not_found"), TOKEN_OWNER_ROOT)
            return
        await _send_done(client, query.message.chat.id, t(_LANG, "admin_titles.saved"), TOKEN_OWNER_ROOT)
        await _render_owner_sudo_title_list(query, page)

    @bot.on_callback_query(filters.regex(r"^own:title:sudo:clear:-?\d+:\d+$") & _pm_own)
    @owner_or_above
    async def own_title_sudo_clear(client: Client, query: CallbackQuery):  # noqa: ARG001
        parsed = _parse_owner_title_target(query.data, CB["OWN_TITLE_SUDO_CLEAR_PREFIX"])
        if parsed is None:
            await query.answer(t(_LANG, "common.errors.invalid_callback"), show_alert=True)
            return
        target_id, page = parsed
        if await _get_owner_scoped_sudo_or_deny(query, target_id) is None:
            return
        await query.answer()
        issued_at = int(time.time())
        await query.message.edit_text(
            t(_LANG, "admin_titles.clear_confirm"),
            reply_markup=_owner_confirm_kb(
                f"{CB['OWN_TITLE_SUDO_CLEAR_DO_PREFIX']}{target_id}:{page}:{query.from_user.id}:{issued_at}",
                f"{CB['OWN_TITLE_SUDO_CLEAR_NO_PREFIX']}{target_id}:{page}:{query.from_user.id}:{issued_at}",
                f"{CB['OWN_TITLE_SUDO_LIST_PREFIX']}{page}",
            ),
        )

    @bot.on_callback_query(filters.regex(r"^own:title:sudo:clear:do:-?\d+:\d+:\d+:\d+$") & _pm_own)
    @owner_or_above
    async def own_title_sudo_clear_confirm(client: Client, query: CallbackQuery):  # noqa: ARG001
        parsed = _parse_owner_title_clear(query.data, CB["OWN_TITLE_SUDO_CLEAR_DO_PREFIX"])
        if parsed is None:
            await query.answer(t(_LANG, "common.errors.invalid_callback"), show_alert=True)
            return
        target_id, page, actor_id, issued_at = parsed
        if actor_id != query.from_user.id or _is_stale_owner_confirm_token(issued_at):
            await query.answer(t(_LANG, "admin_titles.stale_confirm"), show_alert=True)
            return
        if await _get_owner_scoped_sudo_or_deny(query, target_id) is None:
            return
        if not await user_repo.set_sudo_admin_title(target_id, None):
            await query.answer(t(_LANG, "sudo_mgmt.not_found"), show_alert=True)
            return
        await query.answer(t(_LANG, "admin_titles.cleared"), show_alert=True)
        await _render_owner_sudo_title_list(query, page)

    @bot.on_callback_query(filters.regex(r"^own:title:sudo:clear:no:-?\d+:\d+:\d+:\d+$") & _pm_own)
    @owner_or_above
    async def own_title_sudo_clear_abort(client: Client, query: CallbackQuery):  # noqa: ARG001
        parsed = _parse_owner_title_clear(query.data, CB["OWN_TITLE_SUDO_CLEAR_NO_PREFIX"])
        if parsed is None:
            await query.answer(t(_LANG, "common.errors.invalid_callback"), show_alert=True)
            return
        _target_id, page, actor_id, issued_at = parsed
        if actor_id != query.from_user.id or _is_stale_owner_confirm_token(issued_at):
            await query.answer(t(_LANG, "admin_titles.stale_confirm"), show_alert=True)
            return
        await query.answer(t(_LANG, "common.cancelled"), show_alert=False)
        await _render_owner_sudo_title_list(query, page)

    @bot.on_callback_query(filters.regex(r"^own:title:apply:sudo:-?\d+:\d+$") & _pm_own)
    @owner_or_above
    async def own_title_apply_sudo(client: Client, query: CallbackQuery):
        parsed = _parse_owner_title_target(query.data, CB["OWN_TITLE_APPLY_SUDO_PREFIX"])
        if parsed is None:
            await query.answer(t(_LANG, "common.errors.invalid_callback"), show_alert=True)
            return
        target_id, page = parsed
        sudo = await _get_owner_scoped_sudo_or_deny(query, target_id)
        if sudo is None:
            return
        title = (getattr(sudo, "admin_title", None) or "").strip()
        if not title:
            await query.answer(t(_LANG, "admin_titles.apply_title_not_set"), show_alert=True)
            return
        await query.answer()
        resp, chat_id = await _ask_value(
            client,
            query.message.chat.id,
            "admin_titles.apply_enter_chat_id",
            lambda message: parse_user_id(
                (getattr(message, "text", "") or "").strip()
            ),
            t(_LANG, "admin_titles.apply_invalid_chat_id"),
            user_id=query.from_user.id,
            return_to=TOKEN_OWNER_ROOT,
        )
        if await notify_ask_abort(client, query.message.chat.id, resp, return_to=TOKEN_OWNER_ROOT, lang=_LANG):
            return
        if await _owner_managed_chat_type(query.from_user.id, chat_id) is None:
            await _send_done(client, query.message.chat.id, t(_LANG, "owner_mgmt.not_in_scope"), TOKEN_OWNER_ROOT)
            return
        preflight = await preflight_admin_title_apply(client, chat_id, target_id)
        if not preflight.ok:
            await _send_done(client, query.message.chat.id, t(_LANG, preflight.message_key), TOKEN_OWNER_ROOT)
            return
        issued_at = int(time.time())
        await deliver_ask_outcome(
            client,
            query.message.chat.id,
            query.from_user.id,
            t(_LANG, "admin_titles.apply_confirm", user_id=target_id, chat_id=chat_id, title=title),
            _owner_confirm_kb(
                f"{CB['OWN_TITLE_APPLY_DO_PREFIX']}{target_id}:{chat_id}:{page}:{query.from_user.id}:{issued_at}",
                f"{CB['OWN_TITLE_APPLY_NO_PREFIX']}{target_id}:{chat_id}:{page}:{query.from_user.id}:{issued_at}",
                f"{CB['OWN_TITLE_SUDO_LIST_PREFIX']}{page}",
            ),
        )

    @bot.on_callback_query(filters.regex(r"^own:title:apply:do:-?\d+:-?\d+:\d+:\d+:\d+$") & _pm_own)
    @owner_or_above
    async def own_title_apply_confirm(client: Client, query: CallbackQuery):
        parsed = _parse_owner_title_apply(query.data, CB["OWN_TITLE_APPLY_DO_PREFIX"])
        if parsed is None:
            await query.answer(t(_LANG, "common.errors.invalid_callback"), show_alert=True)
            return
        target_id, chat_id, page, actor_id, issued_at = parsed
        if actor_id != query.from_user.id or _is_stale_owner_confirm_token(issued_at):
            await query.answer(t(_LANG, "admin_titles.stale_confirm"), show_alert=True)
            return
        sudo = await _get_owner_scoped_sudo_or_deny(query, target_id)
        if sudo is None:
            return
        title = (getattr(sudo, "admin_title", None) or "").strip()
        if not title:
            await query.answer(t(_LANG, "admin_titles.apply_title_not_set"), show_alert=True)
            return
        if await _owner_managed_chat_type(query.from_user.id, chat_id) is None:
            await query.message.edit_text(t(_LANG, "owner_mgmt.not_in_scope"), reply_markup=build_done_kb(_LANG, TOKEN_OWNER_ROOT))
            return
        preflight = await preflight_admin_title_apply(client, chat_id, target_id)
        if not preflight.ok:
            await query.message.edit_text(t(_LANG, preflight.message_key), reply_markup=build_done_kb(_LANG, TOKEN_OWNER_ROOT))
            return
        try:
            await apply_admin_title(client, chat_id, target_id, title)
        except Exception:
            await query.message.edit_text(t(_LANG, "admin_titles.apply_failed"), reply_markup=build_done_kb(_LANG, TOKEN_OWNER_ROOT))
            return
        await query.answer(t(_LANG, "admin_titles.apply_success"), show_alert=True)
        await _render_owner_sudo_title_list(query, page)

    @bot.on_callback_query(filters.regex(r"^own:title:apply:no:-?\d+:-?\d+:\d+:\d+:\d+$") & _pm_own)
    @owner_or_above
    async def own_title_apply_abort(client: Client, query: CallbackQuery):  # noqa: ARG001
        parsed = _parse_owner_title_apply(query.data, CB["OWN_TITLE_APPLY_NO_PREFIX"])
        if parsed is None:
            await query.answer(t(_LANG, "common.errors.invalid_callback"), show_alert=True)
            return
        _target_id, _chat_id, page, actor_id, issued_at = parsed
        if actor_id != query.from_user.id or _is_stale_owner_confirm_token(issued_at):
            await query.answer(t(_LANG, "admin_titles.stale_confirm"), show_alert=True)
            return
        await query.answer(t(_LANG, "admin_titles.apply_cancelled"), show_alert=False)
        await _render_owner_sudo_title_list(query, page)

    @bot.on_callback_query(filters.regex(r"^own:tgprom:sudo:-?\d+:\d+$") & _pm_own)
    @owner_or_above
    async def own_tg_prom_sudo(client: Client, query: CallbackQuery):
        parsed = _parse_owner_title_target(query.data, CB["OWN_TGPROM_SUDO_PREFIX"])
        if parsed is None:
            await query.answer(t(_LANG, "common.errors.invalid_callback"), show_alert=True)
            return
        target_id, page = parsed
        if await _get_owner_scoped_sudo_or_deny(query, target_id) is None:
            return
        await query.answer()
        resp, chat_id = await _ask_value(
            client,
            query.message.chat.id,
            "admin_titles.prom_enter_chat_id",
            lambda message: parse_user_id(
                (getattr(message, "text", "") or "").strip()
            ),
            t(_LANG, "admin_titles.prom_invalid_chat_id"),
            user_id=query.from_user.id,
            return_to=TOKEN_OWNER_ROOT,
        )
        if await notify_ask_abort(client, query.message.chat.id, resp, return_to=TOKEN_OWNER_ROOT, lang=_LANG):
            return
        if await _owner_managed_chat_type(query.from_user.id, chat_id) is None:
            await _send_done(client, query.message.chat.id, t(_LANG, "owner_mgmt.not_in_scope"), TOKEN_OWNER_ROOT)
            return
        preflight = await preflight_manual_telegram_promotion(client, chat_id, target_id)
        if not preflight.ok:
            await _send_done(client, query.message.chat.id, t(_LANG, preflight.message_key), TOKEN_OWNER_ROOT)
            return
        issued_at = int(time.time())
        await deliver_ask_outcome(
            client,
            query.message.chat.id,
            query.from_user.id,
            t(_LANG, "admin_titles.prom_confirm", user_id=target_id, chat_id=chat_id),
            _owner_confirm_kb(
                f"{CB['OWN_TGPROM_DO_PREFIX']}{target_id}:{chat_id}:{page}:{query.from_user.id}:{issued_at}",
                f"{CB['OWN_TGPROM_NO_PREFIX']}{target_id}:{chat_id}:{page}:{query.from_user.id}:{issued_at}",
                f"{CB['OWN_TITLE_SUDO_LIST_PREFIX']}{page}",
            ),
        )

    @bot.on_callback_query(filters.regex(r"^own:tgprom:do:-?\d+:-?\d+:\d+:\d+:\d+$") & _pm_own)
    @owner_or_above
    async def own_tg_prom_confirm(client: Client, query: CallbackQuery):
        parsed = _parse_owner_title_apply(query.data, CB["OWN_TGPROM_DO_PREFIX"])
        if parsed is None:
            await query.answer(t(_LANG, "common.errors.invalid_callback"), show_alert=True)
            return
        target_id, chat_id, page, actor_id, issued_at = parsed
        if actor_id != query.from_user.id or _is_stale_owner_confirm_token(issued_at):
            await query.answer(t(_LANG, "admin_titles.stale_confirm"), show_alert=True)
            return
        sudo = await _get_owner_scoped_sudo_or_deny(query, target_id)
        if sudo is None:
            return
        if await _owner_managed_chat_type(query.from_user.id, chat_id) is None:
            await query.message.edit_text(t(_LANG, "owner_mgmt.not_in_scope"), reply_markup=build_done_kb(_LANG, TOKEN_OWNER_ROOT))
            return
        preflight = await preflight_manual_telegram_promotion(client, chat_id, target_id)
        if not preflight.ok:
            await query.message.edit_text(t(_LANG, preflight.message_key), reply_markup=build_done_kb(_LANG, TOKEN_OWNER_ROOT))
            return
        title = (getattr(sudo, "admin_title", None) or "").strip()
        try:
            await promote_telegram_admin(client, chat_id, target_id)
            if title:
                await apply_admin_title(client, chat_id, target_id, title)
        except Exception:
            await query.message.edit_text(t(_LANG, "admin_titles.prom_failed"), reply_markup=build_done_kb(_LANG, TOKEN_OWNER_ROOT))
            return
        await query.answer(
            t(_LANG, "admin_titles.prom_success_with_title" if title else "admin_titles.prom_success"),
            show_alert=True,
        )
        await _render_owner_sudo_title_list(query, page)

    @bot.on_callback_query(filters.regex(r"^own:tgprom:no:-?\d+:-?\d+:\d+:\d+:\d+$") & _pm_own)
    @owner_or_above
    async def own_tg_prom_abort(client: Client, query: CallbackQuery):  # noqa: ARG001
        parsed = _parse_owner_title_apply(query.data, CB["OWN_TGPROM_NO_PREFIX"])
        if parsed is None:
            await query.answer(t(_LANG, "common.errors.invalid_callback"), show_alert=True)
            return
        _target_id, _chat_id, page, actor_id, issued_at = parsed
        if actor_id != query.from_user.id or _is_stale_owner_confirm_token(issued_at):
            await query.answer(t(_LANG, "admin_titles.stale_confirm"), show_alert=True)
            return
        await query.answer(t(_LANG, "admin_titles.prom_cancelled"), show_alert=False)
        await _render_owner_sudo_title_list(query, page)

    @bot.on_callback_query(filters.regex(r"^own:sp:t:[gcrbsa]:-?\d+:\d+$") & _pm_own)
    @owner_or_above
    async def own_sudo_perm_toggle(client: Client, query: CallbackQuery):  # noqa: ARG001
        parsed = sudo_permission_ui_service.parse_sudo_perm_toggle_payload(
            query.data,
            CB["OWN_SUDO_PERM_TOGGLE_PREFIX"],
        )
        if parsed is None:
            await query.answer(t(_LANG, "sudo_permissions.invalid_permission"), show_alert=True)
            return
        key, target_uid, page = parsed
        field = user_repo.resolve_sudo_permission_field(key)
        if field is None:
            await query.answer(t(_LANG, "sudo_permissions.invalid_permission"), show_alert=True)
            return
        sudo = await user_repo.get_sudo_record(target_uid)
        if await _deny_owner_sudo_not_in_scope(query, sudo):
            return
        assert sudo is not None
        perms = user_repo.sudo_permissions_from_row(sudo)
        if perms[field]:
            actor_id, issued_at = sudo_permission_ui_service.issue_confirm_bound(query.from_user.id)
            await query.message.edit_text(
                t(
                    _LANG,
                    "sudo_permissions.disable_confirm_prompt",
                    label=sudo_permission_ui_service.permission_field_label(_LANG, field),
                    user_id=target_uid,
                ),
                reply_markup=sudo_permission_ui_service.build_owner_sudo_perm_confirm_kb(
                    _LANG,
                    key,
                    target_uid,
                    page,
                    actor_id,
                    issued_at,
                ),
            )
            return
        ok = await user_repo.set_sudo_permission(target_uid, field, True)
        if not ok:
            await query.answer(t(_LANG, "sudo_permissions.update_failed"), show_alert=True)
            return
        await query.answer(t(_LANG, "owner_mgmt.sudo_permission_updated"), show_alert=True)
        await _render_owner_sudo_detail(query, target_uid, page)

    @bot.on_callback_query(filters.regex(r"^own:sp:y:[gcrbsa]:-?\d+:\d+:\d+:\d+$") & _pm_own)
    @owner_or_above
    async def own_sudo_perm_confirm(client: Client, query: CallbackQuery):  # noqa: ARG001
        parsed = sudo_permission_ui_service.parse_sudo_perm_confirm_bound(
            query.data,
            CB["OWN_SUDO_PERM_DO_PREFIX"],
        )
        if parsed is None:
            await query.answer(t(_LANG, "sudo_permissions.invalid_permission"), show_alert=True)
            return
        key, target_uid, page, actor_id, issued_at = parsed
        if actor_id != query.from_user.id:
            await query.answer(t(_LANG, "sudo_permissions.stale_confirm"), show_alert=True)
            return
        if _is_stale_owner_confirm_token(issued_at):
            await query.answer(t(_LANG, "sudo_permissions.stale_confirm"), show_alert=True)
            await _render_owner_sudo_detail(query, target_uid, page)
            return
        field = user_repo.resolve_sudo_permission_field(key)
        if field is None:
            await query.answer(t(_LANG, "sudo_permissions.invalid_permission"), show_alert=True)
            return
        sudo = await user_repo.get_sudo_record(target_uid)
        if await _deny_owner_sudo_not_in_scope(query, sudo):
            return
        ok = await user_repo.set_sudo_permission(target_uid, field, False)
        if not ok:
            await query.answer(t(_LANG, "sudo_permissions.update_failed"), show_alert=True)
            return
        await query.answer(t(_LANG, "owner_mgmt.sudo_permission_updated"), show_alert=True)
        await _render_owner_sudo_detail(query, target_uid, page)

    @bot.on_callback_query(filters.regex(r"^own:sp:n:[gcrbsa]:-?\d+:\d+:\d+:\d+$") & _pm_own)
    @owner_or_above
    async def own_sudo_perm_abort(client: Client, query: CallbackQuery):  # noqa: ARG001
        parsed = sudo_permission_ui_service.parse_sudo_perm_confirm_bound(
            query.data,
            CB["OWN_SUDO_PERM_NO_PREFIX"],
        )
        if parsed is None:
            await query.answer(t(_LANG, "sudo_permissions.invalid_permission"), show_alert=True)
            return
        _, target_uid, page, actor_id, issued_at = parsed
        if actor_id != query.from_user.id:
            await query.answer(t(_LANG, "sudo_permissions.stale_confirm"), show_alert=True)
            return
        if _is_stale_owner_confirm_token(issued_at):
            await query.answer(t(_LANG, "sudo_permissions.stale_confirm"), show_alert=True)
            await _render_owner_sudo_detail(query, target_uid, page)
            return
        await query.answer()
        await _render_owner_sudo_detail(query, target_uid, page)

    @bot.on_callback_query(filters.regex(r"^own:sp:(?:t|y|n):.*") & _pm_own)
    @owner_or_above
    async def own_sudo_perm_malformed(client: Client, query: CallbackQuery):  # noqa: ARG001
        for prefix, parser in (
            (CB["OWN_SUDO_PERM_TOGGLE_PREFIX"], sudo_permission_ui_service.parse_sudo_perm_toggle_payload),
            (CB["OWN_SUDO_PERM_DO_PREFIX"], sudo_permission_ui_service.parse_sudo_perm_confirm_bound),
            (CB["OWN_SUDO_PERM_NO_PREFIX"], sudo_permission_ui_service.parse_sudo_perm_confirm_bound),
        ):
            if query.data.startswith(prefix) and parser(query.data, prefix) is not None:
                return
        await query.answer(t(_LANG, "sudo_permissions.invalid_permission"), show_alert=True)

    @bot.on_callback_query(filters.regex(f"^{CB['OWN_REMOVE_OWNER']}$") & _pm_own)
    @owner_or_above
    async def own_remove_owner(client: Client, query: CallbackQuery):
        await query.answer()
        if not await _require_developer_for_global_owner_route(query):
            return
        chat_id = query.message.chat.id
        resp, uid = await _ask_value(
            client,
            chat_id,
            "ask.user_id",
            lambda message: parse_user_id(getattr(message, "text", None)),
            t(_LANG, "common.errors.invalid_number"),
            user_id=query.from_user.id,
            return_to=TOKEN_OWNER_ROOT,
        )
        if resp.message is None:
            return
        owner = await user_repo.get_owner(uid)
        if owner is None:
            await _send_done(client, chat_id, t(_LANG, "owner_mgmt.not_found"), TOKEN_OWNER_ROOT)
            return
        await user_repo.remove_owner(uid)
        await invalidate_ownerlist()
        await _send_done(client, chat_id, t(_LANG, "owner_mgmt.removed", user=str(uid)), TOKEN_OWNER_ROOT)

    @bot.on_callback_query(filters.regex(f"^{CB['OWN_REMOVE_SUDO']}$") & _pm_own)
    @owner_or_above
    async def own_remove_sudo(client: Client, query: CallbackQuery):
        await query.answer()
        chat_id = query.message.chat.id
        resp, uid = await _ask_value(
            client,
            chat_id,
            "ask.user_id",
            lambda message: parse_user_id(getattr(message, "text", None)),
            t(_LANG, "common.errors.invalid_number"),
            user_id=query.from_user.id,
            return_to=TOKEN_OWNER_ROOT,
        )
        if await notify_ask_abort(client, chat_id, resp, return_to=TOKEN_OWNER_ROOT, lang=_LANG):
            return
        sudo = await user_repo.get_sudo(uid)
        if sudo is None:
            await _send_done(client, chat_id, t(_LANG, "sudo_mgmt.not_found"), TOKEN_OWNER_ROOT)
            return
        if not owner_scope_service.sudo_belongs_to_actor(sudo, query.from_user.id):
            await _send_done(client, chat_id, t(_LANG, "owner_mgmt.sudo_not_in_scope"), TOKEN_OWNER_ROOT)
            return
        await _send_owner_sudo_remove_confirm(client, chat_id, query.from_user.id, uid)

    @bot.on_callback_query(filters.regex(r"^own:sudo:remove:do:-?\d+:\d+:\d+$") & _pm_own)
    @owner_or_above
    async def own_remove_sudo_confirm(client: Client, query: CallbackQuery):  # noqa: ARG001
        await query.answer()
        parsed = _parse_bound_payload(query.data, CB["OWN_REMOVE_SUDO_DO_PREFIX"], 3)
        if parsed is None:
            await query.answer(t(_LANG, "common.errors.invalid_callback"), show_alert=True)
            return
        uid, actor_id, issued_at = parsed
        if actor_id != query.from_user.id:
            await query.answer(t(_LANG, "common.errors.no_access"), show_alert=True)
            return
        if _is_stale_owner_confirm_token(issued_at):
            await query.answer(t(_LANG, "sudo_permissions.stale_confirm"), show_alert=True)
            await _show_owner_root(client, query)
            return
        sudo = await user_repo.get_sudo(uid)
        if sudo is None:
            await query.answer(t(_LANG, "sudo_mgmt.not_found"), show_alert=True)
            await _show_owner_root(client, query)
            return
        if await _deny_owner_sudo_not_in_scope(query, sudo):
            return
        await user_repo.remove_sudo(uid)
        await invalidate_sudolist()
        await query.message.edit_text(
            t(_LANG, "owner_mgmt.sudo_removed", user=str(uid)),
            reply_markup=build_done_kb(_LANG, TOKEN_OWNER_ROOT),
        )

    @bot.on_callback_query(filters.regex(r"^own:sudo:remove:no:-?\d+:\d+:\d+$") & _pm_own)
    @owner_or_above
    async def own_remove_sudo_abort(client: Client, query: CallbackQuery):  # noqa: ARG001
        await query.answer(t(_LANG, "common.cancelled"), show_alert=False)
        parsed = _parse_bound_payload(query.data, CB["OWN_REMOVE_SUDO_NO_PREFIX"], 3)
        if parsed is None:
            await query.answer(t(_LANG, "common.errors.invalid_callback"), show_alert=True)
            return
        _uid, actor_id, issued_at = parsed
        if actor_id != query.from_user.id:
            await query.answer(t(_LANG, "common.errors.no_access"), show_alert=True)
            return
        if _is_stale_owner_confirm_token(issued_at):
            await query.answer(t(_LANG, "sudo_permissions.stale_confirm"), show_alert=True)
        await _show_owner_root(client, query)

    # ── List groups / channels / inactive / renewal (paginated + detail) ───
    @bot.on_callback_query(filters.regex(f"^{CB['OWN_LIST_GROUPS']}$") & _pm_own)
    @owner_or_above
    async def own_list_groups(client: Client, query: CallbackQuery):  # noqa: ARG001
        await query.answer()
        await _render_owner_group_list_page(query, _OWNER_KIND_ACTIVE_GROUPS, 0)

    @bot.on_callback_query(filters.regex(f"^{CB['OWN_LIST_CHANNELS']}$") & _pm_own)
    @owner_or_above
    async def own_list_channels(client: Client, query: CallbackQuery):  # noqa: ARG001
        await query.answer()
        await _render_owner_group_list_page(query, _OWNER_KIND_ACTIVE_CHANNELS, 0)

    @bot.on_callback_query(filters.regex(f"^{CB['OWN_LIST_INACTIVE_GROUPS']}$") & _pm_own)
    @owner_or_above
    async def own_list_inactive_groups(client: Client, query: CallbackQuery):  # noqa: ARG001
        await query.answer()
        await _render_owner_group_list_page(query, _OWNER_KIND_INACTIVE_GROUPS, 0)

    @bot.on_callback_query(filters.regex(f"^{CB['OWN_LIST_INACTIVE_CHANNELS']}$") & _pm_own)
    @owner_or_above
    async def own_list_inactive_channels(client: Client, query: CallbackQuery):  # noqa: ARG001
        await query.answer()
        await _render_owner_group_list_page(query, _OWNER_KIND_INACTIVE_CHANNELS, 0)

    @bot.on_callback_query(filters.regex(f"^{CB['OWN_LIST_RENEWAL']}$") & _pm_own)
    @owner_or_above
    async def own_list_renewal(client: Client, query: CallbackQuery):  # noqa: ARG001
        await query.answer()
        await _render_owner_group_list_page(query, _OWNER_KIND_RENEWAL, 0)

    @bot.on_callback_query(filters.regex(r"^own:grp:l:[a-z_]+:\d+$") & _pm_own)
    @owner_or_above
    async def own_group_list_page(client: Client, query: CallbackQuery):  # noqa: ARG001
        parsed = _parse_owner_group_list_nav_payload(query.data, CB["OWN_GRP_LIST_PREFIX"])
        if parsed is None:
            await query.answer(t(_LANG, "common.errors.invalid_callback"), show_alert=True)
            return
        await query.answer()
        kind, page = parsed
        await _render_owner_group_list_page(query, kind, page)

    @bot.on_callback_query(filters.regex(r"^own:grp:d:[a-z_]+:-?\d+:\d+$") & _pm_own)
    @owner_or_above
    async def own_group_detail(client: Client, query: CallbackQuery):  # noqa: ARG001
        parsed = _parse_owner_group_payload(query.data, CB["OWN_GRP_DETAIL_PREFIX"])
        if parsed is None:
            await query.answer(t(_LANG, "common.errors.invalid_callback"), show_alert=True)
            return
        await query.answer()
        kind, chat_id, page = parsed
        await _render_owner_group_detail(query, kind, chat_id, page)

    @bot.on_callback_query(filters.regex(r"^own:grp:ci:[a-z_]+:-?\d+:\d+$") & _pm_own)
    @owner_or_above
    async def own_group_credit_inc(client: Client, query: CallbackQuery):
        from app.utils.bot_guards import is_developer
        if not is_developer(query.from_user.id):
            await query.answer(t(_LANG, "credit.developer_only"), show_alert=True)
            return
        parsed = _parse_owner_group_payload(query.data, CB["OWN_GRP_CREDIT_INC_PREFIX"])
        if parsed is None:
            await query.answer(t(_LANG, "common.errors.invalid_callback"), show_alert=True)
            return
        await query.answer()
        kind, chat_id, page = parsed
        await _handle_owner_group_row_credit(client, query, kind, chat_id, page, increase=True)

    @bot.on_callback_query(filters.regex(r"^own:grp:cd:[a-z_]+:-?\d+:\d+$") & _pm_own)
    @owner_or_above
    async def own_group_credit_dec(client: Client, query: CallbackQuery):
        from app.utils.bot_guards import is_developer
        if not is_developer(query.from_user.id):
            await query.answer(t(_LANG, "credit.developer_only"), show_alert=True)
            return
        parsed = _parse_owner_group_payload(query.data, CB["OWN_GRP_CREDIT_DEC_PREFIX"])
        if parsed is None:
            await query.answer(t(_LANG, "common.errors.invalid_callback"), show_alert=True)
            return
        await query.answer()
        kind, chat_id, page = parsed
        await _handle_owner_group_row_credit(client, query, kind, chat_id, page, increase=False)

    @bot.on_callback_query(filters.regex(r"^own:grp:lv:[a-z_]+:-?\d+:\d+$") & _pm_own)
    @owner_or_above
    async def own_group_leave_confirm(client: Client, query: CallbackQuery):  # noqa: ARG001
        parsed = _parse_owner_group_payload(query.data, CB["OWN_GRP_LEAVE_CONFIRM_PREFIX"])
        if parsed is None:
            await query.answer(t(_LANG, "common.errors.invalid_callback"), show_alert=True)
            return
        kind, chat_id, page = parsed
        owner_id = query.from_user.id
        chat_type = await _owner_managed_chat_type(owner_id, chat_id)
        if chat_type is None:
            await query.answer(t(_LANG, "owner_mgmt.not_in_scope"), show_alert=True)
            return
        await query.answer()
        row = await admin_report_repo.get_install_row(chat_id)
        title = row.title if row is not None else None
        issued_at = int(time.time())
        payload = f"{kind}:{chat_id}:{page}:{owner_id}:{issued_at}"
        await query.message.edit_text(
            t(_LANG, "reports.leave_confirm_prompt", title=title or str(chat_id), chat_id=chat_id),
            reply_markup=_owner_confirm_kb(
                f"{CB['OWN_GRP_LEAVE_EXEC_PREFIX']}{payload}",
                f"{CB['OWN_GRP_LEAVE_CANCEL_PREFIX']}{payload}",
                CB["WZ_HOME"],
            ),
        )

    @bot.on_callback_query(filters.regex(r"^own:grp:lv:do:[a-z_]+:-?\d+:\d+:\d+:\d+$") & _pm_own)
    @owner_or_above
    async def own_group_leave_exec(client: Client, query: CallbackQuery):
        parsed = _parse_owner_group_leave_payload(query.data, CB["OWN_GRP_LEAVE_EXEC_PREFIX"])
        if parsed is None:
            await query.answer(t(_LANG, "common.errors.invalid_callback"), show_alert=True)
            return
        kind, chat_id, page, actor_id, issued_at = parsed
        if actor_id != query.from_user.id:
            await query.answer(t(_LANG, "common.errors.no_access"), show_alert=True)
            return
        if _is_stale_owner_confirm_token(issued_at):
            await query.answer(t(_LANG, "global_ban.stale_confirm"), show_alert=True)
            await _render_owner_group_list_page(query, kind, page)
            return
        owner_id = query.from_user.id
        chat_type = await _owner_managed_chat_type(owner_id, chat_id)
        if chat_type is None:
            await query.answer(t(_LANG, "owner_mgmt.not_in_scope"), show_alert=True)
            return
        await query.answer()
        row = await admin_report_repo.get_install_row(chat_id)
        title = row.title if row is not None else None
        try:
            await client.leave_chat(chat_id)
        except Exception:
            logger.debug("Owner could not leave chat %s", chat_id)
        if chat_type == "group":
            await group_repo.deactivate_group(chat_id)
        else:
            await channel_repo.deactivate_channel(chat_id)
        await log_repo.log_install(
            chat_id=chat_id,
            chat_title=title,
            chat_type=chat_type,
            triggered_by=owner_id,
            sudo_id=None,
            action="leave",
        )
        try:
            await NotificationService.notify_uninstall(client, chat_id, chat_type, title)
        except Exception:
            logger.debug("Owner leave notify failed for %s", chat_id)
        await AdminDashboardService.invalidate_cache("lists")
        await AdminDashboardService.invalidate_cache("credit")
        await query.message.edit_text(
            t(_LANG, "reports.leave_done", title=title or str(chat_id), chat_id=chat_id),
            reply_markup=build_done_kb(_LANG, TOKEN_OWNER_ROOT),
        )

    @bot.on_callback_query(filters.regex(r"^own:grp:lv:no:[a-z_]+:-?\d+:\d+:\d+:\d+$") & _pm_own)
    @owner_or_above
    async def own_group_leave_cancel(client: Client, query: CallbackQuery):  # noqa: ARG001
        parsed = _parse_owner_group_leave_payload(query.data, CB["OWN_GRP_LEAVE_CANCEL_PREFIX"])
        if parsed is None:
            await query.answer(t(_LANG, "common.errors.invalid_callback"), show_alert=True)
            return
        kind, chat_id, page, actor_id, issued_at = parsed
        if actor_id != query.from_user.id:
            await query.answer(t(_LANG, "common.errors.no_access"), show_alert=True)
            return
        await query.answer(t(_LANG, "common.cancelled"), show_alert=False)
        await _render_owner_group_list_page(query, kind, page)

    # ── Broadcast / Forward ───────────────────────────────────────────────
    async def _handle_broadcast(client: Client, query: CallbackQuery, target: str, mode: str):
        await query.answer()

        chat_id = query.message.chat.id
        resp = await _ask(
            client,
            chat_id,
            "ask.broadcast_msg",
            user_id=query.from_user.id,
            return_to=TOKEN_OWNER_ROOT,
            delete_response=False,
        )
        if await notify_ask_abort(client, chat_id, resp, return_to=TOKEN_OWNER_ROOT, lang=_LANG):
            return
        target_scope = {"group": "groups", "channel": "channels", "private": "users"}[target]
        total = await BroadcastServiceV2._count_recipients(target_scope)
        code = _OWNER_BC_MODE_CODES[(target, mode)]
        issued_at = int(time.time())
        payload = f"{resp.message.chat.id}:{resp.message.id}:{code}:{query.from_user.id}:{issued_at}"
        await deliver_ask_outcome(
            client,
            chat_id,
            query.from_user.id,
            t(
                _LANG,
                "broadcast.owner_confirm_preview",
                scope=label(_LANG, "broadcast_scope", target_scope),
                total=total,
            ),
            _owner_confirm_kb(
                f"{CB['OWN_BC_CONFIRM_EXEC_PREFIX']}{payload}",
                f"{CB['OWN_BC_CONFIRM_CANCEL_PREFIX']}{payload}",
                CB["WZ_HOME"],
            ),
        )

    def _parse_owner_broadcast_confirm_payload(
        data: str, prefix: str
    ) -> tuple[int, int, str, int, int] | None:
        """Parse ``{chat_id}:{message_id}:{code}:{actor_id}:{issued_at}``."""
        if not data.startswith(prefix):
            return None
        parts = data[len(prefix):].split(":")
        if len(parts) != 5:
            return None
        chat_id_raw, message_id_raw, code, actor_raw, issued_raw = parts
        if code not in _OWNER_BC_MODE_DECODE:
            return None
        chat_id = parse_user_id(chat_id_raw)
        if chat_id is None:
            return None
        try:
            message_id = int(message_id_raw)
            actor_id = int(actor_raw)
            issued_at = int(issued_raw)
        except ValueError:
            return None
        return chat_id, message_id, code, actor_id, issued_at

    @bot.on_callback_query(filters.regex(r"^own:bc:x:-?\d+:\d+:[a-z]{2}:\d+:\d+$") & _pm_own)
    @owner_or_above
    async def own_bc_confirm_exec(client: Client, query: CallbackQuery):
        parsed = _parse_owner_broadcast_confirm_payload(query.data, CB["OWN_BC_CONFIRM_EXEC_PREFIX"])
        if parsed is None:
            await query.answer(t(_LANG, "common.errors.invalid_callback"), show_alert=True)
            return
        src_chat_id, message_id, code, actor_id, issued_at = parsed
        if actor_id != query.from_user.id:
            await query.answer(t(_LANG, "common.errors.no_access"), show_alert=True)
            return
        if _is_stale_owner_confirm_token(issued_at):
            await query.answer(t(_LANG, "global_ban.stale_confirm"), show_alert=True)
            await _show_owner_root(client, query)
            return
        await query.answer()
        claim_unavailable = False
        try:
            redis = await get_redis()
            claimed = await redis.set(
                owner_broadcast_confirm_claim_key(
                    actor_id,
                    src_chat_id,
                    message_id,
                    issued_at,
                ),
                "1",
                nx=True,
                ex=TTL_OWNER_BROADCAST_CONFIRM,
            )
        except Exception:
            claimed = False
            claim_unavailable = True
            logger.warning(
                "owner broadcast confirmation claim unavailable actor_id=%s",
                actor_id,
                exc_info=True,
            )
        if claim_unavailable:
            await query.message.edit_text(
                t(_LANG, "common.errors.try_later"),
                reply_markup=build_done_kb(_LANG, TOKEN_OWNER_ROOT),
            )
            return
        if not claimed:
            # A repeated/stale tap must not overwrite the success panel or
            # enqueue another broadcast. The first claimant owns the redraw.
            return
        target, mode = _OWNER_BC_MODE_DECODE[code]
        try:
            source_message = await client.get_messages(src_chat_id, message_id)
        except Exception:
            source_message = None
        if source_message is None or getattr(source_message, "empty", False):
            await query.message.edit_text(
                t(_LANG, "broadcast.owner_source_expired"),
                reply_markup=build_done_kb(_LANG, TOKEN_OWNER_ROOT),
            )
            return
        target_scope = {"group": "groups", "channel": "channels", "private": "users"}[target]
        broadcast_mode = "forward" if mode == "forward" else "send"
        payload = BroadcastServiceV2.extract_payload(source_message)
        source_chat_id = None
        source_message_id = None
        if broadcast_mode == "forward":
            source_chat_id = src_chat_id
            source_message_id = message_id
        try:
            bc = await BroadcastServiceV2.create_broadcast(
                admin_id=query.from_user.id,
                mode=broadcast_mode,
                target_scope=target_scope,
                payload=payload,
                source_chat_id=source_chat_id,
                source_message_id=source_message_id,
            )
        except Exception:
            logger.exception(
                "owner broadcast create failed actor_id=%s scope=%s",
                query.from_user.id,
                target_scope,
            )
            await query.message.edit_text(
                t(_LANG, "common.errors.try_later"),
                reply_markup=build_done_kb(_LANG, TOKEN_OWNER_ROOT),
            )
            return
        total = await BroadcastServiceV2._count_recipients(target_scope)
        create_logged_task(
            BroadcastServiceV2.execute(client, bc.id),
            name=f"owner_broadcast_{bc.id}",
        )
        await query.message.edit_text(
            t(_LANG, "broadcast.owner_queued", id=bc.id, total=total),
            reply_markup=build_done_kb(_LANG, TOKEN_OWNER_ROOT),
        )

    @bot.on_callback_query(filters.regex(r"^own:bc:xn:-?\d+:\d+:[a-z]{2}:\d+:\d+$") & _pm_own)
    @owner_or_above
    async def own_bc_confirm_cancel(client: Client, query: CallbackQuery):  # noqa: ARG001
        parsed = _parse_owner_broadcast_confirm_payload(query.data, CB["OWN_BC_CONFIRM_CANCEL_PREFIX"])
        if parsed is None:
            await query.answer(t(_LANG, "common.errors.invalid_callback"), show_alert=True)
            return
        _src_chat_id, _message_id, _code, actor_id, _issued_at = parsed
        if actor_id != query.from_user.id:
            await query.answer(t(_LANG, "common.errors.no_access"), show_alert=True)
            return
        await query.answer(t(_LANG, "common.cancelled"), show_alert=False)
        await query.message.edit_text(
            t(_LANG, "common.cancelled"),
            reply_markup=build_done_kb(_LANG, TOKEN_OWNER_ROOT),
        )

    @bot.on_callback_query(filters.regex(f"^{CB['OWN_BROADCAST_GROUP']}$") & _pm_own)
    @owner_or_above
    async def own_bc_group(client: Client, query: CallbackQuery):
        await _handle_broadcast(client, query, "group", "copy")

    @bot.on_callback_query(filters.regex(f"^{CB['OWN_FORWARD_GROUP']}$") & _pm_own)
    @owner_or_above
    async def own_fw_group(client: Client, query: CallbackQuery):
        await _handle_broadcast(client, query, "group", "forward")

    @bot.on_callback_query(filters.regex(f"^{CB['OWN_BROADCAST_PRIVATE']}$") & _pm_own)
    @owner_or_above
    async def own_bc_private(client: Client, query: CallbackQuery):
        await _handle_broadcast(client, query, "private", "copy")

    @bot.on_callback_query(filters.regex(f"^{CB['OWN_FORWARD_PRIVATE']}$") & _pm_own)
    @owner_or_above
    async def own_fw_private(client: Client, query: CallbackQuery):
        await _handle_broadcast(client, query, "private", "forward")

    @bot.on_callback_query(filters.regex(f"^{CB['OWN_BROADCAST_CHANNEL']}$") & _pm_own)
    @owner_or_above
    async def own_bc_channel(client: Client, query: CallbackQuery):
        await _handle_broadcast(client, query, "channel", "copy")

    @bot.on_callback_query(filters.regex(f"^{CB['OWN_FORWARD_CHANNEL']}$") & _pm_own)
    @owner_or_above
    async def own_fw_channel(client: Client, query: CallbackQuery):
        await _handle_broadcast(client, query, "channel", "forward")

    @bot.on_callback_query(filters.regex(f"^{CB['OWN_BC_HISTORY']}$") & _pm_own)
    @owner_or_above
    async def own_bc_history(client: Client, query: CallbackQuery):  # noqa: ARG001
        await query.answer()
        await _render_owner_broadcast_history(query)

    @bot.on_callback_query(filters.regex(r"^own:bc:cancel:\d+$") & _pm_own)
    @owner_or_above
    async def own_bc_cancel(client: Client, query: CallbackQuery):  # noqa: ARG001
        await query.answer()
        bc_id = parse_user_id(query.data[len(CB["OWN_BC_CANCEL_PREFIX"]):])
        if bc_id is None:
            await query.answer(t(_LANG, "common.errors.invalid_callback"), show_alert=True)
            return
        bc = await broadcast_repo.get_by_id(bc_id)
        if bc is None or bc.admin_id != query.from_user.id or not _broadcast_is_cancelable(bc):
            await query.answer(t(_LANG, "common.errors.no_access"), show_alert=True)
            return
        issued_at = int(time.time())
        await query.message.edit_text(
            t(_LANG, "broadcast.owner_cancel_confirm", id=bc_id),
            reply_markup=_owner_confirm_kb(
                f"{CB['OWN_BC_CANCEL_DO_PREFIX']}{bc_id}:{query.from_user.id}:{issued_at}",
                f"{CB['OWN_BC_CANCEL_NO_PREFIX']}{bc_id}:{query.from_user.id}:{issued_at}",
                CB["OWN_BC_HISTORY"],
            ),
        )

    @bot.on_callback_query(filters.regex(r"^own:bc:cancel:do:\d+:\d+:\d+$") & _pm_own)
    @owner_or_above
    async def own_bc_cancel_confirm(client: Client, query: CallbackQuery):  # noqa: ARG001
        await query.answer()
        parsed = _parse_bound_payload(query.data, CB["OWN_BC_CANCEL_DO_PREFIX"], 3)
        if parsed is None:
            await query.answer(t(_LANG, "common.errors.invalid_callback"), show_alert=True)
            return
        bc_id, actor_id, issued_at = parsed
        if actor_id != query.from_user.id or _is_stale_owner_confirm_token(issued_at):
            await query.answer(t(_LANG, "common.errors.no_access"), show_alert=True)
            await _render_owner_broadcast_history(query)
            return
        bc = await broadcast_repo.get_by_id(bc_id)
        if bc is None or bc.admin_id != query.from_user.id or not _broadcast_is_cancelable(bc):
            await query.answer(t(_LANG, "common.errors.no_access"), show_alert=True)
            await _render_owner_broadcast_history(query)
            return
        await BroadcastServiceV2.cancel(bc_id)
        await query.answer(t(_LANG, "broadcast.owner_cancelled"), show_alert=True)
        await _render_owner_broadcast_history(query)

    @bot.on_callback_query(filters.regex(r"^own:bc:cancel:no:\d+:\d+:\d+$") & _pm_own)
    @owner_or_above
    async def own_bc_cancel_abort(client: Client, query: CallbackQuery):  # noqa: ARG001
        await query.answer(t(_LANG, "common.cancelled"), show_alert=False)
        parsed = _parse_bound_payload(query.data, CB["OWN_BC_CANCEL_NO_PREFIX"], 3)
        if parsed is None:
            await query.answer(t(_LANG, "common.errors.invalid_callback"), show_alert=True)
            return
        _bc_id, actor_id, issued_at = parsed
        if actor_id != query.from_user.id or _is_stale_owner_confirm_token(issued_at):
            await query.answer(t(_LANG, "common.errors.no_access"), show_alert=True)
            await _show_owner_root(client, query)
            return
        await _render_owner_broadcast_history(query)
    # ── Toggles ───────────────────────────────────────────────────────────


    @bot.on_callback_query(filters.regex(f"^{CB['OWN_FORCE_JOIN_TOGGLE']}$") & _pm_own)
    @owner_or_above
    async def own_force_join_home_shortcut(client: Client, query: CallbackQuery):  # noqa: ARG001
        await query.answer()
        if not await _can_manage_force_join(query.from_user.id):
            await _deny_force_join_access(query)
            return
        await _render_owner_force_join_home(query)

    @bot.on_callback_query(filters.regex(f"^{CB['OWN_FORCE_JOIN_ENABLE_TOGGLE']}$") & _pm_own)
    @owner_or_above
    async def own_force_join_toggle(client: Client, query: CallbackQuery):
        if not await _can_manage_force_join(query.from_user.id):
            await _deny_force_join_access(query)
            return
        await _toggle_owner_setting(client, query, "force_join_enabled", "status.force_join_label")

    @bot.on_callback_query(filters.regex(f"^{CB['OWN_AUTO_LEAVE_TOGGLE']}$") & _pm_own)
    @owner_or_above
    async def own_auto_leave_toggle(client: Client, query: CallbackQuery):
        if not await _require_developer_for_global_owner_route(query):
            return
        await _toggle_owner_setting(client, query, "auto_leave_enabled", "status.auto_leave_label")

    @bot.on_callback_query(filters.regex(f"^{CB['OWN_TRIAL_TOGGLE']}$") & _pm_own)
    @owner_or_above
    async def own_trial_toggle(client: Client, query: CallbackQuery):
        if not await _require_developer_for_global_owner_route(query):
            return
        await _toggle_owner_setting(client, query, "trial_enabled", "status.trial_label")

    @bot.on_callback_query(filters.regex(f"^{CB['OWN_CHANNEL_SECURITY_TOGGLE']}$") & _pm_own)
    @owner_or_above
    async def own_channel_security_toggle(client: Client, query: CallbackQuery):
        await query.answer()
        await query.message.edit_text(
            t(_LANG, "call_security.deprecated_global_toggle"),
            reply_markup=build_done_kb(_LANG, TOKEN_OWNER_ROOT),
        )

    @bot.on_callback_query(filters.regex(f"^{CB['OWN_SET_MEDIA_POLICY']}$") & _pm_own)
    @owner_or_above
    async def own_set_media_policy(client: Client, query: CallbackQuery):
        await query.answer()
        if not await _require_developer_for_global_owner_route(query):
            return
        chat_id = query.message.chat.id
        resp, value = await _ask_value(
            client,
            chat_id,
            "ask.text_input",
            lambda message: (getattr(message, "text", "") or "").strip() or None,
            t(_LANG, "common.errors.invalid_number"),
            user_id=query.from_user.id,
            return_to=TOKEN_OWNER_ROOT,
        )
        if resp.message is None:
            return

        await settings_repo.set_bot_setting("media_policy", value, updated_by=query.from_user.id)
        await _send_done(client, chat_id, t(_LANG, "status.setting_updated"), TOKEN_OWNER_ROOT)

    @bot.on_callback_query(filters.regex(f"^{CB['OWN_MEDIA_AUDIO_TOGGLE']}$") & _pm_own)
    @owner_or_above
    async def own_media_audio_toggle(client: Client, query: CallbackQuery):  # noqa: ARG001
        await query.answer()
        await _toggle_owner_media_feature(query, "audio")

    @bot.on_callback_query(filters.regex(f"^{CB['OWN_MEDIA_VIDEO_TOGGLE']}$") & _pm_own)
    @owner_or_above
    async def own_media_video_toggle(client: Client, query: CallbackQuery):  # noqa: ARG001
        await query.answer()
        await _toggle_owner_media_feature(query, "video")

    @bot.on_callback_query(filters.regex(f"^{CB['OWN_MEDIA_FILE_TOGGLE']}$") & _pm_own)
    @owner_or_above
    async def own_media_file_toggle(client: Client, query: CallbackQuery):  # noqa: ARG001
        await query.answer()
        await _toggle_owner_media_feature(query, "file")

    @bot.on_callback_query(filters.regex(f"^{CB['OWN_MEDIA_DOWNLOAD_TOGGLE']}$") & _pm_own)
    @owner_or_above
    async def own_media_download_toggle(client: Client, query: CallbackQuery):  # noqa: ARG001
        await query.answer()
        await _toggle_owner_media_feature(query, "download")

    @bot.on_callback_query(filters.regex(f"^{CB['OWN_MEDIA_BUTTONS_TOGGLE']}$") & _pm_own)
    @owner_or_above
    async def own_media_buttons_toggle(client: Client, query: CallbackQuery):  # noqa: ARG001
        await query.answer()
        await _toggle_owner_media_feature(query, "buttons")

    async def _handle_owner_credit_adjust(client: Client, query: CallbackQuery, *, increase: bool) -> None:
        from app.utils.bot_guards import is_developer
        if not is_developer(query.from_user.id):
            await query.answer(t(_LANG, "common.errors.no_access"), show_alert=True)
            return
        await query.answer()
        chat_id = query.message.chat.id
        chat_resp, target_chat_id = await _ask_value(
            client,
            chat_id,
            "ask.chat_id",
            lambda message: parse_user_id(
                (getattr(message, "text", "") or "").strip()
            ),
            t(_LANG, "common.errors.invalid_number"),
            user_id=query.from_user.id,
            return_to=TOKEN_OWNER_ROOT,
        )
        if await notify_ask_abort(client, chat_id, chat_resp, return_to=TOKEN_OWNER_ROOT, lang=_LANG):
            return
        chat_type = await _owner_managed_chat_type(query.from_user.id, target_chat_id)
        if chat_type is None:
            await _send_done(client, chat_id, t(_LANG, "owner_mgmt.not_in_scope"), TOKEN_OWNER_ROOT)
            return
        amount_resp, days = await _ask_value(
            client,
            chat_id,
            "ask.amount_days",
            lambda message: parse_bounded_int(
                getattr(message, "text", None),
                min_value=1,
                max_value=MAX_CREDIT_DAYS,
            ),
            t(_LANG, "common.errors.invalid_positive_number"),
            user_id=query.from_user.id,
            return_to=TOKEN_OWNER_ROOT,
        )
        if await notify_ask_abort(client, chat_id, amount_resp, return_to=TOKEN_OWNER_ROOT, lang=_LANG):
            return
        try:
            if increase:
                await CreditService.charge_managed_chat(
                    target_chat_id,
                    chat_type,
                    days,
                    operated_by=query.from_user.id,
                    note="owner_panel",
                )
                key = "reports.credit_updated"
            else:
                await CreditService.adjust_managed_credit(
                    target_chat_id,
                    chat_type,
                    mode="decrease",
                    amount=days,
                    operated_by=query.from_user.id,
                    note="owner_panel",
                )
                key = "reports.credit_deducted_row"
        except ValueError as exc:
            if "not_managed" in str(exc):
                await _send_done(client, chat_id, t(_LANG, "owner_mgmt.not_in_scope"), TOKEN_OWNER_ROOT)
                return
            logger.exception("Owner credit adjustment rejected chat_id=%s", target_chat_id)
            await _send_done(client, chat_id, t(_LANG, "common.errors.try_later"), TOKEN_OWNER_ROOT)
            return
        except Exception:
            logger.exception("Owner credit adjustment failed chat_id=%s", target_chat_id)
            await _send_done(client, chat_id, t(_LANG, "common.errors.try_later"), TOKEN_OWNER_ROOT)
            return
        await _send_done(
            client,
            chat_id,
            t(_LANG, key, title=str(target_chat_id), amount=days),
            TOKEN_OWNER_ROOT,
        )

    @bot.on_callback_query(filters.regex(f"^{CB['OWN_CREDIT_ADD']}$") & _pm_own)
    @owner_or_above
    async def own_credit_add(client: Client, query: CallbackQuery):
        from app.utils.bot_guards import is_developer
        if not is_developer(query.from_user.id):
            await query.answer(t(_LANG, "credit.developer_only"), show_alert=True)
            return
        await _handle_owner_credit_adjust(client, query, increase=True)

    @bot.on_callback_query(filters.regex(f"^{CB['OWN_CREDIT_DEDUCT']}$") & _pm_own)
    @owner_or_above
    async def own_credit_deduct(client: Client, query: CallbackQuery):
        from app.utils.bot_guards import is_developer
        if not is_developer(query.from_user.id):
            await query.answer(t(_LANG, "credit.developer_only"), show_alert=True)
            return
        await _handle_owner_credit_adjust(client, query, increase=False)

    # ── List no-credit ────────────────────────────────────────────────────
    @bot.on_callback_query(filters.regex(f"^{CB['OWN_LIST_NO_CREDIT']}$") & _pm_own)
    @owner_or_above
    async def own_list_no_credit(client: Client, query: CallbackQuery):  # noqa: ARG001
        await query.answer()
        await _render_owner_group_list_page(query, _OWNER_KIND_NO_CREDIT, 0)

    # ── Filters ───────────────────────────────────────────────────────────
    @bot.on_callback_query(filters.regex(f"^{CB['OWN_FILTERS']}$") & _pm_own)
    @owner_or_above
    async def own_filters(client: Client, query: CallbackQuery):
        await query.answer()
        words = await filter_repo.get_filter_words()
        chat_id = query.message.chat.id
        if words:
            text = t(_LANG, "filter_mgmt.list_title") + "\n" + "\n".join(
                t(_LANG, "list_fmt.word_item", word=w) for w in words
            )
        else:
            text = t(_LANG, "filter_mgmt.list_empty")
        await query.message.edit_text(text, reply_markup=build_done_kb(_LANG, TOKEN_OWNER_ROOT))

        resp, word = await _ask_value(
            client,
            chat_id,
            "ask.word_input",
            lambda message: (
                value
                if (value := (getattr(message, "text", "") or "").strip())
                and value != "-"
                else None
            ),
            t(_LANG, "texts_links.invalid_text"),
            user_id=query.from_user.id,
            return_to=TOKEN_OWNER_ROOT,
        )
        if await notify_ask_abort(client, chat_id, resp, return_to=TOKEN_OWNER_ROOT, lang=_LANG):
            return
        if word.startswith("-"):
            await filter_repo.remove_filter_word(word[1:].strip())
            await invalidate_filterwords()
            await _send_done(client, chat_id, t(_LANG, "filter_mgmt.removed", word=word[1:].strip()), TOKEN_OWNER_ROOT)
        else:
            await filter_repo.add_filter_word(word, added_by=query.from_user.id)
            await invalidate_filterwords()
            await _send_done(client, chat_id, t(_LANG, "filter_mgmt.added", word=word), TOKEN_OWNER_ROOT)

    # ── Owner Ban All ─────────────────────────────────────────────────────
    @bot.on_callback_query(filters.regex(f"^{CB['OWN_BANALL_HOME']}$") & _pm_own)
    @owner_or_above
    async def own_banall_home(client: Client, query: CallbackQuery):  # noqa: ARG001
        await query.answer()
        await query.message.edit_text(
            t(_LANG, "global_ban.home_title"),
            reply_markup=_owner_banall_home_kb(),
        )

    @bot.on_callback_query(filters.regex(f"^{CB['OWN_BANALL_ADD']}$") & _pm_own)
    @owner_or_above
    async def own_banall_add(client: Client, query: CallbackQuery):
        await query.answer()
        chat_id = query.message.chat.id
        resp, user_id = await _ask_value(
            client,
            chat_id,
            "global_ban.prompt_user_id",
            lambda message: (
                parsed
                if (
                    parsed := parse_user_id(
                        (getattr(message, "text", "") or "").strip()
                    )
                )
                is not None
                and parsed > 0
                else None
            ),
            t(_LANG, "global_ban.invalid_user_id"),
            user_id=query.from_user.id,
            return_to=TOKEN_OWNER_ROOT,
        )
        if await notify_ask_abort(client, chat_id, resp, return_to=TOKEN_OWNER_ROOT, lang=_LANG):
            return
        try:
            global_ban_repo.validate_global_ban_user_id(user_id)
        except GlobalBanValidationError as exc:
            code = exc.args[0] if exc.args else str(exc)
            key = "global_ban.cannot_ban_developer" if code == "cannot_ban_developer" else "global_ban.invalid_user_id"
            await _send_done(client, chat_id, t(_LANG, key), TOKEN_OWNER_ROOT)
            return
        target_count = await _owner_installed_chat_count()
        issued_at = int(time.time())
        await deliver_ask_outcome(
            client,
            chat_id,
            query.from_user.id,
            t(_LANG, "global_ban.owner_add_preview", user_id=user_id, count=target_count),
            _owner_confirm_kb(
                f"{CB['OWN_BANALL_ADD_DO_PREFIX']}{user_id}:{target_count}:{query.from_user.id}:{issued_at}",
                f"{CB['OWN_BANALL_ADD_NO_PREFIX']}{user_id}:{target_count}:{query.from_user.id}:{issued_at}",
                CB["OWN_BANALL_HOME"],
            ),
        )

    @bot.on_callback_query(filters.regex(r"^own:banall:add:do:\d+:\d+:\d+:\d+$") & _pm_own)
    @owner_or_above
    async def own_banall_add_confirm(client: Client, query: CallbackQuery):
        await query.answer()
        parsed = _parse_bound_payload(query.data, CB["OWN_BANALL_ADD_DO_PREFIX"], 4)
        if parsed is None:
            await query.answer(t(_LANG, "common.errors.invalid_callback"), show_alert=True)
            return
        user_id, _target_count, actor_id, issued_at = parsed
        if actor_id != query.from_user.id:
            await query.answer(t(_LANG, "common.errors.no_access"), show_alert=True)
            return
        if _is_stale_owner_confirm_token(issued_at):
            await query.answer(t(_LANG, "global_ban.stale_confirm"), show_alert=True)
            await _show_owner_root(client, query)
            return
        try:
            _ban, created_new = await global_ban_repo.add_global_ban(user_id, created_by=query.from_user.id)
        except GlobalBanValidationError as exc:
            code = exc.args[0] if exc.args else str(exc)
            key = "global_ban.cannot_ban_developer" if code == "cannot_ban_developer" else "global_ban.invalid_user_id"
            await query.message.edit_text(t(_LANG, key), reply_markup=build_done_kb(_LANG, TOKEN_OWNER_ROOT))
            return
        if not created_new:
            await query.message.edit_text(
                t(_LANG, "global_ban.already_banned", user_id=user_id),
                reply_markup=build_done_kb(_LANG, TOKEN_OWNER_ROOT),
            )
            return
        await query.message.edit_text(
            t(_LANG, "global_ban.added_removal_started", user_id=user_id),
            reply_markup=build_done_kb(_LANG, TOKEN_OWNER_ROOT),
        )
        create_logged_task(
            _run_owner_banall_removal_and_notify(
                client,
                query.message.chat.id,
                user_id,
                query.from_user.id,
            ),
            name=f"owner_banall_removal_{user_id}",
        )

    @bot.on_callback_query(filters.regex(r"^own:banall:add:no:\d+:\d+:\d+:\d+$") & _pm_own)
    @owner_or_above
    async def own_banall_add_abort(client: Client, query: CallbackQuery):  # noqa: ARG001
        await query.answer(t(_LANG, "common.cancelled"), show_alert=False)
        parsed = _parse_bound_payload(query.data, CB["OWN_BANALL_ADD_NO_PREFIX"], 4)
        if parsed is None:
            await query.answer(t(_LANG, "common.errors.invalid_callback"), show_alert=True)
            return
        _user_id, _count, actor_id, issued_at = parsed
        if actor_id != query.from_user.id or _is_stale_owner_confirm_token(issued_at):
            await query.answer(t(_LANG, "common.errors.no_access"), show_alert=True)
            return
        await query.message.edit_text(
            t(_LANG, "common.cancelled"),
            reply_markup=build_done_kb(_LANG, TOKEN_OWNER_ROOT),
        )

    @bot.on_callback_query(filters.regex(r"^own:banall:list:\d+$") & _pm_own)
    @owner_or_above
    async def own_banall_list(client: Client, query: CallbackQuery):  # noqa: ARG001
        await query.answer()
        page = int(query.data.split(":")[-1])
        await _show_owner_banall_list(query, page)

    @bot.on_callback_query(filters.regex(f"^{CB['OWN_BANALL_REMOVE']}$") & _pm_own)
    @owner_or_above
    async def own_banall_remove(client: Client, query: CallbackQuery):
        await query.answer()
        chat_id = query.message.chat.id
        resp, user_id = await _ask_value(
            client,
            chat_id,
            "global_ban.prompt_remove_user_id",
            lambda message: (
                parsed
                if (
                    parsed := parse_user_id(
                        (getattr(message, "text", "") or "").strip()
                    )
                )
                is not None
                and parsed > 0
                else None
            ),
            t(_LANG, "global_ban.invalid_user_id"),
            user_id=query.from_user.id,
            return_to=TOKEN_OWNER_ROOT,
        )
        if await notify_ask_abort(client, chat_id, resp, return_to=TOKEN_OWNER_ROOT, lang=_LANG):
            return
        issued_at = int(time.time())
        await deliver_ask_outcome(
            client,
            chat_id,
            query.from_user.id,
            t(_LANG, "global_ban.owner_remove_confirm", user_id=user_id),
            _owner_confirm_kb(
                f"{CB['OWN_BANALL_RM_DO_PREFIX']}{user_id}:0:{query.from_user.id}:{issued_at}",
                f"{CB['OWN_BANALL_RM_NO_PREFIX']}{user_id}:0:{query.from_user.id}:{issued_at}",
                CB["OWN_BANALL_HOME"],
            ),
        )

    @bot.on_callback_query(filters.regex(r"^own:banall:rm:\d+:\d+$") & _pm_own)
    @owner_or_above
    async def own_banall_row_remove(client: Client, query: CallbackQuery):  # noqa: ARG001
        await query.answer()
        raw = query.data[len(CB["OWN_BANALL_RM_PREFIX"]):].split(":")
        if len(raw) != 2:
            await query.answer(t(_LANG, "common.errors.invalid_callback"), show_alert=True)
            return
        user_id = parse_user_id(raw[0])
        page = parse_user_id(raw[1])
        if user_id is None or page is None:
            await query.answer(t(_LANG, "common.errors.invalid_callback"), show_alert=True)
            return
        issued_at = int(time.time())
        await query.message.edit_text(
            t(_LANG, "global_ban.owner_remove_confirm", user_id=user_id),
            reply_markup=_owner_confirm_kb(
                f"{CB['OWN_BANALL_RM_DO_PREFIX']}{user_id}:{page}:{query.from_user.id}:{issued_at}",
                f"{CB['OWN_BANALL_RM_NO_PREFIX']}{user_id}:{page}:{query.from_user.id}:{issued_at}",
                f"{CB['OWN_BANALL_LIST_PREFIX']}{page}",
            ),
        )

    @bot.on_callback_query(filters.regex(r"^own:banall:rm:do:\d+:\d+:\d+:\d+$") & _pm_own)
    @owner_or_above
    async def own_banall_remove_confirm(client: Client, query: CallbackQuery):  # noqa: ARG001
        await query.answer()
        parsed = _parse_bound_payload(query.data, CB["OWN_BANALL_RM_DO_PREFIX"], 4)
        if parsed is None:
            await query.answer(t(_LANG, "common.errors.invalid_callback"), show_alert=True)
            return
        user_id, page, actor_id, issued_at = parsed
        if actor_id != query.from_user.id or _is_stale_owner_confirm_token(issued_at):
            await query.answer(t(_LANG, "common.errors.no_access"), show_alert=True)
            await _show_owner_root(client, query)
            return
        removed = await global_ban_repo.remove_global_ban(user_id)
        await query.answer(
            t(_LANG, "global_ban.removed" if removed else "global_ban.not_found", user_id=user_id),
            show_alert=True,
        )
        await _show_owner_banall_list(query, page)

    @bot.on_callback_query(filters.regex(r"^own:banall:rm:no:\d+:\d+:\d+:\d+$") & _pm_own)
    @owner_or_above
    async def own_banall_remove_abort(client: Client, query: CallbackQuery):  # noqa: ARG001
        await query.answer(t(_LANG, "common.cancelled"), show_alert=False)
        parsed = _parse_bound_payload(query.data, CB["OWN_BANALL_RM_NO_PREFIX"], 4)
        if parsed is None:
            await query.answer(t(_LANG, "common.errors.invalid_callback"), show_alert=True)
            return
        _user_id, page, actor_id, issued_at = parsed
        if actor_id != query.from_user.id or _is_stale_owner_confirm_token(issued_at):
            await query.answer(t(_LANG, "common.errors.no_access"), show_alert=True)
            await _show_owner_root(client, query)
            return
        await _show_owner_banall_list(query, page)

    # ── Force join manage ─────────────────────────────────────────────────
    @bot.on_callback_query(filters.regex(f"^{CB['OWN_FORCE_JOIN_MANAGE']}$") & _pm_own)
    async def own_force_join_manage(client: Client, query: CallbackQuery):
        await query.answer()
        if not await _can_manage_force_join(query.from_user.id):
            await _deny_force_join_access(query)
            return
        await _render_owner_force_join_home(query)

    @bot.on_callback_query(filters.regex(f"^{CB['OWN_FORCE_JOIN_ADD']}$") & _pm_own)
    async def own_force_join_add(client: Client, query: CallbackQuery):
        await query.answer()
        if not await _can_manage_force_join(query.from_user.id):
            await _deny_force_join_access(query)
            return

        chat_id = query.message.chat.id
        def _parse_force_join_identifier(message) -> str | None:
            raw = (getattr(message, "text", "") or "").strip()
            if raw.startswith("@") and len(raw) > 1:
                return raw
            parsed = parse_user_id(raw)
            return str(parsed) if parsed is not None else None

        resp, identifier = await _ask_value(
            client,
            chat_id,
            "force_join_mgmt.add_prompt",
            _parse_force_join_identifier,
            t(_LANG, "force_join_mgmt.invalid_identifier"),
            user_id=query.from_user.id,
            return_to=TOKEN_OWNER_ROOT,
        )
        if resp.message is None:
            return

        result = await ForcedMembershipService.add_target(client, identifier, query.from_user.id)
        if "error" in result:
            await _render_owner_force_join_home(
                query,
                notice=t(_LANG, "force_join_mgmt.add_inaccessible"),
            )
            return

        await AdminDashboardService.invalidate_cache("general")
        notice = t(_LANG, "force_join_mgmt.added", channel=str(result["target"].channel_id))
        if result.get("verify_status") == "bot_not_admin":
            notice += "\n" + t(_LANG, "force_join_mgmt.add_bot_not_admin")
        await _render_owner_force_join_list(query, 0, notice=notice)

    @bot.on_callback_query(filters.regex(f"^{CB['OWN_FORCE_JOIN_LIST']}$") & _pm_own)
    async def own_force_join_list(client: Client, query: CallbackQuery):
        await query.answer()
        if not await _can_manage_force_join(query.from_user.id):
            await _deny_force_join_access(query)
            return
        await _render_owner_force_join_list(query, 0)

    @bot.on_callback_query(filters.regex(r"^own:fj:pg:\d+$") & _pm_own)
    async def own_force_join_list_page(client: Client, query: CallbackQuery):
        await query.answer()
        if not await _can_manage_force_join(query.from_user.id):
            await _deny_force_join_access(query)
            return
        page = int(query.data.split(":")[3])
        await _render_owner_force_join_list(query, page)

    @bot.on_callback_query(filters.regex(r"^own:fj:rm:-?\d+:\d+$") & _pm_own)
    async def own_force_join_remove(client: Client, query: CallbackQuery):
        await query.answer()
        if not await _can_manage_force_join(query.from_user.id):
            await _deny_force_join_access(query)
            return

        parsed = _parse_owner_force_join_remove(query.data)
        if parsed is None:
            await query.answer(t(_LANG, "common.errors.invalid_number"), show_alert=True)
            return

        channel_id, page = parsed
        target = await force_join_repo.get_active_target_by_channel_id(channel_id)
        if target is None:
            await _render_owner_force_join_list(
                query,
                page,
                notice=t(_LANG, "force_join_mgmt.remove_not_found", channel=str(channel_id)),
            )
            return

        issued_at = int(time.time())
        await query.message.edit_text(
            t(_LANG, "force_join_mgmt.remove_confirm_prompt", channel_id=channel_id),
            reply_markup=_owner_confirm_kb(
                f"{CB['OWN_FORCE_JOIN_REMOVE_DO_PREFIX']}{channel_id}:{page}:{query.from_user.id}:{issued_at}",
                f"{CB['OWN_FORCE_JOIN_REMOVE_NO_PREFIX']}{channel_id}:{page}:{query.from_user.id}:{issued_at}",
                CB["OWN_FORCE_JOIN_TOGGLE"],
            ),
        )

    @bot.on_callback_query(filters.regex(r"^own:fj:rm:do:-?\d+:\d+:\d+:\d+$") & _pm_own)
    async def own_force_join_remove_confirm(client: Client, query: CallbackQuery):
        await query.answer()
        if not await _can_manage_force_join(query.from_user.id):
            await _deny_force_join_access(query)
            return
        parsed = _parse_owner_force_join_remove_confirm(query.data, CB["OWN_FORCE_JOIN_REMOVE_DO_PREFIX"])
        if parsed is None:
            await query.answer(t(_LANG, "common.errors.invalid_callback"), show_alert=True)
            return
        channel_id, page, actor_id, issued_at = parsed
        if actor_id != query.from_user.id:
            await query.answer(t(_LANG, "common.errors.no_access"), show_alert=True)
            return
        if _is_stale_owner_confirm_token(issued_at):
            await query.answer(t(_LANG, "global_ban.stale_confirm"), show_alert=True)
            await _render_owner_force_join_list(query, page)
            return
        await ForcedMembershipService.remove_target(channel_id)
        await AdminDashboardService.invalidate_cache("general")
        await _render_owner_force_join_list(
            query,
            page,
            notice=t(_LANG, "force_join_mgmt.removed", channel=str(channel_id)),
        )

    @bot.on_callback_query(filters.regex(r"^own:fj:rm:no:-?\d+:\d+:\d+:\d+$") & _pm_own)
    async def own_force_join_remove_abort(client: Client, query: CallbackQuery):
        await query.answer(t(_LANG, "common.cancelled"), show_alert=False)
        parsed = _parse_owner_force_join_remove_confirm(query.data, CB["OWN_FORCE_JOIN_REMOVE_NO_PREFIX"])
        if parsed is None:
            await query.answer(t(_LANG, "common.errors.invalid_callback"), show_alert=True)
            return
        _channel_id, page, actor_id, issued_at = parsed
        if actor_id != query.from_user.id:
            await query.answer(t(_LANG, "common.errors.no_access"), show_alert=True)
            return
        if _is_stale_owner_confirm_token(issued_at):
            await query.answer(t(_LANG, "global_ban.stale_confirm"), show_alert=True)
            await _render_owner_force_join_home(query)
            return
        await _render_owner_force_join_list(query, page)

    # ── Sudo manage ─────────────────────────────────────────────────────── ───────────────────────────────────────────────────────
    @bot.on_callback_query(filters.regex(f"^{CB['OWN_SUDO_MANAGE']}$") & _pm_own)
    @owner_or_above
    async def own_sudo_manage(client: Client, query: CallbackQuery):
        await query.answer()
        sudos = await owner_scope_service.get_scoped_sudos_for_actor(query.from_user.id)
        chat_id = query.message.chat.id
        if sudos:
            text = t(_LANG, "sudo_mgmt.list_title") + "\n"
            text += t(_LANG, "sudo_permissions.owner_scope_note") + "\n"
            text += "\n".join(
                t(_LANG, "list_fmt.user_item", user_id=s.user_id, username=s.username or "-")
                for s in sudos
            )
        else:
            text = t(_LANG, "sudo_mgmt.list_empty")
        await query.message.edit_text(text, reply_markup=build_done_kb(_LANG, TOKEN_OWNER_ROOT))

        def _parse_sudo_input(message):
            raw = (getattr(message, "text", "") or "").strip()
            removing = raw.startswith("-")
            uid = parse_user_id(raw[1:] if removing else raw)
            return (removing, uid) if uid is not None and uid > 0 else None

        resp, parsed_input = await _ask_value(
            client,
            chat_id,
            "ask.user_id",
            _parse_sudo_input,
            t(_LANG, "common.errors.invalid_number"),
            user_id=query.from_user.id,
            return_to=TOKEN_OWNER_ROOT,
        )
        if await notify_ask_abort(client, chat_id, resp, return_to=TOKEN_OWNER_ROOT, lang=_LANG):
            return
        if resp.message is None:
            return
        removing, uid = parsed_input
        if removing:
            sudo = await user_repo.get_sudo(uid)
            if sudo is None:
                await _send_done(client, chat_id, t(_LANG, "sudo_mgmt.not_found"), TOKEN_OWNER_ROOT)
                return
            if not owner_scope_service.sudo_belongs_to_actor(sudo, query.from_user.id):
                await _send_done(client, chat_id, t(_LANG, "owner_mgmt.sudo_not_in_scope"), TOKEN_OWNER_ROOT)
                return
            await _send_owner_sudo_remove_confirm(client, chat_id, query.from_user.id, uid)
            return
        ok, message_key = await _owner_add_sudo(query.from_user.id, uid)
        if not ok:
            await _send_done(client, chat_id, t(_LANG, message_key), TOKEN_OWNER_ROOT)
            return
        await _send_done(client, chat_id, t(_LANG, message_key, user=str(uid)), TOKEN_OWNER_ROOT)

    # ── Install limits ────────────────────────────────────────────────────
    @bot.on_callback_query(filters.regex(f"^{CB['OWN_INSTALL_LIMITS']}$") & _pm_own)
    @owner_or_above
    async def own_install_limits(client: Client, query: CallbackQuery):
        await query.answer()
        if not await _require_developer_for_global_owner_route(query):
            return
        chat_id = query.message.chat.id
        resp, value = await _ask_value(
            client,
            chat_id,
            "ask.limit_value",
            lambda message: parse_bounded_int(
                getattr(message, "text", None),
                min_value=0,
                max_value=MAX_LIMIT_VALUE,
            ),
            t(_LANG, "common.errors.invalid_non_negative_number"),
            user_id=query.from_user.id,
            return_to=TOKEN_OWNER_ROOT,
        )
        if resp.message is None:
            return

        await settings_repo.set_bot_setting("max_group_members", str(value), updated_by=query.from_user.id)
        await _send_done(client, chat_id, t(_LANG, "status.limit_updated", value=value), TOKEN_OWNER_ROOT)

    # ── Blacklist ─────────────────────────────────────────────────────────
    @bot.on_callback_query(filters.regex(f"^{CB['OWN_BLACKLIST']}$") & _pm_own)
    @owner_or_above
    async def own_blacklist(client: Client, query: CallbackQuery):
        await query.answer()
        if not await _require_developer_for_global_owner_route(query):
            return
        chat_id = query.message.chat.id

        entries = await blacklist_repo.get_blacklist()
        await query.message.edit_text(
            _build_blacklist_text(entries),
            reply_markup=build_done_kb(_LANG, TOKEN_OWNER_ROOT),
        )

        def _parse_blacklist_input(message):
            raw = (getattr(message, "text", "") or "").strip()
            removing = raw.startswith("-")
            entity_id = parse_user_id(raw[1:] if removing else raw)
            return (removing, entity_id) if entity_id is not None else None

        resp, parsed_input = await _ask_value(
            client,
            chat_id,
            "ask.user_id",
            _parse_blacklist_input,
            t(_LANG, "common.errors.invalid_number"),
            user_id=query.from_user.id,
            return_to=TOKEN_OWNER_ROOT,
        )
        if await notify_ask_abort(client, chat_id, resp, return_to=TOKEN_OWNER_ROOT, lang=_LANG):
            return

        removing, eid = parsed_input
        if removing:
            entity_type = "group" if eid < 0 else "user"
            await blacklist_repo.remove_from_blacklist(eid, entity_type)
            await _send_done(client, chat_id, t(_LANG, "blacklist_mgmt.unblocked", entity=str(eid)), TOKEN_OWNER_ROOT)
            return

        entity_type = "group" if eid < 0 else "user"
        await blacklist_repo.add_to_blacklist(eid, entity_type, blocked_by=query.from_user.id)
        await _send_done(client, chat_id, t(_LANG, "blacklist_mgmt.blocked", entity=str(eid)), TOKEN_OWNER_ROOT)

    # ── Install reports ───────────────────────────────────────────────────
    @bot.on_callback_query(filters.regex(f"^{CB['OWN_INSTALL_REPORTS']}$") & _pm_own)
    @owner_or_above
    async def own_install_reports(client: Client, query: CallbackQuery):
        if not await _require_developer_for_global_owner_route(query):
            return
        logs = await log_repo.get_install_logs(limit=50)
        if not logs:
            await query.answer(t(_LANG, "filter_mgmt.list_empty"), show_alert=True)
            return
        lines = [
            t(
                _LANG,
                "list_fmt.install_log_item",
                title=entry.chat_title or str(entry.chat_id),
                action=label(_LANG, "install_action", entry.action),
            )
            for entry in logs[:30]
        ]
        await query.message.edit_text("\n".join(lines), reply_markup=KeyboardFactory.owner_panel(_LANG))

    # ── No-credit reports ─────────────────────────────────────────────────
    @bot.on_callback_query(filters.regex(f"^{CB['OWN_NO_CREDIT_REPORTS']}$") & _pm_own)
    @owner_or_above
    async def own_no_credit_reports(client: Client, query: CallbackQuery):
        rows = await _get_owner_no_credit_rows(query.from_user.id)
        if not rows:
            await query.answer(t(_LANG, "filter_mgmt.list_empty"), show_alert=True)
            return
        await query.message.edit_text(
            "\n".join(_no_credit_list_lines(rows)),
            reply_markup=KeyboardFactory.owner_panel(_LANG),
        )

    # ── Bot credit ────────────────────────────────────────────────────────
    @bot.on_callback_query(filters.regex(f"^{CB['OWN_BOT_CREDIT']}$") & _pm_own)
    @owner_or_above
    async def own_bot_credit(client: Client, query: CallbackQuery):  # noqa: ARG001
        await query.answer(
            t(_LANG, "credit.financial_surface_removed"),
            show_alert=True,
        )

    # ── Increase bot credit ───────────────────────────────────────────────
    @bot.on_callback_query(filters.regex(f"^{CB['OWN_INCREASE_BOT_CREDIT']}$") & _pm_own)
    @owner_or_above
    async def own_increase_bot_credit(client: Client, query: CallbackQuery):  # noqa: ARG001
        await query.answer(
            t(_LANG, "credit.financial_surface_removed"),
            show_alert=True,
        )

    # ── Bot invoices ──────────────────────────────────────────────────────
    @bot.on_callback_query(filters.regex(f"^{CB['OWN_BOT_INVOICES']}$") & _pm_own)
    @owner_or_above
    async def own_bot_invoices(client: Client, query: CallbackQuery):  # noqa: ARG001
        await query.answer(
            t(_LANG, "credit.financial_surface_removed"),
            show_alert=True,
        )

    # ── Users ─────────────────────────────────────────────────────────────
    @bot.on_callback_query(filters.regex(f"^{CB['OWN_USERS']}$") & _pm_own)
    @owner_or_above
    async def own_users(client: Client, query: CallbackQuery):
        if not await _require_developer_for_global_owner_route(query):
            return
        from app.database.engine import async_session
        from app.database.models import User
        from sqlalchemy import func, select

        async with async_session() as session:
            count = (await session.execute(select(func.count()).select_from(User))).scalar() or 0
        await query.answer(t(_LANG, "owner_mgmt.total_users", count=count), show_alert=True)

    # ── Credit links ──────────────────────────────────────────────────────
    @bot.on_callback_query(filters.regex(f"^{CB['OWN_CREDIT_LINKS']}$") & _pm_own)
    @owner_or_above
    async def own_credit_links(client: Client, query: CallbackQuery):
        await query.answer()
        if not await _require_developer_for_global_owner_route(query):
            return
        all_settings = await settings_repo.get_all_bot_settings()
        lines = [_setting_entry(k, str(v)) for k, v in all_settings.items() if "link" in k or "credit" in k]
        text = "\n".join(lines) if lines else t(_LANG, "filter_mgmt.list_empty")
        await query.message.edit_text(text, reply_markup=KeyboardFactory.owner_panel(_LANG))

    # ── Groups credit list ────────────────────────────────────────────────
    @bot.on_callback_query(filters.regex(f"^{CB['OWN_LIST_GROUPS_CREDIT']}$") & _pm_own)
    @owner_or_above
    async def own_list_groups_credit(client: Client, query: CallbackQuery):  # noqa: ARG001
        await query.answer()
        await _render_owner_group_list_page(query, _OWNER_KIND_ACTIVE_GROUPS, 0)

    # ── Channels credit list ──────────────────────────────────────────────
    @bot.on_callback_query(filters.regex(f"^{CB['OWN_LIST_CHANNELS_CREDIT']}$") & _pm_own)
    @owner_or_above
    async def own_list_channels_credit(client: Client, query: CallbackQuery):  # noqa: ARG001
        await query.answer()
        await _render_owner_group_list_page(query, _OWNER_KIND_ACTIVE_CHANNELS, 0)

    # ── G9: Top-up sudo wallet (§A4.4) ─────────────────────────────────
    @bot.on_callback_query(filters.regex(f"^{CB['OWN_TOPUP_SUDO_WALLET']}$") & _pm_own)
    @owner_or_above
    async def own_topup_sudo_wallet(client: Client, query: CallbackQuery):
        await query.answer(t(_LANG, "wallet.topup_removed_notice"), show_alert=True)
    # ── Owner texts & links ────────────────────────────────────────────
    @bot.on_callback_query(filters.regex(f"^{CB['OWN_TEXTS_BACK']}$") & _pm_own)
    @owner_or_above
    async def own_texts_back(client: Client, query: CallbackQuery):
        await query.answer()
        await _show_owner_root(client, query)

    # Identity-only filters (panel root): deny-capable filters skip the handler
    # on stale taps; role via @owner_or_above, scope answered explicitly.
    @bot.on_callback_query(filters.regex(f"^{CB['OWN_TEXTS_HOME']}$"))
    @bot.on_callback_query(filters.regex(f"^{CB['OWN_TEXTS_LINKS']}$"))
    @owner_or_above
    async def own_texts_home(client: Client, query: CallbackQuery):
        chat = query.message.chat if query.message else None
        if chat is None or getattr(chat.type, "value", chat.type) != "private":
            await query.answer(
                t(_LANG, "common.errors.unknown_callback"), show_alert=True
            )
            return
        await query.answer()
        if not await _require_active_owner_for_text_link_route(query):
            return
        owner_id = query.from_user.id
        text, kb = await build_texts_hub_payload(_LANG, "owner", owner_user_id=owner_id)
        await query.message.edit_text(text, reply_markup=kb)

    @bot.on_callback_query(filters.regex(f"^{CB['OWN_START_STYLE_TOGGLE']}$") & _pm_own)
    @owner_or_above
    async def own_start_style_toggle(client: Client, query: CallbackQuery):  # noqa: ARG001
        if not await _require_active_owner_for_text_link_route(query):
            await query.answer(t(_LANG, "common.errors.no_access"), show_alert=True)
            return
        owner_id = query.from_user.id
        if await resolve_single_active_owner_user_id() != owner_id:
            await query.answer(
                t(_LANG, "start_customization_commands.owner_scope_inactive"),
                show_alert=True,
            )
            return
        updated = await start_custom.toggle_style_mode(
            scope_type="owner",
            owner_user_id=owner_id,
            actor_user_id=query.from_user.id,
        )
        next_mode = updated.mode
        text, kb = await build_texts_hub_payload(_LANG, "owner", owner_user_id=owner_id)
        await query.message.edit_text(text, reply_markup=kb)
        await query.answer(
            t(
                _LANG,
                "texts_links.start_style.saved_owner",
                mode=t(_LANG, f"texts_links.start_style.mode_{next_mode}"),
            ),
            show_alert=False,
        )

    @bot.on_callback_query(filters.regex(r"^own:text:f:[a-z0-9_]+$") & _pm_own)
    @owner_or_above
    async def own_text_field(client: Client, query: CallbackQuery):
        await query.answer()
        if not await _require_active_owner_for_text_link_route(query):
            return
        field = field_from_callback(query.data, CB["OWN_TEXT_FIELD_PREFIX"])
        if field is None:
            await query.answer(t(_LANG, "common.errors.try_later"), show_alert=True)
            return
        text, kb = await build_text_field_payload(
            _LANG,
            "owner",
            field,
            owner_user_id=query.from_user.id,
        )
        await query.message.edit_text(text, reply_markup=kb)

    @bot.on_callback_query(filters.regex(r"^own:text:txt:[a-z0-9_]+$") & _pm_own)
    @owner_or_above
    async def own_text_set_text(client: Client, query: CallbackQuery):
        await query.answer()
        if not await _require_active_owner_for_text_link_route(query):
            return
        field = field_from_callback(query.data, CB["OWN_TEXT_SET_TEXT_PREFIX"])
        if field is None:
            await query.answer(t(_LANG, "common.errors.try_later"), show_alert=True)
            return

        spec = get_field_spec(field)
        if spec is None:
            await query.answer(t(_LANG, "common.errors.try_later"), show_alert=True)
            return
        if not is_owner_editable_field(spec):
            await _send_owner_text_link_edit_denied(client, query)
            return

        owner_id = query.from_user.id
        chat_id = query.message.chat.id

        def _parse_text_value(message) -> str | None:
            value = (getattr(message, "text", "") or "").strip()
            if not value:
                return None
            if spec.kind == "link":
                value = normalize_link_value(value)
                if not is_valid_link_value(value):
                    return None
            return value

        invalid_key = (
            "texts_links.invalid_link"
            if spec.kind == "link"
            else "texts_links.invalid_text"
        )
        resp, value = await _ask_value(
            client,
            chat_id,
            "texts_links.prompt_set_text",
            _parse_text_value,
            t(_LANG, invalid_key),
            user_id=owner_id,
            return_to=TOKEN_OWNER_TEXTS,
            prompt_kwargs={"field": t(_LANG, field_label_key(spec, "owner"))},
        )
        if await _notify_texts_links_ask_abort(client, chat_id, resp, TOKEN_OWNER_TEXTS):
            return

        await owner_text_link_service.set_owner_override(
            owner_id,
            field,
            spec.kind,
            value,
            updated_by=owner_id,
        )
        text, kb = await build_text_field_payload(
            _LANG,
            "owner",
            field,
            owner_user_id=owner_id,
        )
        await deliver_ask_outcome(
            client,
            chat_id,
            owner_id,
            f"{t(_LANG, confirmation_key_for_save(spec, scope='owner'))}\n\n{text}",
            kb,
            query_message=query.message,
        )

    @bot.on_callback_query(filters.regex(r"^own:text:med:[a-z0-9_]+$") & _pm_own)
    @owner_or_above
    async def own_text_set_media(client: Client, query: CallbackQuery):
        await query.answer()
        if not await _require_active_owner_for_text_link_route(query):
            return
        field = field_from_callback(query.data, CB["OWN_TEXT_SET_MEDIA_PREFIX"])
        if field is None:
            await query.answer(t(_LANG, "common.errors.try_later"), show_alert=True)
            return

        spec = get_field_spec(field)
        if spec is None:
            await query.answer(t(_LANG, "common.errors.try_later"), show_alert=True)
            return
        if not is_owner_editable_field(spec):
            await _send_owner_text_link_edit_denied(client, query)
            return

        owner_id = query.from_user.id
        chat_id = query.message.chat.id
        retry_prompt: str | None = None
        while True:
            resp = await _ask(
                client,
                chat_id,
                "texts_links.prompt_set_media",
                user_id=owner_id,
                return_to=TOKEN_OWNER_TEXTS,
                prompt_kwargs={"field": t(_LANG, field_label_key(spec, "owner"))},
                prompt_text=retry_prompt,
                delete_response=False,
            )
            if await _notify_texts_links_ask_abort(client, chat_id, resp, TOKEN_OWNER_TEXTS):
                return

            file_id, caption = extract_media_payload(resp.message)
            if file_id:
                await owner_text_link_service.set_owner_override(
                    owner_id,
                    field,
                    "text",
                    encode_media_value(file_id, caption),
                    updated_by=owner_id,
                )
                text, kb = await build_text_field_payload(
                    _LANG,
                    "owner",
                    field,
                    owner_user_id=owner_id,
                )
                await deliver_ask_outcome(
                    client,
                    chat_id,
                    owner_id,
                    f"{t(_LANG, confirmation_key_for_save(spec, media=True, scope='owner'))}\n\n{text}",
                    kb,
                    query_message=query.message,
                )
                return

            await safe_delete_user_input(resp.message)
            retry_prompt = (
                f"{t(_LANG, 'texts_links.invalid_media')}\n\n"
                f"{t(_LANG, 'texts_links.prompt_set_media', field=t(_LANG, field_label_key(spec, 'owner')))}"
            )

    @bot.on_callback_query(filters.regex(r"^own:text:clr:[a-z0-9_]+$") & _pm_own)
    @owner_or_above
    async def own_text_clear(client: Client, query: CallbackQuery):  # noqa: ARG001
        await query.answer()
        if not await _require_active_owner_for_text_link_route(query):
            return
        field = field_from_callback(query.data, CB["OWN_TEXT_CLEAR_PREFIX"])
        if field is None:
            await query.answer(t(_LANG, "common.errors.try_later"), show_alert=True)
            return
        spec = get_field_spec(field)
        if spec is None:
            await query.answer(t(_LANG, "common.errors.try_later"), show_alert=True)
            return
        if not is_owner_editable_field(spec):
            await _send_owner_text_link_edit_denied(client, query)
            return

        issued_at = int(time.time())
        await query.message.edit_text(
            t(_LANG, "texts_links.clear_confirm_prompt", field=t(_LANG, spec.label_key)),
            reply_markup=KeyboardFactory.owner_text_clear_confirm(
                _LANG, field, query.from_user.id, issued_at
            ),
        )

    @bot.on_callback_query(filters.regex(r"^own:text:clr:do:[a-z0-9_]+:\d+:\d+$") & _pm_own)
    @owner_or_above
    async def own_text_clear_confirm(client: Client, query: CallbackQuery):
        parsed = _parse_owner_text_clear_bound(query.data, CB["OWN_TEXT_CLEAR_EXEC_PREFIX"])
        if parsed is None:
            await query.answer(t(_LANG, "common.errors.invalid_callback"), show_alert=True)
            return
        field, bound_user_id, issued_at = parsed
        if query.from_user.id != bound_user_id:
            await query.answer(t(_LANG, "common.errors.no_access"), show_alert=True)
            return
        if not await _require_active_owner_for_text_link_route(query):
            return
        if _is_stale_owner_confirm_token(issued_at):
            await query.answer(t(_LANG, "common.errors.invalid_callback"), show_alert=True)
            return
        await query.answer()

        owner_id = query.from_user.id
        cleared = await owner_text_link_service.clear_owner_override(owner_id, field)
        text, kb = await build_text_field_payload(
            _LANG,
            "owner",
            field,
            owner_user_id=owner_id,
        )
        await query.message.edit_text(text, reply_markup=kb)
        confirm_key = (
            confirmation_key_for_clear(scope="owner")
            if cleared
            else "texts_links.no_owner_override"
        )
        await _send_done(
            client,
            query.message.chat.id,
            t(_LANG, confirm_key),
            TOKEN_OWNER_TEXTS,
        )

    @bot.on_callback_query(filters.regex(r"^own:text:clr:no:[a-z0-9_]+:\d+:\d+$") & _pm_own)
    @owner_or_above
    async def own_text_clear_abort(client: Client, query: CallbackQuery):
        parsed = _parse_owner_text_clear_bound(query.data, CB["OWN_TEXT_CLEAR_ABORT_PREFIX"])
        if parsed is None:
            await query.answer(t(_LANG, "common.errors.invalid_callback"), show_alert=True)
            return
        field, bound_user_id, issued_at = parsed
        if query.from_user.id != bound_user_id:
            await query.answer(t(_LANG, "common.errors.no_access"), show_alert=True)
            return
        if not await _require_active_owner_for_text_link_route(query):
            return
        if _is_stale_owner_confirm_token(issued_at):
            await query.answer(t(_LANG, "common.errors.invalid_callback"), show_alert=True)
            return
        await query.answer(t(_LANG, "common.cancelled"), show_alert=False)
        text, kb = await build_text_field_payload(_LANG, "owner", field, owner_user_id=query.from_user.id)
        await query.message.edit_text(text, reply_markup=kb)

    @bot.on_callback_query(filters.regex(r"^own:text:clr:(?:do|no):.*") & _pm_own)
    @owner_or_above
    async def own_text_clear_malformed(client: Client, query: CallbackQuery):  # noqa: ARG001
        if _parse_owner_text_clear_bound(query.data, CB["OWN_TEXT_CLEAR_EXEC_PREFIX"]) is not None:
            return
        if _parse_owner_text_clear_bound(query.data, CB["OWN_TEXT_CLEAR_ABORT_PREFIX"]) is not None:
            return
        await query.answer(t(_LANG, "common.errors.invalid_callback"), show_alert=True)

    @bot.on_callback_query(filters.regex(r"^own:text:prv:[a-z0-9_]+$") & _pm_own)
    @owner_or_above
    async def own_text_preview(client: Client, query: CallbackQuery):
        await query.answer()
        if not await _require_active_owner_for_text_link_route(query):
            return
        field = field_from_callback(query.data, CB["OWN_TEXT_PREVIEW_PREFIX"])
        if field is None:
            await query.answer(t(_LANG, "common.errors.try_later"), show_alert=True)
            return
        spec = get_field_spec(field)
        if spec is None:
            await query.answer(t(_LANG, "common.errors.try_later"), show_alert=True)
            return
        if not is_owner_editable_field(spec):
            await _send_owner_text_link_edit_denied(client, query)
            return

        effective = await owner_text_link_service.get_effective_text_link(
            field,
            owner_user_id=query.from_user.id,
        )
        parsed_value = decode_setting_value(
            effective.raw if effective.mode == "media" else None,
        )
        if parsed_value.get("mode") != "media":
            await query.answer(t(_LANG, "texts_links.preview_unavailable"), show_alert=True)
            return

        await replace_panel_with_photo(
            client,
            query,
            parsed_value.get("file_id"),
            caption=parsed_value.get("caption") or None,
            reply_markup=build_done_kb(_LANG, TOKEN_OWNER_TEXTS),
        )


    # ── Owner sales report ─────────────────────────────────────────────
    @bot.on_callback_query(filters.regex(f"^{CB['OWN_SALES_REPORT']}$") & _pm_own)
    @owner_or_above
    async def own_sales_report(client: Client, query: CallbackQuery):  # noqa: ARG001
        await query.answer(
            t(_LANG, "credit.financial_surface_removed"),
            show_alert=True,
        )

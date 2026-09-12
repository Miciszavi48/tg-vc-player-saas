from __future__ import annotations

import logging
import math
import time
from collections.abc import Callable
from datetime import timezone

from pyrogram import Client, filters
from pyrogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup
from pyromod.exceptions import ListenerStopped
from sqlalchemy import and_, func, or_, select

from app.database.engine import async_session
from app.database.models import Channel, CreditHistory, Group, GroupCredit, Sudo
from app.repositories import channel_repo, credit_repo, group_repo, log_repo, settings_repo, user_repo
from app.services import CallService, CreditService
from app.services.panel_message_service import panel_callback_edit
from app.utils.ask_result import (
    AskResult,
    deliver_ask_outcome,
    prompt_for_panel_input,
    safe_delete_user_input,
    safe_stop_listening,
)
from app.utils.cache import get_redis
from app.utils.decorators import sudo_or_above
from app.utils.filters import private_chat_filter, sudo_filter
from app.utils.helpers import MAX_CREDIT_DAYS, parse_bounded_int, parse_user_id
from app.utils.i18n import AUTO_LANG, t
from app.utils.redis_keys import instance_key
from app.utils.sudo_permissions import deny_unless_sudo_permission
from app.utils.ui import CB, KeyboardFactory

logger = logging.getLogger(__name__)

_LANG = AUTO_LANG
_TIMEOUT = 60
_SUDO_PAGE_SIZE = 3
_SUDO_CONFIRM_TTL = 300
_SUDO_LEAVE_CONFIRM_TTL = 300

_pm_sudo = sudo_filter() & private_chat_filter()
_GROUP_KINDS = ("active", "inactive", "no_credit", "renewal")
_GROUP_TITLE_KEYS = {
    "active": "panels.sudo.list_title_active",
    "inactive": "panels.sudo.list_title_inactive",
    "no_credit": "panels.sudo.list_title_no_credit",
    "renewal": "panels.sudo.list_title_renewal",
}


def _sudo_leave_confirm_key(user_id: int) -> str:
    return instance_key(f"sudo_leave_confirm:{user_id}")


def _parse_bound_user_id(data: str, prefix: str) -> int | None:
    if not data.startswith(prefix):
        return None
    raw = data[len(prefix):].strip()
    if not raw.isdigit():
        return None
    return int(raw)


async def _set_sudo_leave_confirmation(user_id: int) -> None:
    r = await get_redis()
    await r.set(_sudo_leave_confirm_key(user_id), "1", ex=_SUDO_LEAVE_CONFIRM_TTL)


async def _pop_sudo_leave_confirmation(user_id: int) -> bool:
    r = await get_redis()
    key = _sudo_leave_confirm_key(user_id)
    exists = await r.get(key)
    await r.delete(key)
    return bool(exists)


async def _clear_sudo_leave_confirmation(user_id: int) -> None:
    r = await get_redis()
    await r.delete(_sudo_leave_confirm_key(user_id))


async def _sudo_panel_kb(user_id: int) -> InlineKeyboardMarkup:  # noqa: ARG001
    return KeyboardFactory.sudo_panel(_LANG)


async def _ask(
    client: Client,
    chat_id: int,
    key: str,
    *,
    user_id: int | None = None,
    return_cb: str = CB["SUDO_CREDIT"],
    prompt_text: str | None = None,
) -> AskResult:
    if user_id is not None:
        await safe_stop_listening(client, chat_id, user_id=user_id)

    cancel_kb = InlineKeyboardMarkup(
        [[InlineKeyboardButton(t(_LANG, "common.buttons.cancel_inline"), callback_data=return_cb)]]
    )
    try:
        message = await prompt_for_panel_input(
            client,
            chat_id,
            user_id,
            prompt_text or t(_LANG, key),
            cancel_kb,
            timeout=_TIMEOUT,
        )
        if message and message.text and message.text.strip().lower() in ("/cancel", "cancel", "لغو"):
            await safe_delete_user_input(message)
            await deliver_ask_outcome(
                client,
                chat_id,
                user_id,
                t(_LANG, "common.cancelled"),
                _sudo_done_kb(return_cb),
            )
            return AskResult(message=None, abort_reason="cancel_text", user_notified=True)
        await safe_delete_user_input(message)
        return AskResult(message=message)
    except ListenerStopped:
        # Navigation callbacks own the redraw after interrupting the listener.
        return AskResult(message=None, abort_reason="listener_stopped", user_notified=True)
    except Exception:
        await deliver_ask_outcome(
            client,
            chat_id,
            user_id,
            t(_LANG, "ask.timeout"),
            _sudo_done_kb(return_cb),
        )
        return AskResult(message=None, abort_reason="timeout", user_notified=True)


async def _ask_value(
    client: Client,
    chat_id: int,
    key: str,
    parser: Callable[[object], object | None],
    invalid_text: str,
    *,
    user_id: int,
    return_cb: str,
) -> tuple[AskResult, object | None]:
    retry_prompt: str | None = None
    while True:
        result = await _ask(
            client,
            chat_id,
            key,
            user_id=user_id,
            return_cb=return_cb,
            prompt_text=retry_prompt,
        )
        if result.message is None:
            return result, None
        value = parser(result.message)
        if value is not None:
            return result, value
        retry_prompt = f"{invalid_text}\n\n{t(_LANG, key)}"


def _state_label(value: bool) -> str:
    state_key = "common.labels.enabled" if value else "common.labels.disabled"
    icon = "✅" if value else "❌"
    return f"{icon} {t(_LANG, state_key)}"


def _value(value: object | None) -> str:
    return str(value) if value not in (None, "") else t(_LANG, "panels.sudo.value_unavailable")


def _format_dt(value) -> str:
    if value is None:
        return t(_LANG, "panels.sudo.value_unavailable")
    try:
        return value.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    except Exception:
        return str(value)


def _sudo_done_kb(back_cb: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[
            InlineKeyboardButton(t(_LANG, "common.buttons.back"), callback_data=back_cb),
            InlineKeyboardButton(t(_LANG, "common.buttons.home"), callback_data=CB["WZ_HOME"]),
        ]]
    )


def _sudo_groups_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    t(_LANG, "panels.sudo.btn_active_groups"),
                    callback_data=f"{CB['SUDO_GRP_LIST_PREFIX']}active:0",
                ),
                InlineKeyboardButton(
                    t(_LANG, "panels.sudo.btn_inactive_groups"),
                    callback_data=f"{CB['SUDO_GRP_LIST_PREFIX']}inactive:0",
                ),
            ],
            [
                InlineKeyboardButton(
                    t(_LANG, "panels.sudo.btn_no_credit_groups"),
                    callback_data=f"{CB['SUDO_GRP_LIST_PREFIX']}no_credit:0",
                ),
                InlineKeyboardButton(
                    t(_LANG, "panels.sudo.btn_renewal_groups"),
                    callback_data=f"{CB['SUDO_GRP_LIST_PREFIX']}renewal:0",
                ),
            ],
            [InlineKeyboardButton(t(_LANG, "panels.sudo.btn_search_group"), callback_data=CB["SUDO_GROUP_SEARCH"])],
            [InlineKeyboardButton(t(_LANG, "common.buttons.home"), callback_data=CB["WZ_HOME"])],
        ]
    )


def _sudo_credit_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(t(_LANG, "panels.sudo.btn_credit_list"), callback_data=CB["SUDO_CREDIT_LIST"]),
                InlineKeyboardButton(t(_LANG, "panels.sudo.btn_no_credit_groups"), callback_data=CB["SUDO_CREDIT_NO_CREDIT"]),
            ],
            [
                InlineKeyboardButton(t(_LANG, "panels.sudo.btn_renewal_groups"), callback_data=CB["SUDO_CREDIT_RENEWAL"]),
                InlineKeyboardButton(t(_LANG, "panels.sudo.btn_credit_history"), callback_data=CB["SUDO_CREDIT_HISTORY"]),
            ],
            [InlineKeyboardButton(t(_LANG, "common.buttons.home"), callback_data=CB["WZ_HOME"])],
        ]
    )


def _group_list_kb(rows, kind: str, page: int, total_pages: int) -> InlineKeyboardMarkup:
    buttons: list[list[InlineKeyboardButton]] = []
    for row in rows:
        title = row.title or str(row.chat_id)
        buttons.append(
            [
                InlineKeyboardButton(
                    title[:48],
                    callback_data=f"{CB['SUDO_GRP_DETAIL_PREFIX']}{kind}:{row.chat_id}:{page}",
                )
            ]
        )
    nav: list[InlineKeyboardButton] = []
    if page > 0:
        nav.append(
            InlineKeyboardButton(
                t(_LANG, "common.buttons.prev"),
                callback_data=f"{CB['SUDO_GRP_LIST_PREFIX']}{kind}:{page - 1}",
            )
        )
    if page + 1 < total_pages:
        nav.append(
            InlineKeyboardButton(
                t(_LANG, "common.buttons.next"),
                callback_data=f"{CB['SUDO_GRP_LIST_PREFIX']}{kind}:{page + 1}",
            )
        )
    if nav:
        buttons.append(nav)
    buttons.append([InlineKeyboardButton(t(_LANG, "common.buttons.back"), callback_data=CB["SUDO_GROUPS"])])
    buttons.append([InlineKeyboardButton(t(_LANG, "common.buttons.home"), callback_data=CB["WZ_HOME"])])
    return InlineKeyboardMarkup(buttons)


def _group_detail_kb(row, kind: str, page: int, perms: dict[str, bool]) -> InlineKeyboardMarkup:
    buttons: list[list[InlineKeyboardButton]] = []
    action_row: list[InlineKeyboardButton] = []
    payload = f"{kind}:{row.chat_id}:{page}"
    if perms.get("can_remove_bot", True):
        action_row.append(
            InlineKeyboardButton(
                t(_LANG, "panels.sudo.leave_installs"),
                callback_data=f"{CB['SUDO_GRP_LEAVE_CONFIRM_PREFIX']}{payload}",
            )
        )
    if action_row:
        buttons.extend(action_row[i:i + 2] for i in range(0, len(action_row), 2))
    buttons.append(
        [
            InlineKeyboardButton(
                t(_LANG, "common.buttons.back"),
                callback_data=f"{CB['SUDO_GRP_LIST_PREFIX']}{kind}:{page}",
            )
        ]
    )
    buttons.append([InlineKeyboardButton(t(_LANG, "common.buttons.home"), callback_data=CB["WZ_HOME"])])
    return InlineKeyboardMarkup(buttons)


def _sudo_leave_group_confirm_kb(kind: str, chat_id: int, page: int, actor_id: int) -> InlineKeyboardMarkup:
    issued_at = int(time.time())
    payload = f"{kind}:{chat_id}:{page}:{actor_id}:{issued_at}"
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    t(_LANG, "common.buttons.confirm"),
                    callback_data=f"{CB['SUDO_GRP_LEAVE_EXEC_PREFIX']}{payload}",
                )
            ],
            [
                InlineKeyboardButton(
                    t(_LANG, "common.buttons.cancel"),
                    callback_data=f"{CB['SUDO_GRP_LEAVE_CANCEL_PREFIX']}{payload}",
                )
            ],
        ]
    )


def _parse_group_kind_page(data: str, prefix: str) -> tuple[str, int] | None:
    if not data.startswith(prefix):
        return None
    parts = data[len(prefix):].split(":")
    if len(parts) != 2:
        return None
    kind, page_raw = parts
    if kind not in _GROUP_KINDS:
        return None
    try:
        page = int(page_raw)
    except ValueError:
        return None
    return kind, max(page, 0)


def _parse_group_payload(data: str, prefix: str) -> tuple[str, int, int] | None:
    if not data.startswith(prefix):
        return None
    parts = data[len(prefix):].split(":")
    if len(parts) != 3:
        return None
    kind, chat_raw, page_raw = parts
    if kind not in _GROUP_KINDS:
        return None
    chat_id = parse_user_id(chat_raw)
    if chat_id is None:
        return None
    try:
        page = int(page_raw)
    except ValueError:
        return None
    return kind, chat_id, max(page, 0)


def _parse_leave_payload(data: str, prefix: str) -> tuple[str, int, int, int, int] | None:
    if not data.startswith(prefix):
        return None
    parts = data[len(prefix):].split(":")
    if len(parts) != 5:
        return None
    kind, chat_raw, page_raw, actor_raw, issued_raw = parts
    if kind not in _GROUP_KINDS:
        return None
    chat_id = parse_user_id(chat_raw)
    if chat_id is None:
        return None
    try:
        page = int(page_raw)
        actor_id = int(actor_raw)
        issued_at = int(issued_raw)
    except ValueError:
        return None
    return kind, chat_id, max(page, 0), actor_id, issued_at


def _base_sudo_group_stmt():
    return (
        select(
            Group.chat_id,
            Group.chat_title,
            Group.invite_link,
            Group.status,
            Group.installed_by,
            GroupCredit.credit_days,
            GroupCredit.expire_at,
        )
        .select_from(Group)
        .outerjoin(
            GroupCredit,
            and_(GroupCredit.chat_id == Group.chat_id, GroupCredit.chat_type == "group"),
        )
    )


def _kind_filters(user_id: int, kind: str):
    filters_ = [Group.installed_by == user_id]
    if kind == "active":
        filters_.append(Group.status == "active")
    elif kind == "inactive":
        filters_.append(Group.status != "active")
    elif kind == "no_credit":
        filters_.extend(
            [
                Group.status == "active",
                or_(GroupCredit.id.is_(None), GroupCredit.credit_days <= 0),
            ]
        )
    elif kind == "renewal":
        filters_.extend(
            [
                Group.status == "active",
                GroupCredit.status == "active",
                GroupCredit.credit_days > 0,
                GroupCredit.credit_days <= 3,
            ]
        )
    return filters_


def _row_from_result(row):
    from app.repositories.admin_report_repo import ChatInstallRow

    return ChatInstallRow(
        chat_id=row.chat_id,
        chat_type="group",
        title=row.chat_title,
        invite_link=row.invite_link,
        credit_days=int(row.credit_days or 0),
        expire_at=row.expire_at,
        status=row.status,
        installed_by=row.installed_by,
    )


async def _fetch_sudo_group_counts(user_id: int) -> dict[str, int]:
    async with async_session() as session:
        async def count_stmt(kind: str | None) -> int:
            if kind is None:
                stmt = select(func.count()).select_from(Group).where(Group.installed_by == user_id)
            else:
                base = _base_sudo_group_stmt().where(*_kind_filters(user_id, kind)).subquery()
                stmt = select(func.count()).select_from(base)
            return int((await session.execute(stmt)).scalar() or 0)

        return {
            "total": await count_stmt(None),
            "active": await count_stmt("active"),
            "inactive": await count_stmt("inactive"),
            "no_credit": await count_stmt("no_credit"),
            "renewal": await count_stmt("renewal"),
        }


async def _fetch_sudo_group_list(user_id: int, kind: str, page: int) -> tuple[list, int, int]:
    stmt = _base_sudo_group_stmt().where(*_kind_filters(user_id, kind))
    async with async_session() as session:
        total = int((await session.execute(select(func.count()).select_from(stmt.subquery()))).scalar() or 0)
        if total == 0:
            return [], 1, 0
        total_pages = max(1, math.ceil(total / _SUDO_PAGE_SIZE))
        page = max(0, min(page, total_pages - 1))
        result = await session.execute(
            stmt.order_by(Group.id.desc()).offset(page * _SUDO_PAGE_SIZE).limit(_SUDO_PAGE_SIZE)
        )
        return [_row_from_result(row) for row in result.all()], total_pages, page


async def _get_sudo_group_row(user_id: int, chat_id: int):
    stmt = _base_sudo_group_stmt().where(Group.installed_by == user_id, Group.chat_id == chat_id)
    async with async_session() as session:
        row = (await session.execute(stmt)).first()
        return _row_from_result(row) if row else None


async def _search_sudo_groups(user_id: int, raw: str) -> list:
    term = (raw or "").strip()
    if not term:
        return []
    filters_ = [Group.installed_by == user_id]
    target_id = parse_user_id(term)
    if target_id is not None:
        filters_.append(Group.chat_id == target_id)
    else:
        filters_.append(Group.chat_title.ilike(f"%{term}%"))
    stmt = _base_sudo_group_stmt().where(*filters_).order_by(Group.id.desc()).limit(10)
    async with async_session() as session:
        result = await session.execute(stmt)
        return [_row_from_result(row) for row in result.all()]


async def _fetch_sudo_credit_history(user_id: int, limit: int = 10) -> list[CreditHistory]:
    async with async_session() as session:
        result = await session.execute(
            select(CreditHistory)
            .where(CreditHistory.operated_by == user_id)
            .order_by(CreditHistory.id.desc())
            .limit(limit)
        )
        return list(result.scalars().all())


async def _fetch_sudo_total_installs(user_id: int) -> int:
    async with async_session() as session:
        row = (
            await session.execute(
                select(Sudo.total_installs).where(Sudo.user_id == user_id, Sudo.is_active.is_(True))
            )
        ).scalar_one_or_none()
        return int(row or 0)


async def _require_group_permission(query: CallbackQuery) -> bool:
    return await deny_unless_sudo_permission(query, query.from_user.id, "can_manage_groups")


async def _require_credit_permission(query: CallbackQuery) -> bool:
    return await deny_unless_sudo_permission(query, query.from_user.id, "can_manage_credit")


async def _require_remove_bot_permission(query: CallbackQuery) -> bool:
    return await deny_unless_sudo_permission(query, query.from_user.id, "can_remove_bot")


def _format_group_list_text(kind: str, rows, page: int, total_pages: int) -> str:
    lines = [t(_LANG, _GROUP_TITLE_KEYS[kind]), ""]
    for index, row in enumerate(rows, start=page * _SUDO_PAGE_SIZE + 1):
        lines.append(
            t(
                _LANG,
                "panels.sudo.list_item",
                index=index,
                title=row.title or str(row.chat_id),
                chat_id=row.chat_id,
                status=_value(row.status),
                credit_days=row.credit_days,
            )
        )
        lines.append("")
    lines.append(t(_LANG, "list_fmt.page_indicator", page=page + 1, total=total_pages))
    return "\n".join(lines)


def _format_group_detail_text(row) -> str:
    return "\n".join(
        [
            t(_LANG, "panels.sudo.detail_title"),
            "",
            t(
                _LANG,
                "panels.sudo.detail_body",
                title=row.title or str(row.chat_id),
                chat_id=row.chat_id,
                status=_value(row.status),
                credit_days=row.credit_days,
                expire_at=_format_dt(row.expire_at),
                installed_by=_value(row.installed_by),
            ),
        ]
    )


async def _render_sudo_groups_menu(client: Client, query: CallbackQuery, *, answer: bool = False) -> None:
    text = "\n\n".join([t(_LANG, "panels.sudo.groups_title"), t(_LANG, "panels.sudo.groups_body")])
    await panel_callback_edit(client, query, text, _sudo_groups_kb(), answer=answer)


async def _render_sudo_credit_menu(client: Client, query: CallbackQuery, *, answer: bool = False) -> None:
    text = "\n\n".join([t(_LANG, "panels.sudo.credit_title"), t(_LANG, "panels.sudo.credit_body")])
    await panel_callback_edit(client, query, text, _sudo_credit_kb(), answer=answer)


async def _render_sudo_group_list(client: Client, query: CallbackQuery, kind: str, page: int) -> None:
    user_id = query.from_user.id
    rows, total_pages, page = await _fetch_sudo_group_list(user_id, kind, page)
    if not rows:
        await panel_callback_edit(
            client,
            query,
            t(_LANG, "panels.sudo.empty"),
            _sudo_done_kb(CB["SUDO_GROUPS"]),
            answer=False,
        )
        return
    await panel_callback_edit(
        client,
        query,
        _format_group_list_text(kind, rows, page, total_pages),
        _group_list_kb(rows, kind, page, total_pages),
        answer=False,
    )


async def _render_sudo_group_detail(client: Client, query: CallbackQuery, kind: str, chat_id: int, page: int) -> None:
    row = await _get_sudo_group_row(query.from_user.id, chat_id)
    if row is None:
        await query.answer(t(_LANG, "panels.sudo.not_in_scope"), show_alert=True)
        return
    perms = await user_repo.get_sudo_permissions(query.from_user.id) or {}
    await panel_callback_edit(
        client,
        query,
        _format_group_detail_text(row),
        _group_detail_kb(row, kind, page, perms),
        answer=False,
    )


async def _credit_target_from_prompt(client: Client, query: CallbackQuery, action: str) -> tuple[int, int] | None:
    if not await _require_credit_permission(query):
        return None
    chat_id = query.message.chat.id
    user_id = query.from_user.id
    retry_prompt: str | None = None
    while True:
        target_resp = await _ask(
            client,
            chat_id,
            "panels.sudo.credit_target_prompt",
            user_id=user_id,
            return_cb=CB["SUDO_CREDIT"],
            prompt_text=retry_prompt,
        )
        if target_resp.message is None:
            return None
        target_id = parse_user_id(target_resp.message.text or "")
        if target_id is not None and await _get_sudo_group_row(user_id, target_id) is not None:
            return target_id, chat_id
        retry_prompt = (
            f"{t(_LANG, 'panels.sudo.not_in_scope')}\n\n"
            f"{t(_LANG, 'panels.sudo.credit_target_prompt')}"
        )


async def _execute_credit_update(
    client: Client,
    query: CallbackQuery,
    *,
    target_id: int,
    days: int,
    mode: str,
    return_cb: str,
    title: str | None = None,
) -> None:
    try:
        result = await CreditService.adjust_sudo_scoped_group_credit(
            query.from_user.id,
            target_id,
            mode=mode,
            amount=days,
            note="sudo_panel",
        )
        key = "panels.sudo.credit_added" if mode == "increase" else "panels.sudo.credit_deducted"
    except ValueError:
        text = t(_LANG, "panels.sudo.not_in_scope")
    except Exception:
        logger.exception("sudo credit update failed target_id=%s mode=%s", target_id, mode)
        text = t(_LANG, "common.errors.try_later")
    else:
        text = t(
            _LANG,
            key,
            amount=days,
            title=title or str(target_id),
            after=result.after,
        )
    await deliver_ask_outcome(
        client,
        query.message.chat.id,
        query.from_user.id,
        text,
        _sudo_done_kb(return_cb),
        query_message=query.message,
    )


async def _handle_credit_prompt(client: Client, query: CallbackQuery, mode: str) -> None:
    target = await _credit_target_from_prompt(client, query, mode)
    if target is None:
        return
    target_id, chat_id = target
    days_resp, days = await _ask_value(
        client,
        chat_id,
        "panels.sudo.credit_days_prompt",
        lambda message: parse_bounded_int(
            getattr(message, "text", None),
            min_value=1,
            max_value=MAX_CREDIT_DAYS,
        ),
        t(_LANG, "common.errors.invalid_positive_number"),
        user_id=query.from_user.id,
        return_cb=CB["SUDO_CREDIT"],
    )
    if days_resp.message is None:
        return
    row = await _get_sudo_group_row(query.from_user.id, target_id)
    if row is None:
        await deliver_ask_outcome(
            client,
            chat_id,
            query.from_user.id,
            t(_LANG, "panels.sudo.not_in_scope"),
            _sudo_done_kb(CB["SUDO_CREDIT"]),
            query_message=query.message,
        )
        return
    await _execute_credit_update(
        client,
        query,
        target_id=target_id,
        days=days,
        mode=mode,
        return_cb=CB["SUDO_CREDIT"],
        title=row.title,
    )


async def _handle_detail_credit(client: Client, query: CallbackQuery, payload: tuple[str, int, int], mode: str) -> None:
    if not await _require_credit_permission(query):
        return
    kind, chat_id, page = payload
    row = await _get_sudo_group_row(query.from_user.id, chat_id)
    if row is None:
        await query.answer(t(_LANG, "panels.sudo.not_in_scope"), show_alert=True)
        return
    resp, days = await _ask_value(
        client,
        query.message.chat.id,
        "panels.sudo.credit_days_prompt",
        lambda message: parse_bounded_int(
            getattr(message, "text", None),
            min_value=1,
            max_value=MAX_CREDIT_DAYS,
        ),
        t(_LANG, "common.errors.invalid_positive_number"),
        user_id=query.from_user.id,
        return_cb=f"{CB['SUDO_GRP_DETAIL_PREFIX']}{kind}:{chat_id}:{page}",
    )
    if resp.message is None:
        return
    await _execute_credit_update(
        client,
        query,
        target_id=chat_id,
        days=days,
        mode=mode,
        return_cb=f"{CB['SUDO_GRP_DETAIL_PREFIX']}{kind}:{chat_id}:{page}",
        title=row.title,
    )


async def _get_current_sudo_leave_targets(
    sudo_user_id: int,
    historical_installs: dict[int, str],
) -> dict[int, str]:
    group_ids = [
        chat_id
        for chat_id, chat_type in historical_installs.items()
        if chat_type != "channel"
    ]
    channel_ids = [
        chat_id
        for chat_id, chat_type in historical_installs.items()
        if chat_type == "channel"
    ]
    targets: dict[int, str] = {}

    async with async_session() as session:
        if group_ids:
            group_rows = await session.execute(
                select(Group.chat_id).where(
                    Group.chat_id.in_(group_ids),
                    Group.status == "active",
                    Group.installed_by == sudo_user_id,
                )
            )
            targets.update(
                {int(chat_id): "group" for chat_id in group_rows.scalars().all()}
            )

        if channel_ids:
            channel_rows = await session.execute(
                select(Channel.chat_id).where(
                    Channel.chat_id.in_(channel_ids),
                    Channel.status == "active",
                    Channel.installed_by == sudo_user_id,
                )
            )
            targets.update(
                {int(chat_id): "channel" for chat_id in channel_rows.scalars().all()}
            )

    return targets


async def _execute_sudo_leave_installs(client: Client, call_py, query: CallbackQuery) -> None:
    if not await _require_remove_bot_permission(query):
        return
    user_id = query.from_user.id
    logs = await log_repo.get_install_logs(sudo_id=user_id, limit=500)
    installs = {
        lg.chat_id: lg.chat_type
        for lg in logs
        if lg.action == "install"
    }
    installs = await _get_current_sudo_leave_targets(user_id, installs)

    left_count = 0
    for cid, chat_type in installs.items():
        try:
            await CallService.leave_voice_chat(call_py, cid)
        except Exception:
            pass

        try:
            await client.leave_chat(cid)
            left_count += 1
        except Exception:
            logger.debug("Could not leave chat %s", cid)

        if chat_type == "channel":
            await channel_repo.deactivate_channel(cid)
        else:
            await group_repo.deactivate_group(cid)

    await query.message.edit_text(
        t(_LANG, "sudo_mgmt.left_chats", count=left_count),
        reply_markup=await _sudo_panel_kb(user_id),
    )


async def render_sudo_home_panel(client: Client, query: CallbackQuery) -> bool:
    """Render sudo panel home via panel_callback_edit."""
    user_id = query.from_user.id if query.from_user else 0
    logs = await log_repo.get_install_logs(sudo_id=user_id, limit=500)
    groups_count = sum(1 for lg in logs if lg.chat_type == "group" and lg.action == "install")
    channels_count = sum(1 for lg in logs if lg.chat_type == "channel" and lg.action == "install")
    text = t(
        _LANG,
        "reports.install_report",
        groups=groups_count,
        channels=channels_count,
        total=groups_count + channels_count,
    )
    return await panel_callback_edit(
        client,
        query,
        text,
        await _sudo_panel_kb(user_id),
        answer=False,
    )


def register(bot: Client, call_py) -> None:

    @bot.on_callback_query(filters.regex(f"^{CB['SUDO_STATUS']}$") & _pm_sudo)
    @sudo_or_above
    async def sudo_status(client: Client, query: CallbackQuery):
        await query.answer()
        counts = await _fetch_sudo_group_counts(query.from_user.id)
        bot_enabled = await settings_repo.get_bot_setting_bool("bot_enabled", default=True)
        sudo_panel_enabled = await settings_repo.get_bot_setting_bool("sudo_panel_enabled", default=True)
        text = "\n\n".join(
            [
                t(_LANG, "panels.sudo.status_title"),
                t(
                    _LANG,
                    "panels.sudo.status_body",
                    bot_enabled=_state_label(bot_enabled),
                    sudo_panel_enabled=_state_label(sudo_panel_enabled),
                    total=counts["total"],
                    active=counts["active"],
                    inactive=counts["inactive"],
                    no_credit=counts["no_credit"],
                    renewal=counts["renewal"],
                ),
            ]
        )
        await panel_callback_edit(client, query, text, _sudo_done_kb(CB["WZ_HOME"]), answer=False)

    @bot.on_callback_query(filters.regex(f"^{CB['SUDO_PERMISSIONS']}$") & _pm_sudo)
    @sudo_or_above
    async def sudo_permissions(client: Client, query: CallbackQuery):
        await query.answer()
        perms = await user_repo.get_sudo_permissions(query.from_user.id) or {}
        lines = [t(_LANG, "panels.sudo.permissions_title"), ""]
        for field in user_repo.SUDO_PERMISSION_FIELD_NAMES:
            label_key = user_repo.SUDO_PERMISSION_FIELD_LABEL_KEYS[field]
            state = t(
                _LANG,
                "sudo_permissions.state_enabled" if perms.get(field, True) else "sudo_permissions.state_disabled",
            )
            lines.append(t(_LANG, "panels.sudo.permission_row", label=t(_LANG, label_key), state=state))
        await panel_callback_edit(client, query, "\n".join(lines), _sudo_done_kb(CB["WZ_HOME"]), answer=False)

    @bot.on_callback_query(filters.regex(f"^{CB['SUDO_GROUPS']}$") & _pm_sudo)
    @sudo_or_above
    async def sudo_groups(client: Client, query: CallbackQuery):
        if not await _require_group_permission(query):
            return
        await query.answer()
        await _render_sudo_groups_menu(client, query)

    @bot.on_callback_query(filters.regex(r"^sudo:grp:l:[a-z_]+:\d+$") & _pm_sudo)
    @sudo_or_above
    async def sudo_group_list(client: Client, query: CallbackQuery):
        if not await _require_group_permission(query):
            return
        await query.answer()
        payload = _parse_group_kind_page(query.data, CB["SUDO_GRP_LIST_PREFIX"])
        if payload is None:
            await query.answer(t(_LANG, "panels.sudo.invalid_payload"), show_alert=True)
            return
        await _render_sudo_group_list(client, query, payload[0], payload[1])

    @bot.on_callback_query(filters.regex(r"^sudo:grp:d:[a-z_]+:-?\d+:\d+$") & _pm_sudo)
    @sudo_or_above
    async def sudo_group_detail(client: Client, query: CallbackQuery):
        if not await _require_group_permission(query):
            return
        await query.answer()
        payload = _parse_group_payload(query.data, CB["SUDO_GRP_DETAIL_PREFIX"])
        if payload is None:
            await query.answer(t(_LANG, "panels.sudo.invalid_payload"), show_alert=True)
            return
        await _render_sudo_group_detail(client, query, *payload)

    @bot.on_callback_query(filters.regex(f"^{CB['SUDO_GROUP_SEARCH']}$") & _pm_sudo)
    @sudo_or_above
    async def sudo_group_search(client: Client, query: CallbackQuery):
        if not await _require_group_permission(query):
            return
        await query.answer()
        resp = await _ask(
            client,
            query.message.chat.id,
            "panels.sudo.search_prompt",
            user_id=query.from_user.id,
            return_cb=CB["SUDO_GROUPS"],
        )
        if resp.message is None:
            return
        rows = await _search_sudo_groups(query.from_user.id, resp.message.text or "")
        if not rows:
            await deliver_ask_outcome(
                client,
                query.message.chat.id,
                query.from_user.id,
                t(_LANG, "panels.sudo.search_no_results"),
                _sudo_done_kb(CB["SUDO_GROUPS"]),
                query_message=query.message,
            )
            return
        lines = [t(_LANG, "panels.sudo.search_title"), ""]
        for index, row in enumerate(rows, start=1):
            lines.append(
                t(
                    _LANG,
                    "panels.sudo.list_item",
                    index=index,
                    title=row.title or str(row.chat_id),
                    chat_id=row.chat_id,
                    status=_value(row.status),
                    credit_days=row.credit_days,
                )
            )
            lines.append("")
        await deliver_ask_outcome(
            client,
            query.message.chat.id,
            query.from_user.id,
            "\n".join(lines),
            InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            row.title or str(row.chat_id),
                            callback_data=(
                                f"{CB['SUDO_GRP_DETAIL_PREFIX']}"
                                f"{'active' if row.status == 'active' else 'inactive'}:{row.chat_id}:0"
                            ),
                        )
                    ]
                    for row in rows[:5]
                ]
                + [[InlineKeyboardButton(t(_LANG, "common.buttons.back"), callback_data=CB["SUDO_GROUPS"])]]
            ),
            query_message=query.message,
        )

    @bot.on_callback_query(filters.regex(f"^{CB['SUDO_CREDIT']}$") & _pm_sudo)
    @sudo_or_above
    async def sudo_credit(client: Client, query: CallbackQuery):
        if not await _require_credit_permission(query):
            return
        await query.answer()
        await _render_sudo_credit_menu(client, query)

    @bot.on_callback_query(filters.regex(f"^{CB['SUDO_CREDIT_ADD']}$") & _pm_sudo)
    @sudo_or_above
    async def sudo_credit_add(client: Client, query: CallbackQuery):
        from app.utils.bot_guards import is_developer
        if not is_developer(query.from_user.id):
            await query.answer(t(_LANG, "credit.developer_only"), show_alert=True)
            return
        await query.answer()
        await _handle_credit_prompt(client, query, "increase")

    @bot.on_callback_query(filters.regex(f"^{CB['SUDO_CREDIT_DEDUCT']}$") & _pm_sudo)
    @sudo_or_above
    async def sudo_credit_deduct(client: Client, query: CallbackQuery):
        from app.utils.bot_guards import is_developer
        if not is_developer(query.from_user.id):
            await query.answer(t(_LANG, "credit.developer_only"), show_alert=True)
            return
        await query.answer()
        await _handle_credit_prompt(client, query, "decrease")

    @bot.on_callback_query(filters.regex(r"^sudo:grp:ci:[a-z_]+:-?\d+:\d+$") & _pm_sudo)
    @sudo_or_above
    async def sudo_group_credit_inc(client: Client, query: CallbackQuery):
        from app.utils.bot_guards import is_developer
        if not is_developer(query.from_user.id):
            await query.answer(t(_LANG, "credit.developer_only"), show_alert=True)
            return
        await query.answer()
        payload = _parse_group_payload(query.data, CB["SUDO_GRP_CREDIT_INC_PREFIX"])
        if payload is None:
            await query.answer(t(_LANG, "panels.sudo.invalid_payload"), show_alert=True)
            return
        await _handle_detail_credit(client, query, payload, "increase")

    @bot.on_callback_query(filters.regex(r"^sudo:grp:cd:[a-z_]+:-?\d+:\d+$") & _pm_sudo)
    @sudo_or_above
    async def sudo_group_credit_dec(client: Client, query: CallbackQuery):
        from app.utils.bot_guards import is_developer
        if not is_developer(query.from_user.id):
            await query.answer(t(_LANG, "credit.developer_only"), show_alert=True)
            return
        await query.answer()
        payload = _parse_group_payload(query.data, CB["SUDO_GRP_CREDIT_DEC_PREFIX"])
        if payload is None:
            await query.answer(t(_LANG, "panels.sudo.invalid_payload"), show_alert=True)
            return
        await _handle_detail_credit(client, query, payload, "decrease")

    @bot.on_callback_query(filters.regex(f"^{CB['SUDO_CREDIT_LIST']}$") & _pm_sudo)
    @sudo_or_above
    async def sudo_credit_list(client: Client, query: CallbackQuery):
        if not await _require_credit_permission(query):
            return
        await query.answer()
        await _render_sudo_group_list(client, query, "active", 0)

    @bot.on_callback_query(filters.regex(f"^{CB['SUDO_CREDIT_NO_CREDIT']}$") & _pm_sudo)
    @sudo_or_above
    async def sudo_credit_no_credit(client: Client, query: CallbackQuery):
        if not await _require_credit_permission(query):
            return
        await query.answer()
        await _render_sudo_group_list(client, query, "no_credit", 0)

    @bot.on_callback_query(filters.regex(f"^{CB['SUDO_CREDIT_RENEWAL']}$") & _pm_sudo)
    @sudo_or_above
    async def sudo_credit_renewal(client: Client, query: CallbackQuery):
        if not await _require_credit_permission(query):
            return
        await query.answer()
        await _render_sudo_group_list(client, query, "renewal", 0)

    @bot.on_callback_query(filters.regex(f"^{CB['SUDO_CREDIT_HISTORY']}$") & _pm_sudo)
    @sudo_or_above
    async def sudo_credit_history(client: Client, query: CallbackQuery):
        if not await _require_credit_permission(query):
            return
        await query.answer()
        rows = await _fetch_sudo_credit_history(query.from_user.id)
        if not rows:
            await panel_callback_edit(
                client,
                query,
                t(_LANG, "panels.sudo.empty"),
                _sudo_done_kb(CB["SUDO_CREDIT"]),
                answer=False,
            )
            return
        lines = [t(_LANG, "panels.sudo.credit_history_title"), ""]
        for index, row in enumerate(rows, start=1):
            lines.append(
                t(
                    _LANG,
                    "panels.sudo.credit_history_item",
                    index=index,
                    operation=row.operation,
                    amount=row.amount_days,
                    chat_id=row.chat_id,
                    date=_format_dt(row.operated_at),
                )
            )
        await panel_callback_edit(client, query, "\n".join(lines), _sudo_done_kb(CB["SUDO_CREDIT"]), answer=False)

    @bot.on_callback_query(filters.regex(f"^{CB['SUDO_LISTS']}$") & _pm_sudo)
    @sudo_or_above
    async def sudo_lists(client: Client, query: CallbackQuery):
        await query.answer()
        counts = await _fetch_sudo_group_counts(query.from_user.id)
        total_installs = await _fetch_sudo_total_installs(query.from_user.id)
        text = "\n\n".join(
            [
                t(_LANG, "panels.sudo.lists_title"),
                t(_LANG, "panels.sudo.lists_body", total_installs=total_installs, **counts),
            ]
        )
        await panel_callback_edit(client, query, text, _sudo_done_kb(CB["WZ_HOME"]), answer=False)

    @bot.on_callback_query(filters.regex(r"^sudo:grp:lv:[a-z_]+:-?\d+:\d+$") & _pm_sudo)
    @sudo_or_above
    async def sudo_group_leave_confirm(client: Client, query: CallbackQuery):
        if not await _require_remove_bot_permission(query):
            return
        await query.answer()
        payload = _parse_group_payload(query.data, CB["SUDO_GRP_LEAVE_CONFIRM_PREFIX"])
        if payload is None:
            await query.answer(t(_LANG, "panels.sudo.invalid_payload"), show_alert=True)
            return
        kind, chat_id, page = payload
        row = await _get_sudo_group_row(query.from_user.id, chat_id)
        if row is None:
            await query.answer(t(_LANG, "panels.sudo.not_in_scope"), show_alert=True)
            return
        text = t(
            _LANG,
            "panels.sudo.leave_confirm",
            title=row.title or str(row.chat_id),
            chat_id=row.chat_id,
        )
        await panel_callback_edit(
            client,
            query,
            text,
            _sudo_leave_group_confirm_kb(kind, chat_id, page, query.from_user.id),
            answer=False,
        )

    @bot.on_callback_query(filters.regex(r"^sudo:grp:lv:do:[a-z_]+:-?\d+:\d+:\d+:\d+$") & _pm_sudo)
    @sudo_or_above
    async def sudo_group_leave_exec(client: Client, query: CallbackQuery):
        if not await _require_remove_bot_permission(query):
            return
        await query.answer()
        payload = _parse_leave_payload(query.data, CB["SUDO_GRP_LEAVE_EXEC_PREFIX"])
        if payload is None:
            await query.answer(t(_LANG, "panels.sudo.invalid_payload"), show_alert=True)
            return
        kind, chat_id, page, actor_id, issued_at = payload
        if actor_id != query.from_user.id or int(time.time()) - issued_at > _SUDO_CONFIRM_TTL:
            await query.answer(t(_LANG, "common.errors.invalid_callback"), show_alert=True)
            return
        row = await _get_sudo_group_row(query.from_user.id, chat_id)
        if row is None:
            await query.answer(t(_LANG, "panels.sudo.not_in_scope"), show_alert=True)
            return
        try:
            await CallService.leave_voice_chat(call_py, chat_id)
        except Exception:
            logger.debug("sudo single leave voice-chat cleanup failed chat_id=%s", chat_id, exc_info=True)
        try:
            await client.leave_chat(chat_id)
        except Exception:
            logger.exception("sudo single leave Telegram leave_chat failed chat_id=%s", chat_id)
            await panel_callback_edit(
                client,
                query,
                t(_LANG, "panels.sudo.telegram_action_failed"),
                _sudo_done_kb(f"{CB['SUDO_GRP_DETAIL_PREFIX']}{kind}:{chat_id}:{page}"),
                answer=False,
            )
            return
        await group_repo.deactivate_group(chat_id)
        await panel_callback_edit(
            client,
            query,
            t(_LANG, "panels.sudo.leave_done", title=row.title or str(row.chat_id)),
            _sudo_done_kb(f"{CB['SUDO_GRP_LIST_PREFIX']}{kind}:{page}"),
            answer=False,
        )

    @bot.on_callback_query(filters.regex(r"^sudo:grp:lv:no:[a-z_]+:-?\d+:\d+:\d+:\d+$") & _pm_sudo)
    @sudo_or_above
    async def sudo_group_leave_cancel(client: Client, query: CallbackQuery):
        payload = _parse_leave_payload(query.data, CB["SUDO_GRP_LEAVE_CANCEL_PREFIX"])
        if payload is None:
            await query.answer(t(_LANG, "panels.sudo.invalid_payload"), show_alert=True)
            return
        kind, chat_id, page, actor_id, issued_at = payload
        if actor_id != query.from_user.id or int(time.time()) - issued_at > _SUDO_CONFIRM_TTL:
            await query.answer(t(_LANG, "common.errors.invalid_callback"), show_alert=True)
            return
        await query.answer(t(_LANG, "common.cancelled"), show_alert=False)
        await _render_sudo_group_detail(client, query, kind, chat_id, page)

    # ── Legacy stale-button routes ────────────────────────────────────────
    @bot.on_callback_query(filters.regex(f"^{CB['SUDO_INSTALLS_REPORT']}$") & _pm_sudo)
    @sudo_or_above
    async def sudo_installs_report(client: Client, query: CallbackQuery):
        await query.answer()
        user_id = query.from_user.id
        logs = await log_repo.get_install_logs(sudo_id=user_id, limit=50)
        if not logs:
            await query.answer(t(_LANG, "filter_mgmt.list_empty"), show_alert=True)
            return

        groups_count = sum(1 for lg in logs if lg.chat_type == "group")
        channels_count = sum(1 for lg in logs if lg.chat_type == "channel")
        text = t(
            _LANG,
            "reports.install_report",
            groups=groups_count,
            channels=channels_count,
            total=len(logs),
        )
        await query.message.edit_text(text, reply_markup=await _sudo_panel_kb(user_id))

    @bot.on_callback_query(filters.regex(f"^{CB['SUDO_CREDIT_REPORT']}$") & _pm_sudo)
    @sudo_or_above
    async def sudo_credit_report(client: Client, query: CallbackQuery):
        await query.answer()
        user_id = query.from_user.id
        logs = await log_repo.get_install_logs(sudo_id=user_id, limit=100)
        chat_ids = {lg.chat_id for lg in logs}

        ids = list(chat_ids)[:30]
        credits = await credit_repo.get_credits_by_chat_ids(ids)
        lines = []
        for cid in ids:
            cr = credits.get(cid)
            if cr:
                lines.append(
                    t(_LANG, "reports.credit_report", chat_title=str(cid), days=cr.credit_days)
                )

        if not lines:
            await query.answer(t(_LANG, "filter_mgmt.list_empty"), show_alert=True)
            return
        await query.message.edit_text("\n".join(lines), reply_markup=await _sudo_panel_kb(user_id))

    @bot.on_callback_query(filters.regex(f"^{CB['SUDO_STATS']}$") & _pm_sudo)
    @sudo_or_above
    async def sudo_stats(client: Client, query: CallbackQuery):
        await query.answer()
        await render_sudo_home_panel(client, query)

    @bot.on_callback_query(filters.regex(f"^{CB['SUDO_LEAVE_INSTALLS']}$") & _pm_sudo)
    @sudo_or_above
    async def sudo_leave_installs(client: Client, query: CallbackQuery):
        if not await _require_remove_bot_permission(query):
            return
        await query.answer()
        user_id = query.from_user.id
        await _set_sudo_leave_confirmation(user_id)
        await query.message.edit_text(
            t(_LANG, "sudo_mgmt.leave_confirm"),
            reply_markup=KeyboardFactory.sudo_leave_confirm(_LANG, user_id),
        )

    @bot.on_callback_query(filters.regex(r"^sudo:leave_installs:confirm:\d+$") & _pm_sudo)
    @sudo_or_above
    async def sudo_leave_installs_confirm(client: Client, query: CallbackQuery):
        if not await _require_remove_bot_permission(query):
            return
        bound_user_id = _parse_bound_user_id(query.data, CB["SUDO_LEAVE_CONFIRM_PREFIX"])
        if bound_user_id != query.from_user.id:
            await query.answer(t(_LANG, "common.errors.no_access"), show_alert=True)
            return
        if not await _pop_sudo_leave_confirmation(query.from_user.id):
            await query.answer(t(_LANG, "common.errors.invalid_callback"), show_alert=True)
            return
        await query.answer()
        await _execute_sudo_leave_installs(client, call_py, query)

    @bot.on_callback_query(filters.regex(r"^sudo:leave_installs:cancel:\d+$") & _pm_sudo)
    @sudo_or_above
    async def sudo_leave_installs_cancel(client: Client, query: CallbackQuery):
        bound_user_id = _parse_bound_user_id(query.data, CB["SUDO_LEAVE_CANCEL_PREFIX"])
        if bound_user_id != query.from_user.id:
            await query.answer(t(_LANG, "common.errors.no_access"), show_alert=True)
            return
        await _clear_sudo_leave_confirmation(query.from_user.id)
        await query.answer(t(_LANG, "common.cancelled"), show_alert=False)
        await query.message.edit_text(
            t(_LANG, "sudo_mgmt.leave_cancelled"),
            reply_markup=await _sudo_panel_kb(query.from_user.id),
        )

    @bot.on_callback_query(filters.regex(f"^{CB['SUDO_LOW_CREDIT']}$") & _pm_sudo)
    @sudo_or_above
    async def sudo_low_credit(client: Client, query: CallbackQuery):
        await query.answer()
        user_id = query.from_user.id
        logs = await log_repo.get_install_logs(sudo_id=user_id, limit=500)
        chat_ids = {lg.chat_id for lg in logs if lg.action == "install"}

        ids = list(chat_ids)[:50]
        credits = await credit_repo.get_credits_by_chat_ids(ids)
        low_credit_lines = []
        for cid in ids:
            cr = credits.get(cid)
            if cr and cr.credit_days <= 3:
                low_credit_lines.append(
                    t(_LANG, "reports.credit_report", chat_title=str(cid), days=cr.credit_days)
                )

        if not low_credit_lines:
            await query.answer(t(_LANG, "filter_mgmt.list_empty"), show_alert=True)
            return
        await query.message.edit_text(
            "\n".join(low_credit_lines),
            reply_markup=await _sudo_panel_kb(user_id),
        )

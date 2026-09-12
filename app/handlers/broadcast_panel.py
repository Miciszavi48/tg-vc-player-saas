from __future__ import annotations

import logging
import re
import time

from pyrogram import Client, filters
from pyrogram.types import CallbackQuery, Message
from pyromod.exceptions import ListenerStopped

from app.repositories import broadcast_repo
from app.services.broadcast_service_v2 import BroadcastServiceV2
from app.services.panel_message_service import panel_callback_edit
from app.services.wizard_ui import TOKEN_DEV_BROADCAST, build_cancel_kb, build_done_kb
from app.utils.ask_result import (
    deliver_ask_outcome,
    prompt_for_panel_input,
    safe_delete_user_input,
    safe_stop_listening,
)
from app.utils.decorators import developer_only
from app.utils.filters import dev_filter, private_chat_filter
from app.utils.i18n import AUTO_LANG, label, t
from app.utils.ui import CB, KeyboardFactory

logger = logging.getLogger(__name__)

_LANG = AUTO_LANG
_TIMEOUT = 120
_pm_dev = dev_filter() & private_chat_filter()
_BC_ID_RE = r"[1-9]\d*"
_BC_CANCEL_REQUEST_RE = rf"^{re.escape(CB['BC_CANCEL'])}:{_BC_ID_RE}$"
_BC_CANCEL_CONFIRM_RE = rf"^{re.escape(CB['BC_CANCEL_CONFIRM_PREFIX'])}{_BC_ID_RE}:{_BC_ID_RE}:{_BC_ID_RE}$"
_BC_CANCEL_ABORT_RE = rf"^{re.escape(CB['BC_CANCEL_ABORT_PREFIX'])}{_BC_ID_RE}:{_BC_ID_RE}:{_BC_ID_RE}$"
_BC_CANCEL_VALID_TAIL_RE = rf"(?:{_BC_ID_RE}|confirm:{_BC_ID_RE}:{_BC_ID_RE}:{_BC_ID_RE}|abort:{_BC_ID_RE}:{_BC_ID_RE}:{_BC_ID_RE})"
_CANCELABLE_STATUSES = {"pending", "running"}
_BC_CANCEL_CONFIRM_TTL_SECONDS = 300


def _parse_positive_int(value: str) -> int | None:
    value = value.strip()
    if not value.isdigit():
        return None
    parsed = int(value)
    return parsed if parsed > 0 else None


def _parse_broadcast_id(data: str, prefix: str) -> int | None:
    if not data.startswith(prefix):
        return None
    return _parse_positive_int(data[len(prefix):])


def _parse_bound_cancel(data: str, prefix: str) -> tuple[int, int, int] | None:
    if not data.startswith(prefix):
        return None
    parts = data[len(prefix):].split(":")
    if len(parts) != 3:
        return None
    bc_id = _parse_positive_int(parts[0])
    user_id = _parse_positive_int(parts[1])
    issued_at = _parse_positive_int(parts[2])
    if bc_id is None or user_id is None or issued_at is None:
        return None
    return bc_id, user_id, issued_at


def _is_cancelable(bc) -> bool:
    return getattr(bc, "status", None) in _CANCELABLE_STATUSES


def _is_stale_issued_at(issued_at: int) -> bool:
    return int(time.time()) - issued_at > _BC_CANCEL_CONFIRM_TTL_SECONDS


async def _ask(client: Client, chat_id: int, key: str, lang: str = AUTO_LANG) -> Message | None:
    stopped = await safe_stop_listening(client, chat_id)
    logger.debug(
        "broadcast panel ask start key=%s chat_id=%s stop_listening=%s",
        key,
        chat_id,
        stopped,
    )
    try:
        resp = await prompt_for_panel_input(
            client,
            chat_id,
            chat_id,
            t(lang, key),
            build_cancel_kb(lang, TOKEN_DEV_BROADCAST),
            timeout=_TIMEOUT,
        )
        if resp and resp.text and resp.text.strip().lower() in ("/cancel", "cancel", "لغو"):
            await safe_delete_user_input(resp)
            await deliver_ask_outcome(
                client,
                chat_id,
                chat_id,
                t(lang, "common.cancelled"),
                build_done_kb(lang, TOKEN_DEV_BROADCAST),
            )
            return None
        return resp
    except ListenerStopped:
        logger.debug("broadcast panel ask listener stopped key=%s chat_id=%s", key, chat_id)
        return None
    except Exception as exc:
        logger.debug(
            "broadcast panel ask failed key=%s chat_id=%s reason=%s",
            key,
            chat_id,
            type(exc).__name__,
        )
        await deliver_ask_outcome(
            client,
            chat_id,
            chat_id,
            t(lang, "ask.timeout"),
            build_done_kb(lang, TOKEN_DEV_BROADCAST),
        )
        return None


async def render_broadcast_history_panel(client: Client, query: CallbackQuery) -> bool:
    """Render broadcast history via panel_callback_edit (dev shortcut + bc:history)."""
    broadcasts = await broadcast_repo.get_recent(20)
    return await panel_callback_edit(
        client,
        query,
        t(_LANG, "admin.bc.history_title"),
        KeyboardFactory.broadcast_history(_LANG, broadcasts),
    )


def register(bot: Client, call_py) -> None:  # noqa: ARG001

    @bot.on_callback_query(filters.regex(f"^{CB['BC_HISTORY']}$") & _pm_dev)
    @developer_only
    async def bc_history(client: Client, query: CallbackQuery):
        await render_broadcast_history_panel(client, query)

    @bot.on_callback_query(filters.regex(rf"^{re.escape(CB['BC_DETAIL'])}:\d+$") & _pm_dev)
    @developer_only
    async def bc_detail(client: Client, query: CallbackQuery):
        bc_id_str = query.data.split(":", 2)[2]
        try:
            bc_id = int(bc_id_str)
        except (ValueError, IndexError):
            await query.answer(t(_LANG, "common.errors.try_later"), show_alert=True)
            return
        bc = await broadcast_repo.get_by_id(bc_id)
        if bc is None:
            await query.answer(t(_LANG, "common.errors.try_later"), show_alert=True)
            return
        text = t(
            _LANG, "admin.bc.detail_title",
            id=bc.id,
            mode=label(_LANG, "broadcast_mode", bc.mode),
            scope=label(_LANG, "broadcast_scope", bc.target_scope),
            status=label(_LANG, "broadcast_status", bc.status),
            sent=bc.sent_count,
            fail=bc.fail_count,
            total=bc.total_recipients,
        )
        await query.message.edit_text(
            text,
            reply_markup=KeyboardFactory.broadcast_detail(_LANG, bc),
        )

    @bot.on_callback_query(filters.regex(rf"^{re.escape(CB['BC_DETAIL'])}:(?!\d+$)") & _pm_dev)
    @developer_only
    async def bc_detail_invalid(client: Client, query: CallbackQuery):
        await query.answer(t(_LANG, "common.errors.try_later"), show_alert=True)

    @bot.on_callback_query(filters.regex(_BC_CANCEL_REQUEST_RE) & _pm_dev)
    @developer_only
    async def bc_cancel(client: Client, query: CallbackQuery):
        bc_id = _parse_broadcast_id(query.data, f"{CB['BC_CANCEL']}:")
        if bc_id is None:
            await query.answer(t(_LANG, "common.errors.invalid_callback"), show_alert=True)
            return
        bc = await broadcast_repo.get_by_id(bc_id)
        if bc is None:
            await query.answer(t(_LANG, "common.errors.invalid_callback"), show_alert=True)
            return
        if not _is_cancelable(bc):
            await query.answer(t(_LANG, "admin.bc.cancel_not_allowed"), show_alert=True)
            return
        await query.answer()
        await query.message.edit_text(
            t(_LANG, "admin.bc.cancel_confirm_prompt", id=bc_id),
            reply_markup=KeyboardFactory.broadcast_cancel_confirm(
                _LANG,
                bc_id,
                query.from_user.id,
                int(time.time()),
            ),
        )

    @bot.on_callback_query(filters.regex(_BC_CANCEL_CONFIRM_RE) & _pm_dev)
    @developer_only
    async def bc_cancel_confirm(client: Client, query: CallbackQuery):
        parsed = _parse_bound_cancel(query.data, CB["BC_CANCEL_CONFIRM_PREFIX"])
        if parsed is None:
            await query.answer(t(_LANG, "common.errors.invalid_callback"), show_alert=True)
            return
        bc_id, bound_user_id, issued_at = parsed
        if query.from_user.id != bound_user_id:
            await query.answer(t(_LANG, "common.errors.no_access"), show_alert=True)
            return
        if _is_stale_issued_at(issued_at):
            await query.answer(t(_LANG, "common.errors.invalid_callback"), show_alert=True)
            return
        bc = await broadcast_repo.get_by_id(bc_id)
        if bc is None:
            await query.answer(t(_LANG, "common.errors.invalid_callback"), show_alert=True)
            return
        if not _is_cancelable(bc):
            await query.answer(t(_LANG, "admin.bc.cancel_not_allowed"), show_alert=True)
            return
        await BroadcastServiceV2.cancel(bc_id)
        await query.answer(t(_LANG, "admin.bc.cancel_success"), show_alert=True)
        broadcasts = await broadcast_repo.get_recent(20)
        await query.message.edit_text(
            t(_LANG, "admin.bc.history_title"),
            reply_markup=KeyboardFactory.broadcast_history(_LANG, broadcasts),
        )

    @bot.on_callback_query(filters.regex(_BC_CANCEL_ABORT_RE) & _pm_dev)
    @developer_only
    async def bc_cancel_abort(client: Client, query: CallbackQuery):
        parsed = _parse_bound_cancel(query.data, CB["BC_CANCEL_ABORT_PREFIX"])
        if parsed is None:
            await query.answer(t(_LANG, "common.errors.invalid_callback"), show_alert=True)
            return
        _bc_id, bound_user_id, issued_at = parsed
        if query.from_user.id != bound_user_id:
            await query.answer(t(_LANG, "common.errors.no_access"), show_alert=True)
            return
        if _is_stale_issued_at(issued_at):
            await query.answer(t(_LANG, "common.errors.invalid_callback"), show_alert=True)
            return
        await query.answer(t(_LANG, "common.cancelled"), show_alert=False)
        await query.message.edit_text(
            t(_LANG, "admin.bc.history_title"),
            reply_markup=KeyboardFactory.back_button(_LANG),
        )

    @bot.on_callback_query(filters.regex(rf"^{re.escape(CB['BC_CANCEL'])}:(?!{_BC_CANCEL_VALID_TAIL_RE}$).*") & _pm_dev)
    @developer_only
    async def bc_cancel_invalid(client: Client, query: CallbackQuery):
        await query.answer(t(_LANG, "common.errors.invalid_callback"), show_alert=True)

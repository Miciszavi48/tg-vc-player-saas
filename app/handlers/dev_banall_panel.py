"""Developer Panel: global user ban (ban-all / بن آل)."""

from __future__ import annotations

import asyncio
import logging
import math
import re
import time

from pyrogram import Client, filters
from pyrogram.types import CallbackQuery

from app.handlers.dev_panel import (
    _ask,
    _is_stale_dev_confirm_token,
    _send_done,
)
from app.utils.ask_result import AskResult, notify_ask_abort
from app.repositories import global_ban_repo
from app.repositories.global_ban_repo import GlobalBanValidationError
from app.services.global_ban_service import RemovalSummary, remove_user_from_installed_chats
from app.services.wizard_ui import TOKEN_DEV_MODERATION, build_done_kb
from app.utils.decorators import developer_only
from app.utils.diagnostic_logging import create_logged_task
from app.utils.filters import dev_filter, private_chat_filter
from app.utils.helpers import parse_user_id
from app.utils.i18n import AUTO_LANG, t
from app.utils.ui import CB, KeyboardFactory

logger = logging.getLogger(__name__)

_LANG = AUTO_LANG
_pm_dev = dev_filter() & private_chat_filter()
_PAGE_SIZE = 10

_LIST_PAGE_RE = re.compile(rf"^{re.escape(CB['DEV_BANALL_LIST_PREFIX'])}(\d+)$")
_RM_RE = re.compile(rf"^{re.escape(CB['DEV_BANALL_RM_PREFIX'])}(\d+):(\d+)$")
_CLEAR_DO_RE = re.compile(
    rf"^{re.escape(CB['DEV_BANALL_CLEAR_DO_PREFIX'])}(\d+):(\d+)$"
)
_CLEAR_NO_RE = re.compile(
    rf"^{re.escape(CB['DEV_BANALL_CLEAR_NO_PREFIX'])}(\d+):(\d+)$"
)


def _format_removal_summary(summary: RemovalSummary) -> str:
    return t(
        _LANG,
        "global_ban.removal_summary",
        attempted=summary.attempted,
        removed=summary.removed,
        failed=summary.failed,
        skipped=summary.skipped,
    )


def _format_ban_list_text(bans: list, *, page: int, total: int) -> str:
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


async def _show_banall_list(query: CallbackQuery, page: int) -> None:
    bans, total = await global_ban_repo.list_global_bans(page=page, limit=_PAGE_SIZE)
    total_pages = max(1, math.ceil(total / _PAGE_SIZE)) if total else 1
    await query.message.edit_text(
        _format_ban_list_text(bans, page=page, total=total),
        reply_markup=KeyboardFactory.dev_banall_list(
            _LANG, bans, page=page, total_pages=total_pages,
        ),
    )


async def _run_removal_and_notify(
    client: Client,
    dev_chat_id: int,
    user_id: int,
) -> None:
    try:
        summary = await remove_user_from_installed_chats(client, user_id)
        await client.send_message(
            dev_chat_id,
            _format_removal_summary(summary),
            reply_markup=build_done_kb(_LANG, TOKEN_DEV_MODERATION),
        )
    except Exception:
        logger.exception("Global ban removal sweep failed for user %s", user_id)
        await client.send_message(
            dev_chat_id,
            t(_LANG, "global_ban.removal_failed"),
            reply_markup=build_done_kb(_LANG, TOKEN_DEV_MODERATION),
        )


def register(bot: Client, call_py) -> None:  # noqa: ARG001
    if getattr(bot, "_dev_banall_registered", False):
        return
    setattr(bot, "_dev_banall_registered", True)

    # Identity-only filter; role via @developer_only, scope/staleness answered
    # explicitly (deny-capable filters silently skip panel roots on stale taps).
    @bot.on_callback_query(filters.regex(f"^{CB['DEV_BANALL_HOME']}$"))
    @developer_only
    async def dev_banall_home(client: Client, query: CallbackQuery):  # noqa: ARG001
        chat = query.message.chat if query.message else None
        if chat is None or getattr(chat.type, "value", chat.type) != "private":
            await query.answer(
                t(_LANG, "common.errors.unknown_callback"), show_alert=True
            )
            return
        await query.answer()
        await query.message.edit_text(
            t(_LANG, "global_ban.home_title"),
            reply_markup=KeyboardFactory.dev_banall_home(_LANG),
        )

    @bot.on_callback_query(filters.regex(f"^{CB['DEV_BANALL_ADD']}$") & _pm_dev)
    @developer_only
    async def dev_banall_add(client: Client, query: CallbackQuery):
        await query.answer()
        chat_id = query.message.chat.id
        resp = await _ask(
            client,
            chat_id,
            "global_ban.prompt_user_id",
            lang=_LANG,
            user_id=query.from_user.id,
            return_to=TOKEN_DEV_MODERATION,
        )
        if await notify_ask_abort(client, chat_id, resp, return_to=TOKEN_DEV_MODERATION, lang=_LANG):
            return

        user_id = parse_user_id((resp.message.text or "").strip())
        if user_id is None or user_id <= 0:
            await _send_done(client, chat_id, t(_LANG, "global_ban.invalid_user_id"), TOKEN_DEV_MODERATION)
            return

        try:
            _ban, created_new = await global_ban_repo.add_global_ban(
                user_id,
                created_by=query.from_user.id,
            )
        except GlobalBanValidationError as exc:
            code = exc.args[0] if exc.args else str(exc)
            key = "global_ban.cannot_ban_developer" if code == "cannot_ban_developer" else "global_ban.invalid_user_id"
            await _send_done(client, chat_id, t(_LANG, key), TOKEN_DEV_MODERATION)
            return

        if not created_new:
            await _send_done(client, chat_id, t(_LANG, "global_ban.already_banned", user_id=user_id), TOKEN_DEV_MODERATION)
            return

        await _send_done(
            client,
            chat_id,
            t(_LANG, "global_ban.added_removal_started", user_id=user_id),
            TOKEN_DEV_MODERATION,
        )
        create_logged_task(
            _run_removal_and_notify(client, chat_id, user_id),
            name=f"banall_removal_{user_id}",
        )

    @bot.on_callback_query(filters.regex(f"^{CB['DEV_BANALL_REMOVE']}$") & _pm_dev)
    @developer_only
    async def dev_banall_remove(client: Client, query: CallbackQuery):
        await query.answer()
        chat_id = query.message.chat.id
        resp = await _ask(
            client,
            chat_id,
            "global_ban.prompt_remove_user_id",
            lang=_LANG,
            user_id=query.from_user.id,
            return_to=TOKEN_DEV_MODERATION,
        )
        if await notify_ask_abort(client, chat_id, resp, return_to=TOKEN_DEV_MODERATION, lang=_LANG):
            return

        user_id = parse_user_id((resp.message.text or "").strip())
        if user_id is None or user_id <= 0:
            await _send_done(client, chat_id, t(_LANG, "global_ban.invalid_user_id"), TOKEN_DEV_MODERATION)
            return

        removed = await global_ban_repo.remove_global_ban(user_id)
        if not removed:
            await _send_done(client, chat_id, t(_LANG, "global_ban.not_found", user_id=user_id), TOKEN_DEV_MODERATION)
            return

        await _send_done(client, chat_id, t(_LANG, "global_ban.removed", user_id=user_id), TOKEN_DEV_MODERATION)

    @bot.on_callback_query(filters.regex(_LIST_PAGE_RE) & _pm_dev)
    @developer_only
    async def dev_banall_list(client: Client, query: CallbackQuery):  # noqa: ARG001
        await query.answer()
        match = _LIST_PAGE_RE.match(query.data or "")
        if match is None:
            return
        page = int(match.group(1))
        await _show_banall_list(query, page)

    @bot.on_callback_query(filters.regex(_RM_RE) & _pm_dev)
    @developer_only
    async def dev_banall_row_remove(client: Client, query: CallbackQuery):
        match = _RM_RE.match(query.data or "")
        if match is None:
            await query.answer(t(_LANG, "common.errors.invalid_callback"), show_alert=True)
            return
        user_id = int(match.group(1))
        page = int(match.group(2))
        removed = await global_ban_repo.remove_global_ban(user_id)
        if removed:
            await query.answer(t(_LANG, "global_ban.removed", user_id=user_id), show_alert=True)
        else:
            await query.answer(t(_LANG, "global_ban.not_found", user_id=user_id), show_alert=True)
        await _show_banall_list(query, page)

    @bot.on_callback_query(filters.regex(f"^{CB['DEV_BANALL_CLEAR']}$") & _pm_dev)
    @developer_only
    async def dev_banall_clear(client: Client, query: CallbackQuery):  # noqa: ARG001
        await query.answer()
        issued_at = int(time.time())
        await query.message.edit_text(
            t(_LANG, "global_ban.clear_confirm"),
            reply_markup=KeyboardFactory.dev_banall_clear_confirm(
                _LANG, query.from_user.id, issued_at,
            ),
        )

    @bot.on_callback_query(filters.regex(_CLEAR_DO_RE) & _pm_dev)
    @developer_only
    async def dev_banall_clear_confirm(client: Client, query: CallbackQuery):  # noqa: ARG001
        match = _CLEAR_DO_RE.match(query.data or "")
        if match is None:
            await query.answer(t(_LANG, "common.errors.invalid_callback"), show_alert=True)
            return
        actor_id = int(match.group(1))
        issued_at = int(match.group(2))
        if query.from_user.id != actor_id:
            await query.answer(t(_LANG, "common.errors.no_access"), show_alert=True)
            return
        if _is_stale_dev_confirm_token(issued_at):
            await query.answer(t(_LANG, "global_ban.stale_confirm"), show_alert=True)
            return
        count = await global_ban_repo.clear_global_bans()
        await query.message.edit_text(
            t(_LANG, "global_ban.cleared", count=count),
            reply_markup=KeyboardFactory.dev_banall_home(_LANG),
        )

    @bot.on_callback_query(filters.regex(_CLEAR_NO_RE) & _pm_dev)
    @developer_only
    async def dev_banall_clear_cancel(client: Client, query: CallbackQuery):  # noqa: ARG001
        match = _CLEAR_NO_RE.match(query.data or "")
        if match is None:
            await query.answer(t(_LANG, "common.errors.invalid_callback"), show_alert=True)
            return
        actor_id = int(match.group(1))
        issued_at = int(match.group(2))
        if query.from_user.id != actor_id:
            await query.answer(t(_LANG, "common.errors.no_access"), show_alert=True)
            return
        if _is_stale_dev_confirm_token(issued_at):
            await query.answer(t(_LANG, "global_ban.stale_confirm"), show_alert=True)
            return
        await query.answer(t(_LANG, "global_ban.clear_cancelled"), show_alert=False)
        await query.message.edit_text(
            t(_LANG, "global_ban.home_title"),
            reply_markup=KeyboardFactory.dev_banall_home(_LANG),
        )

    @bot.on_callback_query(filters.regex(r"^dev:banall:clear:(?:do|no):.*") & _pm_dev)
    @developer_only
    async def dev_banall_clear_malformed(client: Client, query: CallbackQuery):  # noqa: ARG001
        if _CLEAR_DO_RE.match(query.data or "") or _CLEAR_NO_RE.match(query.data or ""):
            return
        await query.answer(t(_LANG, "common.errors.invalid_callback"), show_alert=True)

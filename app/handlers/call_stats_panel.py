"""Inline selection panel for scoped voice-call statistics."""

from __future__ import annotations

import logging
import re

from pyrogram import Client, filters
from pyrogram.types import CallbackQuery

from app.handlers.group_text_call_commands import can_manage_call_command_user
from app.handlers.priority import PANEL_CALLBACK_GROUP
from app.repositories import call_stats_settings_repo
from app.services import call_stats_panel_service as panel_svc
from app.services.panel_message_service import panel_callback_edit
from app.utils.callback_trace import mark_route_seen, trace_route
from app.utils.i18n import AUTO_LANG, t
from app.utils.telegram_message import answer_callback_safe
from app.utils.ui import KeyboardFactory

logger = logging.getLogger(__name__)

_LANG = AUTO_LANG

_SEL_RE = re.compile(
    r"^grp:callstats:sel:(?P<scope>all|vip|admin):(?P<period>all|week|today):(?P<chat_id>-?\d+):(?P<user_id>\d+)$"
)
_CLOSE_RE = re.compile(r"^grp:callstats:close:(?P<chat_id>-?\d+):(?P<user_id>\d+)$")


def _mark_callstats_route(query: CallbackQuery, handler_name: str) -> None:
    if getattr(query, "_musicbot_callback_route_seen", False):
        return
    mark_route_seen(query)
    trace_route(
        query,
        handler=handler_name,
        module=__name__,
        group=PANEL_CALLBACK_GROUP,
    )


async def _validate_callback(
    client: Client,
    query: CallbackQuery,
    *,
    chat_id: int,
    user_id: int,
) -> bool:
    """Validate chat/user binding, feature toggle, and manage permission."""
    message = query.message
    if message is None or message.chat is None:
        await answer_callback_safe(query, t(_LANG, "call_stats_panel.invalid_callback"), show_alert=True)
        return False
    raw_chat_type = getattr(message.chat, "type", None)
    chat_type = getattr(raw_chat_type, "value", raw_chat_type)
    if chat_type not in ("group", "supergroup"):
        await answer_callback_safe(query, t(_LANG, "call_stats_panel.invalid_callback"), show_alert=True)
        return False
    if int(message.chat.id) != int(chat_id):
        await answer_callback_safe(query, t(_LANG, "call_stats_panel.invalid_callback"), show_alert=True)
        return False
    if query.from_user is None or int(query.from_user.id) != int(user_id):
        await answer_callback_safe(query, t(_LANG, "call_stats_panel.invalid_callback"), show_alert=True)
        return False
    if not await call_stats_settings_repo.get_enabled(chat_id):
        await answer_callback_safe(query, t(_LANG, "call_stats_panel.feature_disabled"), show_alert=True)
        return False
    if not await can_manage_call_command_user(client, chat_id, user_id):
        await answer_callback_safe(query, t(_LANG, "group_text_call.no_permission"), show_alert=True)
        return False
    return True


def register(bot: Client, call_py) -> None:  # noqa: ARG001
    """Register call-stats panel callback handlers."""

    @bot.on_callback_query(filters.regex(_SEL_RE), group=PANEL_CALLBACK_GROUP)
    async def call_stats_select(client: Client, query: CallbackQuery):
        _mark_callstats_route(query, "call_stats_select")
        match = _SEL_RE.match(query.data or "")
        if match is None:
            await answer_callback_safe(query, t(_LANG, "call_stats_panel.invalid_callback"), show_alert=True)
            return
        chat_id = int(match.group("chat_id"))
        user_id = int(match.group("user_id"))
        if not await _validate_callback(client, query, chat_id=chat_id, user_id=user_id):
            return
        scope = match.group("scope")
        period = match.group("period")
        report = await panel_svc.build_panel_report(
            chat_id,
            scope=scope,  # type: ignore[arg-type]
            period=period,  # type: ignore[arg-type]
            lang=_LANG,
        )
        await panel_callback_edit(
            client,
            query,
            report,
            reply_markup=KeyboardFactory.call_stats_selection_menu(_LANG, chat_id, user_id),
        )

    @bot.on_callback_query(filters.regex(_CLOSE_RE), group=PANEL_CALLBACK_GROUP)
    async def call_stats_close(client: Client, query: CallbackQuery):
        _mark_callstats_route(query, "call_stats_close")
        match = _CLOSE_RE.match(query.data or "")
        if match is None:
            await answer_callback_safe(query, t(_LANG, "call_stats_panel.invalid_callback"), show_alert=True)
            return
        chat_id = int(match.group("chat_id"))
        user_id = int(match.group("user_id"))
        if not await _validate_callback(client, query, chat_id=chat_id, user_id=user_id):
            return
        await answer_callback_safe(query)
        try:
            await query.message.delete()
        except Exception:
            await panel_callback_edit(
                client,
                query,
                t(_LANG, "common.buttons.close"),
                answer=False,
            )

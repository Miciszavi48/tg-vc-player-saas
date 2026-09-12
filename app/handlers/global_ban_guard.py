"""Early runtime guard for globally banned users."""

from __future__ import annotations

import logging

from pyrogram import Client
from pyrogram.types import CallbackQuery, Message

from app.handlers.priority import GLOBAL_BAN_GROUP
from app.utils.bot_guards import deny_if_globally_banned

logger = logging.getLogger(__name__)


def register(bot: Client, call_py) -> None:  # noqa: ARG001
    """Block commands and callbacks from globally banned users."""

    @bot.on_message(group=GLOBAL_BAN_GROUP)
    async def global_ban_message_guard(client: Client, message: Message):
        user = message.from_user
        if user is None:
            return
        if await deny_if_globally_banned(message, user.id):
            if hasattr(message, "stop_propagation"):
                message.stop_propagation()

    @bot.on_callback_query(group=GLOBAL_BAN_GROUP)
    async def global_ban_callback_guard(client: Client, query: CallbackQuery):
        user = query.from_user
        if user is None:
            return
        if await deny_if_globally_banned(query, user.id):
            if hasattr(query, "stop_propagation"):
                query.stop_propagation()

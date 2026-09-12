"""Handler for /addhelper and افزودن هلپر — manually trigger helper join to group."""
from __future__ import annotations

import logging
import re

from pyrogram import Client, filters
from pyrogram.types import Message

from app.repositories import admin_repo
from app.utils.bot_guards import is_developer
from app.utils.filters import group_chat_filter
from app.utils.i18n import AUTO_LANG, t

logger = logging.getLogger(__name__)

_LANG = AUTO_LANG
_grp = group_chat_filter()

_ADD_HELPER_TEXT_CMDS = ["افزودن هلپر"]


def _add_helper_filter():
    text_pattern = "|".join(re.escape(c) for c in _ADD_HELPER_TEXT_CMDS)
    return (
        filters.command("addhelper")
        | filters.regex(rf"^(?:{text_pattern})(?:\s|$)", flags=re.IGNORECASE)
    ) & _grp


async def _has_permission(user_id: int, chat_id: int) -> bool:
    if is_developer(user_id):
        return True
    from app.repositories import user_repo
    if await user_repo.is_owner(user_id):
        return True
    if await user_repo.is_sudo(user_id):
        return True
    return await admin_repo.is_music_admin_or_above(user_id, chat_id)


def register(bot: Client, call_py) -> None:  # noqa: ARG001

    @bot.on_message(_add_helper_filter())
    async def addhelper_command(client: Client, message: Message):  # noqa: ARG001
        user_id = message.from_user.id if message.from_user else 0
        chat_id = message.chat.id

        if not await _has_permission(user_id, chat_id):
            await message.reply(t(_LANG, "admin.helpers.join_group_no_permission"))
            return

        await message.reply(t(_LANG, "admin.helpers.join_group_progress"))

        from app.services.call_service import ensure_helper_present_for_group
        result = await ensure_helper_present_for_group(
            chat_id,
            reason="manual_addhelper",
            bot_client=client,
        )

        result_keys = {
            "success": "admin.helpers.join_group_success",
            "already_present": "admin.helpers.join_group_already_present",
            "unavailable": "admin.helpers.join_group_unavailable",
            "invite_failed": "admin.helpers.join_group_invite_failed",
            "channel_invalid": "admin.helpers.join_group_channel_invalid",
            "bot_unavailable": "admin.helpers.join_group_bot_unavailable",
            "promote_failed": "admin.helpers.join_group_promote_failed",
            "helper_user_unknown": "admin.helpers.join_group_helper_user_unknown",
            "failed": "admin.helpers.join_group_failed",
        }
        key = result_keys.get(result, "admin.helpers.join_group_failed")

        await message.reply(t(_LANG, key))

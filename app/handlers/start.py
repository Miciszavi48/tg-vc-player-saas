from __future__ import annotations

import logging

from pyrogram import Client, filters
from pyrogram.types import Message

from app.handlers.force_join import check_force_join
from app.handlers.priority import PRIVATE_COMMAND_GROUP
from app.repositories import user_repo
from app.services import start_customization_runtime as start_runtime
from app.services.language_service import resolve_lang
from app.services.panel_router import build_private_root_payload, detect_private_role
from app.services.analytics_service import track_event
from app.services.notification_service import NotificationService
from app.services.wizard_ui import clear_runtime_state
from app.utils.bot_guards import deny_if_globally_banned, is_bot_enabled

logger = logging.getLogger(__name__)

def register(bot: Client, call_py) -> None:  # noqa: ARG001
    if getattr(bot, "_start_handler_registered", False):
        return
    setattr(bot, "_start_handler_registered", True)

    @bot.on_message(
        filters.command("start") & filters.private,
        group=PRIVATE_COMMAND_GROUP,
    )
    async def start_handler(client: Client, message: Message):
        user = message.from_user
        if user is None:
            return

        user_id = user.id
        await clear_runtime_state(client, user_id, message.chat.id)
        logger.debug("start command cleared wizard runtime state user_id=%s chat_id=%s", user_id, message.chat.id)

        lang = await resolve_lang(chat_id=message.chat.id, user_id=user_id)

        if await deny_if_globally_banned(message, user_id):
            message.stop_propagation()
            return

        await user_repo.upsert_user(
            user_id=user_id,
            username=user.username,
            first_name=user.first_name,
        )

        passed = await check_force_join(client, message, user_id)
        if not passed:
            message.stop_propagation()
            return

        role = await detect_private_role(user_id)

        await track_event("bot.start", chat_type="private", role=role)

        if role in ("developer", "owner", "sudo"):
            try:
                await NotificationService.notify_bot_start(
                    client,
                    user_id,
                    username=user.username,
                    role=role,
                )
            except Exception:
                logger.debug("notify_bot_start failed for user %s", user_id, exc_info=True)

        if role in ("developer", "owner", "sudo") or await is_bot_enabled():
            await start_runtime.deliver_regular_start(
                client,
                message,
                user_id=user_id,
                first_name=user.first_name or str(user_id),
                lang=lang,
                management_role=role if role in ("developer", "owner", "sudo") else None,
            )
        else:
            text, kb = await build_private_root_payload(
                client,
                user_id,
                user.first_name or str(user_id),
                lang=lang,
                include_welcome=True,
            )
            await message.reply(text, reply_markup=kb)
        message.stop_propagation()

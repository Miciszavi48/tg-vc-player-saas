"""PANEL-04: remove Telegram service messages when a chat opts in.

Telegram emits service messages for joins, leaves, pins, title/photo changes and
video-chat lifecycle events. When `ChatSettings.service_clean_enabled` is on, the
bot deletes them so the group stays clean. The toggle is off by default, so no
existing chat changes behaviour until an admin enables it.
"""

from __future__ import annotations

import logging

from pyrogram import Client, filters
from pyrogram.types import Message

from app.handlers.priority import FILTER_WORDS_GROUP
from app.repositories import settings_repo

logger = logging.getLogger(__name__)

# Service-message attributes Pyrogram/Kurigram sets on Message.
_SERVICE_ATTRIBUTES: tuple[str, ...] = (
    "new_chat_members",
    "left_chat_member",
    "new_chat_title",
    "new_chat_photo",
    "delete_chat_photo",
    "group_chat_created",
    "supergroup_chat_created",
    "channel_chat_created",
    "pinned_message",
    "video_chat_started",
    "video_chat_ended",
    "video_chat_members_invited",
    "video_chat_scheduled",
)


def is_service_message(message: Message) -> bool:
    """True when the message is a Telegram service event, not user content."""
    return any(getattr(message, attr, None) for attr in _SERVICE_ATTRIBUTES)


def service_message_filter():
    """Filter matching only service messages, so user content never matches."""

    async def func(_flt, _client, message: Message) -> bool:
        return is_service_message(message)

    return filters.create(func, name="ServiceMessageFilter")


def register(bot: Client, call_py) -> None:  # noqa: ARG001
    """Register the opt-in service-message cleaner."""

    @bot.on_message(
        filters.group & service_message_filter(),
        group=FILTER_WORDS_GROUP,
    )
    async def service_message_cleaner(client: Client, message: Message):  # noqa: ARG001
        chat_id = getattr(getattr(message, "chat", None), "id", None)
        if chat_id is None:
            message.continue_propagation()
            return

        try:
            chat_settings = await settings_repo.get_chat_settings(int(chat_id))
        except Exception:
            logger.debug("service-clean lookup failed chat_id=%s", chat_id, exc_info=True)
            message.continue_propagation()
            return

        if chat_settings is None or not getattr(
            chat_settings, "service_clean_enabled", False
        ):
            message.continue_propagation()
            return

        try:
            await message.delete()
        except Exception:
            # Missing delete rights or an already-removed message is not an error.
            logger.debug("service-clean delete failed chat_id=%s", chat_id, exc_info=True)
        message.continue_propagation()

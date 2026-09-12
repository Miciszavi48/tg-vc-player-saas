from __future__ import annotations

from pyrogram import filters
from pyrogram.types import CallbackQuery, Message

from app.repositories import blacklist_repo, user_repo
from app.repositories import admin_repo
from app.services import group_runtime_state_service
from app.utils.bot_guards import is_developer
from app.utils.sudo_permissions import can_use_sudo_admin_bypass


def dev_filter():
    async def func(flt, client, update: Message | CallbackQuery):
        user = update.from_user
        if user is None:
            return False
        return is_developer(user.id)
    return filters.create(func, name="DevFilter")


def sudo_filter():
    async def func(flt, client, update: Message | CallbackQuery):
        user = update.from_user
        if user is None:
            return False
        if is_developer(user.id):
            return True
        return await user_repo.is_sudo(user.id)
    return filters.create(func, name="SudoFilter")


def owner_filter():
    async def func(flt, client, update: Message | CallbackQuery):
        user = update.from_user
        if user is None:
            return False
        if is_developer(user.id):
            return True
        return await user_repo.is_owner(user.id)
    return filters.create(func, name="OwnerFilter")


def credit_filter():
    async def func(flt, client, update: Message | CallbackQuery):
        chat = update.chat if isinstance(update, Message) else (
            update.message.chat if update.message else None
        )
        if chat is None:
            return False
        chat_type_value = getattr(getattr(chat, "type", None), "value", None) or getattr(chat, "type", None)
        chat_type = "channel" if str(chat_type_value) == "channel" else "group"
        state = await group_runtime_state_service.get_runtime_credit_state(chat.id, chat_type)
        return state.has_runtime_credit
    return filters.create(func, name="CreditFilter")


def music_admin_filter():
    async def func(flt, client, update: Message | CallbackQuery):
        user = update.from_user
        chat = update.chat if isinstance(update, Message) else (
            update.message.chat if update.message else None
        )
        if user is None or chat is None:
            return False
        from app.utils.player_permissions import can_open_group_settings

        return await can_open_group_settings(client, chat.id, user.id)
    return filters.create(func, name="MusicAdminFilter")


def video_admin_filter():
    async def func(flt, client, update: Message | CallbackQuery):
        user = update.from_user
        chat = update.chat if isinstance(update, Message) else (
            update.message.chat if update.message else None
        )
        if user is None or chat is None:
            return False
        if is_developer(user.id):
            return True
        if await admin_repo.is_video_admin(user.id, chat.id):
            return True
        if await admin_repo.is_player_deputy_or_above(user.id, chat.id):
            return True
        return await can_use_sudo_admin_bypass(user.id)
    return filters.create(func, name="VideoAdminFilter")


def not_blacklisted():
    async def func(flt, client, update: Message | CallbackQuery):
        user = update.from_user
        chat = update.chat if isinstance(update, Message) else (
            update.message.chat if update.message else None
        )
        if user and await blacklist_repo.is_blacklisted(user.id, "user"):
            return False
        if chat and await blacklist_repo.is_blacklisted(chat.id, "group"):
            return False
        return True
    return filters.create(func, name="NotBlacklistedFilter")


def private_chat_filter():
    async def func(flt, client, update: Message | CallbackQuery):
        chat = update.chat if isinstance(update, Message) else (
            update.message.chat if update.message else None
        )
        if chat is None:
            return False
        return chat.type.value == "private"
    return filters.create(func, name="PrivateChatFilter")


def group_chat_filter():
    async def func(flt, client, update: Message | CallbackQuery):
        chat = update.chat if isinstance(update, Message) else (
            update.message.chat if update.message else None
        )
        if chat is None:
            return False
        return chat.type.value in ("group", "supergroup")
    return filters.create(func, name="GroupChatFilter")


def install_chat_filter():
    """Match group, supergroup, and channel chats (installed player contexts)."""

    async def func(flt, client, update: Message | CallbackQuery):
        chat = update.chat if isinstance(update, Message) else (
            update.message.chat if update.message else None
        )
        if chat is None:
            return False
        return chat.type.value in ("group", "supergroup", "channel")

    return filters.create(func, name="InstallChatFilter")

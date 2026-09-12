from __future__ import annotations

import logging
import re

from pyrogram import Client, filters
from pyrogram.types import Message

from app.repositories import admin_repo
from app.utils.decorators import (
    group_music_admin,
    group_video_admin,
    music_admin_or_above,
    player_deputy_or_above,
    player_owner_management,
)
from app.utils.helpers import mention_user
from app.utils.i18n import t

logger = logging.getLogger(__name__)

_LANG = "fa"

_PROMOTE_MUSIC_CMDS = ["ترفیع موزیک", "promotmusic"]
_DEMOTE_MUSIC_CMDS = ["عزل موزیک", "demotemusic"]
_PROMOTE_VIDEO_CMDS = ["ترفیع ویدیو", "promotvideo"]
_DEMOTE_VIDEO_CMDS = ["عزل ویدیو", "demotevideo"]
_SET_CREATOR_CMDS = ["ترفیع مالک", "setcreator"]
_DEL_CREATOR_CMDS = ["عزل مالک", "delcreator"]
_PROMOTE_VIP_CMDS = ["ترفیع ویژه", "ارتقا ویژه پلیر", "promotevip", "SetVip Player"]
_DEMOTE_VIP_CMDS = ["عزل ویژه", "عزل ویژه پلیر", "demotevip", "RemVip Player"]
_CONFIG_MUSIC_CMDS = ["پیکربندی موزیک", "configmusic"]
_DEL_CONFIG_MUSIC_CMDS = ["پاکسازی مدیران موزیک", "delconfigmusic"]
_LIST_MUSIC_CMDS = ["لیست مدیران موزیک", "listmusic"]
_CONFIG_VIDEO_CMDS = ["پیکربندی ویدیو", "configvideo"]
_DEL_CONFIG_VIDEO_CMDS = ["پاکسازی مدیران ویدیو", "delconfigvideo"]
_LIST_VIDEO_CMDS = ["لیست مدیران ویدیو", "listvideo"]


def _build_filter(cmds: list[str]):
    pattern = "|".join(re.escape(c) for c in cmds)
    return filters.regex(rf"^(?:{pattern})(?:\s|$)", flags=re.IGNORECASE)


def _get_target_user(message: Message) -> tuple[int | None, str | None, str | None]:
    """Extract target user from reply or text mention."""
    if message.reply_to_message and message.reply_to_message.from_user:
        u = message.reply_to_message.from_user
        return u.id, u.username, u.first_name
    if message.text:
        parts = message.text.split()
        for p in parts[1:]:
            try:
                return int(p), None, None
            except ValueError:
                if p.startswith("@"):
                    return None, p[1:], None
    return None, None, None


def register(bot: Client, call_py) -> None:  # noqa: ARG001

    # ── Promote music admin ───────────────────────────────────────────────
    @bot.on_message(_build_filter(_PROMOTE_MUSIC_CMDS) & filters.group)
    @player_deputy_or_above
    async def promote_music(client: Client, message: Message):
        uid, uname, fname = _get_target_user(message)
        if uid is None:
            await message.reply(t(_LANG, "common.errors.invalid_number"))
            return
        chat_id = message.chat.id
        if await admin_repo.is_music_admin(uid, chat_id):
            name = mention_user(uid, fname or str(uid))
            await message.reply(t(_LANG, "promotion.already_admin", user=name))
            return
        await admin_repo.promote_music_admin(
            chat_id, uid, username=uname, display_name=fname,
            promoted_by=message.from_user.id if message.from_user else None,
        )
        name = mention_user(uid, fname or str(uid))
        await message.reply(t(_LANG, "promotion.promoted", user=name))

    # ── Demote music admin ────────────────────────────────────────────────
    @bot.on_message(_build_filter(_DEMOTE_MUSIC_CMDS) & filters.group)
    @player_deputy_or_above
    async def demote_music(client: Client, message: Message):
        uid, uname, fname = _get_target_user(message)
        if uid is None:
            await message.reply(t(_LANG, "common.errors.invalid_number"))
            return
        chat_id = message.chat.id
        if not await admin_repo.is_music_admin(uid, chat_id):
            name = mention_user(uid, fname or str(uid))
            await message.reply(t(_LANG, "promotion.not_admin", user=name))
            return
        await admin_repo.demote_music_admin(chat_id, uid)
        name = mention_user(uid, fname or str(uid))
        await message.reply(t(_LANG, "promotion.demoted", user=name))

    # ── Promote video admin ───────────────────────────────────────────────
    @bot.on_message(_build_filter(_PROMOTE_VIDEO_CMDS) & filters.group)
    @player_deputy_or_above
    async def promote_video(client: Client, message: Message):
        uid, uname, fname = _get_target_user(message)
        if uid is None:
            await message.reply(t(_LANG, "common.errors.invalid_number"))
            return
        chat_id = message.chat.id
        if await admin_repo.is_video_admin(uid, chat_id):
            name = mention_user(uid, fname or str(uid))
            await message.reply(t(_LANG, "promotion.already_admin", user=name))
            return
        await admin_repo.promote_video_admin(
            chat_id, uid, username=uname, display_name=fname,
            promoted_by=message.from_user.id if message.from_user else None,
        )
        name = mention_user(uid, fname or str(uid))
        await message.reply(t(_LANG, "promotion.promoted", user=name))

    # ── Demote video admin ────────────────────────────────────────────────
    @bot.on_message(_build_filter(_DEMOTE_VIDEO_CMDS) & filters.group)
    @player_deputy_or_above
    async def demote_video(client: Client, message: Message):
        uid, uname, fname = _get_target_user(message)
        if uid is None:
            await message.reply(t(_LANG, "common.errors.invalid_number"))
            return
        chat_id = message.chat.id
        if not await admin_repo.is_video_admin(uid, chat_id):
            name = mention_user(uid, fname or str(uid))
            await message.reply(t(_LANG, "promotion.not_admin", user=name))
            return
        await admin_repo.demote_video_admin(chat_id, uid)
        name = mention_user(uid, fname or str(uid))
        await message.reply(t(_LANG, "promotion.demoted", user=name))

    # ── Set player owner ──────────────────────────────────────────────────
    @bot.on_message(_build_filter(_SET_CREATOR_CMDS) & filters.group)
    @player_owner_management
    async def set_creator(client: Client, message: Message):
        uid, uname, fname = _get_target_user(message)
        if uid is None:
            await message.reply(t(_LANG, "common.errors.invalid_number"))
            return
        chat_id = message.chat.id
        await admin_repo.promote_player_owner(
            chat_id, uid, username=uname, display_name=fname,
            promoted_by=message.from_user.id if message.from_user else None,
        )
        name = mention_user(uid, fname or str(uid))
        await message.reply(t(_LANG, "promotion.promoted", user=name))

    # ── Delete player owner ───────────────────────────────────────────────
    @bot.on_message(_build_filter(_DEL_CREATOR_CMDS) & filters.group)
    @player_owner_management
    async def del_creator(client: Client, message: Message):
        uid, uname, fname = _get_target_user(message)
        if uid is None:
            await message.reply(t(_LANG, "common.errors.invalid_number"))
            return
        chat_id = message.chat.id
        await admin_repo.demote_player_owner(chat_id, uid)
        name = mention_user(uid, fname or str(uid))
        await message.reply(t(_LANG, "promotion.demoted", user=name))

    # ── Config music (bulk promote from group admins) ─────────────────────
    @bot.on_message(_build_filter(_CONFIG_MUSIC_CMDS) & filters.group)
    @player_deputy_or_above
    async def config_music(client: Client, message: Message):
        chat_id = message.chat.id
        try:
            async for member in client.get_chat_members(chat_id, filter=filters.ChatMembersFilter.ADMINISTRATORS):
                if member.user and not member.user.is_bot:
                    await admin_repo.promote_music_admin(
                        chat_id, member.user.id,
                        username=member.user.username,
                        display_name=member.user.first_name,
                        promoted_by=message.from_user.id if message.from_user else None,
                    )
        except Exception:
            logger.exception("config_music failed for %s", chat_id)
        await message.reply(t(_LANG, "status.setting_updated"))

    # ── Clear music admins ────────────────────────────────────────────────
    @bot.on_message(_build_filter(_DEL_CONFIG_MUSIC_CMDS) & filters.group)
    @player_deputy_or_above
    async def del_config_music(client: Client, message: Message):
        await admin_repo.clear_music_admins(message.chat.id)
        await message.reply(t(_LANG, "status.setting_updated"))

    # ── List music admins ─────────────────────────────────────────────────
    @bot.on_message(_build_filter(_LIST_MUSIC_CMDS) & filters.group)
    @group_music_admin
    async def list_music(client: Client, message: Message):
        admins = await admin_repo.get_music_admins(message.chat.id)
        if not admins:
            await message.reply(t(_LANG, "filter_mgmt.list_empty"))
            return
        lines = [t(_LANG, "list_fmt.user_item", user_id=a.user_id, username=a.username or "-") for a in admins]
        await message.reply("\n".join(lines))

    # ── Config video (bulk promote from group admins) ─────────────────────
    @bot.on_message(_build_filter(_CONFIG_VIDEO_CMDS) & filters.group)
    @player_deputy_or_above
    async def config_video(client: Client, message: Message):
        chat_id = message.chat.id
        try:
            async for member in client.get_chat_members(chat_id, filter=filters.ChatMembersFilter.ADMINISTRATORS):
                if member.user and not member.user.is_bot:
                    await admin_repo.promote_video_admin(
                        chat_id, member.user.id,
                        username=member.user.username,
                        display_name=member.user.first_name,
                        promoted_by=message.from_user.id if message.from_user else None,
                    )
        except Exception:
            logger.exception("config_video failed for %s", chat_id)
        await message.reply(t(_LANG, "status.setting_updated"))

    # ── Clear video admins ────────────────────────────────────────────────
    @bot.on_message(_build_filter(_DEL_CONFIG_VIDEO_CMDS) & filters.group)
    @player_deputy_or_above
    async def del_config_video(client: Client, message: Message):
        await admin_repo.clear_video_admins(message.chat.id)
        await message.reply(t(_LANG, "status.setting_updated"))

    # ── List video admins ─────────────────────────────────────────────────
    @bot.on_message(_build_filter(_LIST_VIDEO_CMDS) & filters.group)
    @group_video_admin
    async def list_video(client: Client, message: Message):
        admins = await admin_repo.get_video_admins(message.chat.id)
        if not admins:
            await message.reply(t(_LANG, "filter_mgmt.list_empty"))
            return
        lines = [t(_LANG, "list_fmt.user_item", user_id=a.user_id, username=a.username or "-") for a in admins]
        await message.reply("\n".join(lines))

    # ── Promote VIP ──────────────────────────────────────────────────────
    @bot.on_message(_build_filter(_PROMOTE_VIP_CMDS) & filters.group)
    @music_admin_or_above
    async def promote_vip(client: Client, message: Message):
        uid, uname, fname = _get_target_user(message)
        if uid is None:
            await message.reply(t(_LANG, "common.errors.invalid_number"))
            return
        chat_id = message.chat.id
        if await admin_repo.is_vip(uid, chat_id):
            name = mention_user(uid, fname or str(uid))
            await message.reply(t(_LANG, "promotion.already_admin", user=name))
            return
        await admin_repo.promote_vip(
            chat_id, uid, username=uname, display_name=fname,
            promoted_by=message.from_user.id if message.from_user else None,
        )
        name = mention_user(uid, fname or str(uid))
        await message.reply(t(_LANG, "promotion.promoted", user=name))

    # ── Demote VIP ───────────────────────────────────────────────────────
    @bot.on_message(_build_filter(_DEMOTE_VIP_CMDS) & filters.group)
    @music_admin_or_above
    async def demote_vip(client: Client, message: Message):
        uid, uname, fname = _get_target_user(message)
        if uid is None:
            await message.reply(t(_LANG, "common.errors.invalid_number"))
            return
        chat_id = message.chat.id
        if not await admin_repo.is_vip(uid, chat_id):
            name = mention_user(uid, fname or str(uid))
            await message.reply(t(_LANG, "promotion.not_admin", user=name))
            return
        await admin_repo.demote_vip(chat_id, uid)
        name = mention_user(uid, fname or str(uid))
        await message.reply(t(_LANG, "promotion.demoted", user=name))

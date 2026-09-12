"""Hot Seat voice game commands (HOTSEAT-01/02/03).

Creation takes its parameters inline (`صندلی داغ <count> [خصوصی|عمومی]`) rather
than through a two-step wizard: the evidenced wizard needs `safe_ask`, which
currently fails to import in this environment, so an inline form keeps the
feature usable and fully testable. Every guard the evidence names is enforced in
`hot_seat_service`.
"""

from __future__ import annotations

import logging
import re

from pyrogram import Client, filters
from pyrogram.types import Message

from app.handlers.priority import PRIORITY_COMMAND_GROUP
from app.repositories import admin_repo
from app.services import hot_seat_service
from app.utils.i18n import AUTO_LANG, t

logger = logging.getLogger(__name__)

_LANG = AUTO_LANG

_CREATE_CMDS = ["صندلی داغ", "Hot seat", "Hotseat"]
_CANCEL_CMDS = ["لغو صندلی داغ", "Cancel hot seat"]
_ADD_GUEST_CMDS = ["افزودن مهمان", "Add guest"]
_REMOVE_GUEST_CMDS = ["حذف مهمان", "Remove guest"]

_PRIVATE_WORDS = {"خصوصی", "private"}
_PUBLIC_WORDS = {"عمومی", "همه", "all", "public"}

_ERROR_KEYS = {
    "invalid_mode": "hot_seat.invalid_mode",
    "invalid_question_count": "hot_seat.invalid_question_count",
    "channel_mode_blocked": "hot_seat.channel_mode_blocked",
    "already_active": "hot_seat.already_active",
    "no_active_game": "hot_seat.no_active_game",
    "not_private_mode": "hot_seat.not_private_mode",
    "not_joining_state": "hot_seat.not_joining_state",
    "creator_only": "hot_seat.creator_only",
    "already_guest": "hot_seat.already_guest",
    "not_a_guest": "hot_seat.not_a_guest",
}


def _build_filter(cmds: list[str]):
    pattern = "|".join(re.escape(c) for c in cmds)
    return filters.regex(rf"^(?:{pattern})(?:\s|$)", flags=re.IGNORECASE)


def _argument(text: str, cmds: list[str]) -> str:
    stripped = (text or "").strip()
    for cmd in sorted(cmds, key=len, reverse=True):
        if stripped.lower().startswith(cmd.lower()):
            return stripped[len(cmd):].strip()
    return ""


def parse_create_arguments(rest: str) -> tuple[int, str]:
    """Return ``(question_count, mode)`` from the inline creation arguments."""
    count = 5
    mode = hot_seat_service.MODE_ALL
    for token in (rest or "").split():
        lowered = token.strip().lower()
        if lowered.isdigit():
            count = int(lowered)
        elif lowered in _PRIVATE_WORDS:
            mode = hot_seat_service.MODE_PRIVATE
        elif lowered in _PUBLIC_WORDS:
            mode = hot_seat_service.MODE_ALL
    return count, mode


async def _resolve_target_user_id(message: Message, rest: str) -> int | None:
    reply_user = getattr(getattr(message, "reply_to_message", None), "from_user", None)
    if reply_user is not None and getattr(reply_user, "id", None) is not None:
        return int(reply_user.id)
    token = (rest or "").strip().split()[0] if (rest or "").strip() else ""
    return int(token) if token.isdigit() else None


async def _is_group_admin(message: Message) -> bool:
    user_id = getattr(getattr(message, "from_user", None), "id", None)
    chat_id = getattr(getattr(message, "chat", None), "id", None)
    if user_id is None or chat_id is None:
        return False
    from app.utils.bot_guards import is_developer

    if is_developer(int(user_id)):
        return True
    return await admin_repo.is_music_admin_or_above(int(user_id), int(chat_id))


async def _deny(message: Message, reason: str) -> None:
    await message.reply(t(_LANG, _ERROR_KEYS.get(reason, "hot_seat.failed")))


def register(bot: Client, call_py) -> None:  # noqa: ARG001
    """Register Hot Seat text commands."""

    @bot.on_message(_build_filter(_CANCEL_CMDS) & filters.group, group=PRIORITY_COMMAND_GROUP)
    async def hot_seat_cancel(client: Client, message: Message):  # noqa: ARG001
        """HOTSEAT-02: cancel an active Hot Seat."""
        if not await _is_group_admin(message):
            await message.reply(t(_LANG, "hot_seat.no_permission"))
            return
        result = await hot_seat_service.cancel_game(int(message.chat.id))
        if not result.ok:
            await _deny(message, result.reason)
            return
        await message.reply(t(_LANG, "hot_seat.cancelled"))

    @bot.on_message(_build_filter(_ADD_GUEST_CMDS) & filters.group, group=PRIORITY_COMMAND_GROUP)
    async def hot_seat_add_guest(client: Client, message: Message):  # noqa: ARG001
        """HOTSEAT-03: add a guest to a private-mode Hot Seat."""
        rest = _argument(message.text or "", _ADD_GUEST_CMDS)
        target = await _resolve_target_user_id(message, rest)
        if target is None:
            await message.reply(t(_LANG, "hot_seat.target_required"))
            return
        result = await hot_seat_service.add_guest(
            int(message.chat.id),
            target,
            actor_id=getattr(getattr(message, "from_user", None), "id", None),
        )
        if not result.ok:
            await _deny(message, result.reason)
            return
        await message.reply(t(_LANG, "hot_seat.guest_added", user=str(target)))

    @bot.on_message(_build_filter(_REMOVE_GUEST_CMDS) & filters.group, group=PRIORITY_COMMAND_GROUP)
    async def hot_seat_remove_guest(client: Client, message: Message):  # noqa: ARG001
        """HOTSEAT-03: remove a guest while the Hot Seat is still joinable."""
        rest = _argument(message.text or "", _REMOVE_GUEST_CMDS)
        target = await _resolve_target_user_id(message, rest)
        if target is None:
            await message.reply(t(_LANG, "hot_seat.target_required"))
            return
        result = await hot_seat_service.remove_guest(int(message.chat.id), target)
        if not result.ok:
            await _deny(message, result.reason)
            return
        await message.reply(t(_LANG, "hot_seat.guest_removed", user=str(target)))

    @bot.on_message(_build_filter(_CREATE_CMDS) & filters.group, group=PRIORITY_COMMAND_GROUP)
    async def hot_seat_create(client: Client, message: Message):  # noqa: ARG001
        """HOTSEAT-01: create a joinable Hot Seat."""
        if not await _is_group_admin(message):
            await message.reply(t(_LANG, "hot_seat.no_permission"))
            return
        count, mode = parse_create_arguments(_argument(message.text or "", _CREATE_CMDS))
        result = await hot_seat_service.create_game(
            int(message.chat.id),
            question_count=count,
            mode=mode,
            created_by=getattr(getattr(message, "from_user", None), "id", None),
        )
        if not result.ok:
            await _deny(message, result.reason)
            return
        await message.reply(
            t(
                _LANG,
                "hot_seat.created",
                count=str(count),
                mode=t(_LANG, f"hot_seat.mode_{mode}"),
            )
        )

from __future__ import annotations

import logging
import re

from pyrogram import Client, filters
from pyrogram.types import Message

from app.config.settings import settings
from app.handlers.priority import PRIORITY_COMMAND_GROUP
from app.repositories import user_repo
from app.services import CreditService, NotificationService
from app.utils.sudo_permissions import permission_denial_key, sudo_has_permission
from app.utils.helpers import MAX_CREDIT_DAYS, parse_bounded_int
from app.utils.i18n import AUTO_LANG, t
from app.utils.text_commands import normalize_command_text, normalize_digits

logger = logging.getLogger(__name__)

_LANG = AUTO_LANG

_CHARGE_PATTERN = re.compile(
    r"^(?:آپدیت\s+شارژ|update\s+charge)\s+(\d+)$",
    re.IGNORECASE,
)
_CHARGE_VIDEO_PATTERN = re.compile(
    r"^(?:آپدیت\s+شارژ\s+ویدیو|update\s+charge\s+video)\s+(\d+)$",
    re.IGNORECASE,
)


def _charge_filter(pattern: re.Pattern[str]):
    """Match charge commands on normalized text with Persian/Arabic digits."""

    async def func(_flt, _client, message: Message) -> bool:
        text = normalize_digits(normalize_command_text(message))
        if not text:
            return False
        return pattern.fullmatch(text) is not None

    return filters.create(func, name=f"ChargeFilter:{pattern.pattern[:24]}")


def register(bot: Client, call_py) -> None:  # noqa: ARG001

    @bot.on_message(
        filters.group
        & filters.text
        & _charge_filter(_CHARGE_VIDEO_PATTERN),
        group=PRIORITY_COMMAND_GROUP,
    )
    async def handle_charge_video(client: Client, message: Message):
        await _handle_charge(client, message, is_video=True)

    @bot.on_message(
        filters.group
        & filters.text
        & _charge_filter(_CHARGE_PATTERN),
        group=PRIORITY_COMMAND_GROUP,
    )
    async def handle_charge_music(client: Client, message: Message):
        await _handle_charge(client, message, is_video=False)


async def _handle_charge(
    client: Client, message: Message, *, is_video: bool
) -> None:
    user_id = message.from_user.id if message.from_user else None
    if user_id is None:
        return

    from app.utils.bot_guards import is_developer

    if not is_developer(user_id):
        await message.reply_text(t(_LANG, "common.errors.no_access"))
        return

    pattern = _CHARGE_VIDEO_PATTERN if is_video else _CHARGE_PATTERN
    text = normalize_digits(normalize_command_text(message))
    match = pattern.fullmatch(text or "")
    if not match:
        return

    days = parse_bounded_int(match.group(1), min_value=1, max_value=MAX_CREDIT_DAYS)
    if days is None:
        await message.reply_text(t(_LANG, "common.errors.invalid_positive_number"))
        return
    chat_id = message.chat.id
    chat_type = "channel" if message.chat.type.value == "channel" else "group"

    try:
        await CreditService.charge_managed_chat(
            chat_id,
            chat_type,
            days,
            operated_by=user_id,
            note="inline_charge",
        )
    except ValueError as exc:
        reason = str(exc)
        if "not_managed" in reason:
            await message.reply_text(t(_LANG, "credit.charge_group_not_managed"))
            return
        if "insufficient_wallet" in reason:
            await message.reply_text(t(_LANG, "credit.insufficient_wallet"))
            return
        await message.reply_text(t(_LANG, "common.errors.invalid_positive_number"))
        return
    except Exception:
        logger.exception("inline charge failed chat_id=%s", chat_id)
        await message.reply_text(t(_LANG, "credit.update_charge_failed"))
        return

    await message.reply_text(
        t(_LANG, "credit.update_charge_success", days=days)
    )

    try:
        await NotificationService.notify_credit_charge(
            client,
            chat_id=chat_id,
            days=days,
            sudo_info=t(_LANG, "notifications.credit_by_user", user_id=user_id),
        )
    except Exception:
        logger.debug("Failed to send credit charge notification")

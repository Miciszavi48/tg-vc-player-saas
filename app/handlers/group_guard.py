from __future__ import annotations

import logging

from pyrogram import Client, filters
from pyrogram.types import Message

from app.handlers.force_join import check_force_join
from app.handlers.priority import GUARD_GROUP
from app.repositories import blacklist_repo, settings_repo, user_repo
from app.services import group_membership_age_service
from app.utils.diagnostic_logging import log_guard_decision
from app.utils.sudo_permissions import can_use_sudo_admin_bypass
from app.services.language_service import resolve_lang
from app.utils.bot_guards import is_bot_enabled, is_developer
from app.utils.i18n import t

logger = logging.getLogger(__name__)


async def _notify_blacklisted(message: Message, *, lang: str) -> None:
    """Send a visible denial for blacklisted group/user (G24)."""
    try:
        await message.reply(t(lang, "common.errors.blacklisted"))
    except Exception:
        logger.debug(
            "blacklist reply failed chat_id=%s user_id=%s",
            message.chat.id,
            message.from_user.id if message.from_user else None,
        )


def register(bot: Client, call_py) -> None:  # noqa: ARG001
    """Register global group guards.

    These handlers run at a very early group priority (``GUARD_GROUP``) so they
    execute before any feature handler.

    G24: Blacklisted users/chats receive a visible denial.
    G23: Force-join is enforced on non-privileged users for every
         group command.
    """

    @bot.on_message(filters.group, group=GUARD_GROUP)
    async def group_blacklist_and_force_join_guard(
        client: Client, message: Message
    ):
        user = message.from_user
        chat_id = message.chat.id
        user_id = user.id if user else None

        # ── G24: blacklist check ────────────────────────────────────
        if await blacklist_repo.is_blacklisted(chat_id, "group"):
            log_guard_decision(
                "group_guard",
                "block",
                reason="group_blacklisted",
                chat_id=chat_id,
                user_id=user_id,
            )
            lang = await resolve_lang(chat_id=chat_id, user_id=user_id)
            await _notify_blacklisted(message, lang=lang)
            message.stop_propagation()
            return

        if user and await blacklist_repo.is_blacklisted(user.id, "user"):
            log_guard_decision(
                "group_guard",
                "block",
                reason="user_blacklisted",
                chat_id=chat_id,
                user_id=user_id,
            )
            lang = await resolve_lang(chat_id=chat_id, user_id=user_id)
            await _notify_blacklisted(message, lang=lang)
            message.stop_propagation()
            return

        if user and not is_developer(user.id) and not await is_bot_enabled():
            text = (message.text or message.caption or "").strip()
            if text:
                lang = await resolve_lang(chat_id=chat_id, user_id=user.id)
                try:
                    await message.reply(t(lang, "status.bot_disabled"))
                except Exception:
                    pass
            log_guard_decision(
                "group_guard",
                "block",
                reason="bot_disabled",
                chat_id=chat_id,
                user_id=user_id,
            )
            message.stop_propagation()
            return

        # ── G23: force-join check ───────────────────────────────────
        if user is None:
            log_guard_decision("group_guard", "pass", reason="no_user", chat_id=chat_id)
            message.continue_propagation()
            return

        try:
            await group_membership_age_service.record_member_seen(
                chat_id,
                int(user.id),
                source="message_seen",
            )
        except Exception as exc:
            logger.debug(
                "membership message-seen tracking skipped chat_id=%s user_id=%s err=%s",
                chat_id,
                user.id,
                type(exc).__name__,
            )

        if is_developer(user.id):
            log_guard_decision("group_guard", "pass", reason="developer", chat_id=chat_id, user_id=user_id)
            message.continue_propagation()
            return

        if await user_repo.is_owner(user.id):
            log_guard_decision("group_guard", "pass", reason="owner", chat_id=chat_id, user_id=user_id)
            message.continue_propagation()
            return

        if await can_use_sudo_admin_bypass(user.id):
            log_guard_decision("group_guard", "pass", reason="sudo_bypass", chat_id=chat_id, user_id=user_id)
            message.continue_propagation()
            return

        force_join_global = await settings_repo.get_bot_setting("force_join_enabled")
        if force_join_global in ("1", "true"):
            passed = await check_force_join(client, message, user.id)
            if not passed:
                log_guard_decision(
                    "group_guard",
                    "block",
                    reason="force_join",
                    chat_id=chat_id,
                    user_id=user_id,
                )
                message.stop_propagation()
                return

        log_guard_decision("group_guard", "pass", reason="ok", chat_id=chat_id, user_id=user_id)
        message.continue_propagation()

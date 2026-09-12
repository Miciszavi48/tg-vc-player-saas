from __future__ import annotations

import logging
from datetime import date

import jdatetime
import redis.asyncio as redis

from app.config.settings import settings
from app.services.bot_settings_service import parse_log_channel_id, resolve_log_channel_id
from app.utils.cache import get_redis
from app.utils.i18n import label, t
from app.utils.redis_keys import (
    TTL_CREDIT_WARNING_COOLDOWN,
    credit_warning_sent_key,
)

logger = logging.getLogger(__name__)

_DEFAULT_LANG = "fa"


async def claim_credit_warning_slot(
    chat_id: int,
    remaining_days: int,
    chat_type: str = "group",
) -> bool:
    """Claim a one-shot credit-warning slot for this chat, days-left, and local date.

    Uses server local date (``date.today()``), consistent with nightly credit deduct.

    Args:
        chat_id: Target group or channel chat id.
        remaining_days: Credit days remaining at warning time.
        chat_type: Managed chat type, used with chat_id to match credit identity.

    Returns:
        True when this caller may send the warning; False when duplicate or Redis failed.
    """
    chat_type = "channel" if chat_type == "channel" else "group"
    date_str = date.today().isoformat()
    key = credit_warning_sent_key(chat_id, remaining_days, date_str, chat_type)
    try:
        r = await get_redis()
        acquired = await r.set(key, "1", nx=True, ex=TTL_CREDIT_WARNING_COOLDOWN)
        if acquired:
            logger.debug(
                "Credit warning slot claimed chat_id=%s chat_type=%s days=%s date=%s",
                chat_id,
                chat_type,
                remaining_days,
                date_str,
            )
            return True
        logger.info(
            "Credit warning suppressed (duplicate) chat_id=%s chat_type=%s days=%s date=%s",
            chat_id,
            chat_type,
            remaining_days,
            date_str,
        )
        return False
    except (redis.ConnectionError, redis.RedisError, OSError):
        logger.warning(
            "Credit warning cooldown unavailable; skipping send chat_id=%s chat_type=%s days=%s",
            chat_id,
            chat_type,
            remaining_days,
        )
        return False


def _jalali_now() -> str:
    """Return current date+time in Jalali format."""
    return jdatetime.datetime.now().strftime("%Y/%m/%d %H:%M")


def _format_username(username: str | None) -> str:
    if not username:
        return "-"
    clean = str(username).strip().lstrip("@")
    return f"@{clean}" if clean else "-"


class NotificationService:

    @staticmethod
    async def notify_install(
        bot,
        chat_id: int,
        chat_type: str,
        chat_title: str | None,
        sudo_info: str | None = None,
        *,
        installer_role: str | None = None,
        policy_mode: str | None = None,
    ) -> None:
        text = t(
            _DEFAULT_LANG,
            "notifications.install",
            chat_id=chat_id,
            chat_type=label(_DEFAULT_LANG, "chat_type", chat_type),
            chat_title=chat_title or "",
            sudo_info=sudo_info or "-",
            installer_role=label(_DEFAULT_LANG, "installer_role", installer_role or "-"),
            policy_mode=label(_DEFAULT_LANG, "install_policy_mode", policy_mode or "-"),
            timestamp=_jalali_now(),
        )
        await NotificationService.send_to_log_channel(bot, text)

    @staticmethod
    async def notify_uninstall(
        bot,
        chat_id: int,
        chat_type: str,
        chat_title: str | None,
    ) -> None:
        text = t(
            _DEFAULT_LANG,
            "notifications.uninstall",
            chat_id=chat_id,
            chat_type=label(_DEFAULT_LANG, "chat_type", chat_type),
            chat_title=chat_title or "",
            timestamp=_jalali_now(),
        )
        await NotificationService.send_to_log_channel(bot, text)

    @staticmethod
    async def notify_credit_charge(
        bot,
        chat_id: int,
        days: int,
        sudo_info: str | None = None,
    ) -> None:
        text = t(
            _DEFAULT_LANG,
            "notifications.credit_charge",
            chat_id=chat_id,
            days=days,
            sudo_info=sudo_info or "",
            timestamp=_jalali_now(),
        )
        await NotificationService.send_to_log_channel(bot, text)

    @staticmethod
    async def notify_credit_warning(
        bot,
        chat_id: int,
        remaining_days: int,
        chat_type: str = "group",
    ) -> None:
        chat_type = "channel" if chat_type == "channel" else "group"
        if not await claim_credit_warning_slot(chat_id, remaining_days, chat_type):
            return

        from app.utils.safe_sender import safe_send_message

        text = t(
            _DEFAULT_LANG,
            "notifications.credit_warning",
            chat_id=chat_id,
            remaining_days=remaining_days,
        )
        await safe_send_message(bot, chat_id, text)
        await NotificationService.send_to_log_channel(bot, text)
        await NotificationService._dm_responsible_sudo(
            bot,
            chat_id,
            "notifications.credit_warning_sudo_dm",
            chat_type=chat_type,
            remaining_days=remaining_days,
        )

    @staticmethod
    async def notify_credit_expired(bot, chat_id: int, chat_type: str = "group") -> None:
        from app.utils.safe_sender import safe_send_message
        chat_type = "channel" if chat_type == "channel" else "group"

        text = t(
            _DEFAULT_LANG,
            "notifications.credit_expired",
            chat_id=chat_id,
        )
        await safe_send_message(bot, chat_id, text)
        await NotificationService.send_to_log_channel(bot, text)
        await NotificationService._dm_responsible_sudo(
            bot,
            chat_id,
            "notifications.credit_expired_sudo_dm",
            chat_type=chat_type,
        )

    @staticmethod
    async def notify_error(bot, error_text: str) -> None:
        text = t(
            _DEFAULT_LANG,
            "notifications.error",
            error_text=error_text,
        )
        await NotificationService.send_to_log_channel(bot, text)

    @staticmethod
    async def notify_sudo_added(
        bot,
        target_user_id: int,
        actor_id: int,
        *,
        username: str | None = None,
    ) -> None:
        text = t(
            _DEFAULT_LANG,
            "notifications.sudo_added",
            target_user_id=target_user_id,
            actor_id=actor_id,
            username=_format_username(username),
            timestamp=_jalali_now(),
        )
        await NotificationService.send_to_log_channel(bot, text)

    @staticmethod
    async def notify_sudo_removed(bot, target_user_id: int, actor_id: int) -> None:
        text = t(
            _DEFAULT_LANG,
            "notifications.sudo_removed",
            target_user_id=target_user_id,
            actor_id=actor_id,
            timestamp=_jalali_now(),
        )
        await NotificationService.send_to_log_channel(bot, text)

    @staticmethod
    async def notify_owner_added(
        bot,
        target_user_id: int,
        actor_id: int,
        *,
        username: str | None = None,
    ) -> None:
        text = t(
            _DEFAULT_LANG,
            "notifications.owner_added",
            target_user_id=target_user_id,
            actor_id=actor_id,
            username=_format_username(username),
            timestamp=_jalali_now(),
        )
        await NotificationService.send_to_log_channel(bot, text)

    @staticmethod
    async def notify_owner_removed(bot, target_user_id: int, actor_id: int) -> None:
        text = t(
            _DEFAULT_LANG,
            "notifications.owner_removed",
            target_user_id=target_user_id,
            actor_id=actor_id,
            timestamp=_jalali_now(),
        )
        await NotificationService.send_to_log_channel(bot, text)

    @staticmethod
    async def notify_bot_start(
        bot,
        user_id: int,
        *,
        username: str | None = None,
        role: str | None = None,
    ) -> None:
        text = t(
            _DEFAULT_LANG,
            "notifications.bot_start",
            user_id=user_id,
            username=_format_username(username),
            role=label(_DEFAULT_LANG, "installer_role", role or "-"),
            timestamp=_jalali_now(),
        )
        await NotificationService.send_to_log_channel(bot, text)

    @staticmethod
    async def notify_memory_high(bot, *, usage: str) -> None:
        """Send system memory threshold alert to the configured log channel."""
        text = t(_DEFAULT_LANG, "gc.memory_high", usage=usage)
        await NotificationService.send_to_log_channel(bot, text)

    @staticmethod
    async def send_to_log_channel(bot, text: str) -> None:
        from app.utils.safe_sender import safe_send_message

        log_channel: int | None
        try:
            log_channel = await resolve_log_channel_id()
        except Exception:
            logger.debug("resolve_log_channel_id failed; using env fallback", exc_info=True)
            log_channel = parse_log_channel_id(settings.LOG_CHANNEL_ID)

        if not log_channel:
            logger.debug("No log channel configured, skipping log message")
            return

        try:
            await safe_send_message(bot, log_channel, text, mark_unreachable=False)
        except Exception:
            logger.debug("send_to_log_channel failed for chat %s", log_channel, exc_info=True)

    @staticmethod
    async def _dm_responsible_sudo(
        bot,
        chat_id: int,
        key: str,
        chat_type: str = "group",
        **kwargs,
    ) -> None:
        """Look up the responsible sudo from install_logs and send a DM."""
        from app.utils.safe_sender import safe_send_message

        try:
            from app.repositories import log_repo
            logs = await log_repo.get_install_logs_for_chat(
                chat_id,
                action="install",
                limit=1,
                chat_type=chat_type,
            )
            if not logs:
                return
            sudo_id = logs[0].sudo_id
            if sudo_id is None:
                return
            msg = t(_DEFAULT_LANG, key, chat_id=chat_id, **kwargs)
            await safe_send_message(bot, sudo_id, msg)
        except Exception:
            logger.debug("Failed to DM sudo for chat %s (key=%s)", chat_id, key)

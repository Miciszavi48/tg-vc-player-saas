"""Secret-free operational alerts for the global YouTube cookie-session pool."""

from __future__ import annotations

import logging
import math
from datetime import datetime, timedelta, timezone

from app.config.settings import settings
from app.repositories import user_repo, youtube_session_repo
from app.utils.cache import get_redis
from app.utils.i18n import t
from app.utils.redis_keys import (
    TTL_YOUTUBE_SESSION_EXPIRY_ALERT,
    TTL_YOUTUBE_SESSION_UNAVAILABLE_ALERT,
    youtube_session_expiry_alert_key,
    youtube_session_unavailable_alert_key,
)
from app.utils.safe_sender import safe_send_message


logger = logging.getLogger(__name__)
_LANG = "fa"
_EXPIRY_WARNING_DAYS = 7


async def _recipient_ids() -> set[int]:
    recipients = {int(user_id) for user_id in settings.DEVELOPER_IDS}
    owners = await user_repo.get_all_owners()
    recipients.update(int(owner.user_id) for owner in owners)
    return recipients


async def _notify_managers(bot, text: str) -> None:
    try:
        recipients = await _recipient_ids()
    except Exception as exc:
        logger.warning("youtube_session_watchdog_recipients_failed error=%s", type(exc).__name__)
        return
    for user_id in recipients:
        await safe_send_message(bot, user_id, text)


def _remaining_days(value: datetime, now: datetime) -> int:
    seconds = (value - now).total_seconds()
    return max(0, math.ceil(seconds / 86_400))


async def run_youtube_session_watchdog(bot, *, now: datetime | None = None) -> None:
    """Alert current managers without decrypting or testing cookie jars."""
    if bot is None:
        return
    now = now or datetime.now(timezone.utc)
    try:
        summary = await youtube_session_repo.get_summary()
        redis = await get_redis()
    except Exception as exc:
        logger.warning("youtube_session_watchdog_setup_failed error=%s", type(exc).__name__)
        return

    unavailable_key = youtube_session_unavailable_alert_key()
    if summary.total == 0:
        try:
            await redis.delete(unavailable_key)
        except Exception:
            logger.debug("youtube_session_watchdog_clear_empty_failed", exc_info=True)
        return

    if summary.active == 0:
        try:
            claimed = await redis.set(
                unavailable_key,
                "1",
                nx=True,
                ex=TTL_YOUTUBE_SESSION_UNAVAILABLE_ALERT,
            )
        except Exception as exc:
            logger.warning("youtube_session_watchdog_unavailable_claim_failed error=%s", type(exc).__name__)
            return
        if claimed:
            await _notify_managers(
                bot,
                t(
                    _LANG,
                    "youtube_sessions.watchdog_unavailable",
                    active=summary.active,
                    cooldown=summary.cooldown,
                    inactive=summary.disabled_or_invalid,
                ),
            )
        return

    try:
        recovered = bool(await redis.delete(unavailable_key))
    except Exception as exc:
        logger.warning("youtube_session_watchdog_recovery_check_failed error=%s", type(exc).__name__)
        return
    if recovered:
        await _notify_managers(bot, t(_LANG, "youtube_sessions.watchdog_recovered"))

    try:
        expiring = await youtube_session_repo.list_expiring_active_sessions(
            expires_before=now + timedelta(days=_EXPIRY_WARNING_DAYS),
        )
    except Exception as exc:
        logger.warning("youtube_session_watchdog_expiry_query_failed error=%s", type(exc).__name__)
        return

    pending: list[str] = []
    for row in expiring:
        if row.cookie_expires_at is None:
            continue
        try:
            claimed = await redis.set(
                youtube_session_expiry_alert_key(row.id, now.date().isoformat()),
                "1",
                nx=True,
                ex=TTL_YOUTUBE_SESSION_EXPIRY_ALERT,
            )
        except Exception as exc:
            logger.warning("youtube_session_watchdog_expiry_claim_failed error=%s", type(exc).__name__)
            return
        if claimed:
            pending.append(f"YT-{row.id} ({_remaining_days(row.cookie_expires_at, now)} روز)")
    if pending:
        await _notify_managers(
            bot,
            t(_LANG, "youtube_sessions.watchdog_expiry", sessions="، ".join(pending)),
        )

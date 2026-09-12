from __future__ import annotations

import asyncio
import gc
import os
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from pathlib import Path

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from loguru import logger

from app.config.settings import instance_session_name, settings
from app.database.engine import async_session
from app.database.models import Broadcast
from app.repositories import broadcast_repo
from app.services.broadcast_service_v2 import BroadcastServiceV2
from app.services.credit_service import CreditService
from app.services.helper_pool_service import HelperPoolService
from app.services.notification_service import NotificationService
from app.utils.cache import get_redis

scheduler = AsyncIOScheduler(timezone="Asia/Tehran")

# Call-stats windows are reported in Tehran local time (see
# group_text_call_command_repo._TEHRAN), so cadence buckets must match.
_CALL_STATS_TZ = ZoneInfo("Asia/Tehran")

_bot = None
_call_py = None


def scheduler_job_id(name: str) -> str:
    return f"{settings.INSTANCE_ID}:{name}"


def setup_scheduler(bot, call_py=None) -> None:
    """Register the bot reference, add all jobs, and start the scheduler."""
    global _bot, _call_py
    _bot = bot
    _call_py = call_py

    scheduler.add_job(
        midnight_credit_deduct, "cron", hour=0, minute=0,
        id=scheduler_job_id("midnight_credit_deduct"),
        replace_existing=True,
    )
    scheduler.add_job(
        check_group_credit_warnings, "interval", minutes=10,
        id=scheduler_job_id("check_group_credit_warnings"), replace_existing=True,
    )
    scheduler.add_job(
        check_channel_credit_warnings, "interval", minutes=13,
        id=scheduler_job_id("check_channel_credit_warnings"), replace_existing=True,
    )
    scheduler.add_job(
        cleanup_downloads, "interval", minutes=20,
        id=scheduler_job_id("cleanup_downloads"), replace_existing=True,
    )
    scheduler.add_job(
        health_check, "interval", minutes=25,
        id=scheduler_job_id("health_check"), replace_existing=True,
    )
    scheduler.add_job(
        check_trial_expiry, "interval", minutes=18,
        id=scheduler_job_id("check_trial_expiry"), replace_existing=True,
    )
    scheduler.add_job(
        nightly_db_backup, "cron", hour=4,
        id=scheduler_job_id("nightly_db_backup"), replace_existing=True,
    )
    scheduler.add_job(
        helper_health_watchdog, "interval", minutes=15,
        id=scheduler_job_id("helper_health_watchdog"), replace_existing=True,
    )
    scheduler.add_job(
        youtube_session_watchdog, "interval", minutes=15,
        id=scheduler_job_id("youtube_session_watchdog"), replace_existing=True,
    )
    scheduler.add_job(
        gc_and_memory_check, "interval", hours=1,
        id=scheduler_job_id("gc_and_memory_check"), replace_existing=True,
    )
    scheduler.add_job(
        verify_force_join_targets, "interval", hours=2,
        id=scheduler_job_id("verify_force_join_targets"), replace_existing=True,
    )
    scheduler.add_job(
        flush_analytics_counters, "interval", minutes=5,
        id=scheduler_job_id("flush_analytics_counters"), replace_existing=True,
    )
    scheduler.add_job(
        cleanup_expired_whitelists, "cron", hour=3, minute=0,
        id=scheduler_job_id("cleanup_expired_whitelists"), replace_existing=True,
    )
    scheduler.add_job(
        cleanup_expired_player_vips, "interval", minutes=10,
        id=scheduler_job_id("cleanup_expired_player_vips"), replace_existing=True,
    )
    scheduler.add_job(
        apply_call_stats_reset_cadence, "interval", minutes=15,
        id=scheduler_job_id("apply_call_stats_reset_cadence"), replace_existing=True,
    )
    scheduler.add_job(
        media_cache_eviction, "interval", hours=1,
        id=scheduler_job_id("media_cache_eviction"), replace_existing=True,
    )
    scheduler.add_job(
        purge_media_events, "cron", hour=3, minute=30,
        id=scheduler_job_id("purge_media_events"), replace_existing=True,
    )
    scheduler.add_job(
        restore_scheduled_broadcasts, "date",
        run_date=datetime.now(timezone.utc),
        args=[bot],
        id=scheduler_job_id("restore_scheduled_broadcasts"),
        replace_existing=True,
    )
    scheduler.add_job(
        restore_scheduled_call_ends, "date",
        run_date=datetime.now(timezone.utc),
        id=scheduler_job_id("restore_scheduled_call_ends"),
        replace_existing=True,
    )
    scheduler.add_job(
        send_auto_call_stats_morning,
        "cron",
        hour=11,
        minute=0,
        id=scheduler_job_id("send_auto_call_stats_morning"),
        replace_existing=True,
    )
    scheduler.add_job(
        send_auto_call_stats_evening,
        "cron",
        hour=23,
        minute=0,
        id=scheduler_job_id("send_auto_call_stats_evening"),
        replace_existing=True,
    )
    scheduler.add_job(
        send_auto_call_stats_weekly,
        "cron",
        day_of_week="fri",
        hour=21,
        minute=0,
        id=scheduler_job_id("send_auto_call_stats_weekly"),
        replace_existing=True,
    )
    scheduler.start()
    logger.info("Scheduler started with {} jobs", len(scheduler.get_jobs()))


def _ensure_aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _next_recurring_run(run_at: datetime, interval_hours: int, now: datetime | None = None) -> datetime:
    now = _ensure_aware_utc(now or datetime.now(timezone.utc))
    run_at = _ensure_aware_utc(run_at)
    interval = timedelta(hours=interval_hours)
    if run_at > now:
        return run_at
    missed = int((now - run_at).total_seconds() // interval.total_seconds()) + 1
    return run_at + (interval * missed)


async def _run_scheduled_broadcast(client, broadcast_id: int) -> None:
    try:
        await BroadcastServiceV2.execute(client, broadcast_id)
    except Exception:
        logger.exception("Scheduled broadcast {} failed", broadcast_id)


def schedule_broadcast_job(client, bc: Broadcast, now: datetime | None = None) -> str | None:
    if bc.run_at is None:
        return None

    now = _ensure_aware_utc(now or datetime.now(timezone.utc))
    run_at = _ensure_aware_utc(bc.run_at)
    interval_hours = bc.interval_hours

    if interval_hours is not None:
        if interval_hours <= 0:
            logger.warning("Skipping broadcast {} with invalid interval {}", bc.id, interval_hours)
            return None
        job_id = scheduler_job_id(f"bc_recur_{bc.id}")
        scheduler.add_job(
            _run_scheduled_broadcast,
            "interval",
            hours=interval_hours,
            args=[client, bc.id],
            id=job_id,
            replace_existing=True,
            next_run_time=_next_recurring_run(run_at, interval_hours, now),
        )
        return job_id

    job_id = scheduler_job_id(f"bc_sched_{bc.id}")
    scheduler.add_job(
        _run_scheduled_broadcast,
        "date",
        run_date=max(run_at, now),
        args=[client, bc.id],
        id=job_id,
        replace_existing=True,
    )
    return job_id


async def restore_scheduled_broadcasts(client) -> None:
    try:
        broadcasts = await broadcast_repo.get_pending_scheduled()
    except Exception:
        logger.exception("Could not load pending scheduled broadcasts")
        return

    restored = 0
    for bc in broadcasts:
        job_id = schedule_broadcast_job(client, bc)
        if job_id is not None:
            restored += 1
    if restored:
        logger.info("Restored {} scheduled broadcast jobs", restored)


async def restore_scheduled_call_ends() -> None:
    try:
        from app.services.group_text_call_command_service import (
            restore_scheduled_call_end_jobs,
        )

        restored = await restore_scheduled_call_end_jobs(_call_py)
    except Exception:
        logger.exception("Could not restore scheduled call-end jobs")
        return
    if restored:
        logger.info("Restored {} scheduled call-end jobs", restored)


async def _send_auto_call_stats_job(label: str, *, days: int | None = None) -> None:
    if _bot is None:
        return
    try:
        from app.services.group_text_call_command_service import (
            run_auto_call_stats_report,
        )

        sent = await run_auto_call_stats_report(_bot, days=days)
    except Exception:
        logger.exception("Automatic call statistics job {} failed", label)
        return
    if sent:
        logger.info("Automatic call statistics job {} sent {} reports", label, sent)


async def send_auto_call_stats_morning() -> None:
    await _send_auto_call_stats_job("morning")


async def send_auto_call_stats_evening() -> None:
    await _send_auto_call_stats_job("evening")


async def send_auto_call_stats_weekly() -> None:
    await _send_auto_call_stats_job("weekly", days=7)


async def monthly_invoice_auto_send() -> None:
    """Compatibility no-op: recurring payment notices are disabled."""


# ── 1. midnight_credit_deduct ────────────────────────────────────────────────

async def midnight_credit_deduct() -> None:
    """Deduct one day of credit from all active chats at midnight.

    After deduction, process any chats that hit zero credit
    and auto-leave if enabled (spec §25).
    """
    try:
        count = await CreditService.daily_deduct_all()
        logger.info("Midnight credit deduction complete: {} chats processed", count)

        r = await get_redis()
        from app.utils.redis_keys import CREDIT_EXPIRED_PENDING
        expired = await r.smembers(CREDIT_EXPIRED_PENDING)
        if expired:
            for member in expired:
                try:
                    member_str = str(member)
                    if ":" in member_str:
                        chat_type, cid_str = member_str.split(":", 1)
                        chat_type = "channel" if chat_type == "channel" else "group"
                    else:
                        chat_type, cid_str = "group", member_str
                    cid = int(cid_str)
                    await CreditService.auto_leave_check(
                        cid,
                        chat_type,
                        call_py=_call_py,
                        bot=_bot,
                    )
                except Exception:
                    logger.debug("auto_leave_check failed for {}", member)
            await r.delete(CREDIT_EXPIRED_PENDING)
            logger.info("Processed {} expired chats for auto-leave", len(expired))
    except Exception:
        logger.exception("midnight_credit_deduct failed")


# ── 2. check_group_credit_warnings ───────────────────────────────────────────

async def check_group_credit_warnings() -> None:
    """Warn groups whose credit expires within 24 hours."""
    try:
        groups = await CreditService.get_expiring_chats(hours=24, chat_type="group")
        for credit in groups:
            try:
                await NotificationService.notify_credit_warning(
                    _bot, credit.chat_id, credit.credit_days, credit.chat_type,
                )
            except Exception:
                logger.debug("Could not warn group {}", credit.chat_id)
        if groups:
            logger.info("Sent credit warnings to {} groups", len(groups))
    except Exception:
        logger.exception("check_group_credit_warnings failed")


# ── 3. check_channel_credit_warnings ─────────────────────────────────────────

async def check_channel_credit_warnings() -> None:
    """Warn channels whose credit expires within 24 hours."""
    try:
        channels = await CreditService.get_expiring_chats(hours=24, chat_type="channel")
        for credit in channels:
            try:
                await NotificationService.notify_credit_warning(
                    _bot, credit.chat_id, credit.credit_days, credit.chat_type,
                )
            except Exception:
                logger.debug("Could not warn channel {}", credit.chat_id)
        if channels:
            logger.info("Sent credit warnings to {} channels", len(channels))
    except Exception:
        logger.exception("check_channel_credit_warnings failed")


# ── 4. cleanup_downloads ─────────────────────────────────────────────────────

async def cleanup_downloads() -> None:
    """Delete stale download files older than 2 hours (including per-chat subdirs)."""
    try:
        from app.services.media_health_service import (
            DOWNLOAD_RETENTION_SECONDS,
            cleanup_stale_downloads_sync,
            collect_protected_local_paths,
        )

        protected = await collect_protected_local_paths()
        loop = asyncio.get_running_loop()
        removed = await loop.run_in_executor(
            None,
            cleanup_stale_downloads_sync,
            protected,
            DOWNLOAD_RETENTION_SECONDS,
        )
        if removed:
            logger.info("Cleaned up {} old download files", removed)
    except Exception:
        logger.exception("cleanup_downloads failed")


# ── 5. health_check ──────────────────────────────────────────────────────────

async def health_check() -> None:
    """Ping Telegram, check DB and Redis connectivity, log health."""
    try:
        checks: dict[str, bool] = {}

        # Telegram ping
        try:
            me = await _bot.get_me()
            checks["telegram"] = me is not None
        except Exception:
            checks["telegram"] = False

        # Database check
        try:
            async with async_session() as session:
                from sqlalchemy import text
                await session.execute(text("SELECT 1"))
            checks["database"] = True
        except Exception:
            checks["database"] = False

        # Redis check
        try:
            r = await get_redis()
            pong = await r.ping()
            checks["redis"] = pong is True
        except Exception:
            checks["redis"] = False

        status = "HEALTHY" if all(checks.values()) else "DEGRADED"
        logger.info("Health check: {} — {}", status, checks)

        if not all(checks.values()):
            failed = [k for k, v in checks.items() if not v]
            from app.utils.i18n import label, t
            failed_labels = ", ".join(label("fa", "health_service", name) for name in failed)
            msg = t("fa", "notifications.health_degraded", failed=failed_labels)
            await NotificationService.notify_error(_bot, msg)
    except Exception:
        logger.exception("health_check failed")


async def youtube_session_watchdog() -> None:
    """Send deduplicated, secret-free YouTube session-pool operational alerts."""
    try:
        from app.services.youtube_session_watchdog import run_youtube_session_watchdog

        await run_youtube_session_watchdog(_bot)
    except Exception:
        logger.exception("youtube_session_watchdog failed")


# ── 6. check_trial_expiry ────────────────────────────────────────────────────

async def check_trial_expiry() -> None:
    """Expire trials that have passed their trial_expire_at timestamp."""
    try:
        from sqlalchemy import select
        from app.database.models import GroupCredit

        now = datetime.now(timezone.utc)
        async with async_session() as session:
            async with session.begin():
                stmt = (
                    select(GroupCredit)
                    .where(
                        GroupCredit.is_trial.is_(True),
                        GroupCredit.status == "active",
                        GroupCredit.trial_expire_at.isnot(None),
                        GroupCredit.trial_expire_at <= now,
                    )
                    .with_for_update()
                )
                result = await session.execute(stmt)
                expired = list(result.scalars().all())

                for credit in expired:
                    credit.credit_days = 0
                    credit.status = "expired"

        for credit in expired:
            try:
                await NotificationService.notify_credit_expired(
                    _bot,
                    credit.chat_id,
                    credit.chat_type,
                )
            except Exception:
                logger.debug("Could not notify trial expiry for {}", credit.chat_id)

        if expired:
            logger.info("Expired {} trial subscriptions", len(expired))
    except Exception:
        logger.exception("check_trial_expiry failed")


# ── 7. nightly_db_backup ─────────────────────────────────────────────────────

async def nightly_db_backup() -> None:
    """Run pg_dump backup and rotate old backups."""
    try:
        backup_dir = Path(settings.BACKUP_DIR)
        backup_dir.mkdir(parents=True, exist_ok=True)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_file = backup_dir / (
            f"{settings.INSTANCE_ID}_backup_{timestamp}.sql.gz"
        )

        db_url = settings.DATABASE_URL
        sync_url = db_url.replace("+asyncpg", "")

        proc = await asyncio.create_subprocess_exec(
            "pg_dump", sync_url,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()

        if proc.returncode == 0:
            import gzip
            with gzip.open(backup_file, "wb") as f:
                f.write(stdout)
            logger.info("DB backup created: {}", backup_file)
        else:
            logger.error(
                "pg_dump failed exit_code={} stderr_bytes={}",
                proc.returncode,
                len(stderr or b""),
            )
            return

        # Rotate old backups
        retention = timedelta(days=settings.BACKUP_RETENTION_DAYS)
        cutoff = datetime.now() - retention
        for old_file in backup_dir.glob(
            f"{settings.INSTANCE_ID}_backup_*.sql.gz"
        ):
            if old_file.stat().st_mtime < cutoff.timestamp():
                old_file.unlink(missing_ok=True)
                logger.debug("Removed old backup: {}", old_file)

    except Exception:
        logger.exception("nightly_db_backup failed")


# ── 8. check_sudo_wallet_alerts ──────────────────────────────────────────────

async def check_sudo_wallet_alerts() -> None:
    """Compatibility no-op: monetary sudo-wallet alerts are disabled."""


# ── 9. helper_health_watchdog ────────────────────────────────────────────────

async def helper_health_watchdog() -> None:
    """Probe helper account health with full Telegram login test."""
    _FATAL_ERRORS = ("UserDeactivated", "UserBanned", "AuthKeyUnregistered",
                     "AuthKeyDuplicated", "SessionRevoked", "SessionExpired")
    try:
        from app.database.models import HelperAccount
        helpers = await HelperPoolService.get_all_helpers()
        ok = fail = 0
        for helper in helpers:
            if helper.status not in ("active", "quarantined"):
                continue
            session_str = await HelperPoolService.get_helper_session(helper.id)
            if session_str is None:
                logger.warning("Helper {} has no valid session", helper.id)
                await HelperPoolService.quarantine_helper(
                    helper.id, "no_session", duration_seconds=1800)
                fail += 1
                continue
            try:
                from pyrogram import Client
                client = Client(
                    name=instance_session_name(f"hwdog_{helper.id}"),
                    api_id=settings.API_ID,
                    api_hash=settings.API_HASH,
                    session_string=session_str,
                    in_memory=True,
                )
                async with client:
                    me = await client.get_me()
                    from sqlalchemy import update as sa_update
                    async with async_session() as session:
                        async with session.begin():
                            await session.execute(
                                sa_update(HelperAccount)
                                .where(HelperAccount.id == helper.id)
                                .values(tg_user_id=me.id, username=me.username,
                                        display_name=me.first_name, last_login_at=datetime.now(timezone.utc))
                            )
                ok += 1
            except Exception as exc:
                exc_name = type(exc).__name__
                is_fatal = any(sig in exc_name for sig in _FATAL_ERRORS)
                if is_fatal:
                    logger.error("Helper {} FATALLY dead: {}", helper.id, exc_name)
                    async with async_session() as session:
                        async with session.begin():
                            from sqlalchemy import update as sa_update
                            await session.execute(
                                sa_update(HelperAccount)
                                .where(HelperAccount.id == helper.id)
                                .values(status="disabled", last_error=exc_name,
                                        last_error_at=datetime.now(timezone.utc))
                            )
                else:
                    await HelperPoolService.quarantine_helper(
                        helper.id, f"health_check:{exc_name}", duration_seconds=1800)
                fail += 1

        logger.info("Helper health watchdog: {} ok, {} fail", ok, fail)
    except Exception:
        logger.opt(exception=True).error("helper_health_watchdog failed")


# ── 10. gc_and_memory_check ──────────────────────────────────────────────────

async def gc_and_memory_check() -> None:
    """Run garbage collection, log memory usage, kill zombie ffmpeg processes,
    check memory threshold (§A8.4), and run the full streaming watchdog."""
    try:
        import psutil

        gc.collect()

        process = psutil.Process(os.getpid())
        mem_info = process.memory_info()
        mem_mb = mem_info.rss / (1024 * 1024)
        sys_mem = psutil.virtual_memory()
        logger.info("Memory usage: {:.1f} MB (RSS), system {:.1f}%", mem_mb, sys_mem.percent)

        if sys_mem.percent > 70:
            logger.warning("Memory threshold exceeded ({:.1f}% > 70%), triggering watchdog", sys_mem.percent)
            if _bot:
                try:
                    await NotificationService.notify_memory_high(
                        _bot,
                        usage=f"{sys_mem.percent:.0f}",
                    )
                except Exception:
                    pass

        killed = 0
        current_process = psutil.Process(os.getpid())
        for proc in current_process.children(recursive=True):
            try:
                name = proc.name()
                if name in ("ffmpeg", "ffprobe"):
                    age = time.time() - proc.create_time()
                    if age > 1800:
                        proc.kill()
                        killed += 1
                        logger.warning(
                            "Killed zombie {} (pid={}, age={:.0f}s)",
                            name, proc.pid, age,
                        )
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass

        if killed:
            logger.info("Killed {} zombie ffmpeg/ffprobe processes", killed)

        from app.services.watchdog import run_full_watchdog
        await run_full_watchdog(_call_py, _bot)

    except Exception:
        logger.exception("gc_and_memory_check failed")


# ── 11. verify_force_join_targets ─────────────────────────────────────────

async def verify_force_join_targets() -> None:
    """Periodically verify all force-join targets are still accessible."""
    try:
        from app.services.forced_membership_service import ForcedMembershipService

        result = await ForcedMembershipService.verify_all(_bot)
        logger.info(
            "Force-join verification: {} ok, {} broken",
            result.get("ok", 0),
            result.get("broken", 0),
        )
    except Exception:
        logger.exception("verify_force_join_targets failed")


# ── 12. flush_analytics_counters ──────────────────────────────────────────

async def flush_analytics_counters() -> None:
    """Flush Redis analytics counters to Postgres analytics_hourly."""
    try:
        from app.services.analytics_service import flush_analytics
        await flush_analytics()
    except Exception:
        logger.exception("flush_analytics_counters failed")


# ── 13. cleanup_expired_whitelists ────────────────────────────────────────

async def cleanup_expired_whitelists() -> None:
    """Delete expired free_install_whitelist rows (addendum §3.2.2)."""
    try:
        from sqlalchemy import delete, func
        from app.database.models import FreeInstallWhitelist

        async with async_session() as session:
            async with session.begin():
                result = await session.execute(
                    delete(FreeInstallWhitelist).where(
                        FreeInstallWhitelist.expires_at.isnot(None),
                        FreeInstallWhitelist.expires_at < func.now(),
                    )
                )
        if result.rowcount:
            logger.info("Cleaned up {} expired whitelist entries", result.rowcount)
    except Exception:
        logger.exception("cleanup_expired_whitelists failed")


async def cleanup_expired_player_vips() -> None:
    """Remove timed VIP rows whose expiration timestamp has passed."""
    try:
        from app.repositories import admin_repo

        expired = await admin_repo.expire_due_vips()
        if expired:
            logger.info("Expired {} player VIP roles", expired)
    except Exception:
        logger.exception("cleanup_expired_player_vips failed")


def call_stats_reset_bucket(cadence: str, moment: datetime) -> tuple[str, datetime]:
    """Return the cadence bucket label and its start instant for ``moment``."""
    local = moment.astimezone(_CALL_STATS_TZ)
    if cadence == "monthly":
        start_local = local.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        return start_local.strftime("%Y-%m"), start_local.astimezone(timezone.utc)
    start_local = local.replace(hour=0, minute=0, second=0, microsecond=0)
    return start_local.strftime("%Y-%m-%d"), start_local.astimezone(timezone.utc)


async def apply_call_stats_reset_cadence(now: datetime | None = None) -> int:
    """Roll over per-chat call-stats aggregates on their configured cadence.

    The rollover is a non-destructive watermark bump: CallReport rows are kept,
    but stats views start counting from the new cadence bucket. Recording the
    bucket label makes repeated runs within one bucket idempotent.
    """
    rolled = 0
    try:
        from app.repositories import call_stats_settings_repo as stats_settings

        moment = now or datetime.now(timezone.utc)
        cadences = await stats_settings.chat_ids_with_reset_cadence()
        for chat_id, cadence in cadences.items():
            bucket, bucket_start = call_stats_reset_bucket(cadence, moment)
            previous = await stats_settings.get_last_reset_bucket(chat_id)
            if previous == bucket:
                continue
            if previous is not None:
                # Only bump the watermark on a real bucket transition; the first
                # observation just anchors the cadence without hiding history.
                await stats_settings.set_reset_watermark(chat_id, bucket_start)
                rolled += 1
            await stats_settings.set_last_reset_bucket(chat_id, bucket)
        if rolled:
            logger.info("Rolled over call stats for {} chats", rolled)
    except Exception:
        logger.exception("apply_call_stats_reset_cadence failed")
    return rolled


# ── 14. media_cache_eviction ─────────────────────────────────────────────

async def media_cache_eviction() -> None:
    """Hourly: TTL sweep + LRU eviction to keep disk usage under quota."""
    try:
        import asyncio

        from app.services.media_cache import evict_lru, evict_stale

        loop = asyncio.get_running_loop()
        stale = await loop.run_in_executor(None, evict_stale)
        lru = await loop.run_in_executor(None, evict_lru)
        if stale or lru:
            logger.info("Media cache eviction: {} stale + {} LRU files removed", stale, lru)
        from app.services.media_health_service import store_last_eviction_snapshot

        await store_last_eviction_snapshot(stale, lru)
    except Exception:
        logger.exception("media_cache_eviction failed")


# ── 15. purge_media_events ───────────────────────────────────────────────

async def purge_media_events() -> None:
    """Daily: remove media ranking events older than the retention window."""
    try:
        from app.services.media_event_service import purge_old_media_events

        removed = await purge_old_media_events()
        if removed:
            logger.info("Purged {} old media_events rows", removed)
    except Exception:
        logger.opt(exception=True).error("purge_media_events failed")

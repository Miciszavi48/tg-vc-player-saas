from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import and_, or_, select

from app.config.settings import settings
from app.database.engine import async_session
from app.services.analytics_service import track_event
from app.database.models import (
    Channel,
    CreditHistory,
    Group,
    GroupCredit,
    InstallPolicySetting,
)
from app.utils.cache import (
    acquire_lock,
    get_credit_cached,
    invalidate_credit,
    release_lock,
    set_credit_cached,
)
from app.utils.helpers import (
    MAX_CREDIT_DAYS,
    ensure_bounded_int,
)

logger = logging.getLogger(__name__)

_LOCK_TTL_MS = 5000


@dataclass(frozen=True)
class ManagedCreditUpdate:
    credit: GroupCredit
    before: int
    after: int
    status: str


def _install_model(chat_type: str):
    return Channel if chat_type == "channel" else Group


def _clear_trial_credit(credit: GroupCredit) -> None:
    """Paid/manual credit replaces trial expiry as the active credit source."""
    credit.is_trial = False
    credit.trial_started_at = None
    credit.trial_expire_at = None


async def _clear_expired_pending_marker(chat_id: int, chat_type: str) -> None:
    try:
        from app.utils.cache import get_redis
        from app.utils.redis_keys import CREDIT_EXPIRED_PENDING, credit_expired_pending_member

        r = await get_redis()
        await r.srem(CREDIT_EXPIRED_PENDING, credit_expired_pending_member(chat_id, chat_type))
    except Exception:
        logger.debug(
            "credit.expiration.pending_clear_failed chat_id=%s chat_type=%s",
            chat_id,
            chat_type,
            exc_info=True,
        )


async def _invalidate_credit_after_commit(chat_id: int, chat_type: str) -> None:
    """Keep a committed PostgreSQL mutation successful during a cache outage."""
    try:
        await invalidate_credit(chat_id, chat_type)
    except Exception:
        logger.warning(
            "credit.cache_invalidation_failed_after_commit chat_id=%s chat_type=%s",
            chat_id,
            chat_type,
            exc_info=True,
        )


class CreditService:

    @staticmethod
    async def adjust_managed_credit(
        chat_id: int,
        chat_type: str,
        *,
        mode: str,
        amount: int | None,
        operated_by: int | None = None,
        note: str | None = None,
    ) -> ManagedCreditUpdate:
        from app.utils.bot_guards import is_developer
        if not is_developer(operated_by):
            raise ValueError("only_developer_can_mutate_credit")

        chat_type = "channel" if chat_type == "channel" else "group"
        amount_value = int(amount or 0)
        if mode != "unlimited":
            amount_value = ensure_bounded_int(
                amount_value,
                field_name="credit_days",
                min_value=1,
                max_value=MAX_CREDIT_DAYS,
            )

        from app.utils.redis_keys import credit_lock_key

        lock_key = credit_lock_key(chat_id, chat_type)
        token = await acquire_lock(lock_key, _LOCK_TTL_MS)
        if token is None:
            raise RuntimeError(f"Could not acquire credit lock for chat {chat_id}")
        try:
            async with async_session() as session:
                async with session.begin():
                    model = _install_model(chat_type)
                    installed = (
                        await session.execute(
                            select(model)
                            .where(model.chat_id == chat_id, model.status == "active")
                            .with_for_update()
                        )
                    ).scalar_one_or_none()
                    if installed is None:
                        raise ValueError("not_managed")

                    stmt = (
                        select(GroupCredit)
                        .where(
                            GroupCredit.chat_id == chat_id,
                            GroupCredit.chat_type == chat_type,
                        )
                        .with_for_update()
                    )
                    result = await session.execute(stmt)
                    credit = result.scalar_one_or_none()
                    if credit is None:
                        credit = GroupCredit(
                            chat_id=chat_id,
                            chat_type=chat_type,
                            credit_days=0,
                            status="expired",
                            charged_by=operated_by,
                        )
                        session.add(credit)
                        await session.flush()

                    before = int(credit.credit_days or 0)
                    if mode == "unlimited":
                        credit.credit_days = MAX_CREDIT_DAYS
                        credit.status = "unlimited"
                        _clear_trial_credit(credit)
                        operation = "unlimited"
                        history_amount = 0
                    elif mode == "decrease":
                        credit.credit_days = max(0, before - amount_value)
                        credit.status = "expired" if credit.credit_days == 0 else "active"
                        operation = "deduct"
                        history_amount = amount_value
                    else:
                        credit.credit_days = ensure_bounded_int(
                            before + amount_value,
                            field_name="credit_days",
                            min_value=1,
                            max_value=MAX_CREDIT_DAYS,
                        )
                        credit.status = "active"
                        credit.total_charged = int(credit.total_charged or 0) + amount_value
                        _clear_trial_credit(credit)
                        operation = "charge"
                        history_amount = amount_value

                    if operated_by is not None:
                        credit.charged_by = operated_by

                    session.add(
                        CreditHistory(
                            chat_id=chat_id,
                            chat_type=chat_type,
                            operation=operation,
                            amount_days=history_amount,
                            operated_by=operated_by,
                            note=note,
                        )
                    )
                    await session.flush()
                    after = int(credit.credit_days or 0)
                    status = str(credit.status)

                await session.refresh(credit)

            await _invalidate_credit_after_commit(chat_id, chat_type)
            if status in {"active", "unlimited"} and after > 0:
                await _clear_expired_pending_marker(chat_id, chat_type)
            await track_event("credit.managed_update", feature="credit")
            return ManagedCreditUpdate(
                credit=credit,
                before=before,
                after=after,
                status=status,
            )
        finally:
            await release_lock(lock_key, token)

    @staticmethod
    async def charge_managed_chat(
        chat_id: int,
        chat_type: str,
        days: int,
        operated_by: int | None = None,
        note: str | None = None,
    ) -> ManagedCreditUpdate:
        return await CreditService.adjust_managed_credit(
            chat_id,
            chat_type,
            mode="increase",
            amount=days,
            operated_by=operated_by,
            note=note,
        )

    @staticmethod
    async def adjust_sudo_scoped_group_credit(
        sudo_user_id: int,
        chat_id: int,
        *,
        mode: str,
        amount: int,
        note: str | None = None,
    ) -> ManagedCreditUpdate:
        async with async_session() as session:
            row = (
                await session.execute(
                    select(Group.chat_id).where(
                        Group.chat_id == chat_id,
                        Group.status == "active",
                        Group.installed_by == sudo_user_id,
                    )
                )
            ).scalar_one_or_none()
        if row is None:
            raise ValueError("not_in_sudo_scope")
        return await CreditService.adjust_managed_credit(
            chat_id,
            "group",
            mode=mode,
            amount=amount,
            operated_by=sudo_user_id,
            note=note,
        )

    @staticmethod
    async def charge_managed_chat_with_wallet(
        chat_id: int,
        chat_type: str,
        days: int,
        sudo_user_id: int,
        rate_key: str = "music_rate",
        note: str | None = None,
    ) -> ManagedCreditUpdate:
        """Compatibility surface retained as an explicit policy denial."""
        raise ValueError("wallet_credit_disabled")

    @staticmethod
    async def charge(
        chat_id: int,
        chat_type: str,
        days: int,
        operated_by: int | None = None,
        note: str | None = None,
    ) -> GroupCredit:
        from app.utils.bot_guards import is_developer

        if not is_developer(operated_by):
            raise ValueError("only_developer_can_mutate_credit")
        days = ensure_bounded_int(
            days,
            field_name="credit_days",
            min_value=1,
            max_value=MAX_CREDIT_DAYS,
        )
        from app.utils.redis_keys import credit_lock_key
        lock_key = credit_lock_key(chat_id, chat_type)
        token = await acquire_lock(lock_key, _LOCK_TTL_MS)
        if token is None:
            raise RuntimeError(f"Could not acquire credit lock for chat {chat_id}")
        try:
            async with async_session() as session:
                async with session.begin():
                    stmt = (
                        select(GroupCredit)
                        .where(
                            GroupCredit.chat_id == chat_id,
                            GroupCredit.chat_type == chat_type,
                        )
                        .with_for_update()
                    )
                    result = await session.execute(stmt)
                    credit = result.scalar_one_or_none()

                    if credit is None:
                        credit = GroupCredit(
                            chat_id=chat_id,
                            chat_type=chat_type,
                            credit_days=days,
                            charged_by=operated_by,
                            total_charged=days,
                            is_trial=False,
                            trial_started_at=None,
                            trial_expire_at=None,
                            status="active",
                        )
                        session.add(credit)
                    else:
                        credit.credit_days = ensure_bounded_int(
                            credit.credit_days + days,
                            field_name="credit_days",
                            min_value=1,
                            max_value=MAX_CREDIT_DAYS,
                        )
                        credit.total_charged += days
                        if operated_by is not None:
                            credit.charged_by = operated_by
                        credit.status = "active"
                        _clear_trial_credit(credit)

                    await session.flush()

                    history = CreditHistory(
                        chat_id=chat_id,
                        chat_type=chat_type,
                        operation="charge",
                        amount_days=days,
                        operated_by=operated_by,
                        note=note,
                    )
                    session.add(history)

                await session.refresh(credit)

            await _invalidate_credit_after_commit(chat_id, chat_type)
            await _clear_expired_pending_marker(chat_id, chat_type)
            await track_event("credit.charge", feature="credit")
            return credit
        finally:
            await release_lock(lock_key, token)

    @staticmethod
    async def charge_with_wallet(
        chat_id: int,
        chat_type: str,
        days: int,
        sudo_user_id: int,
        rate_key: str = "music_rate",
        note: str | None = None,
    ) -> GroupCredit:
        """Compatibility surface retained as an explicit policy denial."""
        raise ValueError("wallet_credit_disabled")

    @staticmethod
    async def deduct(
        chat_id: int,
        days: int,
        chat_type: str = "group",
        operated_by: int | None = None,
        note: str | None = None,
    ) -> GroupCredit | None:
        from app.utils.bot_guards import is_developer

        if not is_developer(operated_by):
            raise ValueError("only_developer_can_mutate_credit")
        days = ensure_bounded_int(
            days,
            field_name="credit_days",
            min_value=1,
            max_value=MAX_CREDIT_DAYS,
        )
        from app.utils.redis_keys import credit_lock_key
        lock_key = credit_lock_key(chat_id, chat_type)
        token = await acquire_lock(lock_key, _LOCK_TTL_MS)
        if token is None:
            raise RuntimeError(f"Could not acquire credit lock for chat {chat_id}")
        try:
            async with async_session() as session:
                async with session.begin():
                    stmt = (
                        select(GroupCredit)
                        .where(GroupCredit.chat_id == chat_id, GroupCredit.chat_type == chat_type)
                        .with_for_update()
                    )
                    result = await session.execute(stmt)
                    credit = result.scalar_one_or_none()
                    if credit is None:
                        return None

                    credit.credit_days = max(0, credit.credit_days - days)
                    if credit.credit_days == 0:
                        credit.status = "expired"

                    await session.flush()

                    history = CreditHistory(
                        chat_id=chat_id,
                        chat_type=chat_type,
                        operation="deduct",
                        amount_days=days,
                        operated_by=operated_by,
                        note=note,
                    )
                    session.add(history)

                await session.refresh(credit)

            await _invalidate_credit_after_commit(chat_id, chat_type)
            await track_event("credit.deduct", feature="credit")
            return credit
        finally:
            await release_lock(lock_key, token)

    @staticmethod
    async def activate_trial(chat_id: int, chat_type: str) -> GroupCredit:
        from app.utils.redis_keys import credit_lock_key
        lock_key = credit_lock_key(chat_id, chat_type)
        token = await acquire_lock(lock_key, _LOCK_TTL_MS)
        if token is None:
            raise RuntimeError(f"Could not acquire credit lock for chat {chat_id}")
        try:
            trial_days = settings.TRIAL_DAYS
            try:
                async with async_session() as _ps:
                    _ps_stmt = select(InstallPolicySetting).where(
                        InstallPolicySetting.id == 1
                    )
                    _ps_result = await _ps.execute(_ps_stmt)
                    _policy = _ps_result.scalar_one_or_none()
                    if _policy is not None and _policy.trial_days > 0:
                        trial_days = _policy.trial_days
            except Exception:
                logger.debug("Could not load install policy trial_days, using default")
            trial_days = ensure_bounded_int(
                trial_days,
                field_name="trial_days",
                min_value=1,
                max_value=MAX_CREDIT_DAYS,
            )
            now = datetime.now(timezone.utc)

            async with async_session() as session:
                async with session.begin():
                    stmt = (
                        select(GroupCredit)
                        .where(
                            GroupCredit.chat_id == chat_id,
                            GroupCredit.chat_type == chat_type,
                        )
                        .with_for_update()
                    )
                    result = await session.execute(stmt)
                    credit = result.scalar_one_or_none()

                    if credit is not None and credit.is_trial:
                        raise ValueError("Trial already used for this chat")

                    if credit is None:
                        credit = GroupCredit(
                            chat_id=chat_id,
                            chat_type=chat_type,
                            credit_days=trial_days,
                            is_trial=True,
                            trial_started_at=now,
                            trial_expire_at=now + timedelta(days=trial_days),
                            status="active",
                        )
                        session.add(credit)
                    else:
                        credit.credit_days = trial_days
                        credit.is_trial = True
                        credit.trial_started_at = now
                        credit.trial_expire_at = now + timedelta(days=trial_days)
                        credit.status = "active"

                    await session.flush()

                    history = CreditHistory(
                        chat_id=chat_id,
                        chat_type=chat_type,
                        operation="trial",
                        amount_days=trial_days,
                        note="Trial activated",
                    )
                    session.add(history)

                await session.refresh(credit)

            await _invalidate_credit_after_commit(chat_id, chat_type)
            return credit
        finally:
            await release_lock(lock_key, token)

    @staticmethod
    async def get_balance(chat_id: int, chat_type: str = "group") -> int:
        cached = await get_credit_cached(chat_id, chat_type)
        if cached is not None:
            return cached

        async with async_session() as session:
            stmt = select(GroupCredit).where(
                GroupCredit.chat_id == chat_id,
                GroupCredit.chat_type == chat_type,
            )
            result = await session.execute(stmt)
            credit = result.scalar_one_or_none()

        balance = credit.credit_days if credit else 0
        await set_credit_cached(chat_id, balance, chat_type)
        return balance

    @staticmethod
    async def get_expiring_chats(
        hours: int = 24,
        chat_type: str | None = None,
    ) -> list[GroupCredit]:
        from app.services import group_runtime_state_service

        return await group_runtime_state_service.list_expiring_active_credits(
            hours=hours,
            chat_type=chat_type,
        )

    @staticmethod
    async def get_zero_credit_chats() -> list[GroupCredit]:
        async with async_session() as session:
            stmt = select(GroupCredit).where(GroupCredit.credit_days <= 0)
            result = await session.execute(stmt)
            return list(result.scalars().all())

    @staticmethod
    def _daily_deduct_today() -> date:
        """Calendar date for nightly deduction (server local date; matches cron midnight)."""
        return date.today()

    @staticmethod
    def _daily_deduct_eligible_filters(today: date) -> tuple:
        return (
            GroupCredit.status == "active",
            GroupCredit.credit_days > 0,
            or_(
                and_(GroupCredit.chat_type == "group", Group.id.is_not(None)),
                and_(GroupCredit.chat_type == "channel", Channel.id.is_not(None)),
            ),
            or_(
                GroupCredit.last_daily_deducted_on.is_(None),
                GroupCredit.last_daily_deducted_on < today,
            ),
        )

    @staticmethod
    def _process_daily_deduct_row(credit: GroupCredit, today: date) -> bool:
        """Stamp idempotency and decrement one day; return True if expired."""
        credit.last_daily_deducted_on = today
        return CreditService._apply_one_day_deduction(credit)

    @staticmethod
    def _apply_one_day_deduction(credit: GroupCredit) -> bool:
        """Decrement one day; return True if the row became expired."""
        credit.credit_days = max(0, credit.credit_days - 1)
        if credit.credit_days == 0:
            credit.status = "expired"
            return True
        return False

    @staticmethod
    async def _invalidate_processed_credits(credits: list[GroupCredit]) -> None:
        for credit in credits:
            try:
                await _invalidate_credit_after_commit(
                    credit.chat_id,
                    credit.chat_type,
                )
            except Exception:
                logger.warning("Failed to invalidate credit cache for %s", credit.chat_id)

    @staticmethod
    async def _push_expired_pending(r, expired_chats: list[tuple[int, str]]) -> None:
        from app.utils.redis_keys import CREDIT_EXPIRED_PENDING, credit_expired_pending_member

        for chat_id, chat_type in expired_chats:
            await r.sadd(CREDIT_EXPIRED_PENDING, credit_expired_pending_member(chat_id, chat_type))
        if expired_chats:
            await r.expire(CREDIT_EXPIRED_PENDING, 86400)

    @staticmethod
    async def _daily_deduct_legacy() -> int:
        """Single-transaction deduction (production default)."""
        today = CreditService._daily_deduct_today()
        expired_chats: list[tuple[int, str]] = []
        async with async_session() as session:
            async with session.begin():
                stmt = (
                    select(GroupCredit)
                    .outerjoin(
                        Group,
                        and_(
                            Group.chat_id == GroupCredit.chat_id,
                            Group.status == "active",
                            GroupCredit.chat_type == "group",
                        ),
                    )
                    .outerjoin(
                        Channel,
                        and_(
                            Channel.chat_id == GroupCredit.chat_id,
                            Channel.status == "active",
                            GroupCredit.chat_type == "channel",
                        ),
                    )
                    .where(*CreditService._daily_deduct_eligible_filters(today))
                    .with_for_update()
                )
                result = await session.execute(stmt)
                credits = list(result.scalars().all())
                count = 0
                for credit in credits:
                    if CreditService._process_daily_deduct_row(credit, today):
                        expired_chats.append((credit.chat_id, credit.chat_type))
                    count += 1

        await CreditService._invalidate_processed_credits(credits)
        if expired_chats:
            from app.utils.cache import get_redis

            r = await get_redis()
            await CreditService._push_expired_pending(r, expired_chats)
        return count

    @staticmethod
    async def _daily_deduct_batched() -> int:
        """Batched deduction (staging flag); DB ``last_daily_deducted_on`` is authoritative."""
        from app.utils.cache import get_redis

        today = CreditService._daily_deduct_today()
        batch_size = max(1, min(settings.DAILY_DEDUCT_BATCH_SIZE, 1000))
        r = await get_redis()
        total = 0
        last_id = 0

        while True:
            expired_chats: list[tuple[int, str]] = []
            batch_credits: list[GroupCredit] = []
            fetched: list[GroupCredit] = []
            try:
                async with async_session() as session:
                    async with session.begin():
                        stmt = (
                            select(GroupCredit)
                            .outerjoin(
                                Group,
                                and_(
                                    Group.chat_id == GroupCredit.chat_id,
                                    Group.status == "active",
                                    GroupCredit.chat_type == "group",
                                ),
                            )
                            .outerjoin(
                                Channel,
                                and_(
                                    Channel.chat_id == GroupCredit.chat_id,
                                    Channel.status == "active",
                                    GroupCredit.chat_type == "channel",
                                ),
                            )
                            .where(
                                *CreditService._daily_deduct_eligible_filters(today),
                                GroupCredit.id > last_id,
                            )
                            .order_by(GroupCredit.id.asc())
                            .limit(batch_size)
                            .with_for_update(skip_locked=True)
                        )
                        result = await session.execute(stmt)
                        fetched = list(result.scalars().all())
                        if not fetched:
                            break
                        last_id = fetched[-1].id
                        for credit in fetched:
                            if CreditService._process_daily_deduct_row(credit, today):
                                expired_chats.append((credit.chat_id, credit.chat_type))
                            batch_credits.append(credit)
                            total += 1
            except Exception:
                logger.exception(
                    "daily_deduct_batched: batch failed after %s rows (last_id=%s)",
                    total,
                    last_id,
                )
                break

            await CreditService._invalidate_processed_credits(batch_credits)
            if expired_chats:
                await CreditService._push_expired_pending(r, expired_chats)
            if len(fetched) < batch_size:
                break

        return total

    @staticmethod
    async def daily_deduct_all() -> int:
        """Cron: decrement credit by 1 for all active chats.

        Protected by an instance-scoped singleton lock so only one process for
        this bot instance performs the deduction.
        When credits hit zero, records the chat_id for auto-leave processing.
        """
        lock_key = "cron:daily_deduct"
        token = await acquire_lock(lock_key, ttl_ms=120_000)
        if token is None:
            logger.info("daily_deduct_all: another process holds the instance lock; skipping")
            return 0
        try:
            if settings.DAILY_DEDUCT_BATCHING_ENABLED:
                logger.warning(
                    "daily_deduct_all: DAILY_DEDUCT_BATCHING_ENABLED is on — "
                    "batched path for staging; keep disabled in production unless ops approves"
                )
                return await CreditService._daily_deduct_batched()
            return await CreditService._daily_deduct_legacy()
        finally:
            await release_lock(lock_key, token)

    @staticmethod
    async def auto_leave_check(
        chat_id: int,
        chat_type: str = "group",
        call_py=None,
        bot=None,
    ) -> bool:
        """Check if a group should be auto-left due to zero credit.

        Steps (per spec §25):
        1. Check chat_settings.auto_leave_enabled
        2. If True and call_py provided: leave voice call
        3. Send notification to group
        4. Notify responsible sudo via DM
        Returns True if auto-leave was triggered.
        """
        from app.repositories import settings_repo
        from app.services import group_runtime_state_service

        chat_type = "channel" if chat_type == "channel" else "group"
        cs = await settings_repo.get_chat_settings(chat_id, chat_type)
        if cs is None or not cs.auto_leave_enabled:
            return False

        state = await group_runtime_state_service.get_runtime_credit_state(chat_id, chat_type)
        if not state.is_active:
            logger.info(
                "credit.expiration.skip_inactive chat_id=%s chat_type=%s source=db",
                chat_id,
                chat_type,
            )
            return False
        if state.credit is None:
            logger.warning(
                "credit.expiration.skip_missing_credit chat_id=%s chat_type=%s source=db",
                chat_id,
                chat_type,
            )
            return False
        if state.has_runtime_credit:
            logger.info(
                "credit.expiration.skip_active chat_id=%s chat_type=%s status=%s days=%s source=db",
                chat_id,
                chat_type,
                state.credit_status,
                state.credit_days,
            )
            return False

        if call_py is not None:
            try:
                from app.services.call_service import CallService
                await CallService.leave_voice_chat(call_py, chat_id)
            except Exception:
                logger.debug("auto_leave voice chat failed for %d", chat_id)

        if bot is not None:
            from app.utils.i18n import t

            if cs and getattr(cs, "announce_enabled", True):
                from app.utils.safe_sender import safe_send_message
                await safe_send_message(bot, chat_id, t("fa", "credit.expired", chat_title=str(chat_id)))

            try:
                from app.database.models import InstallLog
                from sqlalchemy import select as sa_select

                chat_title = str(chat_id)
                sudo_id = None
                async with async_session() as session:
                    model = _install_model(chat_type)
                    installed = (await session.execute(
                        sa_select(model).where(model.chat_id == chat_id)
                    )).scalar_one_or_none()
                    if installed:
                        chat_title = installed.chat_title or str(chat_id)

                    log_stmt = (
                        sa_select(InstallLog)
                        .where(
                            InstallLog.chat_id == chat_id,
                            InstallLog.chat_type == chat_type,
                            InstallLog.action == "install",
                        )
                        .order_by(InstallLog.occurred_at.desc())
                        .limit(1)
                    )
                    log_row = (await session.execute(log_stmt)).scalar_one_or_none()
                    if log_row:
                        sudo_id = log_row.sudo_id

                if sudo_id:
                    msg = t("fa", "notifications.auto_leave_sudo_dm",
                            chat_title=chat_title, chat_id=chat_id)
                    await safe_send_message(bot, sudo_id, msg)
            except Exception:
                logger.debug("auto_leave sudo lookup failed for %d", chat_id)

        return True

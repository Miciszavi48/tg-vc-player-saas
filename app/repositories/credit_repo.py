from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, or_, select

from app.database.engine import async_session
from app.database.models import Channel, CreditHistory, Group, GroupCredit
from app.utils.helpers import MAX_CREDIT_DAYS, ensure_bounded_int

_CREDIT_BULK_CHUNK = 500


def _clear_trial_credit(credit: GroupCredit) -> None:
    credit.is_trial = False
    credit.trial_started_at = None
    credit.trial_expire_at = None


async def get_credit(chat_id: int, chat_type: str = "group") -> GroupCredit | None:
    async with async_session() as session:
        stmt = select(GroupCredit).where(
            GroupCredit.chat_id == chat_id,
            GroupCredit.chat_type == chat_type,
        )
        result = await session.execute(stmt)
        return result.scalar_one_or_none()


async def get_credits_by_chat_ids(chat_ids: Sequence[int]) -> dict[int, GroupCredit]:
    """Return credits keyed by chat_id for many chats in one session.

    Duplicate input IDs are deduplicated. This helper is for group runtime views
    and filters to group credit rows to avoid cross-type chat_id ambiguity.
    """
    unique = list(dict.fromkeys(chat_ids))
    if not unique:
        return {}

    out: dict[int, GroupCredit] = {}
    async with async_session() as session:
        for offset in range(0, len(unique), _CREDIT_BULK_CHUNK):
            chunk = unique[offset : offset + _CREDIT_BULK_CHUNK]
            stmt = select(GroupCredit).where(
                GroupCredit.chat_id.in_(chunk),
                GroupCredit.chat_type == "group",
            )
            result = await session.execute(stmt)
            for row in result.scalars().all():
                out[row.chat_id] = row
    return out


async def add_credit(
    chat_id: int,
    chat_type: str,
    days: int,
    charged_by: int | None = None,
    note: str | None = None,
) -> GroupCredit:
    days = ensure_bounded_int(
        days,
        field_name="credit_days",
        min_value=1,
        max_value=MAX_CREDIT_DAYS,
    )
    async with async_session() as session:
        stmt = (
            select(GroupCredit)
            .where(GroupCredit.chat_id == chat_id, GroupCredit.chat_type == chat_type)
            .with_for_update()
        )
        result = await session.execute(stmt)
        credit = result.scalar_one_or_none()

        if credit is None:
            credit = GroupCredit(
                chat_id=chat_id,
                chat_type=chat_type,
                credit_days=days,
                charged_by=charged_by,
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
            if charged_by is not None:
                credit.charged_by = charged_by
            credit.status = "active"
            _clear_trial_credit(credit)

        await session.flush()

        history = CreditHistory(
            chat_id=chat_id,
            chat_type=chat_type,
            operation="add",
            amount_days=days,
            operated_by=charged_by,
            note=note,
        )
        session.add(history)
        await session.commit()
        await session.refresh(credit)
        return credit


async def deduct_credit(
    chat_id: int,
    days: int,
    chat_type: str = "group",
    operated_by: int | None = None,
    note: str | None = None,
) -> GroupCredit | None:
    days = ensure_bounded_int(
        days,
        field_name="credit_days",
        min_value=1,
        max_value=MAX_CREDIT_DAYS,
    )
    async with async_session() as session:
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
            chat_type=credit.chat_type,
            operation="deduct",
            amount_days=days,
            operated_by=operated_by,
            note=note,
        )
        session.add(history)
        await session.commit()
        await session.refresh(credit)
        return credit


async def activate_trial(
    chat_id: int, chat_type: str, trial_days: int
) -> GroupCredit:
    trial_days = ensure_bounded_int(
        trial_days,
        field_name="trial_days",
        min_value=1,
        max_value=MAX_CREDIT_DAYS,
    )
    async with async_session() as session:
        now = datetime.now(timezone.utc)
        stmt = (
            select(GroupCredit)
            .where(GroupCredit.chat_id == chat_id, GroupCredit.chat_type == chat_type)
            .with_for_update()
        )
        result = await session.execute(stmt)
        credit = result.scalar_one_or_none()

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
        await session.commit()
        await session.refresh(credit)
        return credit


async def get_expiring_chats(
    hours: int,
    chat_type: str | None = None,
) -> list[GroupCredit]:
    from app.repositories import group_runtime_repo

    return await group_runtime_repo.list_expiring_active_credits(hours, chat_type)


async def get_zero_credit_chats() -> list[GroupCredit]:
    async with async_session() as session:
        stmt = select(GroupCredit).where(GroupCredit.credit_days <= 0)
        result = await session.execute(stmt)
        return list(result.scalars().all())


async def daily_deduct_all() -> int:
    async with async_session() as session:
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
                GroupCredit.status == "active",
                GroupCredit.credit_days > 0,
                or_(
                    and_(GroupCredit.chat_type == "group", Group.id.is_not(None)),
                    and_(GroupCredit.chat_type == "channel", Channel.id.is_not(None)),
                ),
            )
            .with_for_update()
        )
        result = await session.execute(stmt)
        credits = list(result.scalars().all())
        count = 0
        for credit in credits:
            credit.credit_days = max(0, credit.credit_days - 1)
            if credit.credit_days == 0:
                credit.status = "expired"
            count += 1
        await session.commit()
        return count


async def add_credit_history(
    chat_id: int,
    chat_type: str,
    operation: str,
    amount: int,
    operated_by: int | None = None,
    note: str | None = None,
) -> CreditHistory:
    amount = ensure_bounded_int(
        amount,
        field_name="credit_days",
        min_value=1,
        max_value=MAX_CREDIT_DAYS,
    )
    async with async_session() as session:
        history = CreditHistory(
            chat_id=chat_id,
            chat_type=chat_type,
            operation=operation,
            amount_days=amount,
            operated_by=operated_by,
            note=note,
        )
        session.add(history)
        await session.commit()
        await session.refresh(history)
        return history

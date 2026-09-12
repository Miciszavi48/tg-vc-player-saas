"""Repository helpers for monthly bot creator invoices."""

from __future__ import annotations

from datetime import date, datetime, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.database.engine import async_session
from app.database.models import MonthlyInvoice

VALID_MONTHLY_INVOICE_STATUSES: frozenset[str] = frozenset(
    {"pending", "sent", "paid", "failed", "cancelled"}
)


def _validate_status(status: str) -> str:
    value = (status or "").strip().lower()
    if value not in VALID_MONTHLY_INVOICE_STATUSES:
        raise ValueError(f"invalid monthly invoice status: {status!r}")
    return value


def _validate_positive_int(value: int, field_name: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise ValueError(f"{field_name} must be a positive integer")
    return parsed


def _validate_non_negative_int(value: int, field_name: str) -> int:
    parsed = int(value)
    if parsed < 0:
        raise ValueError(f"{field_name} must be non-negative")
    return parsed


async def get_invoice_by_owner_period(
    owner_user_id: int,
    period_start: date,
    period_end: date,
) -> MonthlyInvoice | None:
    owner_id = _validate_positive_int(owner_user_id, "owner_user_id")
    async with async_session() as session:
        result = await session.execute(
            select(MonthlyInvoice).where(
                MonthlyInvoice.owner_user_id == owner_id,
                MonthlyInvoice.period_start == period_start,
                MonthlyInvoice.period_end == period_end,
            )
        )
        return result.scalar_one_or_none()


async def get_invoice_by_id(invoice_id: int) -> MonthlyInvoice | None:
    invoice_pk = _validate_positive_int(invoice_id, "invoice_id")
    async with async_session() as session:
        result = await session.execute(
            select(MonthlyInvoice).where(MonthlyInvoice.id == invoice_pk)
        )
        return result.scalar_one_or_none()


async def create_invoice_if_not_exists(
    *,
    owner_user_id: int,
    bot_identifier: str,
    period_start: date,
    period_end: date,
    due_at: datetime,
    amount: int,
    install_count: int,
    private_count: int,
    group_count: int,
    channel_count: int,
    developer_id: int | None = None,
    status: str = "pending",
) -> MonthlyInvoice:
    owner_id = _validate_positive_int(owner_user_id, "owner_user_id")
    invoice_status = _validate_status(status)
    normalized_bot = (bot_identifier or "").strip() or "bot"
    amount_value = _validate_positive_int(amount, "amount")
    counts = {
        "install_count": _validate_non_negative_int(install_count, "install_count"),
        "private_count": _validate_non_negative_int(private_count, "private_count"),
        "group_count": _validate_non_negative_int(group_count, "group_count"),
        "channel_count": _validate_non_negative_int(channel_count, "channel_count"),
    }

    async with async_session() as session:
        result = await session.execute(
            select(MonthlyInvoice).where(
                MonthlyInvoice.owner_user_id == owner_id,
                MonthlyInvoice.period_start == period_start,
                MonthlyInvoice.period_end == period_end,
            )
        )
        existing = result.scalar_one_or_none()
        if existing is not None:
            return existing

        row = MonthlyInvoice(
            owner_user_id=owner_id,
            bot_identifier=normalized_bot,
            period_start=period_start,
            period_end=period_end,
            due_at=due_at,
            amount=amount_value,
            status=invoice_status,
            developer_id=developer_id,
            **counts,
        )
        session.add(row)
        try:
            await session.commit()
        except IntegrityError:
            await session.rollback()
            result = await session.execute(
                select(MonthlyInvoice).where(
                    MonthlyInvoice.owner_user_id == owner_id,
                    MonthlyInvoice.period_start == period_start,
                    MonthlyInvoice.period_end == period_end,
                )
            )
            existing = result.scalar_one_or_none()
            if existing is None:
                raise
            return existing
        await session.refresh(row)
        return row


async def list_invoices_by_owner(owner_user_id: int, *, limit: int = 20) -> list[MonthlyInvoice]:
    owner_id = _validate_positive_int(owner_user_id, "owner_user_id")
    capped_limit = max(1, min(int(limit), 100))
    async with async_session() as session:
        result = await session.execute(
            select(MonthlyInvoice)
            .where(MonthlyInvoice.owner_user_id == owner_id)
            .order_by(MonthlyInvoice.period_start.desc(), MonthlyInvoice.id.desc())
            .limit(capped_limit)
        )
        return list(result.scalars().all())


async def list_recent_invoices(*, limit: int = 20) -> list[MonthlyInvoice]:
    capped_limit = max(1, min(int(limit), 100))
    async with async_session() as session:
        result = await session.execute(
            select(MonthlyInvoice)
            .order_by(MonthlyInvoice.created_at.desc(), MonthlyInvoice.id.desc())
            .limit(capped_limit)
        )
        return list(result.scalars().all())


async def list_sendable_invoices_by_period(
    *,
    period_start: date,
    period_end: date,
    statuses: tuple[str, ...] = ("pending",),
    limit: int = 500,
) -> list[MonthlyInvoice]:
    normalized = tuple(_validate_status(status) for status in statuses)
    capped_limit = max(1, min(int(limit), 500))
    async with async_session() as session:
        result = await session.execute(
            select(MonthlyInvoice)
            .where(
                MonthlyInvoice.period_start == period_start,
                MonthlyInvoice.period_end == period_end,
                MonthlyInvoice.status.in_(normalized),
            )
            .order_by(MonthlyInvoice.owner_user_id.asc(), MonthlyInvoice.id.asc())
            .limit(capped_limit)
        )
        return list(result.scalars().all())


async def list_invoices_by_statuses(
    statuses: tuple[str, ...] = ("pending", "sent", "failed"),
    *,
    limit: int = 100,
) -> list[MonthlyInvoice]:
    normalized = tuple(_validate_status(status) for status in statuses)
    capped_limit = max(1, min(int(limit), 500))
    async with async_session() as session:
        result = await session.execute(
            select(MonthlyInvoice)
            .where(MonthlyInvoice.status.in_(normalized))
            .order_by(MonthlyInvoice.due_at.asc(), MonthlyInvoice.id.asc())
            .limit(capped_limit)
        )
        return list(result.scalars().all())


async def _mark_status(
    invoice_id: int,
    status: str,
    *,
    sent_at: datetime | None = None,
    paid_at: datetime | None = None,
    delivery_error: str | None = None,
) -> MonthlyInvoice | None:
    invoice_pk = _validate_positive_int(invoice_id, "invoice_id")
    invoice_status = _validate_status(status)
    async with async_session() as session:
        result = await session.execute(
            select(MonthlyInvoice).where(MonthlyInvoice.id == invoice_pk)
        )
        row = result.scalar_one_or_none()
        if row is None:
            return None
        row.status = invoice_status
        if sent_at is not None:
            row.sent_at = sent_at
        if paid_at is not None:
            row.paid_at = paid_at
        if delivery_error is not None:
            row.delivery_error = delivery_error[:4000]
        await session.commit()
        await session.refresh(row)
        return row


async def mark_sent(invoice_id: int, *, sent_at: datetime | None = None) -> MonthlyInvoice | None:
    return await _mark_status(
        invoice_id,
        "sent",
        sent_at=sent_at or datetime.now(timezone.utc),
        delivery_error="",
    )


async def mark_failed(invoice_id: int, delivery_error: str) -> MonthlyInvoice | None:
    return await _mark_status(
        invoice_id,
        "failed",
        delivery_error=(delivery_error or "send failed"),
    )


async def mark_paid(invoice_id: int, *, paid_at: datetime | None = None) -> MonthlyInvoice | None:
    return await _mark_status(
        invoice_id,
        "paid",
        paid_at=paid_at or datetime.now(timezone.utc),
        delivery_error="",
    )

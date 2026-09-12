"""Render-only foundation for monthly bot creator invoices."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone, tzinfo
from typing import Awaitable, Callable

from sqlalchemy import func, select

try:
    from zoneinfo import ZoneInfo
except Exception:  # pragma: no cover - Python always has zoneinfo here.
    ZoneInfo = None  # type: ignore[assignment]

from app.config.settings import settings
from app.database.engine import async_session
from app.database.models import Channel, Group, MonthlyInvoice, Owner, User
from app.repositories import monthly_invoice_repo, settings_repo, user_repo
from app.services import owner_scope_service
from app.utils.i18n import t
from app.utils.safe_sender import safe_send_message

MONTHLY_INVOICE_AMOUNT_KEY = "monthly_invoice_amount"
MONTHLY_INVOICE_AUTO_SEND_ENABLED_KEY = "monthly_invoice_auto_send_enabled"
PROJECT_TIMEZONE_NAME = "Asia/Tehran"


def _project_timezone() -> tzinfo:
    if ZoneInfo is not None:
        try:
            return ZoneInfo(PROJECT_TIMEZONE_NAME)
        except Exception:
            pass
    return timezone(timedelta(hours=3, minutes=30), PROJECT_TIMEZONE_NAME)


PROJECT_TIMEZONE = _project_timezone()


@dataclass(slots=True, frozen=True)
class MonthlyInvoicePeriod:
    period_start: date
    period_end: date
    due_at: datetime


@dataclass(slots=True, frozen=True)
class MonthlyInvoiceStats:
    install_count: int
    private_count: int
    group_count: int
    channel_count: int
    private_count_scope: str = "global_non_banned_users"


@dataclass(slots=True, frozen=True)
class MonthlyInvoicePreview:
    owner_user_id: int
    invoice: MonthlyInvoice
    message: str
    stats: MonthlyInvoiceStats


@dataclass(slots=True, frozen=True)
class MonthlyInvoiceBuildResult:
    amount_configured: bool
    amount: int | None
    period: MonthlyInvoicePeriod
    previews: tuple[MonthlyInvoicePreview, ...]
    skipped_reason: str | None = None


@dataclass(slots=True, frozen=True)
class MonthlyInvoiceDeliveryAttempt:
    ok: bool
    error: str | None = None


@dataclass(slots=True, frozen=True)
class MonthlyInvoiceDeliveryItem:
    invoice_id: int
    owner_user_id: int
    status: str
    error: str | None = None


@dataclass(slots=True, frozen=True)
class MonthlyInvoiceDeliveryResult:
    amount_configured: bool
    period: MonthlyInvoicePeriod
    total: int
    sent: int
    failed: int
    skipped: int
    items: tuple[MonthlyInvoiceDeliveryItem, ...]
    skipped_reason: str | None = None


@dataclass(slots=True, frozen=True)
class MonthlyInvoiceAutoSendResult:
    enabled: bool
    amount_configured: bool
    period: MonthlyInvoicePeriod
    prepared_count: int
    checked_count: int
    sent_count: int
    failed_count: int
    skipped_count: int
    error_count: int
    skipped_reason: str | None = None
    delivery: MonthlyInvoiceDeliveryResult | None = None


MonthlyInvoiceSender = Callable[
    [object, int, str],
    Awaitable[MonthlyInvoiceDeliveryAttempt | bool | None],
]


def _now_in_project_tz(now: datetime | None = None) -> datetime:
    value = now or datetime.now(PROJECT_TIMEZONE)
    if value.tzinfo is None:
        return value.replace(tzinfo=PROJECT_TIMEZONE)
    return value.astimezone(PROJECT_TIMEZONE)


def calculate_calendar_month_period(now: datetime | None = None) -> MonthlyInvoicePeriod:
    current = _now_in_project_tz(now)
    period_start = date(current.year, current.month, 1)
    if current.month == 12:
        next_month = date(current.year + 1, 1, 1)
    else:
        next_month = date(current.year, current.month + 1, 1)
    period_end = next_month - timedelta(days=1)
    due_at = datetime.combine(period_end, time(23, 59, 59), tzinfo=PROJECT_TIMEZONE)
    return MonthlyInvoicePeriod(
        period_start=period_start,
        period_end=period_end,
        due_at=due_at,
    )


async def get_monthly_invoice_amount() -> int | None:
    raw = await settings_repo.get_bot_setting(MONTHLY_INVOICE_AMOUNT_KEY)
    if raw is None:
        return None
    normalized = str(raw).strip().replace(",", "")
    if not normalized:
        return None
    try:
        amount = int(normalized)
    except ValueError:
        return None
    return amount if amount > 0 else None


async def get_monthly_invoice_auto_send_enabled() -> bool:
    return await settings_repo.get_bot_setting_bool(
        MONTHLY_INVOICE_AUTO_SEND_ENABLED_KEY,
        default=False,
    )


async def get_active_owner_recipients() -> list[Owner]:
    return await user_repo.get_all_owners()


async def _count_owner_active_installs(model, owner_user_id: int, sudo_user_ids: frozenset[int]) -> int:
    async with async_session() as session:
        stmt = (
            select(func.count())
            .select_from(model)
            .where(
                model.status == "active",
                owner_scope_service.install_scope_clause(model, owner_user_id, sudo_user_ids),
            )
        )
        return int((await session.execute(stmt)).scalar() or 0)


async def _count_global_private_users() -> int:
    async with async_session() as session:
        stmt = select(func.count()).select_from(User).where(User.is_banned.is_(False))
        return int((await session.execute(stmt)).scalar() or 0)


async def collect_stats_snapshot(owner_user_id: int) -> MonthlyInvoiceStats:
    sudo_user_ids = await owner_scope_service.get_sudo_user_ids_for_owner(owner_user_id)
    group_count = await _count_owner_active_installs(Group, owner_user_id, sudo_user_ids)
    channel_count = await _count_owner_active_installs(Channel, owner_user_id, sudo_user_ids)
    private_count = await _count_global_private_users()
    return MonthlyInvoiceStats(
        install_count=group_count + channel_count,
        private_count=private_count,
        group_count=group_count,
        channel_count=channel_count,
    )


def _remaining_time_text(lang: str, due_at: datetime, now: datetime | None = None) -> str:
    current = _now_in_project_tz(now)
    due = due_at if due_at.tzinfo is not None else due_at.replace(tzinfo=PROJECT_TIMEZONE)
    due = due.astimezone(PROJECT_TIMEZONE)
    remaining = due - current
    if remaining.total_seconds() < 0:
        return t(lang, "monthly_invoice.overdue")
    days = math.ceil(remaining.total_seconds() / 86400)
    if days <= 0:
        return t(lang, "monthly_invoice.due_today")
    return t(lang, "monthly_invoice.remaining_days", days=days)


def render_invoice_message(
    invoice: MonthlyInvoice,
    *,
    lang: str = "fa",
    now: datetime | None = None,
) -> str:
    return t(
        lang,
        "monthly_invoice.message",
        bot_name=invoice.bot_identifier,
        start_date=invoice.period_start.isoformat(),
        end_date=invoice.period_end.isoformat(),
        remaining_time=_remaining_time_text(lang, invoice.due_at, now),
        install_count=invoice.install_count,
        private_count=invoice.private_count,
        group_count=invoice.group_count,
        channel_count=invoice.channel_count,
        amount=str(invoice.amount),
        developer_id=invoice.developer_id if invoice.developer_id is not None else "",
    )


def _safe_delivery_error(error: object) -> str:
    if isinstance(error, BaseException):
        return type(error).__name__[:300]
    text = str(error or "send failed").strip()
    return (text or "send failed")[:300]


def _coerce_delivery_attempt(result: MonthlyInvoiceDeliveryAttempt | bool | None) -> MonthlyInvoiceDeliveryAttempt:
    if isinstance(result, MonthlyInvoiceDeliveryAttempt):
        return result
    if isinstance(result, bool):
        return MonthlyInvoiceDeliveryAttempt(ok=result, error=None if result else "send failed")
    return MonthlyInvoiceDeliveryAttempt(ok=True)


async def _safe_monthly_invoice_sender(
    client: object,
    owner_user_id: int,
    message: str,
) -> MonthlyInvoiceDeliveryAttempt:
    ok = await safe_send_message(
        client,
        owner_user_id,
        message,
        mark_unreachable=False,
    )
    return MonthlyInvoiceDeliveryAttempt(ok=ok, error=None if ok else "send failed")


async def create_current_month_invoice_previews(
    *,
    bot_identifier: str,
    developer_id: int | None = None,
    lang: str = "fa",
    now: datetime | None = None,
) -> MonthlyInvoiceBuildResult:
    period = calculate_calendar_month_period(now)
    amount = await get_monthly_invoice_amount()
    if amount is None:
        return MonthlyInvoiceBuildResult(
            amount_configured=False,
            amount=None,
            period=period,
            previews=(),
            skipped_reason=t(lang, "monthly_invoice.amount_not_configured"),
        )

    creator_id = settings.DEVELOPER_ID if developer_id is None else developer_id
    owners = await get_active_owner_recipients()
    previews: list[MonthlyInvoicePreview] = []
    normalized_bot = (bot_identifier or "").strip() or "bot"

    for owner in owners:
        owner_id = int(owner.user_id)
        stats = await collect_stats_snapshot(owner_id)
        invoice = await monthly_invoice_repo.create_invoice_if_not_exists(
            owner_user_id=owner_id,
            bot_identifier=normalized_bot,
            period_start=period.period_start,
            period_end=period.period_end,
            due_at=period.due_at,
            amount=amount,
            install_count=stats.install_count,
            private_count=stats.private_count,
            group_count=stats.group_count,
            channel_count=stats.channel_count,
            developer_id=creator_id,
        )
        previews.append(
            MonthlyInvoicePreview(
                owner_user_id=owner_id,
                invoice=invoice,
                message=render_invoice_message(invoice, lang=lang, now=now),
                stats=stats,
            )
        )

    return MonthlyInvoiceBuildResult(
        amount_configured=True,
        amount=amount,
        period=period,
        previews=tuple(previews),
    )


async def send_prepared_monthly_invoices(
    client: object,
    *,
    lang: str = "fa",
    now: datetime | None = None,
    sender: MonthlyInvoiceSender | None = None,
    limit: int = 500,
) -> MonthlyInvoiceDeliveryResult:
    period = calculate_calendar_month_period(now)
    amount = await get_monthly_invoice_amount()
    if amount is None:
        return MonthlyInvoiceDeliveryResult(
            amount_configured=False,
            period=period,
            total=0,
            sent=0,
            failed=0,
            skipped=0,
            items=(),
            skipped_reason=t(lang, "monthly_invoice.amount_not_configured"),
        )

    invoices = await monthly_invoice_repo.list_sendable_invoices_by_period(
        period_start=period.period_start,
        period_end=period.period_end,
        statuses=("pending",),
        limit=limit,
    )
    send_func = sender or _safe_monthly_invoice_sender
    items: list[MonthlyInvoiceDeliveryItem] = []
    sent = 0
    failed = 0
    skipped = 0

    for invoice in invoices:
        invoice_id = int(invoice.id)
        owner_id = int(invoice.owner_user_id)
        if invoice.status != "pending":
            skipped += 1
            items.append(
                MonthlyInvoiceDeliveryItem(
                    invoice_id=invoice_id,
                    owner_user_id=owner_id,
                    status="skipped",
                    error="not_pending",
                )
            )
            continue

        if not await user_repo.is_owner(owner_id):
            skipped += 1
            items.append(
                MonthlyInvoiceDeliveryItem(
                    invoice_id=invoice_id,
                    owner_user_id=owner_id,
                    status="skipped",
                    error="inactive_owner",
                )
            )
            continue

        message = render_invoice_message(invoice, lang=lang, now=now)
        try:
            attempt = _coerce_delivery_attempt(
                await send_func(client, owner_id, message)
            )
        except Exception as exc:
            error = _safe_delivery_error(exc)
            await monthly_invoice_repo.mark_failed(invoice_id, error)
            failed += 1
            items.append(
                MonthlyInvoiceDeliveryItem(
                    invoice_id=invoice_id,
                    owner_user_id=owner_id,
                    status="failed",
                    error=error,
                )
            )
            continue

        if attempt.ok:
            await monthly_invoice_repo.mark_sent(invoice_id)
            sent += 1
            items.append(
                MonthlyInvoiceDeliveryItem(
                    invoice_id=invoice_id,
                    owner_user_id=owner_id,
                    status="sent",
                )
            )
            continue

        error = _safe_delivery_error(attempt.error)
        await monthly_invoice_repo.mark_failed(invoice_id, error)
        failed += 1
        items.append(
            MonthlyInvoiceDeliveryItem(
                invoice_id=invoice_id,
                owner_user_id=owner_id,
                status="failed",
                error=error,
            )
        )

    return MonthlyInvoiceDeliveryResult(
        amount_configured=True,
        period=period,
        total=len(invoices),
        sent=sent,
        failed=failed,
        skipped=skipped,
        items=tuple(items),
    )


async def run_monthly_invoice_auto_send_job(
    client: object,
    *,
    lang: str = "fa",
    now: datetime | None = None,
    sender: MonthlyInvoiceSender | None = None,
    bot_identifier: str | None = None,
) -> MonthlyInvoiceAutoSendResult:
    period = calculate_calendar_month_period(now)
    enabled = await get_monthly_invoice_auto_send_enabled()
    if not enabled:
        return MonthlyInvoiceAutoSendResult(
            enabled=False,
            amount_configured=False,
            period=period,
            prepared_count=0,
            checked_count=0,
            sent_count=0,
            failed_count=0,
            skipped_count=0,
            error_count=0,
            skipped_reason=t(lang, "monthly_invoice.auto_send_disabled"),
        )

    amount = await get_monthly_invoice_amount()
    if amount is None:
        return MonthlyInvoiceAutoSendResult(
            enabled=True,
            amount_configured=False,
            period=period,
            prepared_count=0,
            checked_count=0,
            sent_count=0,
            failed_count=0,
            skipped_count=0,
            error_count=0,
            skipped_reason=t(lang, "monthly_invoice.scheduler_amount_missing"),
        )

    try:
        prepared = await create_current_month_invoice_previews(
            bot_identifier=bot_identifier or t(lang, "monthly_invoice.bot_identifier_default"),
            developer_id=settings.DEVELOPER_ID,
            lang=lang,
            now=now,
        )
        if not prepared.amount_configured:
            return MonthlyInvoiceAutoSendResult(
                enabled=True,
                amount_configured=False,
                period=prepared.period,
                prepared_count=0,
                checked_count=0,
                sent_count=0,
                failed_count=0,
                skipped_count=0,
                error_count=0,
                skipped_reason=prepared.skipped_reason,
            )

        delivery = await send_prepared_monthly_invoices(
            client,
            lang=lang,
            now=now,
            sender=sender,
        )
        return MonthlyInvoiceAutoSendResult(
            enabled=True,
            amount_configured=delivery.amount_configured,
            period=delivery.period,
            prepared_count=len(prepared.previews),
            checked_count=delivery.total,
            sent_count=delivery.sent,
            failed_count=delivery.failed,
            skipped_count=delivery.skipped,
            error_count=delivery.failed,
            skipped_reason=delivery.skipped_reason,
            delivery=delivery,
        )
    except Exception as exc:
        return MonthlyInvoiceAutoSendResult(
            enabled=True,
            amount_configured=True,
            period=period,
            prepared_count=0,
            checked_count=0,
            sent_count=0,
            failed_count=0,
            skipped_count=0,
            error_count=1,
            skipped_reason=_safe_delivery_error(exc),
        )

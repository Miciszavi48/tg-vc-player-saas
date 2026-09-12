from __future__ import annotations

import importlib
import json
import re
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

from app.database.engine import async_session
from app.database.models import Channel, Group, MonthlyInvoice, Owner, Sudo, User
from app.repositories import monthly_invoice_repo, settings_repo
from app.services import monthly_invoice_service


def _due(year: int, month: int, day: int) -> datetime:
    return datetime(year, month, day, 23, 59, 59, tzinfo=monthly_invoice_service.PROJECT_TIMEZONE)


async def _add_row(row) -> None:
    async with async_session() as session:
        session.add(row)
        await session.commit()


def test_monthly_invoice_model_has_required_schema() -> None:
    table = MonthlyInvoice.__table__
    columns = {column.name for column in table.columns}
    constraints = {constraint.name for constraint in table.constraints}
    indexes = {idx.name: tuple(col.name for col in idx.columns) for idx in table.indexes}

    assert table.name == "monthly_invoices"
    assert {
        "id",
        "owner_user_id",
        "bot_identifier",
        "period_start",
        "period_end",
        "due_at",
        "amount",
        "status",
        "sent_at",
        "paid_at",
        "install_count",
        "private_count",
        "group_count",
        "channel_count",
        "developer_id",
        "delivery_error",
        "created_at",
        "updated_at",
    }.issubset(columns)
    assert "uq_monthly_invoices_owner_period" in constraints
    assert indexes["idx_monthly_invoices_status_due"] == ("status", "due_at")
    assert indexes["idx_monthly_invoices_owner_created"] == ("owner_user_id", "created_at")


def test_monthly_invoice_migration_metadata() -> None:
    migration = importlib.import_module("app.database.migrations.versions.0028_monthly_invoices")

    assert migration.revision == "0028_monthly_invoices"
    assert migration.down_revision == "0027_player_deputies_vip_expiry"
    assert migration.TABLE_NAME == "monthly_invoices"


def test_schema_drift_checker_tracks_monthly_invoices() -> None:
    import importlib.util
    import sys

    script_path = Path("scripts/db_schema_drift_check.py")
    spec = importlib.util.spec_from_file_location("db_schema_drift_check", script_path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["db_schema_drift_check"] = mod
    spec.loader.exec_module(mod)

    assert "monthly_invoices" in mod.REQUIRED_TABLES
    required = {(table, column) for table, column in mod.REQUIRED_COLUMNS}
    for column in (
        "owner_user_id",
        "period_start",
        "period_end",
        "due_at",
        "amount",
        "status",
        "install_count",
        "private_count",
        "group_count",
        "channel_count",
    ):
        assert ("monthly_invoices", column) in required
    assert "idx_monthly_invoices_status_due" in mod.REQUIRED_INDEXES


@pytest.mark.asyncio
async def test_repository_creates_invoice_and_prevents_duplicate_owner_period() -> None:
    first = await monthly_invoice_repo.create_invoice_if_not_exists(
        owner_user_id=930_001,
        bot_identifier="music-bot",
        period_start=date(2099, 1, 1),
        period_end=date(2099, 1, 31),
        due_at=_due(2099, 1, 31),
        amount=250_000,
        install_count=3,
        private_count=10,
        group_count=2,
        channel_count=1,
        developer_id=123456789,
    )
    duplicate = await monthly_invoice_repo.create_invoice_if_not_exists(
        owner_user_id=930_001,
        bot_identifier="changed",
        period_start=date(2099, 1, 1),
        period_end=date(2099, 1, 31),
        due_at=_due(2099, 1, 31),
        amount=999_000,
        install_count=99,
        private_count=99,
        group_count=99,
        channel_count=99,
        developer_id=123456789,
    )

    assert duplicate.id == first.id
    assert duplicate.amount == 250_000
    assert duplicate.install_count == 3


@pytest.mark.asyncio
async def test_repository_marks_sent_failed_and_paid() -> None:
    invoice = await monthly_invoice_repo.create_invoice_if_not_exists(
        owner_user_id=930_002,
        bot_identifier="music-bot",
        period_start=date(2099, 2, 1),
        period_end=date(2099, 2, 28),
        due_at=_due(2099, 2, 28),
        amount=300_000,
        install_count=1,
        private_count=5,
        group_count=1,
        channel_count=0,
        developer_id=123456789,
    )

    sent = await monthly_invoice_repo.mark_sent(invoice.id)
    assert sent is not None
    assert sent.status == "sent"
    assert sent.sent_at is not None

    failed = await monthly_invoice_repo.mark_failed(invoice.id, "blocked by user")
    assert failed is not None
    assert failed.status == "failed"
    assert failed.delivery_error == "blocked by user"

    paid = await monthly_invoice_repo.mark_paid(invoice.id)
    assert paid is not None
    assert paid.status == "paid"
    assert paid.paid_at is not None
    assert paid.delivery_error == ""


@pytest.mark.asyncio
async def test_repository_lists_by_owner_and_status() -> None:
    invoice = await monthly_invoice_repo.create_invoice_if_not_exists(
        owner_user_id=930_003,
        bot_identifier="music-bot",
        period_start=date(2099, 3, 1),
        period_end=date(2099, 3, 31),
        due_at=_due(2099, 3, 31),
        amount=350_000,
        install_count=2,
        private_count=8,
        group_count=2,
        channel_count=0,
        developer_id=123456789,
    )

    owner_rows = await monthly_invoice_repo.list_invoices_by_owner(930_003)
    status_rows = await monthly_invoice_repo.list_invoices_by_statuses(("pending",))
    recent_rows = await monthly_invoice_repo.list_recent_invoices(limit=20)
    by_id = await monthly_invoice_repo.get_invoice_by_id(invoice.id)

    assert any(row.id == invoice.id for row in owner_rows)
    assert any(row.id == invoice.id for row in status_rows)
    assert any(row.id == invoice.id for row in recent_rows)
    assert by_id is not None
    assert by_id.id == invoice.id


@pytest.mark.asyncio
async def test_repository_lists_sendable_pending_invoices_by_period_only() -> None:
    pending = await monthly_invoice_repo.create_invoice_if_not_exists(
        owner_user_id=930_010,
        bot_identifier="music-bot",
        period_start=date(2099, 4, 1),
        period_end=date(2099, 4, 30),
        due_at=_due(2099, 4, 30),
        amount=250_000,
        install_count=1,
        private_count=1,
        group_count=1,
        channel_count=0,
        developer_id=123456789,
        status="pending",
    )
    for owner_id, status in (
        (930_011, "sent"),
        (930_012, "paid"),
        (930_013, "cancelled"),
    ):
        await monthly_invoice_repo.create_invoice_if_not_exists(
            owner_user_id=owner_id,
            bot_identifier="music-bot",
            period_start=date(2099, 4, 1),
            period_end=date(2099, 4, 30),
            due_at=_due(2099, 4, 30),
            amount=250_000,
            install_count=1,
            private_count=1,
            group_count=1,
            channel_count=0,
            developer_id=123456789,
            status=status,
        )

    rows = await monthly_invoice_repo.list_sendable_invoices_by_period(
        period_start=date(2099, 4, 1),
        period_end=date(2099, 4, 30),
    )

    assert [row.id for row in rows] == [pending.id]


def test_service_calculates_calendar_month_period_in_project_timezone() -> None:
    period = monthly_invoice_service.calculate_calendar_month_period(
        datetime(2026, 7, 2, 12, 0, tzinfo=monthly_invoice_service.PROJECT_TIMEZONE)
    )

    assert period.period_start == date(2026, 7, 1)
    assert period.period_end == date(2026, 7, 31)
    assert period.due_at.date() == date(2026, 7, 31)
    assert period.due_at.hour == 23


@pytest.mark.asyncio
async def test_service_owner_recipients_use_active_global_owners_only() -> None:
    active_owner = 930_101
    inactive_owner = 930_102
    sudo_only = 930_103
    owner_and_sudo = 930_104

    await _add_row(Owner(user_id=active_owner, is_active=True))
    await _add_row(Owner(user_id=inactive_owner, is_active=False))
    await _add_row(Sudo(user_id=sudo_only, is_active=True))
    await _add_row(Owner(user_id=owner_and_sudo, is_active=True))
    await _add_row(Sudo(user_id=owner_and_sudo, is_active=True))

    recipients = await monthly_invoice_service.get_active_owner_recipients()
    recipient_ids = {owner.user_id for owner in recipients}

    assert active_owner in recipient_ids
    assert inactive_owner not in recipient_ids
    assert sudo_only not in recipient_ids
    assert owner_and_sudo in recipient_ids


@pytest.mark.asyncio
async def test_service_handles_missing_monthly_amount_safely() -> None:
    with patch(
        "app.services.monthly_invoice_service.settings_repo.get_bot_setting",
        AsyncMock(return_value=None),
    ):
        result = await monthly_invoice_service.create_current_month_invoice_previews(
            bot_identifier="music-bot",
            developer_id=123456789,
            now=datetime(2026, 7, 2, 12, 0, tzinfo=monthly_invoice_service.PROJECT_TIMEZONE),
        )

    assert result.amount_configured is False
    assert result.amount is None
    assert result.previews == ()
    assert "تنظیم نشده" in (result.skipped_reason or "")


@pytest.mark.asyncio
async def test_service_uses_configured_amount_and_stores_stats_snapshot() -> None:
    owners = [SimpleNamespace(user_id=930_201), SimpleNamespace(user_id=930_202)]

    def _invoice_for_call(**kwargs):
        return MonthlyInvoice(
            id=kwargs["owner_user_id"],
            owner_user_id=kwargs["owner_user_id"],
            bot_identifier=kwargs["bot_identifier"],
            period_start=kwargs["period_start"],
            period_end=kwargs["period_end"],
            due_at=kwargs["due_at"],
            amount=kwargs["amount"],
            install_count=kwargs["install_count"],
            private_count=kwargs["private_count"],
            group_count=kwargs["group_count"],
            channel_count=kwargs["channel_count"],
            developer_id=kwargs["developer_id"],
        )

    create_mock = AsyncMock(side_effect=_invoice_for_call)
    stats = monthly_invoice_service.MonthlyInvoiceStats(
        install_count=4,
        private_count=20,
        group_count=3,
        channel_count=1,
    )

    with (
        patch(
            "app.services.monthly_invoice_service.get_monthly_invoice_amount",
            AsyncMock(return_value=450_000),
        ),
        patch(
            "app.services.monthly_invoice_service.get_active_owner_recipients",
            AsyncMock(return_value=owners),
        ),
        patch(
            "app.services.monthly_invoice_service.collect_stats_snapshot",
            AsyncMock(return_value=stats),
        ),
        patch(
            "app.services.monthly_invoice_service.monthly_invoice_repo.create_invoice_if_not_exists",
            create_mock,
        ),
    ):
        result = await monthly_invoice_service.create_current_month_invoice_previews(
            bot_identifier="music-bot",
            developer_id=123456789,
            now=datetime(2026, 7, 2, 12, 0, tzinfo=monthly_invoice_service.PROJECT_TIMEZONE),
        )

    assert result.amount_configured is True
    assert result.amount == 450_000
    assert len(result.previews) == 2
    first_call = create_mock.await_args_list[0].kwargs
    assert first_call["owner_user_id"] == 930_201
    assert first_call["amount"] == 450_000
    assert first_call["install_count"] == 4
    assert first_call["private_count"] == 20
    assert first_call["group_count"] == 3
    assert first_call["channel_count"] == 1
    assert "450000" in result.previews[0].message


@pytest.mark.asyncio
async def test_service_collects_owner_scoped_group_channel_and_global_private_stats() -> None:
    owner_id = 930_301
    sudo_id = 930_302
    await _add_row(Owner(user_id=owner_id, is_active=True))
    await _add_row(Sudo(user_id=sudo_id, added_by=owner_id, is_active=True))
    await _add_row(Group(chat_id=-930_301_001, chat_title="owner group", status="active", installed_by=owner_id))
    await _add_row(Group(chat_id=-930_301_002, chat_title="sudo group", status="active", installed_by=sudo_id))
    await _add_row(Group(chat_id=-930_301_003, chat_title="inactive group", status="inactive", installed_by=owner_id))
    await _add_row(Channel(chat_id=-930_301_004, chat_title="sudo channel", status="active", installed_by=sudo_id))
    await _add_row(Channel(chat_id=-930_301_005, chat_title="other channel", status="active", installed_by=999_999))
    await _add_row(User(user_id=930_301_010, is_banned=False))
    await _add_row(User(user_id=930_301_011, is_banned=True))

    stats = await monthly_invoice_service.collect_stats_snapshot(owner_id)

    assert stats.group_count == 2
    assert stats.channel_count == 1
    assert stats.install_count == 3
    assert stats.private_count >= 1
    assert stats.private_count_scope == "global_non_banned_users"


@pytest.mark.asyncio
async def test_delivery_sends_pending_invoice_to_active_owner_and_prevents_duplicate_send() -> None:
    owner_id = 930_401
    now = datetime(2099, 5, 2, 12, 0, tzinfo=monthly_invoice_service.PROJECT_TIMEZONE)
    await _add_row(Owner(user_id=owner_id, is_active=True))
    await settings_repo.set_bot_setting(monthly_invoice_service.MONTHLY_INVOICE_AMOUNT_KEY, "450000")
    invoice = await monthly_invoice_repo.create_invoice_if_not_exists(
        owner_user_id=owner_id,
        bot_identifier="music-bot",
        period_start=date(2099, 5, 1),
        period_end=date(2099, 5, 31),
        due_at=_due(2099, 5, 31),
        amount=450_000,
        install_count=2,
        private_count=20,
        group_count=1,
        channel_count=1,
        developer_id=123456789,
    )
    sender = AsyncMock(return_value=True)
    client = SimpleNamespace()

    first = await monthly_invoice_service.send_prepared_monthly_invoices(
        client,
        now=now,
        sender=sender,
    )
    second = await monthly_invoice_service.send_prepared_monthly_invoices(
        client,
        now=now,
        sender=sender,
    )
    refreshed = await monthly_invoice_repo.get_invoice_by_id(invoice.id)

    assert first.total == 1
    assert first.sent == 1
    assert first.failed == 0
    assert second.total == 0
    assert sender.await_count == 1
    assert sender.await_args.args[1] == owner_id
    assert "music-bot" in sender.await_args.args[2]
    assert refreshed is not None
    assert refreshed.status == "sent"
    assert refreshed.sent_at is not None


@pytest.mark.asyncio
async def test_delivery_failed_send_marks_failed_and_continues() -> None:
    owner_a = 930_411
    owner_b = 930_412
    now = datetime(2099, 6, 2, 12, 0, tzinfo=monthly_invoice_service.PROJECT_TIMEZONE)
    await _add_row(Owner(user_id=owner_a, is_active=True))
    await _add_row(Owner(user_id=owner_b, is_active=True))
    await settings_repo.set_bot_setting(monthly_invoice_service.MONTHLY_INVOICE_AMOUNT_KEY, "450000")
    failed_invoice = await monthly_invoice_repo.create_invoice_if_not_exists(
        owner_user_id=owner_a,
        bot_identifier="music-bot",
        period_start=date(2099, 6, 1),
        period_end=date(2099, 6, 30),
        due_at=_due(2099, 6, 30),
        amount=450_000,
        install_count=1,
        private_count=20,
        group_count=1,
        channel_count=0,
        developer_id=123456789,
    )
    sent_invoice = await monthly_invoice_repo.create_invoice_if_not_exists(
        owner_user_id=owner_b,
        bot_identifier="music-bot",
        period_start=date(2099, 6, 1),
        period_end=date(2099, 6, 30),
        due_at=_due(2099, 6, 30),
        amount=450_000,
        install_count=1,
        private_count=20,
        group_count=1,
        channel_count=0,
        developer_id=123456789,
    )
    sender = AsyncMock(
        side_effect=[
            monthly_invoice_service.MonthlyInvoiceDeliveryAttempt(False, "blocked by user"),
            True,
        ]
    )

    result = await monthly_invoice_service.send_prepared_monthly_invoices(
        SimpleNamespace(),
        now=now,
        sender=sender,
    )
    failed_row = await monthly_invoice_repo.get_invoice_by_id(failed_invoice.id)
    sent_row = await monthly_invoice_repo.get_invoice_by_id(sent_invoice.id)

    assert result.total == 2
    assert result.failed == 1
    assert result.sent == 1
    assert sender.await_count == 2
    assert failed_row is not None
    assert failed_row.status == "failed"
    assert failed_row.delivery_error == "blocked by user"
    assert sent_row is not None
    assert sent_row.status == "sent"


@pytest.mark.asyncio
async def test_delivery_skips_inactive_or_sudo_only_and_sends_owner_sudo() -> None:
    inactive_owner = 930_421
    sudo_only = 930_422
    owner_and_sudo = 930_423
    now = datetime(2099, 7, 2, 12, 0, tzinfo=monthly_invoice_service.PROJECT_TIMEZONE)
    await _add_row(Owner(user_id=inactive_owner, is_active=False))
    await _add_row(Sudo(user_id=sudo_only, is_active=True))
    await _add_row(Owner(user_id=owner_and_sudo, is_active=True))
    await _add_row(Sudo(user_id=owner_and_sudo, is_active=True))
    await settings_repo.set_bot_setting(monthly_invoice_service.MONTHLY_INVOICE_AMOUNT_KEY, "450000")
    invoices = []
    for owner_id in (inactive_owner, sudo_only, owner_and_sudo):
        invoices.append(
            await monthly_invoice_repo.create_invoice_if_not_exists(
                owner_user_id=owner_id,
                bot_identifier="music-bot",
                period_start=date(2099, 7, 1),
                period_end=date(2099, 7, 31),
                due_at=_due(2099, 7, 31),
                amount=450_000,
                install_count=1,
                private_count=20,
                group_count=1,
                channel_count=0,
                developer_id=123456789,
            )
        )
    sender = AsyncMock(return_value=True)

    result = await monthly_invoice_service.send_prepared_monthly_invoices(
        SimpleNamespace(),
        now=now,
        sender=sender,
    )
    rows = [await monthly_invoice_repo.get_invoice_by_id(invoice.id) for invoice in invoices]

    assert result.total == 3
    assert result.sent == 1
    assert result.skipped == 2
    sender.assert_awaited_once()
    assert sender.await_args.args[1] == owner_and_sudo
    assert [row.status for row in rows if row is not None] == ["pending", "pending", "sent"]


@pytest.mark.asyncio
async def test_monthly_amount_setting_reads_positive_integer_only() -> None:
    await settings_repo.set_bot_setting(monthly_invoice_service.MONTHLY_INVOICE_AMOUNT_KEY, "123456")
    assert await monthly_invoice_service.get_monthly_invoice_amount() == 123456

    await settings_repo.set_bot_setting(monthly_invoice_service.MONTHLY_INVOICE_AMOUNT_KEY, "0")
    assert await monthly_invoice_service.get_monthly_invoice_amount() is None

    await settings_repo.set_bot_setting(monthly_invoice_service.MONTHLY_INVOICE_AMOUNT_KEY, "not-number")
    assert await monthly_invoice_service.get_monthly_invoice_amount() is None


@pytest.mark.asyncio
async def test_auto_send_enabled_setting_defaults_to_disabled() -> None:
    with patch(
        "app.services.monthly_invoice_service.settings_repo.get_bot_setting_bool",
        AsyncMock(return_value=False),
    ) as get_mock:
        assert await monthly_invoice_service.get_monthly_invoice_auto_send_enabled() is False

    get_mock.assert_awaited_once_with(
        monthly_invoice_service.MONTHLY_INVOICE_AUTO_SEND_ENABLED_KEY,
        default=False,
    )


@pytest.mark.asyncio
async def test_auto_send_job_disabled_does_not_prepare_or_send() -> None:
    prepare_mock = AsyncMock()
    send_mock = AsyncMock()

    with (
        patch(
            "app.services.monthly_invoice_service.get_monthly_invoice_auto_send_enabled",
            AsyncMock(return_value=False),
        ),
        patch(
            "app.services.monthly_invoice_service.create_current_month_invoice_previews",
            prepare_mock,
        ),
        patch(
            "app.services.monthly_invoice_service.send_prepared_monthly_invoices",
            send_mock,
        ),
    ):
        result = await monthly_invoice_service.run_monthly_invoice_auto_send_job(
            SimpleNamespace(),
            now=datetime(2099, 8, 2, 12, 0, tzinfo=monthly_invoice_service.PROJECT_TIMEZONE),
        )

    assert result.enabled is False
    assert result.prepared_count == 0
    assert result.checked_count == 0
    prepare_mock.assert_not_awaited()
    send_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_auto_send_job_enabled_missing_amount_does_not_send() -> None:
    prepare_mock = AsyncMock()
    send_mock = AsyncMock()

    with (
        patch(
            "app.services.monthly_invoice_service.get_monthly_invoice_auto_send_enabled",
            AsyncMock(return_value=True),
        ),
        patch(
            "app.services.monthly_invoice_service.get_monthly_invoice_amount",
            AsyncMock(return_value=None),
        ),
        patch(
            "app.services.monthly_invoice_service.create_current_month_invoice_previews",
            prepare_mock,
        ),
        patch(
            "app.services.monthly_invoice_service.send_prepared_monthly_invoices",
            send_mock,
        ),
    ):
        result = await monthly_invoice_service.run_monthly_invoice_auto_send_job(
            SimpleNamespace(),
            now=datetime(2099, 8, 2, 12, 0, tzinfo=monthly_invoice_service.PROJECT_TIMEZONE),
        )

    assert result.enabled is True
    assert result.amount_configured is False
    assert result.sent_count == 0
    prepare_mock.assert_not_awaited()
    send_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_auto_send_job_enabled_prepares_and_sends_with_mocked_sender() -> None:
    period = monthly_invoice_service.calculate_calendar_month_period(
        datetime(2099, 8, 2, 12, 0, tzinfo=monthly_invoice_service.PROJECT_TIMEZONE)
    )
    invoice = MonthlyInvoice(
        id=8101,
        owner_user_id=930_801,
        bot_identifier="music-bot",
        period_start=period.period_start,
        period_end=period.period_end,
        due_at=period.due_at,
        amount=450_000,
        status="pending",
        install_count=1,
        private_count=2,
        group_count=1,
        channel_count=0,
        developer_id=123456789,
    )
    prepared = monthly_invoice_service.MonthlyInvoiceBuildResult(
        amount_configured=True,
        amount=450_000,
        period=period,
        previews=(
            monthly_invoice_service.MonthlyInvoicePreview(
                owner_user_id=930_801,
                invoice=invoice,
                message="preview",
                stats=monthly_invoice_service.MonthlyInvoiceStats(
                    install_count=1,
                    private_count=2,
                    group_count=1,
                    channel_count=0,
                ),
            ),
        ),
    )
    delivery = monthly_invoice_service.MonthlyInvoiceDeliveryResult(
        amount_configured=True,
        period=period,
        total=1,
        sent=1,
        failed=0,
        skipped=0,
        items=(monthly_invoice_service.MonthlyInvoiceDeliveryItem(8101, 930_801, "sent"),),
    )

    with (
        patch(
            "app.services.monthly_invoice_service.get_monthly_invoice_auto_send_enabled",
            AsyncMock(return_value=True),
        ),
        patch(
            "app.services.monthly_invoice_service.get_monthly_invoice_amount",
            AsyncMock(return_value=450_000),
        ),
        patch(
            "app.services.monthly_invoice_service.create_current_month_invoice_previews",
            AsyncMock(return_value=prepared),
        ) as prepare_mock,
        patch(
            "app.services.monthly_invoice_service.send_prepared_monthly_invoices",
            AsyncMock(return_value=delivery),
        ) as send_mock,
    ):
        result = await monthly_invoice_service.run_monthly_invoice_auto_send_job(
            SimpleNamespace(),
            now=datetime(2099, 8, 2, 12, 0, tzinfo=monthly_invoice_service.PROJECT_TIMEZONE),
        )

    assert result.enabled is True
    assert result.amount_configured is True
    assert result.prepared_count == 1
    assert result.checked_count == 1
    assert result.sent_count == 1
    assert result.failed_count == 0
    prepare_mock.assert_awaited_once()
    send_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_auto_send_job_repeated_run_does_not_duplicate_delivery() -> None:
    owner_id = 930_901
    now = datetime(2099, 9, 2, 12, 0, tzinfo=monthly_invoice_service.PROJECT_TIMEZONE)
    await _add_row(Owner(user_id=owner_id, is_active=True))
    await settings_repo.set_bot_setting(monthly_invoice_service.MONTHLY_INVOICE_AMOUNT_KEY, "450000")
    await settings_repo.set_bot_setting(
        monthly_invoice_service.MONTHLY_INVOICE_AUTO_SEND_ENABLED_KEY,
        "1",
    )
    sender = AsyncMock(return_value=True)

    with patch(
        "app.services.monthly_invoice_service.get_active_owner_recipients",
        AsyncMock(return_value=[SimpleNamespace(user_id=owner_id)]),
    ):
        first = await monthly_invoice_service.run_monthly_invoice_auto_send_job(
            SimpleNamespace(),
            now=now,
            sender=sender,
            bot_identifier="music-bot",
        )
        second = await monthly_invoice_service.run_monthly_invoice_auto_send_job(
            SimpleNamespace(),
            now=now,
            sender=sender,
            bot_identifier="music-bot",
        )

    assert first.prepared_count == 1
    assert first.checked_count == 1
    assert first.sent_count == 1
    assert second.prepared_count == 1
    assert second.checked_count == 0
    assert second.sent_count == 0
    sender.assert_awaited_once()


def test_scheduler_does_not_register_monthly_invoice_auto_send(monkeypatch) -> None:
    import app.scheduler as scheduler_mod

    fake_scheduler = SimpleNamespace(
        add_job=Mock(),
        start=Mock(),
        get_jobs=Mock(return_value=[]),
    )
    job_mock = AsyncMock()
    monkeypatch.setattr(scheduler_mod, "scheduler", fake_scheduler)
    monkeypatch.setattr(scheduler_mod, "monthly_invoice_auto_send", job_mock)

    scheduler_mod.setup_scheduler(SimpleNamespace(), None)

    jobs = fake_scheduler.add_job.call_args_list
    assert not any(call.kwargs.get("id") == "monthly_invoice_auto_send" for call in jobs)
    job_mock.assert_not_awaited()


def test_persian_monthly_invoice_template_preserves_required_placeholders() -> None:
    data = json.loads(
        Path("app/resources/i18n/fa/features/monthly_invoice.json").read_text(encoding="utf-8")
    )
    template = data["monthly_invoice"]["message"]
    placeholders = set(re.findall(r"{([a-z_]+)}", template))

    assert placeholders == {
        "bot_name",
        "start_date",
        "end_date",
        "remaining_time",
        "install_count",
        "private_count",
        "group_count",
        "channel_count",
        "amount",
        "developer_id",
    }


def test_monthly_invoice_i18n_keys_resolve_in_fa_and_en() -> None:
    from app.utils.i18n import t

    kwargs = {
        "bot_name": "music-bot",
        "start_date": "2026-07-01",
        "end_date": "2026-07-31",
        "remaining_time": "29 روز",
        "install_count": 3,
        "private_count": 20,
        "group_count": 2,
        "channel_count": 1,
        "amount": "450000",
        "developer_id": 123456789,
    }
    for lang in ("fa", "en"):
        rendered = t(lang, "monthly_invoice.message", **kwargs)
        assert not rendered.startswith("[missing:")
        assert "music-bot" in rendered
        assert "450000" in rendered
        for status in ("pending", "sent", "paid", "failed", "cancelled"):
            assert not t(lang, f"monthly_invoice.status.{status}").startswith("[missing:")
        for key in (
            "monthly_invoice.send_btn",
            "monthly_invoice.send_title",
            "monthly_invoice.no_prepared_invoices",
            "monthly_invoice.send_result_summary",
            "monthly_invoice.send_failed_owner_ids",
            "monthly_invoice.send_skipped_owner_ids",
            "monthly_invoice.send_no_scheduler_note",
            "monthly_invoice.auto_send_enable_btn",
            "monthly_invoice.auto_send_disable_btn",
            "monthly_invoice.auto_send_enabled_label",
            "monthly_invoice.auto_send_disabled_label",
            "monthly_invoice.auto_send_toggle_saved",
            "monthly_invoice.auto_send_disabled",
            "monthly_invoice.auto_send_safety_note",
            "monthly_invoice.scheduler_amount_missing",
            "monthly_invoice.scheduler_summary",
        ):
            assert not t(
                lang,
                key,
                status="enabled",
                prepared_count=1,
                checked_count=1,
                sent_count=1,
                failed_count=0,
                skipped_count=0,
            ).startswith("[missing:")

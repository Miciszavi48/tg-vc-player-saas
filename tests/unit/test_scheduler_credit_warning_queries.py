from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.repositories import credit_repo
from app.scheduler import check_channel_credit_warnings, check_group_credit_warnings


def _mock_session(rows: list) -> MagicMock:
    sess = AsyncMock()
    sess.__aenter__ = AsyncMock(return_value=sess)
    sess.__aexit__ = AsyncMock(return_value=False)
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = rows
    sess.execute = AsyncMock(return_value=mock_result)
    return sess


@pytest.mark.asyncio
async def test_get_expiring_chats_filters_group_type_in_sql():
    row = SimpleNamespace(chat_id=-1, chat_type="group", credit_days=1)
    sess = _mock_session([row])

    with patch("app.repositories.group_runtime_repo.async_session", return_value=sess):
        result = await credit_repo.get_expiring_chats(24, chat_type="group")

    assert result == [row]
    stmt = sess.execute.await_args.args[0]
    compiled = str(stmt)
    assert "chat_type" in compiled
    assert "groups" in compiled
    assert "status" in compiled


@pytest.mark.asyncio
async def test_get_expiring_chats_filters_channel_type_in_sql():
    row = SimpleNamespace(chat_id=-2, chat_type="channel", credit_days=2)
    sess = _mock_session([row])

    with patch("app.repositories.group_runtime_repo.async_session", return_value=sess):
        result = await credit_repo.get_expiring_chats(24, chat_type="channel")

    assert result == [row]
    stmt = sess.execute.await_args.args[0]
    compiled = str(stmt)
    assert "chat_type" in compiled
    assert "channels" in compiled
    assert "status" in compiled


@pytest.mark.asyncio
async def test_group_warning_job_uses_group_chat_type_only():
    group_row = SimpleNamespace(chat_id=-10, chat_type="group", credit_days=1)
    bot = object()

    with (
        patch("app.scheduler.CreditService.get_expiring_chats", AsyncMock(return_value=[group_row])) as fetch_mock,
        patch("app.scheduler.NotificationService.notify_credit_warning", AsyncMock()) as notify_mock,
        patch("app.scheduler._bot", bot),
    ):
        await check_group_credit_warnings()

    fetch_mock.assert_awaited_once_with(hours=24, chat_type="group")
    notify_mock.assert_awaited_once_with(bot, -10, 1, "group")


@pytest.mark.asyncio
async def test_channel_warning_job_uses_channel_chat_type_only():
    channel_row = SimpleNamespace(chat_id=-20, chat_type="channel", credit_days=3)
    bot = object()

    with (
        patch("app.scheduler.CreditService.get_expiring_chats", AsyncMock(return_value=[channel_row])) as fetch_mock,
        patch("app.scheduler.NotificationService.notify_credit_warning", AsyncMock()) as notify_mock,
        patch("app.scheduler._bot", bot),
    ):
        await check_channel_credit_warnings()

    fetch_mock.assert_awaited_once_with(hours=24, chat_type="channel")
    notify_mock.assert_awaited_once_with(bot, -20, 3, "channel")


@pytest.mark.asyncio
async def test_group_warning_does_not_notify_channel_rows():
    with (
        patch(
            "app.scheduler.CreditService.get_expiring_chats",
            AsyncMock(return_value=[]),
        ),
        patch("app.scheduler.NotificationService.notify_credit_warning", AsyncMock()) as notify_mock,
        patch("app.scheduler._bot", object()),
    ):
        await check_group_credit_warnings()

    notify_mock.assert_not_awaited()

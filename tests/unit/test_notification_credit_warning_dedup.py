from __future__ import annotations

import os
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./.pytest-test-mode.db")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("BOT_TOKEN", "test")
os.environ.setdefault("API_ID", "12345")
os.environ.setdefault("API_HASH", "testhash")
os.environ.setdefault("DEVELOPER_ID", "123456789")

from app.services.notification_service import (
    NotificationService,
    claim_credit_warning_slot,
)
from app.utils.redis_keys import TTL_CREDIT_WARNING_COOLDOWN, credit_warning_sent_key


class _FakeRedis:
    """Minimal async Redis stub supporting SET NX EX for dedup tests."""

    def __init__(self) -> None:
        self.store: dict[str, tuple[str, int | None]] = {}
        self.last_set_kwargs: dict | None = None
        self.raise_on_set: Exception | None = None

    async def set(self, key: str, value: str, *, nx: bool = False, ex: int | None = None) -> bool:
        if self.raise_on_set is not None:
            raise self.raise_on_set
        self.last_set_kwargs = {"key": key, "value": value, "nx": nx, "ex": ex}
        if nx and key in self.store:
            return False
        self.store[key] = (value, ex)
        return True


def _install_log(sudo_id: int, chat_id: int) -> SimpleNamespace:
    return SimpleNamespace(sudo_id=sudo_id, chat_id=chat_id, action="install")


@pytest.mark.asyncio
async def test_first_warning_sends_group_log_and_sudo():
    chat_id = -1003209100513
    sudo_id = 900001
    bot = AsyncMock()
    redis = _FakeRedis()
    fixed_date = date(2026, 6, 8)

    with (
        patch("app.services.notification_service.get_redis", AsyncMock(return_value=redis)),
        patch("app.services.notification_service.date") as date_mod,
        patch("app.utils.safe_sender.safe_send_message", AsyncMock()) as send_mock,
        patch(
            "app.services.notification_service.resolve_log_channel_id",
            AsyncMock(return_value=-100999),
        ),
        patch(
            "app.repositories.log_repo.get_install_logs_for_chat",
            AsyncMock(return_value=[_install_log(sudo_id, chat_id)]),
        ),
    ):
        date_mod.today.return_value = fixed_date
        await NotificationService.notify_credit_warning(bot, chat_id, 2, "group")

    assert send_mock.await_count == 3
    targets = [call.args[1] for call in send_mock.await_args_list]
    assert chat_id in targets
    assert sudo_id in targets
    assert -100999 in targets


@pytest.mark.asyncio
async def test_duplicate_warning_suppresses_all_sends():
    chat_id = -1003209100513
    sudo_id = 900001
    bot = AsyncMock()
    redis = _FakeRedis()
    fixed_date = date(2026, 6, 8)

    with (
        patch("app.services.notification_service.get_redis", AsyncMock(return_value=redis)),
        patch("app.services.notification_service.date") as date_mod,
        patch("app.utils.safe_sender.safe_send_message", AsyncMock()) as send_mock,
        patch(
            "app.services.notification_service.resolve_log_channel_id",
            AsyncMock(return_value=-100999),
        ),
        patch(
            "app.repositories.log_repo.get_install_logs_for_chat",
            AsyncMock(return_value=[_install_log(sudo_id, chat_id)]),
        ),
    ):
        date_mod.today.return_value = fixed_date
        await NotificationService.notify_credit_warning(bot, chat_id, 2, "group")
        await NotificationService.notify_credit_warning(bot, chat_id, 2, "group")

    assert send_mock.await_count == 3


@pytest.mark.asyncio
async def test_remaining_days_change_allows_new_warning():
    chat_id = -1003209100513
    bot = AsyncMock()
    redis = _FakeRedis()
    fixed_date = date(2026, 6, 8)

    with (
        patch("app.services.notification_service.get_redis", AsyncMock(return_value=redis)),
        patch("app.services.notification_service.date") as date_mod,
        patch("app.utils.safe_sender.safe_send_message", AsyncMock()) as send_mock,
        patch(
            "app.services.notification_service.resolve_log_channel_id",
            AsyncMock(return_value=-100999),
        ),
        patch(
            "app.repositories.log_repo.get_install_logs_for_chat",
            AsyncMock(return_value=[]),
        ),
    ):
        date_mod.today.return_value = fixed_date
        await NotificationService.notify_credit_warning(bot, chat_id, 2, "group")
        await NotificationService.notify_credit_warning(bot, chat_id, 1, "group")

    assert send_mock.await_count == 4


@pytest.mark.asyncio
async def test_different_chat_id_allows_separate_warning():
    bot = AsyncMock()
    redis = _FakeRedis()
    fixed_date = date(2026, 6, 8)

    with (
        patch("app.services.notification_service.get_redis", AsyncMock(return_value=redis)),
        patch("app.services.notification_service.date") as date_mod,
        patch("app.utils.safe_sender.safe_send_message", AsyncMock()) as send_mock,
        patch(
            "app.services.notification_service.resolve_log_channel_id",
            AsyncMock(return_value=-100999),
        ),
        patch(
            "app.repositories.log_repo.get_install_logs_for_chat",
            AsyncMock(return_value=[]),
        ),
    ):
        date_mod.today.return_value = fixed_date
        await NotificationService.notify_credit_warning(bot, -1001, 2, "group")
        await NotificationService.notify_credit_warning(bot, -1002, 2, "group")

    assert send_mock.await_count == 4


@pytest.mark.asyncio
async def test_claim_uses_set_nx_ex_with_48h_ttl():
    redis = _FakeRedis()
    fixed_date = date(2026, 6, 8)
    chat_id = -10099

    with (
        patch("app.services.notification_service.get_redis", AsyncMock(return_value=redis)),
        patch("app.services.notification_service.date") as date_mod,
    ):
        date_mod.today.return_value = fixed_date
        assert await claim_credit_warning_slot(chat_id, 2, "group") is True
        assert await claim_credit_warning_slot(chat_id, 2, "group") is False

    assert redis.last_set_kwargs == {
        "key": credit_warning_sent_key(chat_id, 2, "2026-06-08", "group"),
        "value": "1",
        "nx": True,
        "ex": TTL_CREDIT_WARNING_COOLDOWN,
    }
    assert TTL_CREDIT_WARNING_COOLDOWN == 172_800


@pytest.mark.asyncio
async def test_same_chat_id_different_chat_type_allows_separate_warning_claims():
    redis = _FakeRedis()
    fixed_date = date(2026, 6, 8)
    chat_id = -10099

    with (
        patch("app.services.notification_service.get_redis", AsyncMock(return_value=redis)),
        patch("app.services.notification_service.date") as date_mod,
    ):
        date_mod.today.return_value = fixed_date
        assert await claim_credit_warning_slot(chat_id, 2, "group") is True
        assert await claim_credit_warning_slot(chat_id, 2, "channel") is True
        assert await claim_credit_warning_slot(chat_id, 2, "channel") is False


@pytest.mark.asyncio
async def test_credit_warning_key_format():
    from app.utils.redis_keys import instance_key

    assert credit_warning_sent_key(-1003209100513, 2, "2026-06-08", "channel") == (
        instance_key("credit:warn:channel:-1003209100513:2:2026-06-08")
    )


@pytest.mark.asyncio
async def test_redis_failure_skips_send_fail_closed():
    chat_id = -10077
    bot = AsyncMock()
    redis = _FakeRedis()
    redis.raise_on_set = ConnectionError("redis down")

    with (
        patch("app.services.notification_service.get_redis", AsyncMock(return_value=redis)),
        patch("app.utils.safe_sender.safe_send_message", AsyncMock()) as send_mock,
        patch(
            "app.services.notification_service.resolve_log_channel_id",
            AsyncMock(return_value=-100999),
        ),
    ):
        await NotificationService.notify_credit_warning(bot, chat_id, 2, "group")
        assert await claim_credit_warning_slot(chat_id, 2, "group") is False

    send_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_same_date_bucket_suppresses_on_second_claim():
    redis = _FakeRedis()
    fixed_date = date(2026, 6, 8)

    with (
        patch("app.services.notification_service.get_redis", AsyncMock(return_value=redis)),
        patch("app.services.notification_service.date") as date_mod,
    ):
        date_mod.today.return_value = fixed_date
        assert await claim_credit_warning_slot(-50, 1, "group") is True
        assert await claim_credit_warning_slot(-50, 1, "group") is False

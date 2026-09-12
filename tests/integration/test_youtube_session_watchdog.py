"""Focused tests for secret-free YouTube-session operational alerts."""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

os.environ.setdefault("TEST_MODE", "1")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./.pytest-test-mode.db")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/15")
os.environ.setdefault("BOT_TOKEN", "test")
os.environ.setdefault("API_ID", "12345")
os.environ.setdefault("API_HASH", "testhash")
os.environ.setdefault("DEVELOPER_ID", "123456789")

from app.config.settings import settings
from app.repositories.youtube_session_repo import YoutubeSessionSummary
from app.services.youtube_session_watchdog import run_youtube_session_watchdog
from app.utils.redis_keys import youtube_session_unavailable_alert_key


class _FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    async def set(self, key, value, *, nx=False, ex=None):  # noqa: ANN001, ANN201
        if nx and key in self.values:
            return False
        self.values[key] = value
        return True

    async def delete(self, key):  # noqa: ANN001, ANN201
        return int(self.values.pop(key, None) is not None)


@pytest.mark.asyncio
async def test_empty_pool_keeps_anonymous_mode_and_sends_no_alert():
    redis = _FakeRedis()
    sent = AsyncMock(return_value=True)
    with (
        patch("app.services.youtube_session_watchdog.get_redis", AsyncMock(return_value=redis)),
        patch(
            "app.services.youtube_session_watchdog.youtube_session_repo.get_summary",
            AsyncMock(return_value=YoutubeSessionSummary(0, 0, 0, 0, None, None)),
        ),
        patch("app.services.youtube_session_watchdog.safe_send_message", sent),
    ):
        await run_youtube_session_watchdog(SimpleNamespace())

    sent.assert_not_awaited()


@pytest.mark.asyncio
async def test_unavailable_pool_alert_is_deduplicated_and_targets_active_owners(monkeypatch):
    monkeypatch.setattr(settings, "DEVELOPER_IDS", frozenset({10}))
    redis = _FakeRedis()
    summary = YoutubeSessionSummary(2, 0, 1, 1, None, None)
    sent = AsyncMock(return_value=True)
    with (
        patch("app.services.youtube_session_watchdog.get_redis", AsyncMock(return_value=redis)),
        patch("app.services.youtube_session_watchdog.youtube_session_repo.get_summary", AsyncMock(return_value=summary)),
        patch("app.services.youtube_session_watchdog.user_repo.get_all_owners", AsyncMock(return_value=[SimpleNamespace(user_id=20)])),
        patch("app.services.youtube_session_watchdog.safe_send_message", sent),
    ):
        await run_youtube_session_watchdog(SimpleNamespace())
        await run_youtube_session_watchdog(SimpleNamespace())

    assert {call.args[1] for call in sent.await_args_list} == {10, 20}
    assert sent.await_count == 2


@pytest.mark.asyncio
async def test_recovery_and_expiry_alerts_are_secret_free(monkeypatch):
    monkeypatch.setattr(settings, "DEVELOPER_IDS", frozenset({10}))
    now = datetime(2026, 7, 13, tzinfo=timezone.utc)
    redis = _FakeRedis()
    redis.values[youtube_session_unavailable_alert_key()] = "1"
    summary = YoutubeSessionSummary(1, 1, 0, 0, None, None)
    expiring = SimpleNamespace(id=7, cookie_expires_at=now + timedelta(days=2))
    sent = AsyncMock(return_value=True)
    with (
        patch("app.services.youtube_session_watchdog.get_redis", AsyncMock(return_value=redis)),
        patch("app.services.youtube_session_watchdog.youtube_session_repo.get_summary", AsyncMock(return_value=summary)),
        patch("app.services.youtube_session_watchdog.youtube_session_repo.list_expiring_active_sessions", AsyncMock(return_value=[expiring])),
        patch("app.services.youtube_session_watchdog.user_repo.get_all_owners", AsyncMock(return_value=[])),
        patch("app.services.youtube_session_watchdog.safe_send_message", sent),
    ):
        await run_youtube_session_watchdog(SimpleNamespace(), now=now)

    bodies = [call.args[2] for call in sent.await_args_list]
    assert any("YT-7" in body for body in bodies)
    assert all("SID" not in body and "redacted" not in body for body in bodies)

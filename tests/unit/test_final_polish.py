"""Tests for final polish: callback auto-answer, safe_send_message, whitelist cleanup."""
from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./.pytest-test-mode.db")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("BOT_TOKEN", "test")
os.environ.setdefault("API_ID", "12345")
os.environ.setdefault("API_HASH", "testhash")
os.environ.setdefault("DEVELOPER_ID", "123456789")


# ── Issue 1: Auto-answer wrapper exists ──────────────────────────────────

def test_auto_answer_wrapper_function_exists():
    from app.handlers import _wrap_callback_handlers_with_auto_answer
    assert callable(_wrap_callback_handlers_with_auto_answer)


def test_apply_callback_safety_wrapper_exists():
    import asyncio
    from app.handlers import apply_callback_safety_wrapper
    assert asyncio.iscoroutinefunction(apply_callback_safety_wrapper)


# ── Issue 2: safe_send_message ───────────────────────────────────────────

@pytest.mark.asyncio
async def test_safe_send_message_success():
    from app.utils.safe_sender import safe_send_message
    bot = AsyncMock()
    bot.send_message = AsyncMock()
    result = await safe_send_message(bot, 123, "test")
    assert result is True
    bot.send_message.assert_called_once()


@pytest.mark.asyncio
async def test_safe_send_message_user_blocked():
    from app.utils.safe_sender import safe_send_message

    class UserIsBlocked(Exception):
        pass

    bot = AsyncMock()
    bot.send_message = AsyncMock(side_effect=UserIsBlocked())

    with patch("app.utils.safe_sender._mark_user_unreachable", new_callable=AsyncMock) as mock_mark:
        result = await safe_send_message(bot, 123, "test")
        assert result is False
        mock_mark.assert_called_once_with(123)


@pytest.mark.asyncio
async def test_safe_send_message_chat_write_forbidden():
    from app.utils.safe_sender import safe_send_message

    class ChatWriteForbidden(Exception):
        pass

    bot = AsyncMock()
    bot.send_message = AsyncMock(side_effect=ChatWriteForbidden())

    with patch("app.utils.safe_sender._mark_chat_inactive", new_callable=AsyncMock) as mock_mark:
        result = await safe_send_message(bot, -1001234, "test")
        assert result is False
        mock_mark.assert_called_once_with(-1001234)


@pytest.mark.asyncio
async def test_safe_send_message_no_mark_when_disabled():
    from app.utils.safe_sender import safe_send_message

    class PeerIdInvalid(Exception):
        pass

    bot = AsyncMock()
    bot.send_message = AsyncMock(side_effect=PeerIdInvalid())

    with patch("app.utils.safe_sender._mark_user_unreachable", new_callable=AsyncMock) as mock_mark:
        result = await safe_send_message(bot, 999, "test", mark_unreachable=False)
        assert result is False
        mock_mark.assert_not_called()


# ── Issue 2: NotificationService uses safe_send_message ──────────────────

def test_notification_service_uses_safe_sender():
    from pathlib import Path
    src = Path("app/services/notification_service.py").read_text()
    assert "safe_send_message" in src
    assert "from app.utils.safe_sender import safe_send_message" in src


def test_credit_service_auto_leave_uses_safe_sender():
    from pathlib import Path
    src = Path("app/services/credit_service.py").read_text()
    assert "safe_send_message" in src


# ── Issue 3: Whitelist cleanup cron registered ───────────────────────────

def test_whitelist_cleanup_cron_registered():
    from pathlib import Path
    src = Path("app/scheduler.py").read_text(encoding="utf-8")
    assert "cleanup_expired_whitelists" in src
    assert 'id="cleanup_expired_whitelists"' in src or 'scheduler_job_id("cleanup_expired_whitelists")' in src


def test_whitelist_cleanup_function_exists():
    import asyncio
    from importlib import import_module
    mod = import_module("app.scheduler")
    assert hasattr(mod, "cleanup_expired_whitelists")
    assert asyncio.iscoroutinefunction(mod.cleanup_expired_whitelists)


# ── safe_sender module structure ─────────────────────────────────────────

def test_safe_sender_module_structure():
    from app.utils.safe_sender import safe_send_message, _mark_user_unreachable, _mark_chat_inactive
    import asyncio
    assert asyncio.iscoroutinefunction(safe_send_message)
    assert asyncio.iscoroutinefunction(_mark_user_unreachable)
    assert asyncio.iscoroutinefunction(_mark_chat_inactive)

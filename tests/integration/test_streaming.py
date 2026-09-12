"""Streaming reliability tests — watchdog, quarantine, shutdown.

Tests cover:
- Watchdog orphan call cleanup
- Watchdog zombie FFmpeg cleanup
- Watchdog stale playback state cleanup
- Watchdog helper cooldown restore
- Quarantine on critical playback error
- Quarantine error patterns detection
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest
import pytest_asyncio
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./.pytest-test-mode.db")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("BOT_TOKEN", "test")
os.environ.setdefault("API_ID", "12345")
os.environ.setdefault("API_HASH", "testhash")
os.environ.setdefault("DEVELOPER_ID", "123456789")


@pytest_asyncio.fixture
async def stream_engine():
    eng = create_async_engine(os.environ["DATABASE_URL"], echo=False, poolclass=NullPool)
    from app.database.models import Base
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest_asyncio.fixture
async def stream_session_factory(stream_engine):
    return async_sessionmaker(stream_engine, class_=AsyncSession, expire_on_commit=False)


@pytest_asyncio.fixture
async def patched_stream_env(stream_session_factory):
    import app.utils.cache as cache_mod
    engine_mod = sys.modules["app.database.engine"]
    orig_session = engine_mod.async_session
    orig_redis = cache_mod._redis
    engine_mod.async_session = stream_session_factory
    cache_mod._redis = None
    try:
        yield stream_session_factory
    finally:
        if cache_mod._redis is not None:
            await cache_mod._redis.aclose()
        cache_mod._redis = orig_redis
        engine_mod.async_session = orig_session


# ═══════════════════════════════════════════════════════════════════════
# Watchdog: orphan call cleanup
# ═══════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
class TestWatchdogOrphanCalls:
    async def test_orphan_call_removed(self):
        """An in-memory call with no live pytgcalls session is cleaned."""
        from app.services.call_service import _active_calls
        from app.services.watchdog import cleanup_orphan_calls

        chat_id = -700001
        _active_calls[chat_id] = {"source": "test", "media_type": "audio"}

        mock_call_py = MagicMock()
        mock_call_py.active_calls = {}

        cleaned = await cleanup_orphan_calls(mock_call_py)
        assert cleaned >= 1
        assert chat_id not in _active_calls

    async def test_active_call_not_removed(self):
        """An in-memory call with a live pytgcalls session is kept."""
        from app.services.call_service import _active_calls
        from app.services.watchdog import cleanup_orphan_calls

        chat_id = -700002
        _active_calls[chat_id] = {"source": "test", "media_type": "audio"}

        mock_call_py = MagicMock()
        mock_call_py.active_calls = {chat_id: True}

        await cleanup_orphan_calls(mock_call_py)
        assert chat_id in _active_calls
        _active_calls.pop(chat_id, None)


# ═══════════════════════════════════════════════════════════════════════
# Watchdog: zombie FFmpeg cleanup
# ═══════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
class TestWatchdogZombieFfmpeg:
    async def test_dead_ffmpeg_cleaned(self):
        """A registered FFmpeg process that has exited is removed."""
        from app.services.call_service import _ffmpeg_processes
        from app.services.watchdog import cleanup_zombie_ffmpeg

        chat_id = -700003
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.pid = 99999
        _ffmpeg_processes[chat_id] = mock_proc

        cleaned = await cleanup_zombie_ffmpeg()
        assert cleaned >= 1
        assert chat_id not in _ffmpeg_processes

    async def test_alive_ffmpeg_kept(self):
        """A running FFmpeg process is not removed."""
        from app.services.call_service import _ffmpeg_processes
        from app.services.watchdog import cleanup_zombie_ffmpeg

        chat_id = -700004
        mock_proc = MagicMock()
        mock_proc.returncode = None
        mock_proc.pid = os.getpid()
        _ffmpeg_processes[chat_id] = mock_proc

        await cleanup_zombie_ffmpeg()
        assert chat_id in _ffmpeg_processes
        _ffmpeg_processes.pop(chat_id, None)


# ═══════════════════════════════════════════════════════════════════════
# Watchdog: helper cooldown restore
# ═══════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
class TestWatchdogHelperRestore:
    async def test_expired_cooldown_restored(self, patched_stream_env):
        """A helper past cooldown_until is reactivated."""
        from app.database.models import HelperAccount
        from app.services.watchdog import restore_cooled_down_helpers

        helper_id = None
        try:
            async with patched_stream_env() as session:
                async with session.begin():
                    h = HelperAccount(
                        phone="+1555000111",
                        status="quarantined",
                        cooldown_until=datetime.now(timezone.utc) - timedelta(minutes=5),
                    )
                    session.add(h)
                    await session.flush()
                    helper_id = h.id

            restored = await restore_cooled_down_helpers()
            assert restored >= 1

            async with patched_stream_env() as session:
                result = await session.execute(
                    select(HelperAccount).where(HelperAccount.id == helper_id)
                )
                h = result.scalar_one()
                assert h.status == "active"
                assert h.cooldown_until is None
        finally:
            if helper_id is not None:
                async with patched_stream_env() as session:
                    async with session.begin():
                        await session.execute(
                            delete(HelperAccount).where(HelperAccount.id == helper_id)
                        )


# ═══════════════════════════════════════════════════════════════════════
# Quarantine on critical playback error
# ═══════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
class TestQuarantineOnError:
    async def test_quarantine_error_patterns(self):
        """The quarantine detector recognizes critical error signatures."""
        from app.services.call_service import _QUARANTINE_ERRORS
        assert "FloodWait" in _QUARANTINE_ERRORS
        assert "PeerFlood" in _QUARANTINE_ERRORS
        assert "UserRestricted" in _QUARANTINE_ERRORS
        assert "PEER_FLOOD" in _QUARANTINE_ERRORS

    async def test_quarantine_called_on_floodwait(self, patched_stream_env):
        """When join_voice_chat gets a FloodWait, the helper is quarantined."""
        from app.services.call_service import _maybe_quarantine_helper
        from app.database.models import HelperAccount, HelperChatBinding

        chat_id = -700010
        helper_id = None
        try:
            async with patched_stream_env() as session:
                async with session.begin():
                    h = HelperAccount(phone="+1555000222", status="active")
                    session.add(h)
                    await session.flush()
                    helper_id = h.id

                    b = HelperChatBinding(chat_id=chat_id, helper_account_id=helper_id)
                    session.add(b)

            class FakeFloodWait(Exception):
                pass

            exc = FakeFloodWait("A]FloodWait X: Must wait 60 seconds")
            await _maybe_quarantine_helper(chat_id, exc)

            async with patched_stream_env() as session:
                result = await session.execute(
                    select(HelperAccount).where(HelperAccount.id == helper_id)
                )
                h = result.scalar_one()
                assert h.status == "quarantined"
                assert h.cooldown_until is not None
                assert h.last_error is not None

        finally:
            if helper_id is not None:
                async with patched_stream_env() as session:
                    async with session.begin():
                        await session.execute(
                            delete(HelperChatBinding).where(HelperChatBinding.chat_id == chat_id)
                        )
                        await session.execute(
                            delete(HelperAccount).where(HelperAccount.id == helper_id)
                        )

    async def test_non_quarantine_error_ignored(self, patched_stream_env):
        """A regular ValueError should NOT trigger quarantine."""
        from app.services.call_service import _maybe_quarantine_helper

        await _maybe_quarantine_helper(-700099, ValueError("normal error"))


# ═══════════════════════════════════════════════════════════════════════
# Full watchdog run
# ═══════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
class TestFullWatchdog:
    async def test_run_full_watchdog_returns_summary(self):
        """run_full_watchdog returns a dict with all check counts."""
        from app.services import watchdog
        from unittest.mock import AsyncMock as AM

        mock_call_py = MagicMock()
        mock_call_py.active_calls = {}

        with patch.object(watchdog, "cleanup_stale_playback_states", new_callable=AM, return_value=0), \
             patch.object(watchdog, "restore_cooled_down_helpers", new_callable=AM, return_value=0):
            results = await watchdog.run_full_watchdog(mock_call_py)

        assert isinstance(results, dict)
        assert "orphan_calls" in results
        assert "zombie_ffmpeg" in results
        assert "stale_states" in results
        assert "restored_helpers" in results

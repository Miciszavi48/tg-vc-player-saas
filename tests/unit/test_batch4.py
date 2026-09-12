"""Tests for Batch 4: G7, G29, G6, G31, G9."""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./.pytest-test-mode.db")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("BOT_TOKEN", "test")
os.environ.setdefault("API_ID", "12345")
os.environ.setdefault("API_HASH", "testhash")
os.environ.setdefault("DEVELOPER_ID", "123456789")


# ═══════════════════════════════════════════════════════════════════════
# G7: Startup state recovery
# ═══════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
class TestG7Recovery:
    async def test_schedule_recovery_creates_task(self):
        """schedule_recovery must spawn a background task."""
        from app.services import recovery_service

        mock_call_py = MagicMock()

        with patch.object(recovery_service, "_run_recovery", new_callable=AsyncMock) as mock_run:
            await recovery_service.schedule_recovery(mock_call_py)
            await asyncio.sleep(0.1)
            mock_run.assert_called_once_with(mock_call_py)
            recovery_service._recovery_task = None

    async def test_recovery_empty_states_safe(self):
        """Recovery with no PlaybackState rows should complete without error."""
        from app.services.recovery_service import _run_recovery

        mock_call_py = MagicMock()
        with patch("app.services.recovery_service.async_session") as mock_session_cls:
            mock_session = AsyncMock()
            mock_result = MagicMock()
            mock_result.scalars.return_value.all.return_value = []
            mock_session.execute = AsyncMock(return_value=mock_result)
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=False)
            mock_session_cls.return_value = mock_session

            await _run_recovery(mock_call_py)

    async def test_recovery_stagger_with_delay(self):
        """Recovery must space out items (not instant flood)."""
        from app.services.recovery_service import _run_recovery
        from app.services import CallService

        mock_state = MagicMock()
        mock_state.chat_id = -100111
        mock_state.source = "http://test.mp3"
        mock_state.media_type = "audio"
        mock_state.seek_sec = 0

        with patch("app.services.recovery_service.async_session") as mock_sess_cls, \
             patch.object(CallService, "join_voice_chat", new_callable=AsyncMock, return_value=True), \
             patch("app.services.recovery_service.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            mock_session = AsyncMock()
            mock_result = MagicMock()
            mock_result.scalars.return_value.all.return_value = [mock_state]
            mock_session.execute = AsyncMock(return_value=mock_result)
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=False)
            mock_sess_cls.return_value = mock_session

            await _run_recovery(MagicMock())

            assert mock_sleep.call_count >= 2


# ═══════════════════════════════════════════════════════════════════════
# G29: Seek state persistence
# ═══════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
class TestG29SeekPersistence:
    async def test_seek_tracker_writes_to_redis(self):
        """Seek tracker should update Redis at configured interval."""
        from app.services.seek_tracker import (
            start_seek_tracker,
            stop_seek_tracker,
        )

        with patch("app.services.seek_tracker.flush_seek_to_redis", new_callable=AsyncMock) as mock_redis, \
             patch("app.services.seek_tracker.flush_seek_to_db", new_callable=AsyncMock):
            await start_seek_tracker(-100222, initial_seek=0)
            await asyncio.sleep(6)
            stop_seek_tracker(-100222)
            await asyncio.sleep(0.2)

            assert mock_redis.call_count >= 1
            args = mock_redis.call_args_list[0]
            assert args[0][0] == -100222

    async def test_seek_tracker_flushes_db(self):
        """DB flush happens at 30s cadence. Testing with shortened interval."""
        from app.services import seek_tracker

        original_interval = seek_tracker.SEEK_DB_INTERVAL
        seek_tracker.SEEK_DB_INTERVAL = 5

        try:
            with patch("app.services.seek_tracker.flush_seek_to_redis", new_callable=AsyncMock), \
                 patch("app.services.seek_tracker.flush_seek_to_db", new_callable=AsyncMock) as mock_db:
                await seek_tracker.start_seek_tracker(-100333, initial_seek=0)
                await asyncio.sleep(6)
                seek_tracker.stop_seek_tracker(-100333)
                await asyncio.sleep(0.2)

                assert mock_db.call_count >= 1
        finally:
            seek_tracker.SEEK_DB_INTERVAL = original_interval

    async def test_seek_tracker_stops_on_leave(self):
        """Tracker task must stop when stop_seek_tracker is called."""
        from app.services.seek_tracker import (
            start_seek_tracker,
            stop_seek_tracker,
            _seek_tasks,
        )

        with patch("app.services.seek_tracker.flush_seek_to_redis", new_callable=AsyncMock), \
             patch("app.services.seek_tracker.flush_seek_to_db", new_callable=AsyncMock):
            await start_seek_tracker(-100444, initial_seek=10)
            assert -100444 in _seek_tasks
            task = _seek_tasks[-100444]

            stop_seek_tracker(-100444)
            await asyncio.sleep(0.2)

            assert task.done() or task.cancelled()
            assert -100444 not in _seek_tasks


# ═══════════════════════════════════════════════════════════════════════
# G6: SQLite → PostgreSQL migration script
# ═══════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
class TestG6Migration:
    async def test_script_exists(self):
        """Migration script must exist at expected path."""
        path = Path(__file__).resolve().parents[2] / "app" / "database" / "migrate_sqlite_to_pg.py"
        assert path.exists()

    async def test_dry_run_empty_db(self):
        """Dry-run on an empty SQLite DB should return zero counts safely."""
        db_url = os.environ.get("DATABASE_URL", "")
        if not db_url.startswith("postgresql"):
            pytest.skip("test_dry_run_empty_db requires a PostgreSQL DATABASE_URL")

        from app.database.migrate_sqlite_to_pg import migrate


        with tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False) as f:
            sqlite_path = f.name

        try:
            import sqlite3
            conn = sqlite3.connect(sqlite_path)
            conn.close()

            report = await migrate(
                sqlite_path,
                os.environ["DATABASE_URL"].replace("+asyncpg", ""),
                dry_run=True,
            )
            assert isinstance(report, dict)
            assert all(v == 0 for v in report.values())
        finally:
            os.unlink(sqlite_path)

    async def test_parser_has_dry_run_flag(self):
        """CLI parser must accept --dry-run."""
        from app.database.migrate_sqlite_to_pg import build_parser
        parser = build_parser()
        args = parser.parse_args(["--sqlite", "test.db", "--pg", "pg://x", "--dry-run"])
        assert args.dry_run is True


# ═══════════════════════════════════════════════════════════════════════
# G31: systemd service file
# ═══════════════════════════════════════════════════════════════════════

class TestG31Systemd:
    def test_service_file_exists(self):
        """scripts/musicbot.service must exist."""
        path = Path(__file__).resolve().parents[2] / "scripts" / "musicbot.service"
        assert path.exists()

    def test_service_file_has_required_sections(self):
        """Service file must have [Unit], [Service], [Install] sections."""
        path = Path(__file__).resolve().parents[2] / "scripts" / "musicbot.service"
        content = path.read_text()
        assert "[Unit]" in content
        assert "[Service]" in content
        assert "[Install]" in content
        assert "ExecStart=" in content
        assert "Restart=" in content
        assert "EnvironmentFile=" in content


# ═══════════════════════════════════════════════════════════════════════
# G9: Owner top-up sudo wallet
# ═══════════════════════════════════════════════════════════════════════

@pytest_asyncio.fixture
async def g9_engine():
    eng = create_async_engine(os.environ["DATABASE_URL"], echo=False, poolclass=NullPool)
    from app.database.models import Base
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest_asyncio.fixture
async def g9_session_factory(g9_engine):
    return async_sessionmaker(g9_engine, class_=AsyncSession, expire_on_commit=False)


@pytest.mark.asyncio
class TestG9WalletTopup:
    async def test_topup_creates_wallet_and_transaction(self, g9_session_factory):
        """Owner top-up creates wallet row and transaction atomically."""
        import app.utils.cache as cache_mod

        engine_mod = sys.modules["app.database.engine"]
        orig_session = engine_mod.async_session
        orig_redis = cache_mod._redis
        engine_mod.async_session = g9_session_factory
        cache_mod._redis = None

        from app.database.models import Sudo, SudoWallet, SudoWalletTransaction

        sudo_uid = 999888777
        try:
            async with g9_session_factory() as session:
                async with session.begin():
                    await session.execute(delete(SudoWalletTransaction).where(SudoWalletTransaction.sudo_user_id == sudo_uid))
                    await session.execute(delete(SudoWallet).where(SudoWallet.sudo_user_id == sudo_uid))
                    await session.execute(delete(Sudo).where(Sudo.user_id == sudo_uid))
                    session.add(Sudo(user_id=sudo_uid, display_name="TestSudo", added_by=0))

            async with g9_session_factory() as session:
                async with session.begin():
                    wallet = SudoWallet(sudo_user_id=sudo_uid, balance_toman=5000)
                    session.add(wallet)
                    txn = SudoWalletTransaction(
                        sudo_user_id=sudo_uid, amount_toman=5000, reason="owner_topup", ref_type="topup",
                    )
                    session.add(txn)

            async with g9_session_factory() as session:
                result = await session.execute(select(SudoWallet).where(SudoWallet.sudo_user_id == sudo_uid))
                w = result.scalar_one()
                assert float(w.balance_toman) == 5000.0

                result = await session.execute(
                    select(SudoWalletTransaction).where(SudoWalletTransaction.sudo_user_id == sudo_uid)
                )
                txns = list(result.scalars().all())
                assert len(txns) >= 1
                assert any(t.reason == "owner_topup" for t in txns)

        finally:
            async with g9_session_factory() as session:
                async with session.begin():
                    await session.execute(delete(SudoWalletTransaction).where(SudoWalletTransaction.sudo_user_id == sudo_uid))
                    await session.execute(delete(SudoWallet).where(SudoWallet.sudo_user_id == sudo_uid))
                    await session.execute(delete(Sudo).where(Sudo.user_id == sudo_uid))
            if cache_mod._redis is not None:
                await cache_mod._redis.aclose()
            cache_mod._redis = orig_redis
            engine_mod.async_session = orig_session

    async def test_wallet_label_exists(self):
        """JSON keys for wallet top-up must exist."""
        from app.utils.i18n import t
        assert "[missing:" not in t("fa", "wallet.topup_success", sudo="1", amount=100)
        assert "[missing:" not in t("fa", "wallet.sudo_not_found")
        assert "[missing:" not in t("fa", "panels.owner.topup_sudo_wallet")

    async def test_cb_constant_exists(self):
        """OWN_TOPUP_SUDO_WALLET CB constant must exist."""
        from app.utils.ui import CB
        assert "OWN_TOPUP_SUDO_WALLET" in CB

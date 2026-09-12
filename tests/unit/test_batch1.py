"""Tests for Batch 1: G1, G11, G32, G14, G8."""
from __future__ import annotations

import os
import re
import sys

import pytest
import pytest_asyncio
from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.config.settings import settings

os.environ.setdefault(
    "DATABASE_URL",
    "sqlite+aiosqlite:///./.pytest-test-mode.db",
)
os.environ.setdefault("DB_ALLOW_CREATE_ALL", "true")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("BOT_TOKEN", "test")
os.environ.setdefault("API_ID", "12345")
os.environ.setdefault("API_HASH", "testhash")
os.environ.setdefault("DEVELOPER_ID", "123456789")

from app.database.models import (
    Base,
    BotSetting,
    CreditHistory,
    GroupCredit,
    InstallPolicySetting,
    Sudo,
    SudoWallet,
    SudoWalletTransaction,
)


@pytest_asyncio.fixture
async def test_engine():
    engine = create_async_engine(
        os.environ["DATABASE_URL"], echo=False, poolclass=NullPool
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def test_session_factory(test_engine):
    return async_sessionmaker(
        test_engine, class_=AsyncSession, expire_on_commit=False
    )


@pytest_asyncio.fixture
async def patched_env(test_engine, test_session_factory):
    import app.utils.cache as cache_mod

    engine_mod = sys.modules["app.database.engine"]
    original_session = engine_mod.async_session
    original_engine = engine_mod.engine
    original_redis = cache_mod._redis
    engine_mod.async_session = test_session_factory
    engine_mod.engine = test_engine
    try:
        yield test_session_factory
    finally:
        cache_mod._redis = original_redis
        engine_mod.async_session = original_session
        engine_mod.engine = original_engine


async def _cleanup_credit(session_factory, chat_id: int):
    async with session_factory() as session:
        async with session.begin():
            await session.execute(
                delete(CreditHistory).where(CreditHistory.chat_id == chat_id)
            )
            await session.execute(
                delete(GroupCredit).where(GroupCredit.chat_id == chat_id)
            )


async def _cleanup_wallet(session_factory, sudo_user_id: int):
    async with session_factory() as session:
        async with session.begin():
            await session.execute(
                delete(SudoWalletTransaction).where(
                    SudoWalletTransaction.sudo_user_id == sudo_user_id
                )
            )
            await session.execute(
                delete(SudoWallet).where(
                    SudoWallet.sudo_user_id == sudo_user_id
                )
            )
            await session.execute(
                delete(Sudo).where(Sudo.user_id == sudo_user_id)
            )


async def _cleanup_policy(session_factory):
    async with session_factory() as session:
        async with session.begin():
            await session.execute(
                delete(InstallPolicySetting).where(
                    InstallPolicySetting.id == 1
                )
            )


async def _cleanup_bot_settings(session_factory):
    async with session_factory() as session:
        async with session.begin():
            await session.execute(delete(BotSetting))


# ── G1: Command parsing tests ────────────────────────────────────────────────

class TestG1CommandParsing:
    def test_parse_credit_update_command(self):
        pattern = re.compile(
            r"^(?:آپدیت\s+شارژ|update\s+charge)\s+(\d+)$",
            re.IGNORECASE,
        )
        match = pattern.search("آپدیت شارژ 30")
        assert match is not None
        assert int(match.group(1)) == 30

        match_en = pattern.search("update charge 30")
        assert match_en is not None
        assert int(match_en.group(1)) == 30

    def test_parse_credit_update_video_command(self):
        pattern = re.compile(
            r"^(?:آپدیت\s+شارژ\s+ویدیو|update\s+charge\s+video)\s+(\d+)$",
            re.IGNORECASE,
        )
        match = pattern.search("آپدیت شارژ ویدیو 15")
        assert match is not None
        assert int(match.group(1)) == 15

        match_en = pattern.search("update charge video 15")
        assert match_en is not None
        assert int(match_en.group(1)) == 15


# ── G11: Wallet enforcement tests ────────────────────────────────────────────

@pytest.mark.asyncio
class TestG11WalletEnforcement:
    async def test_sudo_wallet_insufficient(self, patched_env):
        sudo_user_id = 777001
        chat_id = -800001
        try:
            async with patched_env() as session:
                async with session.begin():
                    sudo = Sudo(user_id=sudo_user_id, is_active=True)
                    session.add(sudo)

            from app.services.credit_service import CreditService

            with pytest.raises(ValueError, match="wallet_credit_disabled"):
                await CreditService.charge_with_wallet(
                    chat_id, "group", 10, sudo_user_id=sudo_user_id
                )
        finally:
            await _cleanup_credit(patched_env, chat_id)
            await _cleanup_wallet(patched_env, sudo_user_id)

    async def test_developer_wallet_path_is_disabled_without_mutation(self, patched_env):
        dev_id = int(settings.DEVELOPER_ID)
        chat_id = -800002
        try:
            async with patched_env() as session:
                async with session.begin():
                    await session.execute(
                        text(
                            "INSERT INTO bot_settings (key, value) "
                            "VALUES ('music_rate', '10000') "
                            "ON CONFLICT (key) DO NOTHING"
                        )
                    )
                    sudo = Sudo(user_id=dev_id, is_active=True)
                    session.add(sudo)
                    await session.flush()
                    wallet = SudoWallet(
                        sudo_user_id=dev_id,
                        balance_toman=500000,
                        locked_toman=0,
                    )
                    session.add(wallet)

            from app.services.credit_service import CreditService

            with pytest.raises(ValueError, match="wallet_credit_disabled"):
                await CreditService.charge_with_wallet(
                    chat_id, "group", 10, sudo_user_id=dev_id,
                    rate_key="music_rate", note="test_wallet",
                )

            async with patched_env() as session:
                stmt = select(SudoWallet).where(
                    SudoWallet.sudo_user_id == dev_id
                )
                result = await session.execute(stmt)
                wallet = result.scalar_one()
                assert float(wallet.balance_toman) == 500000.0

                txn_stmt = select(SudoWalletTransaction).where(
                    SudoWalletTransaction.sudo_user_id == dev_id
                )
                txn_result = await session.execute(txn_stmt)
                txns = list(txn_result.scalars().all())
                assert txns == []
        finally:
            await _cleanup_credit(patched_env, chat_id)
            await _cleanup_wallet(patched_env, dev_id)
            async with patched_env() as session:
                async with session.begin():
                    await session.execute(
                        delete(BotSetting).where(BotSetting.key == "music_rate")
                    )


# ── G32: Trial days from policy ──────────────────────────────────────────────

@pytest.mark.asyncio
class TestG32TrialDays:
    async def test_trial_days_from_policy(self, patched_env):
        chat_id = -800003
        try:
            async with patched_env() as session:
                async with session.begin():
                    await session.execute(
                        delete(InstallPolicySetting).where(
                            InstallPolicySetting.id == 1
                        )
                    )
                    policy = InstallPolicySetting(
                        id=1,
                        policy_mode="open",
                        trial_days=7,
                        charge_on_install=False,
                        group_install_fee_irr=0,
                        chan_install_fee_irr=0,
                    )
                    session.add(policy)

            from app.services.credit_service import CreditService

            credit = await CreditService.activate_trial(chat_id, "group")
            assert credit.credit_days == 7
            assert credit.is_trial is True
        finally:
            await _cleanup_credit(patched_env, chat_id)
            await _cleanup_policy(patched_env)

    async def test_trial_days_fallback_to_env(self, patched_env):
        chat_id = -800004
        try:
            async with patched_env() as session:
                async with session.begin():
                    await session.execute(
                        delete(InstallPolicySetting).where(
                            InstallPolicySetting.id == 1
                        )
                    )

            from app.config.settings import settings
            from app.services.credit_service import CreditService

            credit = await CreditService.activate_trial(chat_id, "group")
            assert credit.credit_days == settings.TRIAL_DAYS
            assert credit.is_trial is True
        finally:
            await _cleanup_credit(patched_env, chat_id)


# ── G14: Seed data tests ─────────────────────────────────────────────────────

@pytest.mark.asyncio
class TestG14SeedData:
    async def test_bot_settings_seed(self, patched_env):
        try:
            await _cleanup_bot_settings(patched_env)
            await _cleanup_policy(patched_env)

            from app.database.engine import init_db
            await init_db()

            async with patched_env() as session:
                stmt = select(BotSetting)
                result = await session.execute(stmt)
                settings_map = {r.key: r.value for r in result.scalars().all()}

            assert "trial_enabled" in settings_map
            assert settings_map["trial_enabled"] == "true"
            assert "music_rate" in settings_map
            assert settings_map["music_rate"] == "10000"
            assert "base_rate" in settings_map
            assert settings_map["base_rate"] == "50000"
            assert "sudo_links" in settings_map
            assert settings_map["sudo_links"] == "[]"
        finally:
            pass

    async def test_install_policy_seed(self, patched_env):
        try:
            await _cleanup_policy(patched_env)
            await _cleanup_bot_settings(patched_env)

            from app.database.engine import init_db
            await init_db()

            async with patched_env() as session:
                stmt = select(InstallPolicySetting).where(
                    InstallPolicySetting.id == 1
                )
                result = await session.execute(stmt)
                policy = result.scalar_one_or_none()

            assert policy is not None
            assert policy.policy_mode == "open"
            assert policy.trial_days == 3
        finally:
            pass

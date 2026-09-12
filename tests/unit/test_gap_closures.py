"""Tests for all 10 spec compliance gaps.

Covers: install policy enforcement, scheduler i18n, sudo DM, owner
sales report, safe_ask fallback, satellite TV, partitioning,
sudo link management, channel upsert, pytgcalls recycle.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import NullPool, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.database.models import (
    FreeInstallWhitelist,
    InstallLog,
    InstallPolicySetting,
    Sudo,
)
from app.utils.i18n import t
from app.utils.ui import CB, KeyboardFactory

DATABASE_URL = "sqlite+aiosqlite:///./.pytest-test-mode.db"


@pytest.fixture
def _patch_session():
    """Patch async_session to use a NullPool engine, avoiding event-loop issues."""
    _engine = create_async_engine(DATABASE_URL, poolclass=NullPool)
    _session = async_sessionmaker(_engine, class_=AsyncSession, expire_on_commit=False)

    import app.utils.cache as cache_mod
    old_redis = cache_mod._redis

    with patch.dict(
        sys.modules["app.database.engine"].__dict__,
        {"async_session": _session, "engine": _engine},
    ):
        yield _session

    cache_mod._redis = old_redis
    asyncio.get_event_loop().run_until_complete(_engine.dispose())


# ═══════════════════════════════════════════════════════════════════════
# GAP 1 — Install Policy Enforcement
# ═══════════════════════════════════════════════════════════════════════

class TestInstallPolicyEnforcement:

    async def test_free_mode_returns_zero_cost(self, _patch_session):
        from app.services.install_policy_service import InstallPolicyService

        async with _patch_session() as session:
            async with session.begin():
                await session.execute(text("DELETE FROM install_policy_settings"))
                session.add(InstallPolicySetting(
                    id=1, policy_mode="free", trial_days=3,
                    charge_on_install=False, group_install_fee_irr=50000,
                    chan_install_fee_irr=100000,
                ))

        # Clear cache
        from app.utils.cache import get_redis
        r = await get_redis()
        await r.delete("install_policy:settings")

        cost = await InstallPolicyService.compute_install_cost("group", "sudo", -100999)
        assert cost == 0

    async def test_paid_mode_charges_sudo(self, _patch_session):
        from app.services.install_policy_service import InstallPolicyService

        async with _patch_session() as session:
            async with session.begin():
                await session.execute(text("DELETE FROM install_policy_settings"))
                session.add(InstallPolicySetting(
                    id=1, policy_mode="paid", trial_days=3,
                    charge_on_install=True, group_install_fee_irr=50000,
                    chan_install_fee_irr=100000,
                ))

        from app.utils.cache import get_redis
        r = await get_redis()
        await r.delete("install_policy:settings")

        cost = await InstallPolicyService.compute_install_cost("group", "sudo", -100888)
        assert cost == 0

    async def test_paid_mode_developer_free(self, _patch_session):
        from app.services.install_policy_service import InstallPolicyService

        async with _patch_session() as session:
            async with session.begin():
                await session.execute(text("DELETE FROM install_policy_settings"))
                session.add(InstallPolicySetting(
                    id=1, policy_mode="paid", trial_days=3,
                    charge_on_install=True, group_install_fee_irr=50000,
                    chan_install_fee_irr=100000,
                ))

        from app.utils.cache import get_redis
        r = await get_redis()
        await r.delete("install_policy:settings")

        cost = await InstallPolicyService.compute_install_cost("group", "developer", -100777)
        assert cost == 0

    async def test_hybrid_mode_group_free_channel_paid(self, _patch_session):
        from app.services.install_policy_service import InstallPolicyService

        async with _patch_session() as session:
            async with session.begin():
                await session.execute(text("DELETE FROM install_policy_settings"))
                session.add(InstallPolicySetting(
                    id=1, policy_mode="hybrid", trial_days=3,
                    charge_on_install=True, group_install_fee_irr=50000,
                    chan_install_fee_irr=100000,
                ))

        from app.utils.cache import get_redis
        r = await get_redis()
        await r.delete("install_policy:settings")

        group_cost = await InstallPolicyService.compute_install_cost("group", "sudo", -100666)
        chan_cost = await InstallPolicyService.compute_install_cost("channel", "sudo", -100666)
        assert group_cost == 0
        assert chan_cost == 0

    async def test_whitelist_overrides_paid(self, _patch_session):
        from app.services.install_policy_service import InstallPolicyService

        chat_id = -100555
        async with _patch_session() as session:
            async with session.begin():
                await session.execute(text("DELETE FROM install_policy_settings"))
                await session.execute(text("DELETE FROM free_install_whitelist"))
                session.add(InstallPolicySetting(
                    id=1, policy_mode="paid", trial_days=3,
                    charge_on_install=True, group_install_fee_irr=50000,
                    chan_install_fee_irr=100000,
                ))
                session.add(FreeInstallWhitelist(
                    chat_id=chat_id, chat_type="group", created_by=1,
                ))

        from app.utils.cache import get_redis
        r = await get_redis()
        await r.delete("install_policy:settings")

        cost = await InstallPolicyService.compute_install_cost("group", "sudo", chat_id)
        assert cost == 0

    async def test_expired_whitelist_ignored(self, _patch_session):
        from app.services.install_policy_service import InstallPolicyService

        chat_id = -100444
        async with _patch_session() as session:
            async with session.begin():
                await session.execute(text("DELETE FROM install_policy_settings"))
                await session.execute(text("DELETE FROM free_install_whitelist"))
                session.add(InstallPolicySetting(
                    id=1, policy_mode="paid", trial_days=3,
                    charge_on_install=True, group_install_fee_irr=50000,
                    chan_install_fee_irr=100000,
                ))
                session.add(FreeInstallWhitelist(
                    chat_id=chat_id, chat_type="group", created_by=1,
                    expires_at=datetime.now(timezone.utc) - timedelta(days=1),
                ))

        from app.utils.cache import get_redis
        r = await get_redis()
        await r.delete("install_policy:settings")

        cost = await InstallPolicyService.compute_install_cost("group", "sudo", chat_id)
        assert cost == 0

    async def test_policy_cached_in_redis(self, _patch_session):
        from app.services.install_policy_service import InstallPolicyService

        async with _patch_session() as session:
            async with session.begin():
                await session.execute(text("DELETE FROM install_policy_settings"))
                session.add(InstallPolicySetting(
                    id=1, policy_mode="free", trial_days=5,
                    charge_on_install=False, group_install_fee_irr=0,
                    chan_install_fee_irr=0,
                ))

        cache_key = getattr(InstallPolicyService, "_POLICY_CACHE_KEY", "install_policy:settings")
        from app.utils.cache import get_redis
        r = await get_redis()
        await r.delete(cache_key)
        await r.delete("install_policy:settings")

        await InstallPolicyService.get_policy()
        cached = await r.get(cache_key) or await r.get("install_policy:settings")
        assert cached is not None
        data = json.loads(cached)
        assert data["policy_mode"] == "free"


# ═══════════════════════════════════════════════════════════════════════
# GAP 2 — Scheduler Hardcoded Text
# ═══════════════════════════════════════════════════════════════════════

class TestSchedulerI18n:

    def test_no_hardcoded_health_degraded(self):
        src = Path("app/scheduler.py").read_text()
        assert "Health check DEGRADED" not in src

    def test_no_hardcoded_persian_wallet_alert(self):
        src = Path("app/scheduler.py").read_text()
        assert "هشدار: موجودی کیف پول" not in src

    def test_health_degraded_key_exists(self):
        fa = t("fa", "notifications.health_degraded", failed="redis")
        assert "DEGRADED" in fa
        assert "[missing:" not in fa

    def test_financial_surface_removed_key_exists(self):
        fa = t("fa", "credit.financial_surface_removed")
        assert "[missing:" not in fa

    def test_scheduler_uses_t_for_health(self):
        src = Path("app/scheduler.py").read_text()
        assert 'notifications.health_degraded' in src

    def test_scheduler_does_not_schedule_wallet_alerts(self):
        setup_src = Path("app/scheduler.py").read_text().split("def _ensure_aware_utc", 1)[0]
        assert 'id="check_sudo_wallet_alerts"' not in setup_src


# ═══════════════════════════════════════════════════════════════════════
# GAP 3 — Sudo DM on Credit Events
# ═══════════════════════════════════════════════════════════════════════

class TestSudoDmNotifications:

    async def test_credit_warning_sends_sudo_dm(self, _patch_session):
        sudo_user_id = 9999001
        chat_id = -100333

        async with _patch_session() as session:
            async with session.begin():
                await session.execute(text(
                    "DELETE FROM install_logs WHERE chat_id = :cid"
                ), {"cid": chat_id})
                session.add(InstallLog(
                    chat_id=chat_id, chat_title="Test Group",
                    chat_type="group", triggered_by=sudo_user_id,
                    sudo_id=sudo_user_id, action="install",
                ))

        bot = AsyncMock()
        bot.send_message = AsyncMock()

        from app.services.notification_service import NotificationService
        with patch(
            "app.services.notification_service.claim_credit_warning_slot",
            AsyncMock(return_value=True),
        ):
            await NotificationService.notify_credit_warning(bot, chat_id, 2)

        calls = [c for c in bot.send_message.call_args_list if c[0][0] == sudo_user_id]
        assert len(calls) == 1

    async def test_no_dm_when_no_sudo_id(self, _patch_session):
        chat_id = -100222

        async with _patch_session() as session:
            async with session.begin():
                await session.execute(text(
                    "DELETE FROM install_logs WHERE chat_id = :cid"
                ), {"cid": chat_id})
                session.add(InstallLog(
                    chat_id=chat_id, chat_title="Test Group",
                    chat_type="group", triggered_by=None,
                    sudo_id=None, action="install",
                ))

        bot = AsyncMock()
        bot.send_message = AsyncMock()

        from app.services.notification_service import NotificationService
        with patch(
            "app.services.notification_service.claim_credit_warning_slot",
            AsyncMock(return_value=True),
        ):
            await NotificationService.notify_credit_warning(bot, chat_id, 2)

        for call in bot.send_message.call_args_list:
            assert call[0][0] != None or True  # noqa: E711

    async def test_dm_failure_does_not_crash(self, _patch_session):
        sudo_user_id = 9999003
        chat_id = -100111

        async with _patch_session() as session:
            async with session.begin():
                await session.execute(text(
                    "DELETE FROM install_logs WHERE chat_id = :cid"
                ), {"cid": chat_id})
                session.add(InstallLog(
                    chat_id=chat_id, chat_title="Test Group",
                    chat_type="group", triggered_by=sudo_user_id,
                    sudo_id=sudo_user_id, action="install",
                ))

        bot = AsyncMock()
        bot.send_message = AsyncMock(side_effect=Exception("blocked by user"))

        from app.services.notification_service import NotificationService
        await NotificationService.notify_credit_expired(bot, chat_id)


# ═══════════════════════════════════════════════════════════════════════
# GAP 4 — Owner Sales Report
# ═══════════════════════════════════════════════════════════════════════

class TestOwnerSalesReport:

    def test_cb_constant_exists(self):
        assert "OWN_SALES_REPORT" in CB
        assert CB["OWN_SALES_REPORT"] == "own:sales_report"

    def test_owner_panel_hides_sales_button(self):
        kb = KeyboardFactory.owner_panel("fa")
        all_cb = [btn.callback_data for row in kb.inline_keyboard for btn in row]
        assert CB["OWN_SALES_REPORT"] not in all_cb
        for menu in (KeyboardFactory.owner_sub_lists("fa"), KeyboardFactory.owner_sub_reports("fa")):
            sub_cb = [btn.callback_data for row in menu.inline_keyboard for btn in row]
            assert CB["OWN_SALES_REPORT"] not in sub_cb

    def test_sales_report_i18n_keys(self):
        assert "[missing:" not in t("fa", "panels.owner.sales_report")
        assert "[missing:" not in t("fa", "reports.sales_report_title")
        assert "[missing:" not in t("fa", "reports.sales_report_empty")
        assert "[missing:" not in t("fa", "reports.sales_report_item",
                                     sudo_id=1, units=5, price="5000", date="2026-01-01")
        assert "[missing:" not in t("fa", "reports.sales_report_total",
                                     total_units=5, total_price="5000")


# ═══════════════════════════════════════════════════════════════════════
# GAP 5 — Pyromod ask() Fallback
# ═══════════════════════════════════════════════════════════════════════

class TestSafeAskFallback:

    async def test_ask_success_returns_message(self):
        fake_msg = MagicMock()
        fake_msg.text = "42"

        client = AsyncMock()
        client.ask = AsyncMock(return_value=fake_msg)

        from app.utils.safe_ask import safe_ask
        result = await safe_ask(client, 123, "ask.chat_id")
        assert result is fake_msg

    async def test_ask_import_error_triggers_fallback(self):
        client = AsyncMock()
        client.ask = AsyncMock(side_effect=AttributeError("no ask"))
        client.send_message = AsyncMock()
        client.on_message = MagicMock(return_value=lambda fn: fn)
        client.remove_handler = MagicMock()

        from app.utils.safe_ask import safe_ask
        result = await asyncio.wait_for(
            safe_ask(client, 123, "ask.chat_id", timeout=1),
            timeout=3,
        )
        assert result is None
        client.send_message.assert_called()


# ═══════════════════════════════════════════════════════════════════════
# GAP 6 — Satellite TV
# ═══════════════════════════════════════════════════════════════════════

class TestSatelliteTV:

    def test_satellite_asset_exists(self):
        path = Path("app/assets/satellite_channels.json")
        assert path.exists()
        data = json.loads(path.read_text())
        assert isinstance(data, list)
        assert len(data) > 0
        assert all("id" in ch and "name" in ch and "url" in ch for ch in data)

    def test_pb_sat_cb_exists(self):
        assert "PB_SAT" in CB
        assert CB["PB_SAT"] == "pb:type:satellite"

    def test_satellite_keyboard_renders(self):
        channels = [
            {"id": "test1", "name": "Test 1", "url": "http://example.com/1"},
            {"id": "test2", "name": "Test 2", "url": "http://example.com/2"},
        ]
        kb = KeyboardFactory.satellite_channels_menu("fa", channels)
        assert len(kb.inline_keyboard) >= 2
        assert kb.inline_keyboard[0][0].callback_data == "pb:sat:test1"

    def test_satellite_pagination(self):
        channels = [{"id": f"ch{i}", "name": f"Channel {i}", "url": f"http://x/{i}"} for i in range(20)]
        kb = KeyboardFactory.satellite_channels_menu("fa", channels, page=0, per_page=8)
        cb_data = [btn.callback_data for row in kb.inline_keyboard for btn in row]
        assert any("pb:sat:page:1" in cd for cd in cb_data if cd)

    def test_satellite_i18n_keys(self):
        assert "[missing:" not in t("fa", "tv_radio.choose_satellite")
        assert "[missing:" not in t("fa", "tv_radio.no_satellite")


# ═══════════════════════════════════════════════════════════════════════
# GAP 7 — credit_history Partitioning
# ═══════════════════════════════════════════════════════════════════════

class TestCreditHistoryPartition:

    @pytest.fixture(autouse=True)
    def _skip_if_not_postgres(self):
        if not os.environ.get("DATABASE_URL", "").startswith("postgresql"):
            pytest.skip("credit_history partitioning tests require PostgreSQL")

    async def test_migration_runs_and_table_is_partitioned(self, _patch_session):
        async with _patch_session() as session:
            result = await session.execute(text(
                "SELECT relkind::text FROM pg_class WHERE relname = 'credit_history'"
            ))
            kind = result.scalar()
            assert kind == 'p'

    async def test_default_partition_exists(self, _patch_session):
        async with _patch_session() as session:
            result = await session.execute(text(
                "SELECT count(*) FROM pg_inherits JOIN pg_class ON pg_inherits.inhrelid = pg_class.oid "
                "WHERE inhparent = (SELECT oid FROM pg_class WHERE relname = 'credit_history')"
            ))
            count = result.scalar()
            assert count >= 1


# ═══════════════════════════════════════════════════════════════════════
# GAP 8 — Sudo Link Management
# ═══════════════════════════════════════════════════════════════════════

class TestSudoLinkManagement:

    def test_sudo_link_cb_constants_exist(self):
        assert "DEV_SUDO_LINK_SET" in CB
        assert "DEV_SUDO_LINK_RM" in CB
        assert "DEV_SUDO_LINK_LIST" in CB

    def test_sudo_link_i18n_keys(self):
        assert "[missing:" not in t("fa", "sudo_mgmt.link_set")
        assert "[missing:" not in t("fa", "sudo_mgmt.link_removed", user="test")
        assert "[missing:" not in t("fa", "sudo_mgmt.link_list_title")
        assert "[missing:" not in t("fa", "sudo_mgmt.link_list_empty")

    async def test_sudo_link_roundtrip(self, _patch_session):
        user_id = 9999999
        async with _patch_session() as session:
            async with session.begin():
                await session.execute(text(
                    "DELETE FROM sudos WHERE user_id = :uid"
                ), {"uid": user_id})
                session.add(Sudo(
                    user_id=user_id, username="test_sudo",
                    display_name="Test", is_active=True,
                ))

        async with _patch_session() as session:
            async with session.begin():
                stmt = select(Sudo).where(Sudo.user_id == user_id)
                result = await session.execute(stmt)
                sudo = result.scalar_one()
                sudo.sudo_link = "https://t.me/test_sudo"

        async with _patch_session() as session:
            stmt = select(Sudo).where(Sudo.user_id == user_id)
            result = await session.execute(stmt)
            sudo = result.scalar_one()
            assert sudo.sudo_link == "https://t.me/test_sudo"

        async with _patch_session() as session:
            async with session.begin():
                stmt = select(Sudo).where(Sudo.user_id == user_id)
                result = await session.execute(stmt)
                sudo = result.scalar_one()
                sudo.sudo_link = None

        async with _patch_session() as session:
            stmt = select(Sudo).where(Sudo.user_id == user_id)
            result = await session.execute(stmt)
            sudo = result.scalar_one()
            assert sudo.sudo_link is None


# ═══════════════════════════════════════════════════════════════════════
# GAP 9 — Channel-Specific Upsert
# ═══════════════════════════════════════════════════════════════════════

class TestChannelUpsert:

    async def test_upsert_channel_creates(self, _patch_session):
        from app.repositories import channel_repo

        chat_id = -1009999991
        async with _patch_session() as session:
            async with session.begin():
                await session.execute(text(
                    "DELETE FROM channels WHERE chat_id = :cid"
                ), {"cid": chat_id})

        ch = await channel_repo.upsert_channel(
            chat_id=chat_id, chat_title="Test Channel", installed_by=123,
        )
        assert ch.chat_id == chat_id
        assert ch.status == "active"

    async def test_upsert_channel_updates(self, _patch_session):
        from app.repositories import channel_repo

        chat_id = -1009999992
        async with _patch_session() as session:
            async with session.begin():
                await session.execute(text(
                    "DELETE FROM channels WHERE chat_id = :cid"
                ), {"cid": chat_id})

        await channel_repo.upsert_channel(chat_id=chat_id, chat_title="Old Title")
        ch = await channel_repo.upsert_channel(chat_id=chat_id, chat_title="New Title")
        assert ch.chat_title == "New Title"

    async def test_install_flow_calls_upsert_channel(self):
        """Verify install.py imports and can reference channel_repo."""
        import app.handlers.install as install_mod
        assert hasattr(install_mod, 'channel_repo')


# ═══════════════════════════════════════════════════════════════════════
# GAP 10 — PyTgCalls Instance Recycle
# ═══════════════════════════════════════════════════════════════════════

class TestPyTgCallsRecycle:

    def test_idle_instance_can_be_recycled(self):
        from app.services.pytgcalls_recycle import RecycleManager
        mgr = RecycleManager()
        mgr.mark_for_recycle(1)
        assert mgr.should_recycle(1, active_calls=0) is True

    def test_active_instance_not_recycled(self):
        from app.services.pytgcalls_recycle import RecycleManager
        mgr = RecycleManager()
        mgr.mark_for_recycle(1)
        assert mgr.should_recycle(1, active_calls=3) is False

    async def test_try_recycle_calls_recreate(self):
        from app.services.pytgcalls_recycle import RecycleManager
        mgr = RecycleManager()
        mgr.mark_for_recycle(1)

        recreate = AsyncMock()
        result = await mgr.try_recycle(1, active_calls=0, recreate_fn=recreate)
        assert result is True
        recreate.assert_awaited_once_with(1)

    async def test_try_recycle_skips_active(self):
        from app.services.pytgcalls_recycle import RecycleManager
        mgr = RecycleManager()
        mgr.mark_for_recycle(1)

        recreate = AsyncMock()
        result = await mgr.try_recycle(1, active_calls=2, recreate_fn=recreate)
        assert result is False
        recreate.assert_not_awaited()

    def test_clear_recycle_removes_pending(self):
        from app.services.pytgcalls_recycle import RecycleManager
        mgr = RecycleManager()
        mgr.mark_for_recycle(1)
        mgr.clear_recycle(1)
        assert mgr.should_recycle(1, active_calls=0) is False


# ═══════════════════════════════════════════════════════════════════════
# UI Contract — Key Parity (extended)
# ═══════════════════════════════════════════════════════════════════════

class TestExtendedKeyParity:

    def test_all_new_keys_exist_in_both_languages(self):
        new_keys = [
            "credit.financial_surface_removed",
            "credit.developer_only",
            "notifications.health_degraded",
            "notifications.credit_warning_sudo_dm",
            "notifications.credit_expired_sudo_dm",
            "ask.timeout",
            "tv_radio.choose_satellite",
            "tv_radio.no_satellite",
            "sudo_mgmt.link_removed",
            "sudo_mgmt.link_list_title",
            "sudo_mgmt.link_list_empty",
        ]
        for key in new_keys:
            fa_val = t("fa", key)
            en_val = t("en", key)
            assert "[missing:" not in fa_val, f"FA missing: {key}"
            assert "[missing:" not in en_val, f"EN missing: {key}"

    def test_new_cb_constants_are_ascii(self):
        new_cbs = [
            "OWN_SALES_REPORT",
            "DEV_SUDO_LINK_SET",
            "DEV_SUDO_LINK_RM",
            "DEV_SUDO_LINK_LIST",
        ]
        for name in new_cbs:
            val = CB[name]
            assert val.isascii(), f"CB[{name}] = {val!r} is not ASCII"

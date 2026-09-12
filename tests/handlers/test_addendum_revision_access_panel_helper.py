"""Tests for Tasks 1–3: sudo/owner billing bypass, panel/help routing, helper join."""
from __future__ import annotations

import os
import sys
from types import SimpleNamespace

os.environ.setdefault("TEST_MODE", "1")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./.pytest-test-mode.db")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/15")
os.environ.setdefault("BOT_TOKEN", "test")
os.environ.setdefault("API_ID", "12345")
os.environ.setdefault("API_HASH", "testhash")
os.environ.setdefault("DEVELOPER_ID", "999888777")

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


# ── Task 1: Sudo/Owner Billing Bypass ────────────────────────────────────────

class TestSudoBypass:
    """Verify install cost is always 0 for sudo/owner/developer, normal path still works."""

    @pytest.fixture
    def mock_policy(self):
        policy = MagicMock()
        policy.policy_mode = "paid"
        policy.charge_on_install = True
        policy.group_install_fee_irr = 500
        policy.chan_install_fee_irr = 1000
        return policy

    @pytest.mark.asyncio
    async def test_sudo_install_cost_zero(self, mock_policy):
        with (
            patch(
                "app.services.install_policy_service.InstallPolicyService.is_free_install",
                new=AsyncMock(return_value=False),
            ),
            patch(
                "app.services.install_policy_service.InstallPolicyService.get_policy",
                new=AsyncMock(return_value=mock_policy),
            ),
        ):
            from app.services.install_policy_service import InstallPolicyService
            cost = await InstallPolicyService.compute_install_cost("group", "sudo", 12345)
            assert cost == 0, "sudo must never be charged an install fee"

    @pytest.mark.asyncio
    async def test_owner_install_cost_zero(self, mock_policy):
        with (
            patch(
                "app.services.install_policy_service.InstallPolicyService.is_free_install",
                new=AsyncMock(return_value=False),
            ),
            patch(
                "app.services.install_policy_service.InstallPolicyService.get_policy",
                new=AsyncMock(return_value=mock_policy),
            ),
        ):
            from app.services.install_policy_service import InstallPolicyService
            cost = await InstallPolicyService.compute_install_cost("group", "owner", 12345)
            assert cost == 0, "owner must never be charged an install fee"

    @pytest.mark.asyncio
    async def test_developer_install_cost_zero(self, mock_policy):
        with (
            patch(
                "app.services.install_policy_service.InstallPolicyService.is_free_install",
                new=AsyncMock(return_value=False),
            ),
            patch(
                "app.services.install_policy_service.InstallPolicyService.get_policy",
                new=AsyncMock(return_value=mock_policy),
            ),
        ):
            from app.services.install_policy_service import InstallPolicyService
            cost = await InstallPolicyService.compute_install_cost("group", "developer", 12345)
            assert cost == 0, "developer must never be charged an install fee"

    @pytest.mark.asyncio
    async def test_normal_user_group_install_cost_nonzero(self, mock_policy):
        with (
            patch(
                "app.services.install_policy_service.InstallPolicyService.is_free_install",
                new=AsyncMock(return_value=False),
            ),
            patch(
                "app.services.install_policy_service.InstallPolicyService.get_policy",
                new=AsyncMock(return_value=mock_policy),
            ),
        ):
            from app.services.install_policy_service import InstallPolicyService
            cost = await InstallPolicyService.compute_install_cost("group", "other", 12345)
            assert cost == 500, "regular users must still be charged group_install_fee"

    @pytest.mark.asyncio
    async def test_normal_user_channel_install_cost_nonzero(self, mock_policy):
        with (
            patch(
                "app.services.install_policy_service.InstallPolicyService.is_free_install",
                new=AsyncMock(return_value=False),
            ),
            patch(
                "app.services.install_policy_service.InstallPolicyService.get_policy",
                new=AsyncMock(return_value=mock_policy),
            ),
        ):
            from app.services.install_policy_service import InstallPolicyService
            cost = await InstallPolicyService.compute_install_cost("channel", "other", 12345)
            assert cost == 1000, "regular users must still be charged chan_install_fee"

    @pytest.mark.asyncio
    async def test_free_policy_costs_nothing(self):
        policy = MagicMock()
        policy.policy_mode = "free"
        policy.charge_on_install = False
        with (
            patch(
                "app.services.install_policy_service.InstallPolicyService.is_free_install",
                new=AsyncMock(return_value=False),
            ),
            patch(
                "app.services.install_policy_service.InstallPolicyService.get_policy",
                new=AsyncMock(return_value=policy),
            ),
        ):
            from app.services.install_policy_service import InstallPolicyService
            cost = await InstallPolicyService.compute_install_cost("group", "other", 12345)
            assert cost == 0

    @pytest.mark.asyncio
    async def test_whitelist_costs_nothing(self, mock_policy):
        with (
            patch(
                "app.services.install_policy_service.InstallPolicyService.is_free_install",
                new=AsyncMock(return_value=True),
            ),
            patch(
                "app.services.install_policy_service.InstallPolicyService.get_policy",
                new=AsyncMock(return_value=mock_policy),
            ),
        ):
            from app.services.install_policy_service import InstallPolicyService
            cost = await InstallPolicyService.compute_install_cost("group", "other", 12345)
            assert cost == 0


# ── Task 2: Command / Panel Routing ──────────────────────────────────────────

class TestCommandRouting:
    """Verify /panel and پنل route to group panel, /help and راهنما route to help."""

    def test_panel_filter_pattern_matches_slash_command(self):
        import re
        pattern = re.compile(r"^(?:پنل)\s*$", re.IGNORECASE)
        assert pattern.match("پنل")
        assert pattern.match("پنل ")
        assert not pattern.match("پنل پلیر")

    def test_group_settings_filter_matches_slash_settings(self):
        import re
        pattern = re.compile(r"^(?:تنظیمات|settings)\s*$", re.IGNORECASE)
        assert pattern.match("تنظیمات")
        assert pattern.match("settings")
        assert not pattern.match("تنظیمات پلیر")

    def test_help_cmd_pattern_matches_all_aliases(self):
        import re
        pattern = re.compile(r"^(/help|راهنما|کمک|help)$", re.IGNORECASE)
        for cmd in ["/help", "راهنما", "کمک", "help"]:
            assert pattern.match(cmd), f"Expected match for {cmd}"
        assert not pattern.match("راهنما کامل")

    def test_panel_uses_different_filter_from_help(self):
        import re
        panel_pattern = re.compile(r"^(?:پنل)(?:\s|$)", re.IGNORECASE)
        help_pattern = re.compile(r"^(/help|راهنما|کمک|help)$", re.IGNORECASE)
        assert not panel_pattern.match("راهنما")
        assert not help_pattern.match("پنل")


# ── Task 3: Helper Join Logic ─────────────────────────────────────────────────

class TestHelperAutoJoin:
    """Verify ensure_helper_present_for_group logic."""

    @pytest.mark.asyncio
    async def test_ensure_helper_no_binding_success(self):
        from app.services.call_service import ensure_helper_present_for_group
        from app.services.helper_pool_service import HelperJoinResult

        mock_session_ctx = AsyncMock()
        mock_session_ctx.__aenter__ = AsyncMock(return_value=mock_session_ctx)
        mock_session_ctx.__aexit__ = AsyncMock(return_value=False)
        mock_session_ctx.execute = AsyncMock(
            return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=None))
        )

        with (
            patch("app.services.call_service.async_session", return_value=mock_session_ctx),
            patch(
                "app.services.helper_pool_service.HelperPoolService.reserve_best_helper",
                new=AsyncMock(return_value=SimpleNamespace(id=42)),
            ),
            patch(
                "app.services.helper_pool_service.HelperPoolService.ensure_helper_joined_detailed",
                new=AsyncMock(return_value=HelperJoinResult(ok=True, reason="joined")),
            ),
            patch(
                "app.services.helper_pool_service.HelperPoolService.bind_chat_to_helper",
                new=AsyncMock(),
            ),
            patch(
                "app.services.helper_admin_service.ensure_helper_call_admin",
                new=AsyncMock(return_value=SimpleNamespace(ok=True, reason="promoted")),
            ),
            patch("app.repositories.helper_event_repo.log_event", new=AsyncMock()),
        ):
            result = await ensure_helper_present_for_group(chat_id=100, reason="test")
        assert result == "success"

    @pytest.mark.asyncio
    async def test_ensure_helper_already_present(self):
        from app.services.call_service import ensure_helper_present_for_group

        binding = MagicMock()
        binding.helper_account_id = 7

        mock_session_ctx = AsyncMock()
        mock_session_ctx.__aenter__ = AsyncMock(return_value=mock_session_ctx)
        mock_session_ctx.__aexit__ = AsyncMock(return_value=False)
        mock_session_ctx.execute = AsyncMock(
            return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=binding))
        )

        with (
            patch("app.services.call_service.async_session", return_value=mock_session_ctx),
            patch(
                "app.services.helper_pool_service.HelperPoolService.ensure_helper_joined",
                new=AsyncMock(return_value=True),
            ),
            patch(
                "app.services.helper_admin_service.ensure_helper_call_admin",
                new=AsyncMock(return_value=SimpleNamespace(ok=True, reason="already_admin")),
            ),
            patch("app.repositories.helper_event_repo.log_event", new=AsyncMock()),
        ):
            result = await ensure_helper_present_for_group(chat_id=100, reason="test")
        assert result == "already_present"

    @pytest.mark.asyncio
    async def test_ensure_helper_unavailable(self):
        from app.services.call_service import ensure_helper_present_for_group

        mock_session_ctx = AsyncMock()
        mock_session_ctx.__aenter__ = AsyncMock(return_value=mock_session_ctx)
        mock_session_ctx.__aexit__ = AsyncMock(return_value=False)
        mock_session_ctx.execute = AsyncMock(
            return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=None))
        )

        with (
            patch("app.services.call_service.async_session", return_value=mock_session_ctx),
            patch(
                "app.services.helper_pool_service.HelperPoolService.reserve_best_helper",
                new=AsyncMock(return_value=None),
            ),
        ):
            result = await ensure_helper_present_for_group(chat_id=100, reason="test")
        assert result == "unavailable"


class TestAddHelperProgressMessage:
    """/addhelper must use a dedicated progress key, not the try_later error text."""

    def test_handler_uses_progress_key_not_try_later(self):
        import inspect
        import app.handlers.add_helper as add_helper

        source = inspect.getsource(add_helper)
        assert "admin.helpers.join_group_progress" in source, (
            "/addhelper must send admin.helpers.join_group_progress as progress message"
        )
        assert "common.errors.try_later" not in source, (
            "/addhelper must not use common.errors.try_later as progress message"
        )

    @pytest.mark.parametrize("lang", ["fa", "en"])
    def test_progress_key_resolves(self, lang):
        from app.utils.i18n import TextService

        ts = TextService()
        val = ts.t(lang, "admin.helpers.join_group_progress")
        assert not val.startswith("[missing:"), f"Progress key missing for {lang}"
        assert not val.startswith("[invalid:"), f"Progress key invalid for {lang}"

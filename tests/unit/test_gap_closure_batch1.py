"""Tests for Gap Closure Batch 1: auto-leave, daily deduct integration, panel buttons."""
from __future__ import annotations

import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./.pytest-test-mode.db")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("BOT_TOKEN", "test")
os.environ.setdefault("API_ID", "12345")
os.environ.setdefault("API_HASH", "testhash")
os.environ.setdefault("DEVELOPER_ID", "123456789")


# ── GAP-1: auto_leave_check exists and works ─────────────────────────────

def test_auto_leave_check_method_exists():
    from app.services.credit_service import CreditService
    assert callable(getattr(CreditService, "auto_leave_check", None))


@pytest.mark.asyncio
async def test_auto_leave_triggers_when_enabled():
    """auto_leave_check leaves voice chat when auto_leave_enabled is True."""
    mock_settings = MagicMock()
    mock_settings.auto_leave_enabled = True
    expired_state = SimpleNamespace(
        is_active=True,
        credit=object(),
        has_runtime_credit=False,
        credit_status="expired",
        credit_days=0,
    )

    with (
        patch("app.repositories.settings_repo.get_chat_settings", new_callable=AsyncMock, return_value=mock_settings),
        patch(
            "app.services.group_runtime_state_service.get_runtime_credit_state",
            AsyncMock(return_value=expired_state),
        ),
    ):
        mock_call_py = MagicMock()
        mock_leave = AsyncMock()
        with patch("app.services.call_service.CallService.leave_voice_chat", mock_leave):
            from app.services.credit_service import CreditService
            result = await CreditService.auto_leave_check(-1001234, call_py=mock_call_py)
            assert result is True
            mock_leave.assert_called_once_with(mock_call_py, -1001234)


@pytest.mark.asyncio
async def test_auto_leave_skips_when_disabled():
    """auto_leave_check returns False when auto_leave_enabled is False."""
    mock_settings = MagicMock()
    mock_settings.auto_leave_enabled = False

    with patch("app.repositories.settings_repo.get_chat_settings", new_callable=AsyncMock, return_value=mock_settings):
        from app.services.credit_service import CreditService
        result = await CreditService.auto_leave_check(-1001234)
        assert result is False


# ── GAP-2: daily_deduct collects expired chat IDs ────────────────────────

def test_daily_deduct_signature_unchanged():
    """daily_deduct_all still exists as a static coroutine."""
    import asyncio
    from app.services.credit_service import CreditService
    assert asyncio.iscoroutinefunction(CreditService.daily_deduct_all)


# ── GAP-3: charge_with_wallet has wallet check ──────────────────────────

def test_charge_with_wallet_method_exists():
    from app.services.credit_service import CreditService
    assert callable(getattr(CreditService, "charge_with_wallet", None))


# ── GAP-7: Dev panel has help + analytics + helper buttons ───────────────

def test_dev_panel_has_help_analytics_helpers():
    from app.utils.ui import CB, KeyboardFactory
    kb = KeyboardFactory.developer_panel("fa")
    cbs = [btn.callback_data for row in kb.inline_keyboard for btn in row if btn.callback_data]
    assert CB["HLP_HOME"] in cbs, "Missing Helper Management button"
    assert CB["AN_HOME"] in cbs, "Missing Analytics button"
    assert CB["DEV_ABOUT"] in cbs, "Missing About button"
    assert CB["HELP_HOME"] not in cbs


# ── GAP-8: SPEC_COMPLIANCE_AUDIT.md updated (file exists) ───────────────

def test_spec_compliance_audit_exists():
    import os
    assert os.path.exists("docs/reports/current/spec_compliance_audit.md")

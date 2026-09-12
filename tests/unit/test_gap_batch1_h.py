"""Tests for HIGH-priority gap closures: H1-H5."""
from __future__ import annotations

import json
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.i18n_test_utils import load_en_i18n, load_fa_i18n

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./.pytest-test-mode.db")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("BOT_TOKEN", "test")
os.environ.setdefault("API_ID", "12345")
os.environ.setdefault("API_HASH", "testhash")
os.environ.setdefault("DEVELOPER_ID", "123456789")


# ── GAP-H1: Status page includes server metrics ─────────────────────────

def test_status_template_has_server_fields():
    fa = load_fa_i18n()
    bot_info = fa["status"]["bot_info"]
    for field in ("cpu", "ram_used", "ram_total", "ram_pct", "disk_used", "disk_total", "disk_pct", "uptime"):
        assert f"{{{field}}}" in bot_info, f"Missing {{{{ {field} }}}} in status.bot_info"


def test_status_template_en_has_server_fields():
    en = load_en_i18n()
    bot_info = en["status"]["bot_info"]
    assert "{cpu}" in bot_info
    assert "{uptime}" in bot_info


# ── GAP-H2: Invoice history handler + Jalali dates ──────────────────────

def test_invoice_history_cb_constant():
    from app.utils.ui import CB
    assert "DEV_INVOICE_HISTORY" in CB


def test_invoice_history_i18n_keys():
    fa = load_fa_i18n()
    dev = fa["panels"]["developer"]
    assert "invoice_history_title" in dev
    assert "invoice_history_item" in dev
    assert "invoice_history_empty" in dev


# ── GAP-H3: VIP promote/demote ──────────────────────────────────────────

def test_vip_repo_methods_exist():
    from app.repositories import admin_repo
    assert callable(getattr(admin_repo, "is_vip", None))
    assert callable(getattr(admin_repo, "promote_vip", None))
    assert callable(getattr(admin_repo, "demote_vip", None))


def test_vip_commands_defined():
    from app.handlers.promotion import _PROMOTE_VIP_CMDS, _DEMOTE_VIP_CMDS
    assert "ترفیع ویژه" in _PROMOTE_VIP_CMDS
    assert "ارتقا ویژه پلیر" in _PROMOTE_VIP_CMDS
    assert "promotevip" in _PROMOTE_VIP_CMDS
    assert "SetVip Player" in _PROMOTE_VIP_CMDS
    assert "عزل ویژه" in _DEMOTE_VIP_CMDS
    assert "عزل ویژه پلیر" in _DEMOTE_VIP_CMDS
    assert "demotevip" in _DEMOTE_VIP_CMDS
    assert "RemVip Player" in _DEMOTE_VIP_CMDS


# ── GAP-H4: Auto-leave notifies sudo ────────────────────────────────────

def test_auto_leave_sudo_dm_i18n_key():
    fa = load_fa_i18n()
    assert "auto_leave_sudo_dm" in fa["notifications"]
    en = load_en_i18n()
    assert "auto_leave_sudo_dm" in en["notifications"]


@pytest.mark.asyncio
async def test_auto_leave_sends_sudo_dm():
    """auto_leave_check sends DM to the responsible sudo."""
    mock_cs = MagicMock()
    mock_cs.auto_leave_enabled = True

    mock_log = MagicMock()
    mock_log.sudo_id = 777

    mock_group = MagicMock()
    mock_group.chat_title = "Test Group"
    expired_state = SimpleNamespace(
        is_active=True,
        credit=object(),
        has_runtime_credit=False,
        credit_status="expired",
        credit_days=0,
    )

    with patch("app.repositories.settings_repo.get_chat_settings", new_callable=AsyncMock, return_value=mock_cs), \
         patch("app.services.call_service.CallService.leave_voice_chat", new_callable=AsyncMock), \
         patch(
             "app.services.group_runtime_state_service.get_runtime_credit_state",
             AsyncMock(return_value=expired_state),
         ):
        mock_bot = AsyncMock()
        mock_bot.send_message = AsyncMock()

        with patch("app.services.credit_service.async_session") as mock_session_ctx:
            mock_session = AsyncMock()
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=False)
            mock_session_ctx.return_value = mock_session

            mock_result_grp = MagicMock()
            mock_result_grp.scalar_one_or_none.return_value = mock_group
            mock_result_log = MagicMock()
            mock_result_log.scalar_one_or_none.return_value = mock_log
            mock_session.execute = AsyncMock(side_effect=[mock_result_grp, mock_result_log])

            from app.services.credit_service import CreditService
            result = await CreditService.auto_leave_check(-1001234, call_py=MagicMock(), bot=mock_bot)
            assert result is True
            assert mock_bot.send_message.call_count >= 2


# ── GAP-H5: Call security enforcement ────────────────────────────────────

def test_check_prerequisites_has_security_call_check():
    """_check_prerequisites references security_call_enabled."""
    from pathlib import Path
    src = Path("app/handlers/playback.py").read_text()
    assert "security_call_enabled" in src


def test_vip_check_in_prerequisites():
    """_check_prerequisites checks VIP status when security_call is enabled."""
    from pathlib import Path
    playback_src = Path("app/handlers/playback.py").read_text(encoding="utf-8")
    auth_src = Path("app/utils/playback_auth.py").read_text(encoding="utf-8")
    perm_src = Path("app/utils/player_permissions.py").read_text(encoding="utf-8")
    assert "authorize_playback_action" in playback_src
    assert ("is_vip" in auth_src) or ("can_play_media" in auth_src and "is_vip" in perm_src)

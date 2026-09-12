"""Tests for the Advanced Broadcast Wizard FSM, checkboxes, Jalali, and scheduling."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import jdatetime
import pytest


# ── CB constants ────────────────────────────────────────────────────────

def test_bcw_cb_constants_exist():
    from app.utils.ui import CB
    required = [
        "BCW_START", "BCW_MODE_SEND", "BCW_MODE_FWD",
        "BCW_TGT_USERS", "BCW_TGT_GROUPS", "BCW_TGT_CHANNELS", "BCW_TGT_NEXT",
        "BCW_FILTER_ALL", "BCW_FILTER_7D", "BCW_FILTER_30D",
        "BCW_SEND_NOW", "BCW_SEND_AT", "BCW_SEND_AFTER", "BCW_SEND_RECURRING",
        "BCW_CONFIRM", "BCW_CANCEL",
        "BCW_BACK_MODE", "BCW_BACK_TGT", "BCW_BACK_FILTER", "BCW_BACK_SCHED",
    ]
    for key in required:
        assert key in CB, f"Missing CB key: {key}"
        assert isinstance(CB[key], str) and CB[key].startswith("bcw:")


# ── Keyboard builders ───────────────────────────────────────────────────

def test_targets_keyboard_selected_state():
    from app.handlers.broadcast_wizard import _targets_keyboard
    kb = _targets_keyboard("en", {"users", "channels"})
    texts = [btn.text for row in kb.inline_keyboard for btn in row]
    assert any("✅" in t_ and "Users" in t_ for t_ in texts)
    assert any("✅" in t_ and "Channels" in t_ for t_ in texts)
    assert any("◻️" in t_ and "Groups" in t_ for t_ in texts)


def test_targets_keyboard_empty():
    from app.handlers.broadcast_wizard import _targets_keyboard
    kb = _targets_keyboard("en", set())
    texts = [btn.text for row in kb.inline_keyboard for btn in row]
    assert not any("✅" in t_ for t_ in texts)


def test_mode_keyboard_has_both_options():
    from app.handlers.broadcast_wizard import _mode_keyboard
    kb = _mode_keyboard("en")
    cbs = [btn.callback_data for row in kb.inline_keyboard for btn in row]
    from app.utils.ui import CB
    assert CB["BCW_MODE_SEND"] in cbs
    assert CB["BCW_MODE_FWD"] in cbs


def test_filter_keyboard():
    from app.handlers.broadcast_wizard import _filter_keyboard
    kb = _filter_keyboard("en")
    cbs = [btn.callback_data for row in kb.inline_keyboard for btn in row]
    from app.utils.ui import CB
    assert CB["BCW_FILTER_ALL"] in cbs
    assert CB["BCW_FILTER_7D"] in cbs
    assert CB["BCW_FILTER_30D"] in cbs
    assert CB["BCW_BACK_TGT"] in cbs


def test_schedule_keyboard():
    from app.handlers.broadcast_wizard import _schedule_keyboard
    kb = _schedule_keyboard("en")
    cbs = [btn.callback_data for row in kb.inline_keyboard for btn in row]
    from app.utils.ui import CB
    assert CB["BCW_SEND_NOW"] in cbs
    assert CB["BCW_SEND_AT"] in cbs
    assert CB["BCW_SEND_AFTER"] in cbs
    assert CB["BCW_SEND_RECURRING"] in cbs
    assert CB["BCW_BACK_FILTER"] in cbs


# ── FSM state ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_state_roundtrip():
    from app.handlers.broadcast_wizard import _set_state, _get_state, _clear_state
    with patch("app.handlers.broadcast_wizard.get_redis") as mock_redis:
        r = AsyncMock()
        mock_redis.return_value = r
        r.set = AsyncMock()
        r.get = AsyncMock(return_value=json.dumps({"step": "mode_selection", "mode": "send"}))
        r.delete = AsyncMock()

        await _set_state(999, {"step": "mode_selection", "mode": "send"})
        r.set.assert_called_once()

        state = await _get_state(999)
        assert state["step"] == "mode_selection"

        await _clear_state(999)
        r.delete.assert_called_once()


@pytest.mark.asyncio
async def test_get_state_returns_none_when_empty():
    from app.handlers.broadcast_wizard import _get_state
    with patch("app.handlers.broadcast_wizard.get_redis") as mock_redis:
        r = AsyncMock()
        mock_redis.return_value = r
        r.get = AsyncMock(return_value=None)
        assert await _get_state(123) is None


# ── Jalali parsing ──────────────────────────────────────────────────────

def test_jalali_parse_valid():
    jdt = jdatetime.datetime.strptime("1403/12/05 18:30", "%Y/%m/%d %H:%M")
    gdt = jdt.togregorian()
    run_at = datetime(gdt.year, gdt.month, gdt.day, gdt.hour, gdt.minute, tzinfo=timezone.utc)
    assert run_at.year == 2025
    assert run_at.month == 2
    assert run_at.day == 23
    assert run_at.hour == 18
    assert run_at.minute == 30


def test_jalali_parse_invalid():
    with pytest.raises(ValueError):
        jdatetime.datetime.strptime("invalid_date", "%Y/%m/%d %H:%M")


# ── Confirm text builder ────────────────────────────────────────────────

def test_build_confirm_text_now():
    from app.handlers.broadcast_wizard import _build_confirm_text
    from app.utils.i18n import label, t
    state = {"mode": "send", "targets": ["users", "groups"], "filter": "all", "schedule": "now"}
    text = _build_confirm_text(state)
    assert label("fa", "broadcast_mode", "send") in text
    assert label("fa", "broadcast_scope", "users") in text


def test_build_confirm_text_recurring():
    from app.handlers.broadcast_wizard import _build_confirm_text
    from app.utils.i18n import t
    state = {"mode": "forward", "targets": ["channels"], "filter": "7d",
             "schedule": "recurring", "interval_hours": 6}
    text = _build_confirm_text(state)
    assert t("fa", "broadcast.wizard.schedule_every_hours", hours=6) in text


def test_build_confirm_text_at():
    from app.handlers.broadcast_wizard import _build_confirm_text
    state = {"mode": "send", "targets": ["users"], "filter": "30d",
             "schedule": "at", "run_at": "2025-03-01T14:00:00+00:00"}
    text = _build_confirm_text(state)
    assert "2025-03-01" in text


# ── DB model columns ────────────────────────────────────────────────────

def test_broadcast_model_has_scheduling_columns():
    from app.database.models import Broadcast
    cols = {c.name for c in Broadcast.__table__.columns}
    assert "run_at" in cols
    assert "interval_hours" in cols
    assert "target_types_json" in cols
    assert "filter_type" in cols
    assert "source_admin_chat_id" in cols
    assert "source_admin_msg_id" in cols


# ── i18n parity ─────────────────────────────────────────────────────────

def test_i18n_wizard_keys_parity():
    from tests.i18n_test_utils import load_en_i18n, load_fa_i18n

    fa = load_fa_i18n()
    en = load_en_i18n()
    fa_wizard = fa.get("broadcast", {}).get("wizard", {})
    en_wizard = en.get("broadcast", {}).get("wizard", {})
    assert fa_wizard.keys() == en_wizard.keys(), (
        f"Key mismatch: FA={set(fa_wizard) - set(en_wizard)} EN={set(en_wizard) - set(fa_wizard)}"
    )
    assert len(fa_wizard) >= 25


# ── Dev panel integration ───────────────────────────────────────────────

def test_dev_broadcast_submenu_has_wizard_button():
    from app.utils.ui import CB, KeyboardFactory
    kb = KeyboardFactory.dev_sub_broadcast("en")
    cbs = [btn.callback_data for row in kb.inline_keyboard for btn in row]
    assert CB["BCW_START"] in cbs


# ── Migration file ──────────────────────────────────────────────────────

def test_migration_0008_exists():
    import importlib
    mod = importlib.import_module("app.database.migrations.versions.0008_broadcast_scheduling")
    assert mod.revision == "0008_broadcast_scheduling"
    assert mod.down_revision == "0007_credit_history_partition"

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./.pytest-test-mode.db")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("BOT_TOKEN", "test")
os.environ.setdefault("API_ID", "12345")
os.environ.setdefault("API_HASH", "testhash")
os.environ.setdefault("DEVELOPER_ID", "123456789")

from app.utils.ui import CB, KeyboardFactory  # noqa: E402


def _callback_data_set(kb) -> set[str]:
    return {
        btn.callback_data
        for row in getattr(kb, "inline_keyboard", [])
        for btn in row
        if getattr(btn, "callback_data", None)
    }


def test_broadcast_history_is_reachable_from_developer_broadcast_menu():
    callbacks = _callback_data_set(KeyboardFactory.dev_sub_broadcast("en"))

    assert CB["BCW_START"] in callbacks
    assert CB["BC_HISTORY"] in callbacks


def test_broadcast_history_still_generates_detail_callbacks():
    broadcast = SimpleNamespace(
        id=42,
        target_scope="users",
        status="queued",
        sent_count=0,
        total_recipients=10,
    )
    callbacks = _callback_data_set(KeyboardFactory.broadcast_history("en", [broadcast]))

    assert f"{CB['BC_DETAIL']}:42" in callbacks


def test_sudo_current_stats_and_lists_are_reachable_from_sudo_panel():
    callbacks = _callback_data_set(KeyboardFactory.sudo_panel("en"))

    assert CB["SUDO_STATUS"] in callbacks
    assert CB["SUDO_LISTS"] in callbacks
    assert CB["SUDO_MY_STATS"] not in callbacks


def test_generic_confirm_dialog_builder_was_removed():
    assert not hasattr(KeyboardFactory, "confirm_dialog")


def test_removed_placeholder_callbacks_are_not_generated_by_ui_builders():
    ui_text = Path("app/utils/ui.py").read_text(encoding="utf-8")

    for name in ("BC_CONFIRM_SEND", "BC_CONFIRM_FWD", "CONFIRM_YES", "CONFIRM_NO"):
        assert name not in CB
        assert f"CB[\"{name}\"]" not in ui_text


def test_removed_placeholder_handlers_are_not_registered():
    broadcast_panel_text = Path("app/handlers/broadcast_panel.py").read_text(encoding="utf-8")
    callbacks_text = Path("app/handlers/callbacks.py").read_text(encoding="utf-8")

    assert "def bc_confirm_send" not in broadcast_panel_text
    assert "def bc_confirm_fwd" not in broadcast_panel_text
    assert "def confirm_yes" not in callbacks_text
    assert "def confirm_no" not in callbacks_text

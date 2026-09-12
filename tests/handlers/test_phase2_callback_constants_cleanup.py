from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./.pytest-test-mode.db")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("BOT_TOKEN", "test")
os.environ.setdefault("API_ID", "12345")
os.environ.setdefault("API_HASH", "testhash")
os.environ.setdefault("DEVELOPER_ID", "123456789")

from app.utils.ui import CB, KeyboardFactory  # noqa: E402


REMOVED_CALLBACK_CONSTANTS = {
    "START_BUY_CREATOR",
    "START_BUY_SUDO_1",
    "START_BUY_SUDO_2",
    "START_GUIDE",
    "START_BOT_CH",
    "START_SUPPORT",
    "START_CUSTOM",
    "START_ADD_GROUP",
    "START_ADD_CHANNEL",
    "PB_SAT_PAGE_NEXT",
    "PB_SAT_PAGE_PREV",
    "POST_INSTALL_ADD_HELPER",
    "POST_INSTALL_GUIDE",
    "PB_FAV_RM",
    "CONFIRM_YES",
    "CONFIRM_NO",
    "FM_ADD_CONFIRM",
    "FM_ADD_CANCEL",
    "CS_VERIFY_ALL",
    "CS_DISABLE_BROKEN",
    "BC_CONFIRM_SEND",
    "BC_CONFIRM_FWD",
    "HELP_TV",
    "HELP_SAT",
    "HELP_OPEN_PRIVATE",
}


def _callback_data_set(kb) -> set[str]:
    return {
        btn.callback_data
        for row in getattr(kb, "inline_keyboard", [])
        for btn in row
        if getattr(btn, "callback_data", None)
    }


def test_removed_callback_constants_are_absent_from_cb_namespace():
    for name in REMOVED_CALLBACK_CONSTANTS:
        assert name not in CB


def test_phase1_wired_callbacks_remain_active():
    assert CB["BC_HISTORY"] in _callback_data_set(KeyboardFactory.dev_sub_broadcast("en"))
    callbacks = _callback_data_set(KeyboardFactory.sudo_panel("en"))
    assert CB["SUDO_LISTS"] in callbacks
    assert CB["SUDO_MY_STATS"] not in callbacks


def test_url_buttons_do_not_reintroduce_callback_constants():
    start_kb = KeyboardFactory.start_menu(
        "en",
        {
            "creator": "https://t.me/creator",
            "sudo_1": "https://t.me/sudo1",
            "sudo_2": "https://t.me/sudo2",
            "guide_channel": "https://t.me/guide",
            "bot_channel": "https://t.me/bot",
            "support_group": "https://t.me/support",
            "custom_link": "https://example.com",
            "add_to_group": "https://t.me/bot?startgroup=true",
            "add_to_channel": "https://t.me/bot?startchannel=true",
        },
    )
    start_callbacks = _callback_data_set(start_kb)
    start_urls = {
        btn.url
        for row in start_kb.inline_keyboard
        for btn in row
        if getattr(btn, "url", None)
    }
    start_labels = {
        btn.text
        for row in start_kb.inline_keyboard
        for btn in row
    }
    start_row_lengths = [len(row) for row in start_kb.inline_keyboard]
    help_callbacks = _callback_data_set(KeyboardFactory.help_home("en", "regular", is_group=True))
    help_urls = [
        btn.url
        for row in KeyboardFactory.help_home("en", "regular", is_group=True).inline_keyboard
        for btn in row
        if getattr(btn, "url", None)
    ]

    assert start_callbacks == set()
    assert "https://t.me/creator" in start_urls
    assert "https://t.me/bot?startgroup=true" not in start_urls
    assert "🛒 Buy from Creator" in start_labels
    assert start_row_lengths[0] == 1
    assert all(length <= 2 for length in start_row_lengths[1:])
    assert help_urls == []
    assert CB["HELP_PLAYBACK"] in help_callbacks
    assert "HELP_OPEN_PRIVATE" not in CB


def test_dynamic_prefixes_use_current_constants():
    channels = [{"id": f"ch{i}", "name": f"Channel {i}", "url": f"https://example.com/{i}"} for i in range(20)]
    satellite_callbacks = _callback_data_set(
        KeyboardFactory.satellite_channels_menu("en", channels, page=0, per_page=8)
    )

    assert any(cb.startswith("pb:sat:page:") for cb in satellite_callbacks)
    assert "PB_SAT_PAGE_NEXT" not in CB
    assert "PB_SAT_PAGE_PREV" not in CB


def test_removed_legacy_callback_values_are_not_registered_or_generated():
    app_text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in [
            Path("app/utils/ui.py"),
            Path("app/handlers/broadcast_panel.py"),
            Path("app/handlers/callbacks.py"),
        ]
    )

    for value in ("bc:ok:send", "bc:ok:fwd", "confirm:yes", "confirm:no"):
        assert value not in app_text


def test_force_join_remove_base_constant_is_used_for_dynamic_prefix():
    src = Path("app/handlers/force_join_panel.py").read_text(encoding="utf-8")

    assert CB["FM_REMOVE"] == "fm:rm"
    assert "_FM_REMOVE_PREFIX = f\"{CB['FM_REMOVE']}:\"" in src

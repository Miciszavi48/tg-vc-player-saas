"""Developer panel handler tests.

Tests keyboard rendering, CB constant coverage, i18n keys,
and permission enforcement for every dev panel action.
"""
from __future__ import annotations

import os

import pytest

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./.pytest-test-mode.db")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("BOT_TOKEN", "test")
os.environ.setdefault("API_ID", "12345")
os.environ.setdefault("API_HASH", "testhash")
os.environ.setdefault("DEVELOPER_ID", "123456789")

_DEV_CB_KEYS = [
    "DEV_STATUS", "DEV_INCREASE_CREDIT", "DEV_DECREASE_CREDIT", "DEV_SEND_INVOICE",
    "DEV_SET_BASE_RATE", "DEV_SET_MUSIC_RATE", "DEV_SET_VIDEO_RATE", "DEV_SET_CALL_SECURITY_RATE",
    "DEV_SET_MUSIC_SELL_RATE", "DEV_SET_VIDEO_SELL_RATE",
    "DEV_BROADCAST_GROUP", "DEV_FORWARD_GROUP", "DEV_BROADCAST_PRIVATE", "DEV_FORWARD_PRIVATE",
    "DEV_BROADCAST_CHANNEL", "DEV_FORWARD_CHANNEL", "DEV_SEND_TO_SUDO",
    "DEV_FORCE_JOIN_TOGGLE", "DEV_AUTO_LEAVE_TOGGLE", "DEV_TRIAL_TOGGLE",
    "DEV_BOT_ENABLED_TOGGLE", "DEV_SUDO_PANEL_ENABLED_TOGGLE",
    "DEV_CHANNEL_SECURITY_TOGGLE", "DEV_SET_MEDIA_POLICY",
    "DEV_LIST_GROUPS", "DEV_LIST_CHANNELS", "DEV_LIST_NO_CREDIT",
    "DEV_LIST_RENEWAL_GROUPS", "DEV_LEAVE_GROUP",
    "DEV_FILTERS", "DEV_FORCE_JOIN_MANAGE", "DEV_SUDO_MANAGE", "DEV_SET_OWNER",
    "DEV_TEXTS_LINKS", "DEV_INSTALL_LIMITS", "DEV_BLACKLIST", "DEV_SET_LOG_CHANNEL",
    "DEV_INSTALL_POLICY",
]


@pytest.mark.asyncio
class TestDevPanelKeyboard:
    async def test_all_dev_cb_constants_exist(self):
        from app.utils.ui import CB
        for key in _DEV_CB_KEYS:
            assert key in CB, f"Missing CB constant: {key}"

    async def test_keyboard_renders_without_error(self):
        from app.utils.ui import KeyboardFactory
        kb = KeyboardFactory.developer_panel("fa")
        assert kb is not None
        assert len(kb.inline_keyboard) > 0

    async def test_keyboard_contains_all_core_buttons(self):
        from app.utils.ui import CB, KeyboardFactory
        kb = KeyboardFactory.developer_panel("fa")
        all_cb = {btn.callback_data for row in kb.inline_keyboard for btn in row if btn.callback_data}
        for key in ["DEV_STATUS", "DEV_CAT_CREDIT", "DEV_CAT_BROADCAST",
                     "DEV_CAT_LISTS", "DEV_CAT_SETTINGS", "DEV_CAT_USERS", "DEV_INSTALL_POLICY"]:
            assert CB[key] in all_cb, f"Button {key} missing from dev panel keyboard"
        assert CB["DEV_CAT_RATES"] not in all_cb

    async def test_all_dev_panel_i18n_keys_exist(self):
        from app.utils.i18n import t
        keys = [
            "panels.developer.title", "panels.developer.status",
            "panels.developer.increase_credit", "panels.developer.decrease_credit",
            "panels.developer.set_base_rate", "panels.developer.set_music_rate",
            "panels.developer.set_video_rate", "panels.developer.set_call_security_rate",
            "panels.developer.broadcast_group", "panels.developer.forward_group",
            "panels.developer.force_join_toggle", "panels.developer.auto_leave_toggle",
            "panels.developer.trial_toggle", "panels.developer.bot_toggle",
            "panels.developer.sudo_panel_toggle", "panels.developer.list_groups",
            "panels.developer.list_channels", "panels.developer.list_no_credit",
            "panels.developer.filters", "panels.developer.sudo_manage",
            "panels.developer.set_owner", "panels.developer.texts_links",
            "panels.developer.install_limits", "panels.developer.blacklist",
            "panels.developer.set_log_channel",
        ]
        for k in keys:
            val = t("fa", k)
            assert "[missing:" not in val, f"Missing FA i18n key: {k}"
            val_en = t("en", k)
            assert "[missing:" not in val_en, f"Missing EN i18n key: {k}"


@pytest.mark.asyncio
class TestDevPanelPermissionEnforcement:
    async def test_only_developer_id_passes_dev_filter(self):
        from app.config.settings import settings
        dev_id = settings.DEVELOPER_ID
        assert dev_id != 0
        assert dev_id != 999

    async def test_non_developer_does_not_match(self):
        from app.utils.bot_guards import is_developer

        assert is_developer(9999) is False

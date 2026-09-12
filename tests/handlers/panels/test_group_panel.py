"""Group panel tests — keyboard, i18n keys, toggle names."""
from __future__ import annotations

import os

import pytest

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./.pytest-test-mode.db")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("BOT_TOKEN", "test")
os.environ.setdefault("API_ID", "12345")
os.environ.setdefault("API_HASH", "testhash")
os.environ.setdefault("DEVELOPER_ID", "123456789")


@pytest.mark.asyncio
class TestGroupPanelKeyboard:
    async def test_group_panel_renders(self):
        from app.utils.ui import KeyboardFactory
        kb = KeyboardFactory.group_panel("fa")
        assert len(kb.inline_keyboard) > 0

    async def test_group_settings_has_visible_toggles(self):
        from app.utils.ui import CB, KeyboardFactory
        sd = {k: False for k in [
            "music_video", "security_call", "repeat", "download_users",
            "call_message", "auto_clean", "queue", "auto_ready_call",
            "call_report", "record_call", "show_id", "show_photo", "show_text",
        ]}
        sd["default_media_type"] = "audio"
        sd["language"] = "fa"
        kb = KeyboardFactory.group_settings("fa", sd)
        all_cb = {btn.callback_data for row in kb.inline_keyboard for btn in row if btn.callback_data}
        for key in [
            "GRP_SECURITY_CALL",
            "GRP_DOWNLOAD_USERS",
            "GRP_AUTO_CLEAN",
            "GRP_CALL_MESSAGE",
            "GRP_AUTO_READY_CALL",
            "GRP_MUSIC_VIDEO",
            "GRP_LANGUAGE",
            "GRP_DEFAULT_MEDIA_TYPE",
            "GRP_CALL_REPORT",
            "GRP_QUEUE",
            "GRP_SHOW_ID",
            "GRP_SHOW_PHOTO",
            "GRP_SHOW_TEXT",
        ]:
            assert CB[key] in all_cb, f"Toggle {key} missing"

    async def test_all_setting_labels(self):
        from app.utils.i18n import t
        keys = [
            "panels.group.settings.music_video", "panels.group.settings.security_call",
            "panels.group.settings.repeat", "panels.group.settings.download_users",
            "panels.group.settings.call_message", "panels.group.settings.auto_clean",
            "panels.group.settings.queue", "panels.group.settings.auto_ready_call",
            "panels.group.settings.default_media_button_audio",
            "panels.group.settings.default_media_summary_label",
            "panels.group.settings.language_label",
            "panels.group.settings.language_button_fa",
        ]
        for k in keys:
            assert "[missing:" not in t("fa", k), f"Missing: {k}"

    async def test_help_content_keys(self):
        from app.utils.i18n import t
        for k in [
            "help_content.promote_demote",
            "help_content.play_commands",
            "help_content.general_commands",
            "help_content.manager_commands",
            "help_content.call_commands",
        ]:
            assert "[missing:" not in t("fa", k), f"Missing: {k}"

    async def test_group_help_menu_has_manager_and_call(self):
        from app.utils.ui import CB, KeyboardFactory
        kb = KeyboardFactory.group_help_menu("fa")
        cbs = {btn.callback_data for row in kb.inline_keyboard for btn in row if btn.callback_data}
        assert CB["GRP_MANAGER_COMMANDS"] in cbs
        assert CB["GRP_CALL_COMMANDS"] in cbs

    async def test_support_keys(self):
        from app.utils.i18n import t
        for k in ["panels.group.support.creator", "panels.group.support.sudo",
                   "panels.group.support.guide_channel", "panels.group.support.support_group"]:
            assert "[missing:" not in t("fa", k), f"Missing: {k}"

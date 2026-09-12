"""Tests for panel coverage — Dev, Owner, Sudo, Group."""
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
class TestDevPanel:
    async def test_developer_panel_keyboard_has_all_buttons(self):
        """Dev panel has category buttons for sub-menu navigation."""
        from app.utils.ui import CB, KeyboardFactory

        kb = KeyboardFactory.developer_panel("fa")
        all_cb = [btn.callback_data for row in kb.inline_keyboard for btn in row]

        required = [
            CB["DEV_STATUS"],
            CB["DEV_CAT_CREDIT"],
            CB["DEV_CAT_BROADCAST"],
            CB["DEV_CAT_LISTS"],
            CB["DEV_CAT_SETTINGS"],
            CB["DEV_CAT_USERS"],
            CB["DEV_CAT_FORCE_JOIN"],
            CB["DEV_CAT_MODERATION"],
            CB["DEV_CAT_TEXTS"],
            CB["DEV_INSTALL_POLICY"],
        ]
        for cb_val in required:
            assert cb_val in all_cb, f"Missing button for {cb_val}"
        assert CB["DEV_CAT_MONTHLY_INVOICE"] not in all_cb
        assert CB["DEV_CAT_RATES"] not in all_cb

    async def test_dev_cb_constants_exist(self):
        """All dev panel CB constants including new ones exist."""
        from app.utils.ui import CB

        new_keys = [
            "DEV_SET_MUSIC_SELL_RATE",
            "DEV_SET_VIDEO_SELL_RATE",
            "DEV_SEND_TO_SUDO",
            "DEV_LIST_RENEWAL_GROUPS",
            "DEV_LEAVE_GROUP",
            "DEV_CAT_FORCE_JOIN",
            "DEV_CAT_MODERATION",
            "DEV_CAT_MONTHLY_INVOICE",
            "DEV_MONTHLY_INVOICE_CONFIG",
            "DEV_MONTHLY_INVOICE_SET_AMOUNT",
            "DEV_MONTHLY_INVOICE_PREPARE",
            "DEV_MONTHLY_INVOICE_SEND",
            "DEV_MONTHLY_INVOICE_AUTO_TOGGLE",
            "DEV_MONTHLY_INVOICE_LIST",
            "DEV_MONTHLY_INVOICE_DETAIL_PREFIX",
        ]
        for key in new_keys:
            assert key in CB, f"CB missing key {key}"
            assert isinstance(CB[key], str)
            assert len(CB[key]) > 0


@pytest.mark.asyncio
class TestOwnerPanel:
    async def test_owner_panel_keyboard_renders(self):
        """Owner panel keyboard renders without errors."""
        from app.utils.ui import KeyboardFactory

        kb = KeyboardFactory.owner_panel("fa")
        assert kb is not None
        assert len(kb.inline_keyboard) > 0

    async def test_owner_topup_sudo_button_hidden(self):
        """Owner panel no longer exposes sudo wallet top-up."""
        from app.utils.ui import CB, KeyboardFactory

        kb = KeyboardFactory.owner_panel("fa")
        all_cb = [btn.callback_data for row in kb.inline_keyboard for btn in row]
        assert CB["OWN_CAT_BILLING"] not in all_cb
        sub = KeyboardFactory.owner_sub_billing("fa")
        sub_cb = [btn.callback_data for row in sub.inline_keyboard for btn in row]
        assert CB["OWN_TOPUP_SUDO_WALLET"] not in sub_cb


@pytest.mark.asyncio
class TestSudoPanel:
    async def test_sudo_panel_keyboard_renders(self):
        """Sudo panel keyboard renders."""
        from app.utils.ui import KeyboardFactory

        kb = KeyboardFactory.sudo_panel("fa")
        assert kb is not None
        assert len(kb.inline_keyboard) > 0

    async def test_sudo_root_has_current_private_sections(self):
        """Sudo panel exposes the current five PM-only sections."""
        from app.utils.ui import CB, KeyboardFactory

        kb = KeyboardFactory.sudo_panel("fa")
        all_cb = [btn.callback_data for row in kb.inline_keyboard for btn in row]
        assert {
            CB["SUDO_STATUS"],
            CB["SUDO_GROUPS"],
            CB["SUDO_CREDIT"],
            CB["SUDO_PERMISSIONS"],
            CB["SUDO_LISTS"],
        }.issubset(all_cb)
        assert CB["SUDO_LOW_CREDIT"] not in all_cb
        assert CB["SUDO_LEAVE_INSTALLS"] not in all_cb

    async def test_sudo_panel_has_no_removed_invite_or_topup_buttons(self):
        """Sudo panel does not expose removed invite/referral/top-up UI."""
        from app.utils.ui import CB, KeyboardFactory

        kb = KeyboardFactory.sudo_panel("fa")
        all_cb = [btn.callback_data for row in kb.inline_keyboard for btn in row]
        labels = [btn.text for row in kb.inline_keyboard for btn in row]
        joined = "\n".join(labels)

        assert CB["DEV_SUDO_LINK_MENU"] not in all_cb
        assert CB["OWN_TOPUP_SUDO_WALLET"] not in all_cb
        assert "لینک دعوت" not in joined
        assert "زیرمجموعه" not in joined
        assert "شارژ کیف" not in joined


@pytest.mark.asyncio
class TestGroupPanel:
    async def test_group_panel_keyboard_renders(self):
        """Group panel keyboard renders."""
        from app.utils.ui import KeyboardFactory

        kb = KeyboardFactory.group_panel("fa")
        assert kb is not None
        assert len(kb.inline_keyboard) > 0

    async def test_group_settings_has_language_toggle(self):
        """Group settings keyboard includes per-chat language toggle."""
        from app.utils.ui import CB, KeyboardFactory

        sd = {
            "music_video": True,
            "security_call": False,
            "repeat": False,
            "download_users": False,
            "call_message": True,
            "auto_clean": False,
            "queue": True,
            "auto_ready_call": False,
            "call_report": False,
            "record_call": False,
            "show_id": True,
            "show_photo": True,
            "show_text": True,
            "default_media_type": "audio",
            "language": "fa",
        }
        kb = KeyboardFactory.group_settings("fa", sd)
        all_cb = [btn.callback_data for row in kb.inline_keyboard for btn in row]
        assert CB["GRP_LANGUAGE"] in all_cb

    async def test_help_content_keys_exist(self):
        """Help content i18n keys exist and are not [missing:]."""
        from app.utils.i18n import t

        for lang in ("fa", "en"):
            for key in (
                "help_content.promote_demote",
                "help_content.play_commands",
                "help_content.general_commands",
                "help_content.manager_commands",
                "help_content.call_commands",
            ):
                val = t(lang, key)
                assert "[missing:" not in val, f"Missing i18n key {key} for {lang}"
                assert len(val) > 10, f"Help content too short for {key} ({lang})"

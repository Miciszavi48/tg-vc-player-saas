"""Tests for inline keyboard row layouts after UI cleanup."""
from __future__ import annotations

import os

os.environ.setdefault("TEST_MODE", "1")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./.pytest-test-mode.db")
os.environ.setdefault("BOT_TOKEN", "test")
os.environ.setdefault("API_ID", "12345")
os.environ.setdefault("API_HASH", "testhash")
os.environ.setdefault("DEVELOPER_ID", "999888777")

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from pyrogram.enums import ButtonStyle
from pyrogram.types import InlineKeyboardMarkup

from app.services.call_security_service import build_panel_keyboard
from app.utils.button_style import apply_button_style_policy
from app.utils.ui import CB, KeyboardFactory


def _row_widths(kb: InlineKeyboardMarkup) -> list[int]:
    return [len(row) for row in kb.inline_keyboard]


def _callbacks(kb: InlineKeyboardMarkup) -> list[str]:
    return [btn.callback_data for row in kb.inline_keyboard for btn in row]


class TestGroupPanelLayouts:
    @pytest.mark.parametrize("lang", ["fa", "en"])
    def test_group_panel_paired_nav(self, lang: str) -> None:
        kb = KeyboardFactory.group_panel(lang)
        assert _row_widths(kb) == [2, 2, 1]
        assert CB["GRP_SETTINGS"] in _callbacks(kb)
        assert CB["GRP_MANAGEMENT"] in _callbacks(kb)
        assert CB["NAV_CLOSE"] in _callbacks(kb)

    @pytest.mark.parametrize("lang", ["fa", "en"])
    def test_group_settings_uses_multi_column_rows(self, lang: str) -> None:
        settings = {
            "music_video": True,
            "security_call": False,
            "download_users": True,
            "call_message": False,
            "auto_clean": True,
            "auto_ready_call": False,
            "call_report": True,
            "queue": False,
            "show_id": True,
            "show_photo": False,
            "show_text": True,
            "language": "fa",
            "default_media_type": "audio",
        }
        kb = KeyboardFactory.group_settings(lang, settings)
        widths = _row_widths(kb)
        assert max(widths) >= 2, f"expected paired rows, got widths={widths}"
        assert widths[-1] == 2, "back/home should share the last row"
        assert CB["NAV_BACK"] in _callbacks(kb)
        assert CB["WZ_HOME"] in _callbacks(kb)

    @pytest.mark.parametrize("lang", ["fa", "en"])
    def test_group_management_paired(self, lang: str) -> None:
        kb = KeyboardFactory.group_management_menu(lang)
        assert _row_widths(kb) == [2, 2, 1, 1]

    @pytest.mark.parametrize("lang", ["fa", "en"])
    def test_group_support_hides_developer_contact(self, lang: str) -> None:
        callbacks = _callbacks(KeyboardFactory.group_support_menu(lang))

        assert callbacks == [
            CB["GRP_SUDO"],
            CB["GRP_GUIDE_CHANNEL"],
            CB["GRP_SUPPORT_GROUP"],
            # Bot channel / messenger entries, both routed in group_panel.py.
            CB["GRP_BOT_CHANNEL"],
            CB["GRP_MESSENGER"],
            CB["NAV_BACK"],
        ]
        assert CB["GRP_CREATOR"] not in callbacks


class TestAdminPanelLayouts:
    @pytest.mark.parametrize(
        ("role", "expected_key"),
        [
            ("developer", "developer_panel"),
            ("owner", "management_panel"),
            ("sudo", "management_panel"),
        ],
    )
    @pytest.mark.asyncio
    async def test_authorized_start_menu_has_one_native_primary_management_entry(
        self,
        role: str,
        expected_key: str,
    ) -> None:
        from app.utils.i18n import t

        kb = KeyboardFactory.with_management_entry("fa", role, None)
        assert _callbacks(kb) == [CB["WZ_HOME"]]
        button = kb.inline_keyboard[0][0]
        assert button.text == t("fa", f"start.menu.{expected_key}")
        await apply_button_style_policy(kb, style_mode="advanced")
        assert button.style == ButtonStyle.PRIMARY

    @pytest.mark.asyncio
    async def test_confirmation_keyboards_keep_cancel_back_without_extra_home(self) -> None:
        from app.handlers.helper_otp_wizard import _nav_kb
        from app.handlers.helper_panel import _proxy_prompt_kb
        from app.handlers.owner_panel import _owner_confirm_kb

        keyboards = [
            _nav_kb(back_cb=CB["HLP_ADD"]),
            _proxy_prompt_kb(7),
            _owner_confirm_kb("yes", "no", CB["OWN_SUDOS"]),
        ]
        for kb in keyboards:
            callbacks = _callbacks(kb)
            assert CB["WZ_HOME"] not in callbacks
            assert max(_row_widths(kb)) <= 2

        owner_buttons = [
            button
            for row in keyboards[-1].inline_keyboard
            for button in row
        ]
        await apply_button_style_policy(keyboards[-1], style_mode="advanced")
        assert owner_buttons[0].style == ButtonStyle.SUCCESS
        assert owner_buttons[1].style == ButtonStyle.DANGER

    @pytest.mark.parametrize("lang", ["fa", "en"])
    def test_developer_panel_paired_categories(self, lang: str) -> None:
        kb = KeyboardFactory.developer_panel(lang)
        widths = _row_widths(kb)
        assert widths[0] == 1
        assert max(widths) <= 2
        assert widths.count(2) >= 2
        assert CB["DEV_CAT_CREDIT"] in _callbacks(kb)
        assert CB["DEV_CAT_MONTHLY_INVOICE"] not in _callbacks(kb)
        assert CB["DEV_CAT_RATES"] not in _callbacks(kb)
        assert CB["DEV_CAT_FORCE_JOIN"] in _callbacks(kb)
        assert CB["DEV_CAT_MODERATION"] in _callbacks(kb)
        assert CB["HLP_HOME"] in _callbacks(kb)
        assert CB["DEV_ABOUT"] in _callbacks(kb)
        assert CB["HELP_HOME"] not in _callbacks(kb)
        assert CB["NAV_START"] in _callbacks(kb)
        assert CB["NAV_CLOSE"] not in _callbacks(kb)

    @pytest.mark.parametrize("lang", ["fa", "en"])
    def test_dev_monthly_invoice_section_is_disabled(self, lang: str) -> None:
        kb = KeyboardFactory.dev_sub_monthly_invoice(lang)
        callbacks = _callbacks(kb)

        assert callbacks == [CB["NAV_BACK"]]

    @pytest.mark.parametrize("lang", ["fa", "en"])
    def test_owner_panel_paired_categories(self, lang: str) -> None:
        kb = KeyboardFactory.owner_panel(lang)
        assert _row_widths(kb) == [1, 2, 2, 2, 2, 1, 2, 2, 1]
        callbacks = _callbacks(kb)
        assert callbacks == [
            CB["OWN_STATS"],
            CB["OWN_GROUPS"],
            CB["OWN_CREDIT"],
            CB["OWN_SUDOS"],
            CB["OWN_SUDO_TITLES"],
            CB["OWN_START_TEXT"],
            CB["OWN_FORCE_JOIN_TOGGLE"],
            CB["OWN_BROADCAST"],
            CB["OWN_LISTS"],
            CB["OWN_MODERATION"],
            CB["OWN_MEDIA"],
            CB["OWN_REPORTS"],
            CB["OWN_YOUTUBE_SESSIONS"],
            CB["OWN_FAST_CREAT_TOKENS"],
            CB["NAV_START"],
        ]
        assert not any(cb.startswith("dev:") for cb in callbacks)
        assert CB["OWN_CAT_INSTALLS"] not in callbacks
        assert CB["OWN_CAT_USERS"] not in callbacks
        assert CB["OWN_CAT_REPORTS"] not in callbacks
        assert CB["OWN_CAT_TEXTS"] not in callbacks

    @pytest.mark.parametrize("lang", ["fa", "en"])
    def test_owner_media_submenu_uses_runtime_toggles_only(self, lang: str) -> None:
        kb = KeyboardFactory.owner_sub_media(
            lang,
            {"audio": True, "video": False, "file": True, "download": False, "buttons": True},
        )
        callbacks = _callbacks(kb)

        assert callbacks == [
            CB["OWN_MEDIA_AUDIO_TOGGLE"],
            CB["OWN_MEDIA_VIDEO_TOGGLE"],
            CB["OWN_MEDIA_FILE_TOGGLE"],
            CB["OWN_MEDIA_DOWNLOAD_TOGGLE"],
            CB["OWN_MEDIA_BUTTONS_TOGGLE"],
            CB["NAV_BACK"],
        ]
        assert CB["OWN_SET_MEDIA_POLICY"] not in callbacks
        assert not any(cb.startswith("dev:") for cb in callbacks)

    def test_owner_banall_keyboard_uses_owner_confirmation_family(self) -> None:
        from app.handlers.owner_panel import _owner_banall_home_kb, _owner_confirm_kb

        home_callbacks = _callbacks(_owner_banall_home_kb())
        assert home_callbacks == [
            CB["OWN_BANALL_ADD"],
            CB["OWN_BANALL_REMOVE"],
            f"{CB['OWN_BANALL_LIST_PREFIX']}0",
            CB["NAV_BACK"],
            CB["WZ_HOME"],
        ]

        confirm_callbacks = _callbacks(
            _owner_confirm_kb(
                f"{CB['OWN_BANALL_ADD_DO_PREFIX']}123:9:456:789",
                f"{CB['OWN_BANALL_ADD_NO_PREFIX']}123:9:456:789",
            )
        )
        assert confirm_callbacks == [
            f"{CB['OWN_BANALL_ADD_DO_PREFIX']}123:9:456:789",
            f"{CB['OWN_BANALL_ADD_NO_PREFIX']}123:9:456:789",
            CB["WZ_HOME"],
        ]
        assert not any(cb.startswith("dev:") for cb in confirm_callbacks)

    def test_owner_sudo_titles_separate_storage_from_telegram_actions(self) -> None:
        from app.handlers.owner_panel import _owner_sudo_title_rows

        rows = _owner_sudo_title_rows([SimpleNamespace(user_id=222)], 0, 1)
        callbacks = [btn.callback_data for row in rows for btn in row]

        assert f"{CB['OWN_TITLE_SUDO_SET_PREFIX']}222:0" in callbacks
        assert f"{CB['OWN_TITLE_SUDO_CLEAR_PREFIX']}222:0" in callbacks
        assert f"{CB['OWN_TITLE_APPLY_SUDO_PREFIX']}222:0" in callbacks
        assert f"{CB['OWN_TGPROM_SUDO_PREFIX']}222:0" in callbacks
        assert not any(cb.startswith("dev:") for cb in callbacks)

    @pytest.mark.parametrize("lang", ["fa", "en"])
    def test_sudo_panel_private_root_layout(self, lang: str) -> None:
        kb = KeyboardFactory.sudo_panel(lang, show_leave_installs=True)
        assert _row_widths(kb) == [1, 2, 1, 1, 1]
        callbacks = _callbacks(kb)
        assert {
            CB["SUDO_STATUS"],
            CB["SUDO_GROUPS"],
            CB["SUDO_CREDIT"],
            CB["SUDO_PERMISSIONS"],
            CB["SUDO_LISTS"],
            CB["NAV_START"],
        }.issubset(callbacks)
        assert CB["NAV_CLOSE"] not in callbacks
        assert CB["SUDO_INSTALLS_REPORT"] not in callbacks
        assert CB["SUDO_MY_STATS"] not in callbacks
        assert not any(cb.startswith(("dev:", "own:")) for cb in callbacks)

    @pytest.mark.parametrize("lang", ["fa", "en"])
    def test_dev_settings_toggle_suffix_icons(self, lang: str) -> None:
        from app.utils.i18n import t

        active = t(lang, "status_indicator.active")
        inactive = t(lang, "status_indicator.inactive")
        assert active == "✅"
        assert inactive == "☑️"
        kb = KeyboardFactory.dev_sub_settings(
            lang,
            bot_enabled=True,
            sudo_panel_enabled=False,
            force_join_enabled=True,
            auto_leave_enabled=False,
            trial_enabled=True,
        )
        toggle_rows = [
            btn.text
            for row in kb.inline_keyboard
            for btn in row
            if active in btn.text or inactive in btn.text
        ]
        assert len(toggle_rows) == 4
        assert CB["DEV_BOT_ENABLED_TOGGLE"] in _callbacks(kb)
        assert CB["DEV_FORCE_JOIN_TOGGLE"] not in _callbacks(kb)
        assert _callbacks(kb).count(CB["DEV_MEDIA_HEALTH"]) == 1

    @pytest.mark.parametrize("lang", ["fa", "en"])
    def test_dev_force_join_and_moderation_are_separate_sections(self, lang: str) -> None:
        force_join = KeyboardFactory.dev_sub_force_join(lang, force_join_enabled=True)
        moderation = KeyboardFactory.dev_sub_moderation(lang)
        users = KeyboardFactory.dev_sub_users(lang)

        assert CB["DEV_FORCE_JOIN_TOGGLE"] in _callbacks(force_join)
        assert CB["DEV_FORCE_JOIN_MANAGE"] in _callbacks(force_join)
        assert CB["DEV_FORCE_JOIN_TOGGLE"] not in _callbacks(users)
        assert CB["DEV_FORCE_JOIN_MANAGE"] not in _callbacks(users)
        assert CB["DEV_FILTERS"] in _callbacks(moderation)
        assert CB["DEV_BLACKLIST"] in _callbacks(moderation)
        assert CB["DEV_BANALL_HOME"] in _callbacks(moderation)
        assert CB["DEV_FILTERS"] not in _callbacks(users)
        assert CB["DEV_BLACKLIST"] not in _callbacks(users)
        assert CB["DEV_BANALL_HOME"] not in _callbacks(users)

    @pytest.mark.parametrize("lang", ["fa", "en"])
    def test_call_security_enabled_mute_same_row(self, lang: str) -> None:
        settings = MagicMock()
        settings.enabled = True
        settings.owner_access_enabled = False
        settings.mute_incoming_enabled = False
        settings.summary_enabled = True
        settings.report_enabled = False
        settings.membership_age_days = 3
        kb = build_panel_keyboard(lang, settings, show_owner_access=False)
        enabled_row = next(
            row
            for row in kb.inline_keyboard
            if any(btn.callback_data == CB["GRP_CALLSEC_TOGGLE"] for btn in row)
        )
        assert len(enabled_row) == 2
        assert any(btn.callback_data == CB["GRP_CALLSEC_MUTE_IN"] for btn in enabled_row)

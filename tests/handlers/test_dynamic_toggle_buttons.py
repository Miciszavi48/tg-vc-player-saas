"""Tests for Task 4: dynamic toggle button labels with emoji."""
from __future__ import annotations

import os

os.environ.setdefault("TEST_MODE", "1")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./.pytest-test-mode.db")
os.environ.setdefault("BOT_TOKEN", "test")
os.environ.setdefault("API_ID", "12345")
os.environ.setdefault("API_HASH", "testhash")
os.environ.setdefault("DEVELOPER_ID", "999888777")

import pytest

_SETTINGS_STATE = {
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

# State wording that must never appear inside a visible emoji-toggle button label.
_FORBIDDEN_IN_TOGGLE_LABELS = (
    "فعال/غیرفعال",
    "[فعال]",
    "[غیرفعال]",
    "On/Off",
    "on/off",
    "enabled/disabled",
    "active/inactive",
    "[enabled]",
    "[disabled]",
)


class TestDynamicToggleButtons:
    """Verify toggle_label produces correct emoji-appended labels."""

    def test_toggle_label_enabled_has_active_icon(self):
        from app.utils.i18n import t
        from app.utils.ui import toggle_label

        result = toggle_label("fa", "پخش ویدیو", True)
        assert result.endswith(t("fa", "status_indicator.active")), f"Unexpected: {result!r}"
        assert "پخش ویدیو" in result

    def test_toggle_label_disabled_has_inactive_icon(self):
        from app.utils.i18n import t
        from app.utils.ui import toggle_label

        result = toggle_label("fa", "پخش ویدیو", False)
        assert result.endswith(t("fa", "status_indicator.inactive")), f"Unexpected: {result!r}"
        assert "پخش ویدیو" in result

    def test_toggle_label_en_enabled(self):
        from app.utils.i18n import t
        from app.utils.ui import toggle_label

        result = toggle_label("en", "Video Playback", True)
        assert result.endswith(t("en", "status_indicator.active"))
        assert "Video Playback" in result

    def test_toggle_label_en_disabled(self):
        from app.utils.i18n import t
        from app.utils.ui import toggle_label

        result = toggle_label("en", "Video Playback", False)
        assert result.endswith(t("en", "status_indicator.inactive"))
        assert "Video Playback" in result

    def test_toggle_label_no_old_style_brackets(self):
        from app.utils.ui import toggle_label

        result_on = toggle_label("fa", "تست", True)
        result_off = toggle_label("fa", "تست", False)
        assert "[" not in result_on, "Old bracket-style should not appear in enabled label"
        assert "[" not in result_off, "Old bracket-style should not appear in disabled label"
        assert "فعال" not in result_on, "Raw 'فعال' should not be in toggle button label"
        assert "غیرفعال" not in result_off, "Raw 'غیرفعال' should not be in toggle button label"

    def test_toggle_label_exact_output(self):
        from app.utils.i18n import t
        from app.utils.ui import toggle_label

        active = t("fa", "status_indicator.active")
        inactive = t("fa", "status_indicator.inactive")
        assert active == "✅"
        assert inactive == "☑️"
        assert toggle_label("fa", "پخش خودکار", True) == f"پخش خودکار {active}"
        assert toggle_label("fa", "پخش خودکار", False) == f"پخش خودکار {inactive}"

    @pytest.mark.parametrize("lang", ["fa", "en"])
    def test_group_settings_keyboard_uses_emoji(self, lang):
        """group_settings keyboard must not contain old-style state wording in buttons."""
        from app.utils.ui import KeyboardFactory

        kb = KeyboardFactory.group_settings(lang, dict(_SETTINGS_STATE))
        for row in kb.inline_keyboard:
            for btn in row:
                text = btn.text
                for forbidden in _FORBIDDEN_IN_TOGGLE_LABELS:
                    assert forbidden not in text, (
                        f"[{lang}] old state wording {forbidden!r} found in: {text!r}"
                    )

    @pytest.mark.parametrize("lang", ["fa", "en"])
    def test_group_settings_toggle_buttons_have_emoji(self, lang):
        """Every visible boolean toggle in group_settings must carry status_indicator emoji."""
        from app.utils.i18n import t
        from app.utils.ui import KeyboardFactory, CB, _GRP_VISIBLE_SETTING_TOGGLES

        active = t(lang, "status_indicator.active")
        inactive = t(lang, "status_indicator.inactive")
        non_toggle_cbs = {
            CB["GRP_LANGUAGE"],
            CB["GRP_DEFAULT_MEDIA_TYPE"],
            CB["GRP_CALLSEC"],
            CB["NAV_BACK"],
            CB["WZ_HOME"],
        }
        toggle_cbs = set(_GRP_VISIBLE_SETTING_TOGGLES.values()) - non_toggle_cbs

        kb = KeyboardFactory.group_settings(lang, dict(_SETTINGS_STATE))
        seen_toggles = 0
        for row in kb.inline_keyboard:
            for btn in row:
                if btn.callback_data not in toggle_cbs:
                    continue
                seen_toggles += 1
                assert active in btn.text or inactive in btn.text, (
                    f"[{lang}] toggle button missing state emoji: {btn.text!r}"
                )

        assert seen_toggles > 0, "No boolean toggle buttons found in group_settings keyboard"


class TestCallSecurityToggleButtons:
    """Call Security keyboard must use shared status_indicator suffix style."""

    @staticmethod
    def _build_keyboard(lang: str):
        from unittest.mock import MagicMock
        from app.services.call_security_service import build_panel_keyboard

        settings = MagicMock()
        settings.enabled = True
        settings.owner_access_enabled = False
        settings.mute_incoming_enabled = True
        settings.summary_enabled = False
        settings.report_enabled = True
        settings.membership_age_days = 7
        return build_panel_keyboard(lang, settings, show_owner_access=True)

    @pytest.mark.parametrize("lang", ["fa", "en"])
    def test_call_security_toggles_use_status_indicator(self, lang):
        from app.utils.i18n import t

        active = t(lang, "status_indicator.active")
        inactive = t(lang, "status_indicator.inactive")
        kb = self._build_keyboard(lang)
        toggle_texts = [
            btn.text
            for row in kb.inline_keyboard
            for btn in row
            if active in btn.text or inactive in btn.text
        ]
        assert len(toggle_texts) == 5, (
            f"[{lang}] expected 5 emoji toggle buttons, got: {toggle_texts!r}"
        )

    @pytest.mark.parametrize("lang", ["fa", "en"])
    def test_call_security_emoji_is_suffix(self, lang):
        from app.utils.i18n import t

        active = t(lang, "status_indicator.active")
        inactive = t(lang, "status_indicator.inactive")
        kb = self._build_keyboard(lang)
        for row in kb.inline_keyboard:
            for btn in row:
                if active in btn.text or inactive in btn.text:
                    assert btn.text.endswith(active) or btn.text.endswith(inactive), (
                        f"[{lang}] state emoji must be a suffix: {btn.text!r}"
                    )

    def test_state_icon_helper_removed(self):
        import app.services.call_security_service as svc
        assert not hasattr(svc, "_state_icon"), (
            "_state_icon should be removed — replaced by shared toggle_label"
        )

    def test_non_button_status_messages_can_use_words(self):
        """Status text messages (not buttons) may still contain enabled/disabled words."""
        from app.utils.i18n import TextService
        ts = TextService()
        on_label = ts.t("fa", "common.labels.on")
        off_label = ts.t("fa", "common.labels.off")
        assert len(on_label) > 0
        assert len(off_label) > 0

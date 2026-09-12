"""Final cleanup: Channel Security (امنیت کانال) removed; Call Security (امنیت کال) only."""
from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

os.environ.setdefault("TEST_MODE", "1")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./.pytest-test-mode.db")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/15")
os.environ.setdefault("BOT_TOKEN", "test")
os.environ.setdefault("API_ID", "12345")
os.environ.setdefault("API_HASH", "testhash")
os.environ.setdefault("DEVELOPER_ID", "123456789")

from app.config.settings import settings

ROOT = Path(__file__).resolve().parents[2]
FA_JSON = ROOT / "app" / "resources" / "strings" / "fa.json"
EN_JSON = ROOT / "app" / "resources" / "strings" / "en.json"
APP_DIR = ROOT / "app"

FORBIDDEN_FA = "امنیت کانال"
FORBIDDEN_EN = "Channel Security"
LEGACY_CALLBACK_ALLOWLIST = {
    "dev:channel_security",
    "own:channel_security",
    "DEV_CHANNEL_SECURITY_TOGGLE",
    "OWN_CHANNEL_SECURITY_TOGGLE",
}


class _RecorderBot:
    def __init__(self) -> None:
        self.callback_handlers: list = []

    def on_callback_query(self, *_args, **_kwargs):
        def _decorator(fn):
            self.callback_handlers.append(fn)
            return fn

        return _decorator


def _handler_by_name(handlers: list, name: str):
    return next(fn for fn in handlers if fn.__name__ == name)


def _callbacks(kb) -> set[str]:
    return {btn.callback_data for row in kb.inline_keyboard for btn in row if btn.callback_data}


def _keyboard_labels(kb) -> list[str]:
    return [btn.text for row in kb.inline_keyboard for btn in row if btn.text]


def _all_string_values(data: dict) -> list[str]:
    out: list[str] = []
    for value in data.values():
        if isinstance(value, dict):
            out.extend(_all_string_values(value))
        elif isinstance(value, str):
            out.append(value)
    return out


def test_no_active_fa_ui_string_for_channel_security():
    from tests.i18n_test_utils import load_fa_i18n
    fa = load_fa_i18n()
    offenders = [v for v in _all_string_values(fa) if FORBIDDEN_FA in v]
    assert offenders == [], f"FA strings still contain {FORBIDDEN_FA!r}: {offenders}"


def test_no_active_en_ui_string_for_channel_security():
    from tests.i18n_test_utils import load_en_i18n
    en = load_en_i18n()
    offenders = [v for v in _all_string_values(en) if FORBIDDEN_EN in v]
    assert offenders == [], f"EN strings still contain {FORBIDDEN_EN!r}: {offenders}"


def test_fa_en_key_parity_after_channel_security_removal():
    from tests.i18n_test_utils import load_en_i18n, load_fa_i18n
    fa = load_fa_i18n()
    en = load_en_i18n()

    def _leaf_keys(data: dict, prefix: str = "") -> set[str]:
        keys: set[str] = set()
        for key, value in data.items():
            full = f"{prefix}.{key}" if prefix else key
            if isinstance(value, dict):
                keys |= _leaf_keys(value, full)
            else:
                keys.add(full)
        return keys

    assert _leaf_keys(fa) == _leaf_keys(en)


def test_developer_panel_hides_channel_security_toggle():
    from app.utils.ui import CB, KeyboardFactory

    kb = KeyboardFactory.dev_sub_settings("fa")
    assert CB["DEV_CHANNEL_SECURITY_TOGGLE"] not in _callbacks(kb)
    labels = " ".join(_keyboard_labels(kb))
    assert FORBIDDEN_FA not in labels


def test_owner_panel_hides_channel_security_toggle():
    from app.utils.ui import CB, KeyboardFactory

    kb = KeyboardFactory.owner_sub_settings("fa")
    assert CB["OWN_CHANNEL_SECURITY_TOGGLE"] not in _callbacks(kb)
    labels = " ".join(_keyboard_labels(kb))
    assert FORBIDDEN_FA not in labels


@pytest.mark.asyncio
async def test_status_summary_excludes_channel_security():
    from app.services.panel_router import _build_status_summary

    with (
        patch(
            "app.services.panel_router.settings_repo.get_bot_setting_bool",
            AsyncMock(return_value=False),
        ),
        patch(
            "app.services.panel_router.settings_repo.get_bot_setting",
            AsyncMock(return_value="0"),
        ),
    ):
        summary = await _build_status_summary("fa")
    assert "channel_security" not in summary.lower()
    assert FORBIDDEN_FA not in summary


@pytest.mark.asyncio
async def test_dev_channel_security_callback_does_not_write_setting():
    from app.handlers import dev_panel
    from app.utils.i18n import t

    bot = _RecorderBot()
    dev_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "dev_channel_security_toggle")
    query = SimpleNamespace(
        data="dev:channel_security",
        from_user=SimpleNamespace(id=settings.DEVELOPER_ID),
        message=SimpleNamespace(
            chat=SimpleNamespace(id=settings.DEVELOPER_ID, type=SimpleNamespace(value="private")),
            edit_text=AsyncMock(),
        ),
        answer=AsyncMock(),
    )
    with (
        patch("app.handlers.dev_panel._dev_settings_keyboard", AsyncMock(return_value=None)),
        patch("app.handlers.dev_panel.settings_repo.set_bot_setting", AsyncMock()) as set_mock,
    ):
        await handler.__wrapped__(SimpleNamespace(), query)

    set_mock.assert_not_called()
    edit_text = query.message.edit_text.await_args.args[0]
    assert t("fa", "call_security.deprecated_global_toggle") in edit_text
    assert FORBIDDEN_FA not in edit_text


@pytest.mark.asyncio
async def test_own_channel_security_callback_does_not_write_setting():
    from app.handlers import owner_panel
    from app.utils.i18n import t

    bot = _RecorderBot()
    owner_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "own_channel_security_toggle")
    query = SimpleNamespace(
        data="own:channel_security",
        from_user=SimpleNamespace(id=settings.DEVELOPER_ID),
        message=SimpleNamespace(
            chat=SimpleNamespace(id=settings.DEVELOPER_ID, type=SimpleNamespace(value="private")),
            edit_text=AsyncMock(),
        ),
        answer=AsyncMock(),
    )
    with patch("app.handlers.owner_panel.settings_repo.set_bot_setting", AsyncMock()) as set_mock:
        await handler.__wrapped__(SimpleNamespace(), query)

    set_mock.assert_not_called()
    query.answer.assert_awaited()
    edit_text = query.message.edit_text.await_args.args[0]
    assert t("fa", "call_security.deprecated_global_toggle") in edit_text
    assert FORBIDDEN_FA not in edit_text


def test_channel_security_enabled_not_read_by_runtime_enforcement():
    runtime_paths = [
        APP_DIR / "utils" / "playback_auth.py",
        APP_DIR / "handlers" / "playback.py",
        APP_DIR / "handlers" / "call_security_runtime.py",
        APP_DIR / "services" / "call_security_service.py",
        APP_DIR / "services" / "admin_dashboard_service.py",
        APP_DIR / "services" / "panel_router.py",
        APP_DIR / "services" / "wizard_ui.py",
    ]
    for path in runtime_paths:
        text = path.read_text(encoding="utf-8")
        assert "channel_security_enabled" not in text, f"{path} still references channel_security_enabled"


def test_call_security_panel_shows_call_security_label():
    from app.utils.i18n import t

    assert "امنیت" in t("fa", "call_security.feature_enabled")
    assert t("en", "call_security.feature_enabled") == "Call Security"


def test_call_security_panel_shows_membership_age_not_account_age():
    from app.utils.i18n import t

    assert "قدمت عضویت" in t("fa", "call_security.feature_membership_age")
    assert "قدمت اکانت" not in t("fa", "call_security.btn_membership_age", days=7)
    assert t("en", "call_security.feature_membership_age") in ("Membership Age", "Group membership age")


def test_playback_lock_label_unchanged():
    from app.utils.i18n import t
    from app.utils.ui import CB

    assert "قفل پخش" in t("fa", "panels.group.settings.security_call")
    assert CB["GRP_SECURITY_CALL"] == "grp:set:security_call"


def test_grp_callsec_callback_present():
    from app.utils.ui import CB, KeyboardFactory

    assert CB["GRP_CALLSEC"] == "grp:callsec"
    sd = {
        "music_video": True,
        "security_call": False,
        "repeat": False,
        "download_users": True,
        "call_message": True,
        "auto_clean": False,
        "queue": False,
        "auto_ready_call": False,
        "call_report": True,
        "record_call": True,
        "show_id": True,
        "show_photo": True,
        "show_text": True,
        "default_media_type": "audio",
        "language": "fa",
    }
    kb = KeyboardFactory.group_settings("fa", sd)
    cbs = _callbacks(kb)
    assert CB["GRP_CALLSEC"] in cbs
    labels = " ".join(_keyboard_labels(kb))
    assert "امنیت" in labels
    assert FORBIDDEN_FA not in labels


def test_admin_dashboard_general_summary_excludes_channel_security():
    import inspect

    from app.services.admin_dashboard_service import AdminDashboardService

    source = inspect.getsource(AdminDashboardService.get_general_summary)
    assert "channel_security" not in source

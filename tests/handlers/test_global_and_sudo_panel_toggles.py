"""Tests for global bot on/off and sudo panel on/off developer toggles."""
from __future__ import annotations

import os
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./.pytest-test-mode.db")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("BOT_TOKEN", "test")
os.environ.setdefault("API_ID", "12345")
os.environ.setdefault("API_HASH", "testhash")
os.environ.setdefault("DEVELOPER_ID", "123456789")

if "pyromod" not in sys.modules:
    pyromod_module = ModuleType("pyromod")
    pyromod_exceptions = ModuleType("pyromod.exceptions")
    pyromod_exceptions.ListenerStopped = Exception
    pyromod_module.exceptions = pyromod_exceptions
    sys.modules["pyromod"] = pyromod_module
    sys.modules["pyromod.exceptions"] = pyromod_exceptions

from app.config.settings import settings
from app.handlers import dev_panel
from app.services.panel_router import build_private_root_payload
from app.utils.i18n import t
from app.utils.playback_auth import authorize_playback_action
from app.utils.ui import CB, KeyboardFactory


class _RecorderBot:
    def __init__(self) -> None:
        self.callback_handlers: list = []

    def on_callback_query(self, *args, **kwargs):  # noqa: ANN001, ANN002
        def _decorator(fn):
            self.callback_handlers.append(fn)
            return fn

        return _decorator

    def __getattr__(self, name: str):
        if name.startswith("on_"):

            def _register_other(*args, **kwargs):  # noqa: ANN001, ANN002
                def _decorator(fn):
                    return fn

                return _decorator

            return _register_other
        raise AttributeError(name)


def _handler_by_name(handlers: list, name: str):
    for fn in handlers:
        if fn.__name__ == name:
            return fn
    raise AssertionError(f"handler not found: {name}")


def _kb_callbacks(kb) -> set[str]:
    return {
        btn.callback_data
        for row in kb.inline_keyboard
        for btn in row
        if btn.callback_data
    }


def _pm_query(user_id: int, data: str):
    return SimpleNamespace(
        from_user=SimpleNamespace(id=user_id, first_name="Tester"),
        data=data,
        answer=AsyncMock(),
        message=SimpleNamespace(
            chat=SimpleNamespace(id=100, type=SimpleNamespace(value="private")),
            edit_text=AsyncMock(),
        ),
    )


def _general_summary(
    *,
    bot_enabled: bool = True,
    sudo_panel_enabled: bool = True,
) -> dict:
    return {
        "force_join_enabled": False,
        "auto_leave_enabled": True,
        "trial_enabled": True,
        "bot_enabled": bot_enabled,
        "sudo_panel_enabled": sudo_panel_enabled,
        "required_channels": 0,
    }


@pytest.mark.asyncio
async def test_dev_settings_submenu_shows_bot_toggle():
    kb = KeyboardFactory.dev_sub_settings("en", bot_enabled=True, sudo_panel_enabled=True)
    assert CB["DEV_BOT_ENABLED_TOGGLE"] in _kb_callbacks(kb)


@pytest.mark.asyncio
async def test_dev_settings_submenu_shows_sudo_panel_toggle():
    kb = KeyboardFactory.dev_sub_settings("en", bot_enabled=False, sudo_panel_enabled=False)
    assert CB["DEV_SUDO_PANEL_ENABLED_TOGGLE"] in _kb_callbacks(kb)


@pytest.mark.asyncio
async def test_developer_can_toggle_bot_enabled():
    bot = _RecorderBot()
    dev_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "dev_bot_enabled_toggle")
    query = _pm_query(settings.DEVELOPER_ID, CB["DEV_BOT_ENABLED_TOGGLE"])

    with (
        patch("app.handlers.dev_panel.settings_repo.toggle_bot_setting", AsyncMock(return_value=False)) as toggle,
        patch(
            "app.handlers.dev_panel.AdminDashboardService.get_general_summary",
            AsyncMock(return_value=_general_summary(bot_enabled=False)),
        ),
        patch("app.handlers.dev_panel.AdminDashboardService.invalidate_cache", AsyncMock()),
    ):
        await handler.__wrapped__(SimpleNamespace(), query)

    toggle.assert_awaited_once()
    assert toggle.await_args.kwargs.get("default") is True
    query.message.edit_text.assert_awaited_once()


@pytest.mark.asyncio
async def test_developer_can_toggle_sudo_panel_enabled():
    bot = _RecorderBot()
    dev_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "dev_sudo_panel_enabled_toggle")
    query = _pm_query(settings.DEVELOPER_ID, CB["DEV_SUDO_PANEL_ENABLED_TOGGLE"])

    with (
        patch("app.handlers.dev_panel.settings_repo.toggle_bot_setting", AsyncMock(return_value=False)) as toggle,
        patch(
            "app.handlers.dev_panel.AdminDashboardService.get_general_summary",
            AsyncMock(return_value=_general_summary(sudo_panel_enabled=False)),
        ),
        patch("app.handlers.dev_panel.AdminDashboardService.invalidate_cache", AsyncMock()),
    ):
        await handler.__wrapped__(SimpleNamespace(), query)

    toggle.assert_awaited_once()
    assert toggle.await_args.args[0] == "sudo_panel_enabled"
    query.message.edit_text.assert_awaited_once()


@pytest.mark.asyncio
async def test_non_developer_cannot_trigger_bot_toggle():
    bot = _RecorderBot()
    dev_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "dev_bot_enabled_toggle")
    query = _pm_query(9999, CB["DEV_BOT_ENABLED_TOGGLE"])

    with patch("app.handlers.dev_panel.settings_repo.toggle_bot_setting", AsyncMock()) as toggle:
        result = await handler(SimpleNamespace(), query)

    assert result is None
    toggle.assert_not_awaited()
    query.answer.assert_awaited()


@pytest.mark.asyncio
async def test_developer_panel_when_bot_off():
    with (
        patch("app.services.panel_router.is_bot_enabled", AsyncMock(return_value=False)),
        patch("app.services.panel_router.detect_private_role", AsyncMock(return_value="developer")),
        patch("app.services.panel_router.get_start_welcome_text", AsyncMock(return_value="welcome")),
        patch("app.services.panel_router._build_status_summary", AsyncMock(return_value="summary")),
    ):
        text, kb = await build_private_root_payload(
            None,
            settings.DEVELOPER_ID,
            "Dev",
            lang="en",
            include_welcome=False,
        )

    assert "Developer Panel" in text
    assert kb is not None


@pytest.mark.asyncio
async def test_developer_can_toggle_bot_on_when_bot_off():
    bot = _RecorderBot()
    dev_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "dev_bot_enabled_toggle")
    query = _pm_query(settings.DEVELOPER_ID, CB["DEV_BOT_ENABLED_TOGGLE"])

    with (
        patch("app.handlers.dev_panel.settings_repo.toggle_bot_setting", AsyncMock(return_value=True)) as toggle,
        patch(
            "app.handlers.dev_panel.AdminDashboardService.get_general_summary",
            AsyncMock(return_value=_general_summary(bot_enabled=True)),
        ),
        patch("app.handlers.dev_panel.AdminDashboardService.invalidate_cache", AsyncMock()),
    ):
        await handler.__wrapped__(SimpleNamespace(), query)

    toggle.assert_awaited_once()
    query.message.edit_text.assert_awaited_once()


@pytest.mark.asyncio
async def test_regular_user_start_when_bot_off():
    with (
        patch("app.services.panel_router.is_bot_enabled", AsyncMock(return_value=False)),
        patch("app.services.panel_router.detect_private_role", AsyncMock(return_value="regular")),
        patch("app.services.panel_router.get_start_welcome_text", AsyncMock(return_value="welcome")),
        patch("app.services.panel_router.build_start_links", AsyncMock(return_value={})),
    ):
        text, _kb = await build_private_root_payload(
            None,
            4242,
            "User",
            lang="en",
            include_welcome=True,
        )

    assert t("en", "status.bot_disabled") in text


@pytest.mark.asyncio
async def test_playback_blocked_when_bot_off():
    update = SimpleNamespace(
        from_user=SimpleNamespace(id=4242),
        message=SimpleNamespace(chat=SimpleNamespace(id=-1001)),
        answer=AsyncMock(),
    )
    client = SimpleNamespace(get_chat_member=AsyncMock())

    with patch("app.utils.playback_auth.deny_if_bot_disabled", AsyncMock(return_value=True)):
        allowed = await authorize_playback_action(client, update, lang="en")

    assert allowed is False


@pytest.mark.asyncio
async def test_sudo_user_blocked_when_sudo_panel_off():
    with (
        patch("app.services.panel_router.is_bot_enabled", AsyncMock(return_value=True)),
        patch("app.services.panel_router.is_sudo_panel_enabled", AsyncMock(return_value=False)),
        patch("app.services.panel_router.detect_private_role", AsyncMock(return_value="sudo")),
        patch("app.services.panel_router.get_start_welcome_text", AsyncMock(return_value="welcome")),
        patch("app.services.panel_router.build_start_links", AsyncMock(return_value={})),
    ):
        text, _kb = await build_private_root_payload(
            None,
            5555,
            "Sudo",
            lang="en",
            include_welcome=False,
        )

    assert t("en", "status.sudo_panel_disabled") in text


@pytest.mark.asyncio
async def test_owner_unaffected_when_sudo_panel_off():
    with (
        patch("app.services.panel_router.is_bot_enabled", AsyncMock(return_value=True)),
        patch("app.services.panel_router.is_sudo_panel_enabled", AsyncMock(return_value=False)),
        patch("app.services.panel_router.detect_private_role", AsyncMock(return_value="owner")),
        patch("app.services.panel_router.get_start_welcome_text", AsyncMock(return_value="welcome")),
        patch("app.services.panel_router._build_status_summary", AsyncMock(return_value="summary")),
    ):
        text, kb = await build_private_root_payload(
            None,
            7777,
            "Owner",
            lang="en",
            include_welcome=False,
        )

    assert "Owner" in text or "owner" in text.lower()
    assert t("en", "status.sudo_panel_disabled") not in text
    assert kb is not None


@pytest.mark.asyncio
async def test_sudo_panel_works_when_re_enabled():
    with (
        patch("app.services.panel_router.is_bot_enabled", AsyncMock(return_value=True)),
        patch("app.services.panel_router.is_sudo_panel_enabled", AsyncMock(return_value=True)),
        patch("app.services.panel_router.detect_private_role", AsyncMock(return_value="sudo")),
        patch("app.services.panel_router.get_start_welcome_text", AsyncMock(return_value="welcome")),
    ):
        text, kb = await build_private_root_payload(
            None,
            5555,
            "Sudo",
            lang="en",
            include_welcome=False,
        )

    assert t("en", "status.sudo_panel_disabled") not in text
    assert CB["SUDO_STATUS"] in _kb_callbacks(kb)


@pytest.mark.asyncio
async def test_start_flow_does_not_crash_when_bot_off():
    with (
        patch("app.services.panel_router.is_bot_enabled", AsyncMock(return_value=False)),
        patch("app.services.panel_router.detect_private_role", AsyncMock(return_value="regular")),
        patch("app.services.panel_router.get_start_welcome_text", AsyncMock(return_value="welcome")),
        patch("app.services.panel_router.build_start_links", AsyncMock(return_value={"creator": "https://example.com"})),
    ):
        text, kb = await build_private_root_payload(
            None,
            4242,
            "User",
            lang="fa",
            include_welcome=True,
        )

    assert text
    assert kb.inline_keyboard

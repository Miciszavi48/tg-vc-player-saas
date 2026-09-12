from __future__ import annotations

import os
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

os.environ.setdefault("TEST_MODE", "1")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./.pytest-test-mode.db")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/15")
os.environ.setdefault("BOT_TOKEN", "test")
os.environ.setdefault("API_ID", "12345")
os.environ.setdefault("API_HASH", "testhash")
os.environ.setdefault("DEVELOPER_ID", "123456789")

if "pyromod" not in sys.modules:
    pyromod_module = ModuleType("pyromod")
    pyromod_exceptions = ModuleType("pyromod.exceptions")

    class _ListenerStopped(Exception):
        pass

    pyromod_exceptions.ListenerStopped = _ListenerStopped
    pyromod_module.exceptions = pyromod_exceptions
    sys.modules["pyromod"] = pyromod_module
    sys.modules["pyromod.exceptions"] = pyromod_exceptions

from app.config.settings import settings
from app.handlers import dev_panel, owner_panel
from app.services import start_customization_service as start_custom
from app.services.owner_text_link_service import TextLinkValue
from app.services.texts_links_ui import build_texts_hub_payload
from app.utils.i18n import t
from app.utils.ui import CB

_PURE_OWNER = 900_101
_REGULAR_USER = 900_202
_SUDO_USER = 900_303


class _RecorderBot:
    def __init__(self) -> None:
        self.callback_handlers: list = []
        self.message_handlers: list = []

    def on_callback_query(self, *args, **kwargs):  # noqa: ANN001, ANN002
        def _decorator(fn):
            self.callback_handlers.append(fn)
            return fn

        return _decorator

    def on_message(self, *args, **kwargs):  # noqa: ANN001, ANN002
        def _decorator(fn):
            self.message_handlers.append(fn)
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


def _kb_callbacks(kb) -> set[str]:
    return {
        btn.callback_data
        for row in kb.inline_keyboard
        for btn in row
        if btn.callback_data
    }


def _button_text(kb, callback_data: str) -> str:
    for row in kb.inline_keyboard:
        for button in row:
            if button.callback_data == callback_data:
                return button.text
    raise AssertionError(f"button not found: {callback_data}")


@pytest.mark.asyncio
async def test_dev_text_hub_shows_global_start_style_toggle():
    with (
        patch(
            "app.services.texts_links_ui.start_custom.get_effective_style_mode",
            AsyncMock(return_value=start_custom.EffectiveStyle("simple", "default")),
        ),
        patch("app.services.texts_links_ui.settings_repo.get_bot_setting", AsyncMock(return_value=None)),
    ):
        text, kb = await build_texts_hub_payload("fa", "dev")

    assert t("fa", "texts_links.start_style.title") in text
    assert t("fa", "texts_links.start_style.mode_simple") in text
    assert t("fa", "texts_links.start_style.developer_scope_note") in text
    assert "RBGNNNNR" in text
    assert "NNNNNNNN" in text
    assert CB["DEV_START_STYLE_TOGGLE"] in _kb_callbacks(kb)
    assert "پیش‌فرض سراسری" in _button_text(kb, CB["DEV_START_STYLE_TOGGLE"])


@pytest.mark.asyncio
async def test_owner_text_hub_shows_owner_start_style_toggle():
    with (
        patch(
            "app.services.texts_links_ui.start_custom.get_effective_style_mode",
            AsyncMock(return_value=start_custom.EffectiveStyle("advanced", "owner")),
        ),
        patch(
            "app.services.owner_text_link_service.get_effective_text_link",
            AsyncMock(return_value=TextLinkValue(mode="empty", raw=None, source="empty")),
        ),
        patch("app.services.texts_links_ui.settings_repo.get_bot_setting", AsyncMock(return_value=None)),
    ):
        text, kb = await build_texts_hub_payload("fa", "owner", owner_user_id=_PURE_OWNER)

    assert t("fa", "texts_links.start_style.mode_advanced") in text
    assert t("fa", "texts_links.start_style.scope_owner") in text
    assert t("fa", "texts_links.start_style.owner_scope_note") in text
    assert CB["OWN_START_STYLE_TOGGLE"] in _kb_callbacks(kb)
    assert "حالت اختصاصی من" in _button_text(kb, CB["OWN_START_STYLE_TOGGLE"])


@pytest.mark.asyncio
async def test_dev_start_style_toggle_writes_global_scope():
    bot = _RecorderBot()
    dev_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "dev_start_style_toggle")
    query = _pm_query(settings.DEVELOPER_ID, CB["DEV_START_STYLE_TOGGLE"])

    with (
        patch(
            "app.handlers.dev_panel.start_custom.toggle_style_mode",
            AsyncMock(return_value=start_custom.EffectiveStyle("advanced", "global")),
        ) as toggle_mock,
        patch(
            "app.handlers.dev_panel.build_texts_hub_payload",
            AsyncMock(return_value=("hub", None)),
        ),
    ):
        await handler.__wrapped__(SimpleNamespace(), query)

    toggle_mock.assert_awaited_once_with(
        scope_type="global",
        owner_user_id=None,
        actor_user_id=settings.DEVELOPER_ID,
    )
    query.message.edit_text.assert_awaited_once()
    assert t("fa", "texts_links.start_style.mode_advanced") in query.answer.await_args.args[0]
    assert "/start" in query.answer.await_args.args[0]
    assert "سازنده" in query.answer.await_args.args[0]


@pytest.mark.asyncio
async def test_owner_start_style_toggle_writes_owner_scope_only():
    bot = _RecorderBot()
    owner_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "own_start_style_toggle")
    query = _pm_query(_PURE_OWNER, CB["OWN_START_STYLE_TOGGLE"])

    with (
        patch("app.handlers.owner_panel.user_repo.is_owner", AsyncMock(return_value=True)),
        patch(
            "app.handlers.owner_panel.resolve_single_active_owner_user_id",
            AsyncMock(return_value=_PURE_OWNER),
        ),
        patch(
            "app.handlers.owner_panel.start_custom.toggle_style_mode",
            AsyncMock(return_value=start_custom.EffectiveStyle("advanced", "owner")),
        ) as toggle_mock,
        patch(
            "app.handlers.owner_panel.build_texts_hub_payload",
            AsyncMock(return_value=("hub", None)),
        ),
    ):
        await handler.__wrapped__(SimpleNamespace(), query)

    toggle_mock.assert_awaited_once_with(
        scope_type="owner",
        owner_user_id=_PURE_OWNER,
        actor_user_id=_PURE_OWNER,
    )
    query.message.edit_text.assert_awaited_once()
    assert t("fa", "texts_links.start_style.mode_advanced") in query.answer.await_args.args[0]
    assert "/start" in query.answer.await_args.args[0]


@pytest.mark.asyncio
async def test_regular_user_cannot_forge_dev_start_style_toggle():
    bot = _RecorderBot()
    dev_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "dev_start_style_toggle")
    query = _pm_query(_REGULAR_USER, CB["DEV_START_STYLE_TOGGLE"])

    with (
        patch(
            "app.handlers.dev_panel.start_custom.toggle_style_mode",
            AsyncMock(),
        ) as toggle_mock,
        patch("app.utils.decorators._deny_access", AsyncMock()) as deny_mock,
    ):
        await handler(SimpleNamespace(), query)

    toggle_mock.assert_not_awaited()
    deny_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_sudo_cannot_forge_owner_start_style_toggle():
    bot = _RecorderBot()
    owner_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "own_start_style_toggle")
    query = _pm_query(_SUDO_USER, CB["OWN_START_STYLE_TOGGLE"])

    with (
        patch("app.utils.decorators.deny_if_bot_disabled", AsyncMock(return_value=False)),
        patch("app.utils.decorators.user_repo.is_owner", AsyncMock(return_value=False)),
        patch("app.utils.decorators._deny_access", AsyncMock()) as deny_mock,
        patch(
            "app.handlers.owner_panel.start_custom.toggle_style_mode",
            AsyncMock(),
        ) as toggle_mock,
    ):
        await handler(SimpleNamespace(), query)

    toggle_mock.assert_not_awaited()
    deny_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_developer_owner_callback_does_not_write_owner_scope_without_active_owner():
    bot = _RecorderBot()
    owner_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "own_start_style_toggle")
    query = _pm_query(settings.DEVELOPER_ID, CB["OWN_START_STYLE_TOGGLE"])

    with (
        patch("app.handlers.owner_panel.user_repo.is_owner", AsyncMock(return_value=False)),
        patch(
            "app.handlers.owner_panel.start_custom.toggle_style_mode",
            AsyncMock(),
        ) as toggle_mock,
    ):
        await handler.__wrapped__(SimpleNamespace(), query)

    toggle_mock.assert_not_awaited()
    query.message.edit_text.assert_awaited_once()
    assert t("fa", "texts_links.owner_dev_use_global_panel") in query.message.edit_text.await_args.args[0]

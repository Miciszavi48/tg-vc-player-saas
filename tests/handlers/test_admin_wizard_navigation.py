from __future__ import annotations

import os
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock
from unittest.mock import patch

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

    class _ListenerStopped(Exception):
        pass

    pyromod_exceptions.ListenerStopped = _ListenerStopped
    pyromod_module.exceptions = pyromod_exceptions
    sys.modules["pyromod"] = pyromod_module
    sys.modules["pyromod.exceptions"] = pyromod_exceptions

from app.config.settings import settings
from app.handlers import callbacks, owner_panel
from app.services.wizard_ui import TOKEN_DEV_USERS, TOKEN_OWNER_ROOT
from app.utils.ui import CB


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


@pytest.mark.asyncio
async def test_owner_list_shows_buttons():
    bot = _RecorderBot()
    owner_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "own_list_owners")

    query = _pm_query(900001, CB["OWN_LIST_OWNERS"])
    owners = [SimpleNamespace(user_id=11, display_name="Owner A", username="owner_a")]

    with patch("app.handlers.owner_panel.user_repo.get_all_owners", AsyncMock(return_value=owners)):
        await handler.__wrapped__(SimpleNamespace(), query)

    assert query.message.edit_text.await_count == 1
    kb = query.message.edit_text.call_args.kwargs["reply_markup"]
    cbs = _kb_callbacks(kb)
    assert f"{CB['WZ_BACK_PREFIX']}{TOKEN_OWNER_ROOT}" in cbs
    assert CB["WZ_HOME"] in cbs


@pytest.mark.asyncio
async def test_remove_sudo_flow_no_dead_end():
    bot = _RecorderBot()
    owner_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "own_remove_sudo")

    client = SimpleNamespace(
        stop_listening=AsyncMock(),
        ask=AsyncMock(return_value=SimpleNamespace(text="123")),
        send_message=AsyncMock(),
    )
    query = _pm_query(900001, CB["OWN_REMOVE_SUDO"])

    with (
        patch("app.handlers.owner_panel.remember_return_token", AsyncMock()),
        patch("app.handlers.owner_panel.user_repo.get_sudo", AsyncMock(return_value=SimpleNamespace(user_id=123, added_by=900001))),
        patch("app.handlers.owner_panel.user_repo.remove_sudo", AsyncMock()),
        patch("app.handlers.owner_panel.invalidate_sudolist", AsyncMock()),
    ):
        await handler.__wrapped__(client, query)

    prompt_kb = client.ask.call_args.kwargs["reply_markup"]
    prompt_cbs = _kb_callbacks(prompt_kb)
    assert f"{CB['WZ_CANCEL_PREFIX']}{TOKEN_OWNER_ROOT}" in prompt_cbs
    assert CB["WZ_HOME"] not in prompt_cbs

    assert client.send_message.await_count == 1
    confirm_kb = client.send_message.call_args.kwargs["reply_markup"]
    confirm_cbs = _kb_callbacks(confirm_kb)
    assert any(cb.startswith(f"{CB['OWN_REMOVE_SUDO_DO_PREFIX']}123:900001:") for cb in confirm_cbs)
    assert any(cb.startswith(f"{CB['OWN_REMOVE_SUDO_NO_PREFIX']}123:900001:") for cb in confirm_cbs)
    assert CB["OWN_SUDOS"] in confirm_cbs


@pytest.mark.asyncio
async def test_cancel_returns_to_previous_menu():
    bot = _RecorderBot()
    callbacks.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "wz_cancel")

    client = SimpleNamespace(stop_listening=AsyncMock())
    query = _pm_query(settings.DEVELOPER_ID, f"{CB['WZ_CANCEL_PREFIX']}{TOKEN_DEV_USERS}")
    fake_redis = SimpleNamespace(delete=AsyncMock())

    with (
        patch("app.services.wizard_ui.get_redis", AsyncMock(return_value=fake_redis)),
        patch("app.services.wizard_ui.AdminDashboardService.get_users_summary", AsyncMock(return_value={"owners": 1, "sudos": 1})),
    ):
        await handler(client, query)

    assert query.message.edit_text.await_count == 1
    kb = query.message.edit_text.call_args.kwargs["reply_markup"]
    cbs = _kb_callbacks(kb)
    assert CB["DEV_LIST_OWNERS"] in cbs
    assert CB["DEV_REMOVE_OWNER"] in cbs


@pytest.mark.asyncio
async def test_role_root_navigation_after_done():
    bot = _RecorderBot()
    callbacks.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "wz_home")

    client = SimpleNamespace(get_me=AsyncMock(return_value=SimpleNamespace(username="test_bot")))
    query = _pm_query(settings.DEVELOPER_ID, CB["WZ_HOME"])

    with patch("app.services.panel_router.settings_repo.get_bot_setting", AsyncMock(return_value="1")):
        await handler(client, query)

    assert query.message.edit_text.await_count == 1
    kb = query.message.edit_text.call_args.kwargs["reply_markup"]
    cbs = _kb_callbacks(kb)
    assert CB["DEV_CAT_CREDIT"] in cbs
    assert CB["DEV_CAT_USERS"] in cbs

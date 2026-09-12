"""Developer Panel destructive-action hardening tests (2026-06-01)."""
from __future__ import annotations

import os
import sys
import time
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
from app.handlers import dev_panel, helper_panel
from app.services.texts_links_ui import all_field_keys
from app.utils.ask_result import AskResult
from app.utils.i18n import t
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


def _developer_client():
    return SimpleNamespace(
        get_me=AsyncMock(return_value=SimpleNamespace(username="testbot")),
        send_message=AsyncMock(),
    )


@pytest.mark.asyncio
async def test_legacy_broadcast_redirects_to_wizard_without_ask():
    bot = _RecorderBot()
    dev_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "dev_legacy_broadcast_to_wizard")
    query = _pm_query(settings.DEVELOPER_ID, CB["DEV_BROADCAST_GROUP"])
    client = _developer_client()

    with (
        patch("app.handlers.dev_panel.begin_broadcast_wizard", AsyncMock()) as begin_mock,
        patch("app.handlers.dev_panel._ask", AsyncMock()) as ask_mock,
    ):
        await handler.__wrapped__(client, query)

    ask_mock.assert_not_awaited()
    begin_mock.assert_awaited_once()
    assert begin_mock.await_args.kwargs.get("legacy_notice") is True


@pytest.mark.asyncio
async def test_text_clear_first_click_does_not_clear():
    bot = _RecorderBot()
    dev_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "dev_text_clear")
    field = all_field_keys()[0]
    query = _pm_query(settings.DEVELOPER_ID, f"{CB['DEV_TEXT_CLEAR_PREFIX']}{field}")

    with patch("app.handlers.dev_panel.settings_repo.set_bot_setting", AsyncMock()) as clear_mock:
        await handler.__wrapped__(_developer_client(), query)

    clear_mock.assert_not_awaited()
    cbs = _kb_callbacks(query.message.edit_text.await_args.kwargs["reply_markup"])
    assert any(cb.startswith(CB["DEV_TEXT_CLEAR_EXEC_PREFIX"]) for cb in cbs)


@pytest.mark.asyncio
async def test_text_clear_confirm_clears_for_same_developer():
    bot = _RecorderBot()
    dev_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "dev_text_clear_confirm")
    field = all_field_keys()[0]
    uid = settings.DEVELOPER_ID
    data = f"{CB['DEV_TEXT_CLEAR_EXEC_PREFIX']}{field}:{uid}:{int(time.time())}"
    query = _pm_query(uid, data)

    with (
        patch("app.handlers.dev_panel.settings_repo.set_bot_setting", AsyncMock()) as clear_mock,
        patch("app.handlers.dev_panel.build_text_field_payload", AsyncMock(return_value=("ok", MagicMock()))),
    ):
        await handler.__wrapped__(_developer_client(), query)

    clear_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_text_clear_cancel_does_not_clear():
    bot = _RecorderBot()
    dev_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "dev_text_clear_abort")
    field = all_field_keys()[0]
    uid = settings.DEVELOPER_ID
    data = f"{CB['DEV_TEXT_CLEAR_ABORT_PREFIX']}{field}:{uid}:{int(time.time())}"
    query = _pm_query(uid, data)

    with (
        patch("app.handlers.dev_panel.settings_repo.set_bot_setting", AsyncMock()) as clear_mock,
        patch("app.handlers.dev_panel.build_text_field_payload", AsyncMock(return_value=("ok", MagicMock()))),
    ):
        await handler.__wrapped__(_developer_client(), query)

    clear_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_text_clear_confirm_wrong_user_rejected():
    bot = _RecorderBot()
    dev_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "dev_text_clear_confirm")
    field = all_field_keys()[0]
    data = f"{CB['DEV_TEXT_CLEAR_EXEC_PREFIX']}{field}:999999001:{int(time.time())}"
    query = _pm_query(settings.DEVELOPER_ID, data)

    with patch("app.handlers.dev_panel.settings_repo.set_bot_setting", AsyncMock()) as clear_mock:
        await handler.__wrapped__(_developer_client(), query)

    clear_mock.assert_not_awaited()
    query.answer.assert_awaited_with(t("fa", "common.errors.no_access"), show_alert=True)


@pytest.mark.asyncio
async def test_owner_remove_prompt_does_not_remove_immediately():
    bot = _RecorderBot()
    dev_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "dev_remove_owner")
    query = _pm_query(settings.DEVELOPER_ID, CB["DEV_REMOVE_OWNER"])
    target_uid = 42424242

    with (
            patch(
                "app.handlers.dev_panel._ask",
                AsyncMock(return_value=AskResult(message=SimpleNamespace(text=str(target_uid)))),
            ),
        patch("app.handlers.dev_panel.user_repo.get_owner", AsyncMock(return_value=object())),
        patch("app.handlers.dev_panel.user_repo.remove_owner", AsyncMock()) as remove_mock,
    ):
        client = _developer_client()
        await handler.__wrapped__(client, query)

    remove_mock.assert_not_awaited()
    cbs = _kb_callbacks(client.send_message.await_args.kwargs["reply_markup"])
    assert any(cb.startswith(CB["DEV_OWNER_REMOVE_EXEC_PREFIX"]) for cb in cbs)


@pytest.mark.asyncio
async def test_owner_remove_confirm_removes():
    bot = _RecorderBot()
    dev_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "dev_owner_remove_confirm")
    target_uid = 42424242
    uid = settings.DEVELOPER_ID
    data = f"{CB['DEV_OWNER_REMOVE_EXEC_PREFIX']}{target_uid}:{uid}:{int(time.time())}"
    query = _pm_query(uid, data)

    with (
        patch("app.handlers.dev_panel.user_repo.get_owner", AsyncMock(return_value=object())),
        patch("app.handlers.dev_panel.user_repo.remove_owner", AsyncMock()) as remove_mock,
        patch("app.handlers.dev_panel.invalidate_ownerlist", AsyncMock()),
        patch("app.handlers.dev_panel._send_done", AsyncMock()) as done_mock,
    ):
        await handler.__wrapped__(_developer_client(), query)

    remove_mock.assert_awaited_once_with(target_uid)
    done_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_helper_enable_first_click_shows_confirmation():
    bot = _RecorderBot()
    helper_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "hlp_enable")
    helper_id = 7
    query = _pm_query(settings.DEVELOPER_ID, f"{CB['HLP_ENABLE']}{helper_id}")
    client = _developer_client()

    with patch("app.handlers.helper_panel.HelperPoolService.activate_helper", AsyncMock()) as activate_mock:
        await handler(client, query)

    activate_mock.assert_not_awaited()
    cbs = _kb_callbacks(query.message.edit_text.await_args.kwargs["reply_markup"])
    assert any(cb.startswith(CB["HLP_ENABLE_CONFIRM_PREFIX"]) for cb in cbs)


@pytest.mark.asyncio
async def test_helper_enable_confirm_applies_for_same_developer():
    bot = _RecorderBot()
    helper_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "hlp_enable_confirm")
    helper_id = 7
    uid = settings.DEVELOPER_ID
    data = f"{CB['HLP_ENABLE_CONFIRM_PREFIX']}{helper_id}:{uid}:{int(time.time())}"
    query = _pm_query(uid, data)

    with (
        patch("app.handlers.helper_panel.HelperPoolService.activate_helper", AsyncMock()) as activate_mock,
        patch("app.handlers.helper_panel.helper_event_repo.log_event", AsyncMock()),
        patch("app.handlers.helper_panel._render_helper_detail", AsyncMock(return_value=True)),
    ):
        await handler(_developer_client(), query)

    activate_mock.assert_awaited_once_with(helper_id)


@pytest.mark.asyncio
async def test_helper_enable_cancel_does_not_apply():
    bot = _RecorderBot()
    helper_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "hlp_enable_abort")
    helper_id = 7
    uid = settings.DEVELOPER_ID
    data = f"{CB['HLP_ENABLE_ABORT_PREFIX']}{helper_id}:{uid}:{int(time.time())}"
    query = _pm_query(uid, data)

    with (
        patch("app.handlers.helper_panel.HelperPoolService.activate_helper", AsyncMock()) as activate_mock,
        patch("app.handlers.helper_panel._render_helper_detail", AsyncMock(return_value=True)),
    ):
        await handler(_developer_client(), query)

    activate_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_helper_rotate_key_still_requires_confirmation():
    bot = _RecorderBot()
    helper_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "hlp_rotate_key")
    query = _pm_query(settings.DEVELOPER_ID, CB["HLP_ROTATE_KEY"])

    with patch("app.handlers.helper_panel._rotate_helper_session_keys", AsyncMock()) as rotate_mock:
        await handler(_developer_client(), query)

    rotate_mock.assert_not_awaited()
    cbs = _kb_callbacks(query.message.edit_text.await_args.kwargs["reply_markup"])
    assert any(cb.startswith(CB["HLP_ROTATE_CONFIRM_PREFIX"]) for cb in cbs)

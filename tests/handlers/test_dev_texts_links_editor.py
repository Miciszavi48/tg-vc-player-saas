from __future__ import annotations

import json
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
from app.handlers import callbacks, dev_panel
from app.services.texts_links_ui import AskResult
from app.services.wizard_ui import TOKEN_DEV_TEXTS
from app.utils.i18n import t
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
async def test_dev_texts_hub_opens():
    bot = _RecorderBot()
    dev_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "dev_texts_home")

    query = _pm_query(settings.DEVELOPER_ID, CB["DEV_TEXTS_HOME"])
    client = SimpleNamespace()

    with patch("app.services.texts_links_ui.settings_repo.get_bot_setting", AsyncMock(return_value=None)):
        await handler.__wrapped__(client, query)

    assert query.answer.await_count == 1
    assert query.message.edit_text.await_count == 1
    kb = query.message.edit_text.call_args.kwargs["reply_markup"]
    cbs = _kb_callbacks(kb)
    assert any(cb.startswith(CB["DEV_TEXT_FIELD_PREFIX"]) for cb in cbs)
    assert CB["DEV_TEXTS_BACK"] in cbs
    assert CB["WZ_HOME"] not in cbs


@pytest.mark.asyncio
async def test_set_start_text_updates_db_and_returns_buttons():
    bot = _RecorderBot()
    dev_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "dev_text_set_text")

    query = _pm_query(settings.DEVELOPER_ID, f"{CB['DEV_TEXT_SET_TEXT_PREFIX']}start_text")
    client = SimpleNamespace(send_message=AsyncMock())

    with (
        patch(
            "app.handlers.dev_panel._ask",
            AsyncMock(return_value=AskResult(message=SimpleNamespace(text="sample"))),
        ),
        patch("app.handlers.dev_panel.settings_repo.set_bot_setting", AsyncMock()) as set_mock,
        patch("app.services.texts_links_ui.settings_repo.get_bot_setting", AsyncMock(return_value="sample")),
    ):
        await handler.__wrapped__(client, query)

    assert query.answer.await_count == 1
    assert set_mock.await_count == 1
    client.send_message.assert_not_awaited()
    args = set_mock.await_args
    assert args.args[0] == "start_text"
    assert args.args[1] == "sample"
    assert args.kwargs["updated_by"] == settings.DEVELOPER_ID

    assert query.message.edit_text.await_count == 1
    assert t("fa", "texts_links.saved_text") in query.message.edit_text.call_args.args[0]
    kb = query.message.edit_text.call_args.kwargs["reply_markup"]
    cbs = _kb_callbacks(kb)
    assert CB["DEV_TEXTS_HOME"] in cbs
    assert CB["WZ_HOME"] not in cbs


@pytest.mark.asyncio
async def test_set_start_media_saves_file_id_and_caption():
    bot = _RecorderBot()
    dev_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "dev_text_set_media")

    query = _pm_query(settings.DEVELOPER_ID, f"{CB['DEV_TEXT_SET_MEDIA_PREFIX']}start_text")
    media_msg = SimpleNamespace(
        text=None,
        photo=[SimpleNamespace(file_id="photo_small"), SimpleNamespace(file_id="photo_big")],
        document=None,
        caption="my caption",
    )
    client = SimpleNamespace(send_message=AsyncMock())

    with (
        patch("app.handlers.dev_panel._ask", AsyncMock(return_value=AskResult(message=media_msg))),
        patch("app.handlers.dev_panel.settings_repo.set_bot_setting", AsyncMock()) as set_mock,
        patch("app.services.texts_links_ui.settings_repo.get_bot_setting", AsyncMock(return_value='{"__mode":"media","file_id":"photo_big","caption":"my caption"}')),
    ):
        await handler.__wrapped__(client, query)

    assert query.answer.await_count == 1
    assert set_mock.await_count == 1
    client.send_message.assert_not_awaited()
    query.message.edit_text.assert_awaited_once()
    assert t("fa", "texts_links.saved_media") in query.message.edit_text.await_args.args[0]
    payload = json.loads(set_mock.await_args.args[1])
    assert payload["__mode"] == "media"
    assert payload["file_id"] == "photo_big"
    assert payload["caption"] == "my caption"


@pytest.mark.asyncio
async def test_cancel_returns_to_previous_page():
    bot = _RecorderBot()
    callbacks.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "wz_cancel")

    query = _pm_query(settings.DEVELOPER_ID, f"{CB['WZ_CANCEL_PREFIX']}{TOKEN_DEV_TEXTS}")
    client = SimpleNamespace(stop_listening=AsyncMock())
    fake_redis = SimpleNamespace(delete=AsyncMock())

    with (
        patch("app.services.wizard_ui.get_redis", AsyncMock(return_value=fake_redis)),
        patch("app.services.texts_links_ui.settings_repo.get_bot_setting", AsyncMock(return_value=None)),
    ):
        await handler(client, query)

    assert query.message.edit_text.await_count == 1
    kb = query.message.edit_text.call_args.kwargs["reply_markup"]
    cbs = _kb_callbacks(kb)
    assert any(cb.startswith(CB["DEV_TEXT_FIELD_PREFIX"]) for cb in cbs)
    assert CB["DEV_TEXTS_BACK"] in cbs
    assert CB["WZ_HOME"] not in cbs


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("handler_name", "setting_key"),
    [
        ("dev_force_join_toggle", "force_join_enabled"),
        ("dev_trial_toggle", "trial_enabled"),
        ("dev_auto_leave_toggle", "auto_leave_enabled"),
    ],
)
async def test_dev_toggle_callbacks_flip_and_refresh(handler_name: str, setting_key: str):
    bot = _RecorderBot()
    dev_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, handler_name)

    query = _pm_query(settings.DEVELOPER_ID, "dummy")

    with (
        patch("app.handlers.dev_panel.settings_repo.toggle_bot_setting", AsyncMock(return_value=True)) as toggle_mock,
        patch("app.handlers.dev_panel._show_dev_settings_menu", AsyncMock()) as settings_show_mock,
        patch("app.handlers.dev_panel._show_dev_force_join_menu", AsyncMock()) as force_join_show_mock,
        patch("app.handlers.dev_panel.AdminDashboardService.invalidate_cache", AsyncMock()),
        patch("app.handlers.dev_panel.ForcedMembershipService.invalidate_cache", AsyncMock()),
    ):
        await handler.__wrapped__(SimpleNamespace(), query)

    assert query.answer.await_count == 1
    assert toggle_mock.await_count == 1
    assert toggle_mock.await_args.args[0] == setting_key
    assert toggle_mock.await_args.kwargs["updated_by"] == settings.DEVELOPER_ID
    if setting_key == "force_join_enabled":
        assert force_join_show_mock.await_count == 1
        assert settings_show_mock.await_count == 0
    else:
        assert settings_show_mock.await_count == 1
        assert force_join_show_mock.await_count == 0

"""LiveBug-3: text/link panel UX confirmation and cleanup tests."""
from __future__ import annotations

import json
import os
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, patch

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
from app.handlers import dev_panel, owner_panel
from app.services.owner_text_link_service import TextLinkValue
from app.services.texts_links_ui import (
    AskResult,
    build_text_field_payload,
    build_texts_hub_payload,
    is_valid_link_value,
)
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


def _kb_labels(kb) -> set[str]:
    return {
        btn.text
        for row in kb.inline_keyboard
        for btn in row
        if btn.text
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
async def test_dev_text_save_redraws_same_panel_with_confirmation():
    bot = _RecorderBot()
    dev_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "dev_text_set_text")
    query = _pm_query(settings.DEVELOPER_ID, f"{CB['DEV_TEXT_SET_TEXT_PREFIX']}start_text")
    client = SimpleNamespace(send_message=AsyncMock())

    with (
        patch("app.handlers.dev_panel._ask", AsyncMock(return_value=AskResult(message=SimpleNamespace(text="hello")))),
        patch("app.handlers.dev_panel.settings_repo.set_bot_setting", AsyncMock()) as set_mock,
        patch("app.services.texts_links_ui.settings_repo.get_bot_setting", AsyncMock(return_value="hello")),
    ):
        await handler.__wrapped__(client, query)

    set_mock.assert_awaited_once()
    client.send_message.assert_not_awaited()
    query.message.edit_text.assert_awaited_once()
    assert t("fa", "texts_links.saved_text") in query.message.edit_text.await_args.args[0]


@pytest.mark.asyncio
async def test_dev_link_save_redraws_same_panel_with_confirmation():
    bot = _RecorderBot()
    dev_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "dev_text_set_text")
    query = _pm_query(settings.DEVELOPER_ID, f"{CB['DEV_TEXT_SET_TEXT_PREFIX']}bot_channel_link")
    client = SimpleNamespace(send_message=AsyncMock())

    with (
        patch(
            "app.handlers.dev_panel._ask",
            AsyncMock(return_value=AskResult(message=SimpleNamespace(text="https://t.me/channel"))),
        ),
        patch("app.handlers.dev_panel.settings_repo.set_bot_setting", AsyncMock()) as set_mock,
        patch("app.services.texts_links_ui.settings_repo.get_bot_setting", AsyncMock(return_value="https://t.me/channel")),
    ):
        await handler.__wrapped__(client, query)

    set_mock.assert_awaited_once()
    client.send_message.assert_not_awaited()
    query.message.edit_text.assert_awaited_once()
    assert t("fa", "texts_links.saved_link") in query.message.edit_text.await_args.args[0]


@pytest.mark.asyncio
async def test_dev_media_save_redraws_same_panel_with_confirmation():
    bot = _RecorderBot()
    dev_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "dev_text_set_media")
    query = _pm_query(settings.DEVELOPER_ID, f"{CB['DEV_TEXT_SET_MEDIA_PREFIX']}start_text")
    media_msg = SimpleNamespace(
        text=None,
        photo=[SimpleNamespace(file_id="photo_small"), SimpleNamespace(file_id="photo_big")],
        document=None,
        caption="cap",
    )
    client = SimpleNamespace(send_message=AsyncMock())

    with (
        patch("app.handlers.dev_panel._ask", AsyncMock(return_value=AskResult(message=media_msg))),
        patch("app.handlers.dev_panel.settings_repo.set_bot_setting", AsyncMock()) as set_mock,
        patch(
            "app.services.texts_links_ui.settings_repo.get_bot_setting",
            AsyncMock(return_value='{"__mode":"media","file_id":"photo_big","caption":"cap"}'),
        ),
    ):
        await handler.__wrapped__(client, query)

    set_mock.assert_awaited_once()
    client.send_message.assert_not_awaited()
    query.message.edit_text.assert_awaited_once()
    assert t("fa", "texts_links.saved_media") in query.message.edit_text.await_args.args[0]


@pytest.mark.asyncio
async def test_dev_clear_confirm_sends_cleared_confirmation():
    bot = _RecorderBot()
    dev_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "dev_text_clear_confirm")
    user_id = settings.DEVELOPER_ID
    issued_at = 1_700_000_000
    query = _pm_query(user_id, f"{CB['DEV_TEXT_CLEAR_EXEC_PREFIX']}start_text:{user_id}:{issued_at}")
    client = SimpleNamespace(send_message=AsyncMock())

    with (
        patch("app.handlers.dev_panel._is_stale_dev_confirm_token", return_value=False),
        patch("app.handlers.dev_panel.settings_repo.set_bot_setting", AsyncMock()) as set_mock,
        patch("app.services.texts_links_ui.settings_repo.get_bot_setting", AsyncMock(return_value=None)),
    ):
        await handler.__wrapped__(client, query)

    set_mock.assert_awaited_once()
    assert t("fa", "texts_links.cleared") in client.send_message.await_args.args[1]


@pytest.mark.asyncio
async def test_listener_stopped_sends_abort_feedback():
    bot = _RecorderBot()
    dev_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "dev_text_set_text")
    query = _pm_query(settings.DEVELOPER_ID, f"{CB['DEV_TEXT_SET_TEXT_PREFIX']}start_text")
    client = SimpleNamespace(send_message=AsyncMock())

    with patch(
        "app.handlers.dev_panel._ask",
        AsyncMock(return_value=AskResult(message=None, abort_reason="listener_stopped")),
    ):
        await handler.__wrapped__(client, query)

    assert t("fa", "texts_links.ask_cancelled_or_timeout") in client.send_message.await_args.args[1]


@pytest.mark.asyncio
async def test_link_field_keyboard_excludes_media_button():
    with patch("app.services.texts_links_ui.settings_repo.get_bot_setting", AsyncMock(return_value=None)):
        _text, kb = await build_text_field_payload("fa", "dev", "bot_channel_link")

    cbs = _kb_callbacks(kb)
    assert not any(cb.startswith(CB["DEV_TEXT_SET_MEDIA_PREFIX"]) for cb in cbs)
    assert f"{CB['DEV_TEXT_SET_TEXT_PREFIX']}bot_channel_link" in cbs


@pytest.mark.asyncio
async def test_text_link_submenus_do_not_show_back_and_home_together():
    with patch("app.services.texts_links_ui.settings_repo.get_bot_setting", AsyncMock(return_value=None)):
        _hub_text, hub_kb = await build_texts_hub_payload("fa", "dev")
        _field_text, field_kb = await build_text_field_payload("fa", "dev", "bot_channel_link")

    assert CB["DEV_TEXTS_BACK"] in _kb_callbacks(hub_kb)
    assert CB["WZ_HOME"] not in _kb_callbacks(hub_kb)
    assert CB["DEV_TEXTS_HOME"] in _kb_callbacks(field_kb)
    assert CB["WZ_HOME"] not in _kb_callbacks(field_kb)


@pytest.mark.asyncio
async def test_text_field_keyboard_includes_media_button():
    with patch("app.services.texts_links_ui.settings_repo.get_bot_setting", AsyncMock(return_value=None)):
        _text, kb = await build_text_field_payload("fa", "dev", "start_text")

    cbs = _kb_callbacks(kb)
    assert f"{CB['DEV_TEXT_SET_MEDIA_PREFIX']}start_text" in cbs


@pytest.mark.asyncio
async def test_clear_button_label_delete_value_callback_unchanged():
    with patch("app.services.texts_links_ui.settings_repo.get_bot_setting", AsyncMock(return_value=None)):
        _text, kb = await build_text_field_payload("fa", "dev", "start_text")

    labels = _kb_labels(kb)
    cbs = _kb_callbacks(kb)
    assert t("fa", "texts_links.buttons.clear") == "🗑 حذف مقدار"
    assert "🗑 حذف مقدار" in labels
    assert f"{CB['DEV_TEXT_CLEAR_PREFIX']}start_text" in cbs


def test_developer_pv_link_label_not_confusing():
    label = t("fa", "texts_links.developer_pv_link")
    lowered = label.lower()
    assert "اصلی" in label
    assert "سازنده" in label
    assert "پروف" not in lowered
    assert "profile" not in lowered
    assert "ادمین" not in lowered


@pytest.mark.asyncio
async def test_invalid_link_rejected_without_db_write():
    bot = _RecorderBot()
    dev_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "dev_text_set_text")
    query = _pm_query(settings.DEVELOPER_ID, f"{CB['DEV_TEXT_SET_TEXT_PREFIX']}bot_channel_link")
    client = SimpleNamespace(send_message=AsyncMock())

    ask = AsyncMock(
        side_effect=[
            AskResult(message=SimpleNamespace(text="not-a-valid-link")),
            AskResult(message=None, abort_reason="listener_stopped", user_notified=True),
        ]
    )
    with (
        patch("app.handlers.dev_panel._ask", ask),
        patch("app.handlers.dev_panel.settings_repo.set_bot_setting", AsyncMock()) as set_mock,
    ):
        await handler.__wrapped__(client, query)

    set_mock.assert_not_awaited()
    assert ask.await_count == 2
    assert t("fa", "texts_links.invalid_link") in ask.await_args_list[1].kwargs["prompt_text"]
    client.send_message.assert_not_awaited()


def test_is_valid_link_value_accepts_common_formats():
    assert is_valid_link_value("https://t.me/channel")
    assert is_valid_link_value("http://example.com")
    assert is_valid_link_value("t.me/foo")
    assert not is_valid_link_value("@username")
    assert not is_valid_link_value("plain-text")


@pytest.mark.asyncio
async def test_owner_texts_home_allows_pure_active_owner():
    bot = _RecorderBot()
    owner_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "own_texts_home")
    pure_owner_id = 900_001
    query = _pm_query(pure_owner_id, CB["OWN_TEXTS_HOME"])
    client = SimpleNamespace()

    with (
        patch("app.handlers.owner_panel.user_repo.is_owner", AsyncMock(return_value=True)),
        patch(
            "app.services.owner_text_link_service.get_effective_text_link",
            AsyncMock(return_value=TextLinkValue(mode="empty", raw=None, source="empty")),
        ),
    ):
        await handler.__wrapped__(client, query)

    query.message.edit_text.assert_awaited_once()
    assert t("fa", "texts_links.owner_hub_title") in query.message.edit_text.await_args.args[0]


@pytest.mark.asyncio
async def test_developer_without_owner_role_redirected_to_dev_panel():
    bot = _RecorderBot()
    owner_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "own_texts_home")
    query = _pm_query(settings.DEVELOPER_ID, CB["OWN_TEXTS_HOME"])
    client = SimpleNamespace()

    with (
        patch("app.handlers.owner_panel.is_developer", return_value=True),
        patch("app.handlers.owner_panel.user_repo.is_owner", AsyncMock(return_value=False)),
    ):
        await handler.__wrapped__(client, query)

    query.message.edit_text.assert_awaited_once()
    assert t("fa", "texts_links.owner_dev_use_global_panel") in query.message.edit_text.await_args.args[0]


def test_callback_constants_unchanged():
    assert CB["DEV_TEXT_SET_TEXT_PREFIX"] == "dev:text:txt:"
    assert CB["DEV_TEXT_SET_MEDIA_PREFIX"] == "dev:text:med:"
    assert CB["DEV_TEXT_CLEAR_PREFIX"] == "dev:text:clr:"
    assert CB["OWN_TEXT_SET_TEXT_PREFIX"] == "own:text:txt:"
    assert CB["OWN_TEXT_SET_MEDIA_PREFIX"] == "own:text:med:"
    assert CB["OWN_TEXT_CLEAR_PREFIX"] == "own:text:clr:"


def test_new_string_keys_exist_and_json_parity():
    from app.utils.i18n import texts

    fa = texts.load("fa")
    en = texts.load("en")

    keys = [
        "texts_links.saved_text",
        "texts_links.saved_link",
        "texts_links.saved_media",
        "texts_links.cleared",
        "texts_links.ask_cancelled_or_timeout",
        "texts_links.invalid_link",
        "texts_links.buttons.set_link",
        "texts_links.group_storage_only",
        "texts_links.storage_only_section_note",
        "texts_links.runtime_status.storage_only",
        "texts_links.runtime_locations.group_support",
        "texts_links.runtime_effects.owner_private_start_unused",
        "texts_links.media_runtime.caption_only",
    ]

    def _get(data: dict, dotted: str) -> str:
        node = data
        for part in dotted.split("."):
            node = node[part]
        return node

    for key in keys:
        assert _get(fa, key)
        assert _get(en, key)

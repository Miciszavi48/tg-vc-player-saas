from __future__ import annotations

import os
import logging
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

os.environ.setdefault("TEST_MODE", "1")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./.pytest-specific-callbacks.db")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/15")
os.environ.setdefault("BOT_TOKEN", "test")
os.environ.setdefault("API_ID", "12345")
os.environ.setdefault("API_HASH", "testhash")
os.environ.setdefault("DEVELOPER_ID", "123456789")

if "pyromod" not in sys.modules:
    pyromod_module = ModuleType("pyromod")
    pyromod_exceptions = ModuleType("pyromod.exceptions")
    pyromod_exceptions.ListenerStopped = type("ListenerStopped", (Exception,), {})
    pyromod_module.exceptions = pyromod_exceptions
    sys.modules["pyromod"] = pyromod_module
    sys.modules["pyromod.exceptions"] = pyromod_exceptions

from app.config.settings import settings
from app.handlers import analytics_panel, callbacks, dev_panel, group_panel, help_center, helper_panel, owner_panel
from app.handlers.helper_panel import HelperPanelSummary
from app.utils.i18n import t
from app.utils.ui import CB, KeyboardFactory


class _RecorderBot:
    def __init__(self) -> None:
        self.callback_handlers: list = []
        self.callback_meta: dict[str, dict] = {}

    def on_callback_query(self, *args, **kwargs):  # noqa: ANN001, ANN002
        def _decorator(fn):
            self.callback_handlers.append(fn)
            self.callback_meta[fn.__name__] = {"args": args, "kwargs": kwargs}
            return fn

        return _decorator

    def on_message(self, *args, **kwargs):  # noqa: ANN001, ANN002
        def _decorator(fn):
            return fn

        return _decorator


def _handler_by_name(bot: _RecorderBot, name: str):
    return next(fn for fn in bot.callback_handlers if fn.__name__ == name)


def _kb_callbacks(kb) -> set[str]:
    return {
        btn.callback_data
        for row in getattr(kb, "inline_keyboard", [])
        for btn in row
        if getattr(btn, "callback_data", None)
    }


def _query(
    data: str,
    *,
    user_id: int | None = None,
    chat_id: int = 12345,
    chat_type: str = "private",
    edit_side_effect=None,
):
    if user_id is None:
        user_id = settings.DEVELOPER_ID
    return SimpleNamespace(
        id=f"query-{data}",
        data=data,
        from_user=SimpleNamespace(id=user_id, first_name="Tester"),
        answer=AsyncMock(),
        message=SimpleNamespace(
            chat=SimpleNamespace(id=chat_id, type=SimpleNamespace(value=chat_type)),
            text="menu",
            edit_text=AsyncMock(side_effect=edit_side_effect),
            edit_caption=AsyncMock(),
            edit_reply_markup=AsyncMock(),
            reply=AsyncMock(),
        ),
    )


def _developer_client():
    return SimpleNamespace(
        get_me=AsyncMock(return_value=SimpleNamespace(username="test_bot")),
        send_message=AsyncMock(),
    )


def test_compared_callbacks_are_generated_from_expected_keyboards():
    developer_callbacks = _kb_callbacks(KeyboardFactory.developer_panel("fa"))
    assert CB["DEV_CAT_BROADCAST"] == "dev:cat:bc"
    assert CB["DEV_CAT_USERS"] == "dev:cat:users"
    assert CB["HLP_HOME"] == "hlp:home"
    assert CB["NAV_BACK"] == "nav:back"
    assert {CB["DEV_CAT_BROADCAST"], CB["DEV_CAT_USERS"], CB["HLP_HOME"]} <= developer_callbacks

    users_callbacks = _kb_callbacks(KeyboardFactory.dev_sub_users("fa"))
    assert CB["NAV_BACK"] in users_callbacks
    assert CB["DEV_ADMIN_TITLES"] in users_callbacks

    broadcast_kb = KeyboardFactory.dev_sub_broadcast("fa")
    broadcast_callbacks = _kb_callbacks(broadcast_kb)
    assert CB["NAV_BACK"] in broadcast_callbacks
    assert CB["BCW_START"] in broadcast_callbacks
    assert CB["DEV_BROADCAST_GROUP"] in broadcast_callbacks

    helper_callbacks = _kb_callbacks(KeyboardFactory.helper_home("fa", 1, 0, 0))
    assert CB["HLP_HOME"] in helper_callbacks
    assert CB["NAV_BACK"] in helper_callbacks


def test_compared_callbacks_are_registered_with_expected_handlers_and_priority():
    bot = _RecorderBot()
    group_panel.register(bot, None)
    callbacks.register(bot, None)
    dev_panel.register(bot, None)
    helper_panel.register(bot, None)

    assert "grp_nav_back" in bot.callback_meta
    assert "nav_back" in bot.callback_meta
    assert "dev_cat_broadcast" in bot.callback_meta
    assert "dev_cat_users" in bot.callback_meta
    assert "dev_shortcut_helper_home" in bot.callback_meta
    assert "dev_shortcut_analytics_home" in bot.callback_meta
    assert "dev_shortcut_help_about" in bot.callback_meta
    assert "hlp_home" in bot.callback_meta
    assert bot.callback_meta["grp_nav_back"]["kwargs"]["group"] == -850
    assert bot.callback_meta["nav_back"]["kwargs"].get("group", 0) == 0
    assert bot.callback_meta["dev_cat_broadcast"]["kwargs"].get("group", 0) == 0
    assert bot.callback_meta["dev_cat_users"]["kwargs"].get("group", 0) == 0
    assert bot.callback_meta["dev_shortcut_helper_home"]["kwargs"].get("group", 0) == 0
    assert bot.callback_meta["hlp_home"]["kwargs"].get("group", 0) == 0


def test_developer_panel_tools_row_handlers_registered_in_dev_panel():
    bot = _RecorderBot()
    dev_panel.register(bot, None)
    for name in (
        "dev_cat_lists",
        "dev_shortcut_helper_home",
        "dev_shortcut_analytics_home",
        "dev_shortcut_help_about",
        "dev_shortcut_bcw_start",
        "dev_shortcut_bc_history",
    ):
        assert name in bot.callback_meta, name
    assert bot.callback_meta["dev_shortcut_helper_home"]["kwargs"].get("group", 0) == 0


@pytest.mark.asyncio
async def test_nav_back_answers_and_uses_safe_private_root_edit(caplog):
    caplog.set_level(logging.INFO, logger="callback_trace")
    bot = _RecorderBot()
    callbacks.register(bot, None)
    handler = _handler_by_name(bot, "nav_back")
    query = _query(CB["NAV_BACK"])

    with (
        patch(
            "app.handlers.callbacks.build_private_root_payload",
            AsyncMock(return_value=("private root", KeyboardFactory.developer_panel("fa"))),
        ),
        patch("app.services.panel_message_service.get_redis", AsyncMock()),
    ):
        await handler(_developer_client(), query)

    query.answer.assert_awaited_once_with()
    query.message.edit_text.assert_awaited_once()
    assert "callback.edit" in caplog.text
    assert "data=nav:back" in caplog.text
    assert "nav_back.received" in caplog.text
    assert "nav_back.route_selected" in caplog.text
    assert "route_type=private_root" in caplog.text
    assert "nav_back.answer_sent" in caplog.text


@pytest.mark.asyncio
async def test_dev_cat_broadcast_answers_and_edits_with_broadcast_submenu(caplog):
    caplog.set_level(logging.INFO, logger="callback_trace")
    bot = _RecorderBot()
    dev_panel.register(bot, None)
    handler = _handler_by_name(bot, "dev_cat_broadcast")
    query = _query(CB["DEV_CAT_BROADCAST"])

    await handler(_developer_client(), query)

    reply_markup = query.message.edit_text.await_args.kwargs["reply_markup"]
    callbacks_in_markup = _kb_callbacks(reply_markup)
    assert CB["BCW_START"] in callbacks_in_markup
    assert CB["DEV_BROADCAST_GROUP"] in callbacks_in_markup
    assert CB["NAV_BACK"] in callbacks_in_markup
    query.answer.assert_awaited_once()
    query.message.edit_text.assert_awaited_once()
    assert "callback.edit" in caplog.text
    assert "data=dev:cat:bc" in caplog.text


@pytest.mark.asyncio
async def test_dev_cat_users_answers_and_edits_with_users_submenu(caplog):
    caplog.set_level(logging.INFO, logger="callback_trace")
    bot = _RecorderBot()
    dev_panel.register(bot, None)
    handler = _handler_by_name(bot, "dev_cat_users")
    query = _query(CB["DEV_CAT_USERS"])

    with patch("app.handlers.dev_panel._build_dev_users_text", AsyncMock(return_value="users panel")):
        await handler(_developer_client(), query)

    reply_markup = query.message.edit_text.await_args.kwargs["reply_markup"]
    callbacks_in_markup = _kb_callbacks(reply_markup)
    assert CB["DEV_ADMIN_TITLES"] in callbacks_in_markup
    assert CB["DEV_LIST_OWNERS"] in callbacks_in_markup
    assert CB["NAV_BACK"] in callbacks_in_markup
    query.answer.assert_awaited_once()
    query.message.edit_text.assert_awaited_once()
    assert "callback.edit" in caplog.text
    assert "data=dev:cat:users" in caplog.text


@pytest.mark.asyncio
async def test_hlp_home_answers_and_edits_with_helper_home(caplog):
    caplog.set_level(logging.INFO, logger="callback_trace")
    bot = _RecorderBot()
    helper_panel.register(bot, None)
    handler = _handler_by_name(bot, "hlp_home")
    query = _query(CB["HLP_HOME"])

    with (
        patch("app.handlers.helper_panel._clear_helper_wizard_states", AsyncMock()),
        patch(
            "app.handlers.helper_panel._get_helper_panel_summary",
            AsyncMock(return_value=HelperPanelSummary(total=3, active=2, disabled=1, quarantined=0, available=2)),
        ),
        patch("app.services.panel_message_service.get_redis", AsyncMock()),
    ):
        await handler(_developer_client(), query)

    query.answer.assert_awaited()
    query.message.edit_text.assert_awaited_once()
    callbacks_in_markup = _kb_callbacks(query.message.edit_text.await_args.kwargs["reply_markup"])
    assert CB["HLP_LIST"] in callbacks_in_markup
    assert CB["HLP_HOME"] in callbacks_in_markup
    assert "callback.edit" in caplog.text
    assert "data=hlp:home" in caplog.text


@pytest.mark.asyncio
async def test_dev_shortcut_helper_home_matches_dev_cat_lists_pattern(caplog):
    caplog.set_level(logging.INFO, logger="callback_trace")
    bot = _RecorderBot()
    dev_panel.register(bot, None)
    handler = _handler_by_name(bot, "dev_shortcut_helper_home")
    query = _query(CB["HLP_HOME"])

    with (
        patch("app.handlers.helper_panel._clear_helper_wizard_states", AsyncMock()),
        patch(
            "app.handlers.helper_panel._get_helper_panel_summary",
            AsyncMock(return_value=HelperPanelSummary(total=3, active=2, disabled=1, quarantined=0, available=2)),
        ),
        patch("app.services.panel_message_service.get_redis", AsyncMock()),
    ):
        await handler(_developer_client(), query)

    query.answer.assert_awaited()
    query.message.edit_text.assert_awaited_once()
    callbacks_in_markup = _kb_callbacks(query.message.edit_text.await_args.kwargs["reply_markup"])
    assert CB["HLP_LIST"] in callbacks_in_markup
    assert "callback.edit" in caplog.text
    assert "data=hlp:home" in caplog.text


@pytest.mark.asyncio
async def test_dev_shortcut_analytics_home_edits_with_analytics_keyboard(caplog):
    caplog.set_level(logging.INFO, logger="callback_trace")
    bot = _RecorderBot()
    dev_panel.register(bot, None)
    handler = _handler_by_name(bot, "dev_shortcut_analytics_home")
    query = _query(CB["AN_HOME"])

    with patch("app.services.panel_message_service.get_redis", AsyncMock()):
        await handler(_developer_client(), query)

    query.answer.assert_awaited()
    query.message.edit_text.assert_awaited_once()
    callbacks_in_markup = _kb_callbacks(query.message.edit_text.await_args.kwargs["reply_markup"])
    assert CB["AN_YESTERDAY"] in callbacks_in_markup
    assert "data=an:home" in caplog.text


@pytest.mark.asyncio
async def test_dev_shortcut_help_about_denies_private_help_panel(caplog):
    caplog.set_level(logging.INFO, logger="callback_trace")
    bot = _RecorderBot()
    dev_panel.register(bot, None)
    handler = _handler_by_name(bot, "dev_shortcut_help_about")
    query = _query(CB["HELP_HOME"])

    with (
        patch("app.handlers.help_center._get_role", AsyncMock(return_value="developer")),
        patch("app.services.panel_message_service.get_redis", AsyncMock()),
    ):
        await handler(_developer_client(), query)

    query.answer.assert_awaited()
    assert query.answer.await_args.args[0] == t("fa", "help.errors.group_only_callback")
    assert query.answer.await_args.kwargs["show_alert"] is True
    query.message.edit_text.assert_not_awaited()


@pytest.mark.asyncio
async def test_nav_back_from_dev_submenu_returns_developer_panel(caplog):
    caplog.set_level(logging.INFO, logger="callback_trace")
    bot = _RecorderBot()
    callbacks.register(bot, None)
    dev_panel.register(bot, None)
    lists_handler = _handler_by_name(bot, "dev_cat_lists")
    nav_handler = _handler_by_name(bot, "nav_back")
    query = _query(CB["DEV_CAT_LISTS"])

    with (
        patch("app.handlers.dev_panel._build_dev_lists_text", AsyncMock(return_value="lists panel")),
        patch("app.services.panel_message_service.get_redis", AsyncMock()),
    ):
        await lists_handler(_developer_client(), query)

    back_query = _query(CB["NAV_BACK"])
    with (
        patch(
            "app.handlers.callbacks.build_private_root_payload",
            AsyncMock(
                return_value=(
                    "developer root",
                    KeyboardFactory.developer_panel("fa"),
                )
            ),
        ),
        patch("app.services.panel_message_service.get_redis", AsyncMock()),
    ):
        await nav_handler(_developer_client(), back_query)

    back_query.answer.assert_awaited_once_with()
    back_query.message.edit_text.assert_awaited()
    callbacks_in_markup = _kb_callbacks(back_query.message.edit_text.await_args.kwargs["reply_markup"])
    assert CB["DEV_CAT_LISTS"] in callbacks_in_markup
    assert CB["HLP_HOME"] in callbacks_in_markup
    assert "nav_back.received" in caplog.text


@pytest.mark.asyncio
async def test_hlp_home_wrong_chat_type_has_visible_guard_feedback():
    bot = _RecorderBot()
    helper_panel.register(bot, None)
    handler = _handler_by_name(bot, "hlp_home")
    query = _query(CB["HLP_HOME"], chat_id=-100123, chat_type="supergroup")

    await handler(_developer_client(), query)

    query.answer.assert_awaited_once_with(t("fa", "admin.helpers.private_only"), show_alert=True)
    query.message.edit_text.assert_awaited_once()


@pytest.mark.asyncio
async def test_hlp_home_edit_failure_uses_send_fallback():
    bot = _RecorderBot()
    helper_panel.register(bot, None)
    handler = _handler_by_name(bot, "hlp_home")
    client = _developer_client()
    client.send_message = AsyncMock(
        return_value=SimpleNamespace(id=999, message_id=999),
    )
    query = _query(CB["HLP_HOME"], edit_side_effect=RuntimeError("cant edit"))
    query.message.edit_caption = AsyncMock(side_effect=RuntimeError("cant edit"))
    query.message.edit_reply_markup = AsyncMock(side_effect=RuntimeError("cant edit"))

    fake_redis = SimpleNamespace(
        set=AsyncMock(),
        get=AsyncMock(return_value=None),
    )

    with (
        patch("app.handlers.helper_panel._clear_helper_wizard_states", AsyncMock()),
        patch(
            "app.handlers.helper_panel._get_helper_panel_summary",
            AsyncMock(return_value=HelperPanelSummary(total=3, active=2, disabled=1, quarantined=0, available=2)),
        ),
        patch("app.services.panel_message_service.get_redis", AsyncMock(return_value=fake_redis)),
    ):
        await handler(client, query)

    query.answer.assert_awaited()
    client.send_message.assert_awaited()


@pytest.mark.asyncio
async def test_dev_shortcut_bcw_start_uses_panel_callback_edit(caplog):
    caplog.set_level(logging.INFO, logger="callback_trace")
    bot = _RecorderBot()
    dev_panel.register(bot, None)
    handler = _handler_by_name(bot, "dev_shortcut_bcw_start")
    query = _query(CB["BCW_START"])

    with (
        patch("app.handlers.broadcast_wizard._clear_state", AsyncMock()),
        patch("app.handlers.broadcast_wizard._set_state", AsyncMock()),
        patch("app.handlers.broadcast_wizard.remember_return_token", AsyncMock()),
        patch("app.utils.ask_result.safe_stop_listening", AsyncMock()),
        patch("app.services.panel_message_service.get_redis", AsyncMock()),
    ):
        await handler(_developer_client(), query)

    query.answer.assert_awaited()
    query.message.edit_text.assert_awaited_once()
    assert "callback.edit" in caplog.text
    assert "data=bcw:start" in caplog.text


@pytest.mark.asyncio
async def test_dev_shortcut_bc_history_uses_panel_callback_edit(caplog):
    caplog.set_level(logging.INFO, logger="callback_trace")
    bot = _RecorderBot()
    dev_panel.register(bot, None)
    handler = _handler_by_name(bot, "dev_shortcut_bc_history")
    query = _query(CB["BC_HISTORY"])

    with (
        patch("app.handlers.broadcast_panel.broadcast_repo.get_recent", AsyncMock(return_value=[])),
        patch("app.services.panel_message_service.get_redis", AsyncMock()),
    ):
        await handler(_developer_client(), query)

    query.answer.assert_awaited()
    query.message.edit_text.assert_awaited_once()
    assert "callback.edit" in caplog.text
    assert "data=bc:history" in caplog.text


@pytest.mark.asyncio
async def test_wz_home_uses_panel_callback_edit(caplog):
    caplog.set_level(logging.INFO, logger="callback_trace")
    bot = _RecorderBot()
    callbacks.register(bot, None)
    handler = _handler_by_name(bot, "wz_home")
    query = _query(CB["WZ_HOME"])

    with (
        patch("app.handlers.callbacks.clear_runtime_state", AsyncMock()),
        patch(
            "app.handlers.callbacks.resolve_navigation_payload",
            AsyncMock(return_value=("root", KeyboardFactory.developer_panel("fa"))),
        ),
        patch("app.services.panel_message_service.get_redis", AsyncMock()),
    ):
        await handler(_developer_client(), query)

    query.answer.assert_awaited_once_with()
    query.message.edit_text.assert_awaited_once()
    assert "callback.edit" in caplog.text


@pytest.mark.asyncio
async def test_an_yesterday_uses_panel_callback_edit(caplog):
    caplog.set_level(logging.INFO, logger="callback_trace")
    bot = _RecorderBot()
    analytics_panel.register(bot, None)
    handler = _handler_by_name(bot, "an_yesterday")
    query = _query(CB["AN_YESTERDAY"])

    with (
        patch(
            "app.handlers.analytics_panel.get_report",
            AsyncMock(return_value={"range": "yesterday", "events_total": 0}),
        ),
        patch("app.services.panel_message_service.get_redis", AsyncMock()),
    ):
        await handler(_developer_client(), query)

    assert query.answer.await_count >= 1
    query.message.edit_text.assert_awaited_once()
    assert "callback.edit" in caplog.text


@pytest.mark.asyncio
async def test_help_dev_section_uses_panel_callback_edit(caplog):
    caplog.set_level(logging.INFO, logger="callback_trace")
    bot = _RecorderBot()
    help_center.register(bot, None)
    handler = _handler_by_name(bot, "help_route_legacy")
    query = _query(CB["HELP_DEV_PANEL"], chat_id=-100123, chat_type="supergroup")

    with (
        patch("app.handlers.help_center._get_role", AsyncMock(return_value="developer")),
        patch("app.services.panel_message_service.get_redis", AsyncMock()),
    ):
        await handler(_developer_client(), query)

    query.answer.assert_awaited()
    query.message.edit_text.assert_awaited_once()
    assert "callback.edit" in caplog.text

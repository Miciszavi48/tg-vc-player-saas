"""Regression tests for group panel nav:back / wz:home navigation."""
from __future__ import annotations

import os
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

os.environ.setdefault("TEST_MODE", "1")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./.pytest-test-mode.db")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/15")
os.environ.setdefault("BOT_TOKEN", "test")
os.environ.setdefault("API_ID", "12345")
os.environ.setdefault("API_HASH", "testhash")
os.environ.setdefault("DEVELOPER_ID", "123456789")


class _RecorderBot:
    def __init__(self) -> None:
        self.callback_handlers: list[tuple] = []

    def on_callback_query(self, *args, **kwargs):  # noqa: ANN002, ANN003
        def _decorator(fn):
            self.callback_handlers.append((fn, args, kwargs))
            return fn

        return _decorator

    def on_message(self, *args, **kwargs):  # noqa: ANN002, ANN003
        def _decorator(fn):
            return fn

        return _decorator

    def __getattr__(self, name: str):
        if name.startswith("on_"):
            def _register_other(*args, **kwargs):  # noqa: ANN002, ANN003
                def _decorator(fn):
                    return fn

                return _decorator

            return _register_other
        raise AttributeError(name)


def _handler_by_name(handlers: list[tuple], name: str):
    for fn, _args, _kwargs in handlers:
        if fn.__name__ == name:
            return fn
    raise AssertionError(f"handler not found: {name}")


def _group_query(data: str, *, user_id: int = 42, chat_id: int = -100123):
    message = SimpleNamespace(
        chat=SimpleNamespace(id=chat_id, type=SimpleNamespace(value="supergroup")),
        edit_text=AsyncMock(),
        text="settings panel",
        caption=None,
    )
    return SimpleNamespace(
        data=data,
        from_user=SimpleNamespace(id=user_id),
        message=message,
        answer=AsyncMock(),
        stop_propagation=MagicMock(),
    )


def test_group_panel_registers_priority_nav_handlers():
    from app.handlers import group_panel

    bot = _RecorderBot()
    group_panel.register(bot, None)
    names = {fn.__name__ for fn, _a, _k in bot.callback_handlers}
    assert "grp_nav_back" in names
    assert "grp_wz_home" in names

    grp_back_entry = next(item for item in bot.callback_handlers if item[0].__name__ == "grp_nav_back")
    assert grp_back_entry[2].get("group") == -850


@pytest.mark.asyncio
async def test_grp_nav_back_edits_to_group_root(caplog):
    from app.handlers import group_panel
    from app.utils.i18n import t
    from app.utils.ui import CB, KeyboardFactory

    caplog.set_level(logging.INFO, logger="callback_trace")
    bot = _RecorderBot()
    group_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "grp_nav_back")
    query = _group_query(CB["NAV_BACK"])

    with (
        patch(
            "app.utils.player_permissions.can_open_group_panel",
            AsyncMock(return_value=True),
        ),
        patch(
            "app.handlers.group_panel.safe_edit_navigation_message",
            AsyncMock(return_value=True),
        ) as edit_mock,
    ):
        await handler(SimpleNamespace(), query)

    edit_mock.assert_awaited_once()
    assert edit_mock.await_args.args[2] == t("fa", "panels.group.title")
    assert edit_mock.await_args.kwargs["reply_markup"] == KeyboardFactory.group_panel("fa")
    query.answer.assert_awaited_once()
    query.stop_propagation.assert_called_once()
    assert "nav_back.received" in caplog.text
    assert "handler=grp_nav_back" in caplog.text
    assert "route_type=group_root" in caplog.text
    assert "nav_back.edit_success" in caplog.text


@pytest.mark.asyncio
async def test_grp_nav_back_shows_alert_when_edit_fails():
    from app.handlers import group_panel
    from app.utils.i18n import t
    from app.utils.ui import CB

    bot = _RecorderBot()
    group_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "grp_nav_back")
    query = _group_query(CB["NAV_BACK"])

    with (
        patch(
            "app.utils.player_permissions.can_open_group_panel",
            AsyncMock(return_value=True),
        ),
        patch(
            "app.handlers.group_panel.safe_edit_navigation_message",
            AsyncMock(return_value=False),
        ),
    ):
        await handler(SimpleNamespace(), query)

    query.answer.assert_awaited_once()
    assert query.answer.await_args.kwargs.get("show_alert") is True
    assert t("fa", "common.errors.navigation_failed") in query.answer.await_args.args[0]


@pytest.mark.asyncio
async def test_grp_wz_home_survives_clear_runtime_failure():
    from app.handlers import group_panel
    from app.utils.ui import CB

    bot = _RecorderBot()
    group_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "grp_wz_home")
    query = _group_query(CB["WZ_HOME"])

    with (
        patch(
            "app.handlers.group_panel.clear_runtime_state",
            AsyncMock(side_effect=RuntimeError("redis down")),
        ),
        patch(
            "app.utils.player_permissions.can_open_group_panel",
            AsyncMock(return_value=True),
        ),
        patch(
            "app.handlers.group_panel.safe_edit_navigation_message",
            AsyncMock(return_value=True),
        ) as edit_mock,
    ):
        await handler(SimpleNamespace(), query)

    edit_mock.assert_awaited_once()
    query.answer.assert_awaited_once()
    query.stop_propagation.assert_called_once()

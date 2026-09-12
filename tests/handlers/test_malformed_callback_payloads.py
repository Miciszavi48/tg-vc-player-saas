from __future__ import annotations

import os
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock

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

from app.handlers import broadcast_panel, helper_panel, tv_radio
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


def _query(data: str):
    return SimpleNamespace(
        from_user=SimpleNamespace(id=123456789, first_name="Dev"),
        data=data,
        answer=AsyncMock(),
        message=SimpleNamespace(
            chat=SimpleNamespace(id=100, type=SimpleNamespace(value="private")),
            edit_text=AsyncMock(),
        ),
    )


def _callback_data_set(kb) -> set[str]:
    return {
        btn.callback_data
        for row in getattr(kb, "inline_keyboard", [])
        for btn in row
        if getattr(btn, "callback_data", None)
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("handler_name", "payload"),
    [
        ("on_tv_select", "pb:tv:@@@"),
        ("on_satellite_select", "pb:sat:"),
        ("on_radio_select", "pb:radio:invalid/id"),
    ],
)
async def test_tv_radio_handlers_reject_malformed_payloads(handler_name: str, payload: str):
    bot = _RecorderBot()
    tv_radio.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, handler_name)
    query = _query(payload)

    await handler(SimpleNamespace(), query)

    assert query.answer.await_count == 1
    query.message.edit_text.assert_awaited_once()
    kb = query.message.edit_text.await_args.kwargs["reply_markup"]
    cbs = _callback_data_set(kb)
    assert CB["NAV_BACK"] in cbs
    assert CB["WZ_HOME"] in cbs


@pytest.mark.asyncio
async def test_broadcast_invalid_detail_cancel_payloads_are_safely_answered():
    bot = _RecorderBot()
    broadcast_panel.register(bot, None)

    detail_invalid = _handler_by_name(bot.callback_handlers, "bc_detail_invalid")
    cancel_invalid = _handler_by_name(bot.callback_handlers, "bc_cancel_invalid")

    q_detail = _query(f"{CB['BC_DETAIL']}:bad")
    q_cancel = _query(f"{CB['BC_CANCEL']}:bad")

    await detail_invalid.__wrapped__(SimpleNamespace(), q_detail)
    await cancel_invalid.__wrapped__(SimpleNamespace(), q_cancel)

    assert q_detail.answer.await_count == 1
    assert q_cancel.answer.await_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("handler_name", "payload"),
    [
        ("hlp_detail", f"{CB['HLP_DETAIL_PREFIX']}0"),
        ("hlp_set_proxy_start", f"{CB['HLP_SET_PROXY_PREFIX']}0"),
        ("hlp_enable", f"{CB['HLP_ENABLE']}0"),
        ("hlp_disable", f"{CB['HLP_DISABLE']}0"),
        ("hlp_quarantine", f"{CB['HLP_QUARANTINE']}0"),
        ("hlp_unquarantine", f"{CB['HLP_UNQUARANTINE']}0"),
    ],
)
async def test_helper_zero_id_payloads_are_rejected_before_side_effects(
    handler_name: str,
    payload: str,
):
    bot = _RecorderBot()
    helper_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, handler_name)
    query = _query(payload)

    await handler(SimpleNamespace(), query)

    query.answer.assert_awaited_once_with(
        t("fa", "common.errors.invalid_callback"),
        show_alert=True,
    )
    query.message.edit_text.assert_not_awaited()

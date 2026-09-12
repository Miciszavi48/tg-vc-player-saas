from __future__ import annotations

import os
import re
import sys
from pathlib import Path
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

from app.handlers import dev_panel, group_panel, helper_panel, owner_panel, sudo_panel
from app.services.wizard_ui import TOKEN_DEV_MODERATION, TOKEN_OWNER_ROOT
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


def _group_query(user_id: int, data: str):
    return SimpleNamespace(
        from_user=SimpleNamespace(id=user_id, first_name="GroupAdmin"),
        data=data,
        answer=AsyncMock(),
        message=SimpleNamespace(
            chat=SimpleNamespace(id=-1001, type=SimpleNamespace(value="supergroup")),
            edit_text=AsyncMock(),
        ),
    )


@pytest.mark.asyncio
async def test_dev_blacklist_flow_has_navigation_buttons():
    bot = _RecorderBot()
    dev_panel.register(bot, None)

    handler = _handler_by_name(bot.callback_handlers, "dev_blacklist")
    invoke = getattr(handler, "__wrapped__", handler)

    query = _pm_query(123456789, CB["DEV_BLACKLIST"])
    client = SimpleNamespace(
        stop_listening=AsyncMock(),
        ask=AsyncMock(return_value=SimpleNamespace(text="--12345")),
        send_message=AsyncMock(),
    )

    with (
        patch("app.handlers.dev_panel.remember_return_token", AsyncMock()),
        patch(
            "app.handlers.dev_panel.blacklist_repo.get_blacklist",
            AsyncMock(return_value=[SimpleNamespace(entity_type="user", entity_id=12345)]),
        ),
        patch("app.handlers.dev_panel.blacklist_repo.remove_from_blacklist", AsyncMock()) as rm_mock,
    ):
        await invoke(client, query)

    assert query.answer.await_count == 1
    assert query.message.edit_text.await_count == 1
    assert rm_mock.await_count == 1
    assert rm_mock.await_args.args[0] == -12345
    assert rm_mock.await_args.args[1] == "group"

    kb = query.message.edit_text.call_args.kwargs["reply_markup"]
    cbs = _kb_callbacks(kb)
    assert f"{CB['WZ_BACK_PREFIX']}{TOKEN_DEV_MODERATION}" in cbs
    assert CB["WZ_HOME"] in cbs


@pytest.mark.asyncio
async def test_owner_blacklist_flow_denies_global_only_route():
    bot = _RecorderBot()
    owner_panel.register(bot, None)

    handler = _handler_by_name(bot.callback_handlers, "own_blacklist")
    invoke = getattr(handler, "__wrapped__", handler)

    query = _pm_query(900001, CB["OWN_BLACKLIST"])
    client = SimpleNamespace(stop_listening=AsyncMock(), ask=AsyncMock(), send_message=AsyncMock())

    with (
        patch("app.handlers.owner_panel.is_developer", return_value=False),
        patch("app.handlers.owner_panel.blacklist_repo.add_to_blacklist", AsyncMock()) as add_mock,
    ):
        await invoke(client, query)

    assert query.answer.await_count == 1
    assert query.message.edit_text.await_count == 1
    assert client.ask.await_count == 0
    assert add_mock.await_count == 0

    kb = query.message.edit_text.call_args.kwargs["reply_markup"]
    cbs = _kb_callbacks(kb)
    assert f"{CB['WZ_BACK_PREFIX']}{TOKEN_OWNER_ROOT}" in cbs
    assert CB["WZ_HOME"] in cbs


@pytest.mark.asyncio
async def test_sudo_panel_stats_callback_answers_and_edits():
    bot = _RecorderBot()
    sudo_panel.register(bot, None)

    handler = _handler_by_name(bot.callback_handlers, "sudo_stats")
    invoke = getattr(handler, "__wrapped__", handler)

    logs = [
        SimpleNamespace(chat_type="group", action="install"),
        SimpleNamespace(chat_type="channel", action="install"),
    ]
    query = _pm_query(700001, CB["SUDO_STATS"])

    with patch("app.handlers.sudo_panel.log_repo.get_install_logs", AsyncMock(return_value=logs)):
        await invoke(SimpleNamespace(), query)

    assert query.answer.await_count == 1
    assert query.message.edit_text.await_count == 1


@pytest.mark.asyncio
async def test_group_management_page_has_summary_and_keyboard():
    bot = _RecorderBot()
    group_panel.register(bot, None)

    handler = _handler_by_name(bot.callback_handlers, "grp_management")
    invoke = getattr(handler, "__wrapped__", handler)

    query = _group_query(600001, CB["GRP_MANAGEMENT"])

    with patch(
        "app.handlers.group_panel._build_group_management_summary",
        AsyncMock(return_value="management summary"),
    ):
        await invoke(SimpleNamespace(), query)

    assert query.answer.await_count == 1
    assert query.message.edit_text.await_count == 1
    assert query.message.edit_text.call_args.args[0] == "management summary"

    kb = query.message.edit_text.call_args.kwargs["reply_markup"]
    cbs = _kb_callbacks(kb)
    assert CB["GRP_OWNERS_LIST"] in cbs
    assert CB["GRP_ADMINS_LIST"] in cbs
    assert CB["GRP_VIP_LIST"] in cbs


def test_keyboard_callbacks_have_handler_coverage():
    ui_text = Path("app/utils/ui.py").read_text(encoding="utf-8")
    ui_body = ui_text[ui_text.find("class KeyboardFactory") :]
    keyboard_keys = set(re.findall(r'CB\["([A-Z0-9_]+)"\]', ui_body)) | set(
        re.findall(r"CB\['([A-Z0-9_]+)'\]", ui_body)
    )

    handler_keys: set[str] = set()
    for path in Path("app/handlers").glob("*.py"):
        txt = path.read_text(encoding="utf-8")
        handler_keys |= set(re.findall(r'CB\["([A-Z0-9_]+)"\]', txt))
        handler_keys |= set(re.findall(r"CB\['([A-Z0-9_]+)'\]", txt))

    missing = keyboard_keys - handler_keys

    dynamic_helper_keys = {
        "HLP_SET_PROXY_PREFIX",
        "HLP_ENABLE",
        "HLP_DISABLE",
        "HLP_QUARANTINE",
        "HLP_UNQUARANTINE",
    }
    centralized_dispatch_keys: set[str] = set()
    playback_dispatcher = Path("app/services/playback_callback_dispatcher.py").read_text(encoding="utf-8")
    if "CALLBACK_TO_ACTION" in playback_dispatcher:
        centralized_dispatch_keys |= {
            key for key in keyboard_keys
            if key.startswith("PB_") and CB[key] in playback_dispatcher
        }
    help_center = Path("app/handlers/help_center.py").read_text(encoding="utf-8")
    if "handle_help_callback" in help_center and "_register_help_callback_routes" in help_center:
        centralized_dispatch_keys |= {key for key in keyboard_keys if key.startswith("HELP_")}

    # Dynamic helper callbacks are consumed via literal regex routes.
    assert missing <= dynamic_helper_keys | centralized_dispatch_keys

    helper_text = Path("app/handlers/helper_panel.py").read_text(encoding="utf-8")
    assert r"^hlp:proxy:\d+$" in helper_text
    assert r"^hlp:en:\d+$" in helper_text
    assert r"^hlp:dis:\d+$" in helper_text
    assert r"^hlp:q:\d+$" in helper_text
    assert r"^hlp:uq:\d+$" in helper_text


def test_helper_panel_registers_dynamic_callbacks():
    bot = _RecorderBot()
    helper_panel.register(bot, None)

    names = {fn.__name__ for fn in bot.callback_handlers}
    assert "hlp_set_proxy_start" in names
    assert "hlp_enable" in names
    assert "hlp_disable" in names
    assert "hlp_quarantine" in names
    assert "hlp_unquarantine" in names

"""Reachability and install correctness fixes (sudo links, broadcast cancel UI, blacklist)."""
from __future__ import annotations

import importlib.util
import sys
import time
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.utils.ui import CB, KeyboardFactory


def _callback_data_set(kb) -> set[str]:
    return {
        btn.callback_data
        for row in getattr(kb, "inline_keyboard", [])
        for btn in row
        if getattr(btn, "callback_data", None)
    }


def _load_install_module():
    spec = importlib.util.spec_from_file_location(
        "reachability_install_under_test",
        Path("app/handlers/install.py"),
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class _RecorderBot:
    def __init__(self) -> None:
        self.callback_handlers: list = []

    def on_callback_query(self, *args, **kwargs):  # noqa: ANN001, ANN002
        def _decorator(fn):
            self.callback_handlers.append(fn)
            return fn

        return _decorator

    def on_message(self, *args, **kwargs):  # noqa: ANN001, ANN002
        def _decorator(fn):
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


def _query(data: str, user_id: int = 123456789):
    return SimpleNamespace(
        from_user=SimpleNamespace(id=user_id, first_name="Dev"),
        data=data,
        answer=AsyncMock(),
        message=SimpleNamespace(
            chat=SimpleNamespace(id=100, type=SimpleNamespace(value="private")),
            edit_text=AsyncMock(),
        ),
    )


# ── Issue 1: dev:sudo:link menu reachability ───────────────────────────────


def test_sudo_link_menu_hidden_from_dev_sub_users():
    callbacks = _callback_data_set(KeyboardFactory.dev_sub_users("en"))
    assert CB["DEV_SUDO_LINK_MENU"] not in callbacks


def test_sudo_link_panel_exposes_set_remove_list_actions():
    callbacks = _callback_data_set(KeyboardFactory.sudo_link_panel("en"))
    assert callbacks == {
        CB["DEV_SUDO_LINK_SET"],
        CB["DEV_SUDO_LINK_RM"],
        CB["DEV_SUDO_LINK_LIST"],
        CB["NAV_BACK"],
    }


@pytest.mark.asyncio
async def test_dev_sudo_link_menu_stale_callback_shows_removed_notice():
    if "pyromod" not in sys.modules:
        pyromod_module = ModuleType("pyromod")
        pyromod_exceptions = ModuleType("pyromod.exceptions")
        pyromod_exceptions.ListenerStopped = Exception
        pyromod_module.exceptions = pyromod_exceptions
        sys.modules["pyromod"] = pyromod_module
        sys.modules["pyromod.exceptions"] = pyromod_exceptions

    from app.handlers import dev_panel

    bot = _RecorderBot()
    dev_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "dev_sudo_link_menu")
    query = _query(CB["DEV_SUDO_LINK_MENU"])

    await handler.__wrapped__(SimpleNamespace(), query)

    query.answer.assert_awaited_once()
    assert query.answer.await_args.kwargs.get("show_alert") is True
    query.message.edit_text.assert_not_awaited()


@pytest.mark.asyncio
async def test_dev_sudo_link_menu_rejects_non_developer():
    if "pyromod" not in sys.modules:
        pyromod_module = ModuleType("pyromod")
        pyromod_exceptions = ModuleType("pyromod.exceptions")
        pyromod_exceptions.ListenerStopped = Exception
        pyromod_module.exceptions = pyromod_exceptions
        sys.modules["pyromod"] = pyromod_module
        sys.modules["pyromod.exceptions"] = pyromod_exceptions

    from app.handlers import dev_panel

    bot = _RecorderBot()
    dev_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "dev_sudo_link_menu")
    query = _query(CB["DEV_SUDO_LINK_MENU"], user_id=999999)

    with patch("app.utils.decorators.is_developer", return_value=False):
        await handler(SimpleNamespace(), query)

    query.answer.assert_awaited_once()
    assert query.answer.await_args.kwargs.get("show_alert") is True


# ── Issue 2: broadcast cancel UI exposure ──────────────────────────────────


@pytest.mark.parametrize("status", ["pending", "running"])
def test_broadcast_history_shows_cancel_for_cancelable_status(status: str):
    bc = SimpleNamespace(
        id=7,
        target_scope="users",
        status=status,
        sent_count=0,
        total_recipients=5,
    )
    callbacks = _callback_data_set(KeyboardFactory.broadcast_history("en", [bc]))
    assert f"{CB['BC_DETAIL']}:7" in callbacks
    assert f"{CB['BC_CANCEL']}:7" in callbacks


@pytest.mark.parametrize("status", ["done", "failed", "canceled", "completed"])
def test_broadcast_history_hides_cancel_for_terminal_status(status: str):
    bc = SimpleNamespace(
        id=8,
        target_scope="users",
        status=status,
        sent_count=10,
        total_recipients=10,
    )
    callbacks = _callback_data_set(KeyboardFactory.broadcast_history("en", [bc]))
    assert f"{CB['BC_DETAIL']}:8" in callbacks
    assert f"{CB['BC_CANCEL']}:8" not in callbacks


def test_broadcast_detail_shows_cancel_only_when_cancelable():
    pending = SimpleNamespace(id=3, status="pending")
    done = SimpleNamespace(id=4, status="done")

    pending_callbacks = _callback_data_set(KeyboardFactory.broadcast_detail("en", pending))
    done_callbacks = _callback_data_set(KeyboardFactory.broadcast_detail("en", done))

    assert f"{CB['BC_CANCEL']}:3" in pending_callbacks
    assert CB["BC_HISTORY"] in pending_callbacks
    assert f"{CB['BC_CANCEL']}:4" not in done_callbacks
    assert CB["BC_HISTORY"] in done_callbacks


@pytest.mark.asyncio
async def test_broadcast_cancel_confirm_still_works_from_exposed_button():
    if "pyromod" not in sys.modules:
        pyromod_module = ModuleType("pyromod")
        pyromod_exceptions = ModuleType("pyromod.exceptions")
        pyromod_exceptions.ListenerStopped = Exception
        pyromod_module.exceptions = pyromod_exceptions
        sys.modules["pyromod"] = pyromod_module
        sys.modules["pyromod.exceptions"] = pyromod_exceptions

    from app.handlers import broadcast_panel

    bot = _RecorderBot()
    broadcast_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "bc_cancel_confirm")
    query = _query(f"{CB['BC_CANCEL_CONFIRM_PREFIX']}9:123456789:{int(time.time())}")

    with (
        patch(
            "app.handlers.broadcast_panel.broadcast_repo.get_by_id",
            AsyncMock(return_value=SimpleNamespace(id=9, status="running")),
        ),
        patch("app.handlers.broadcast_panel.broadcast_repo.get_recent", AsyncMock(return_value=[])),
        patch("app.handlers.broadcast_panel.BroadcastServiceV2.cancel", AsyncMock()) as cancel_mock,
    ):
        await handler.__wrapped__(SimpleNamespace(), query)

    cancel_mock.assert_awaited_once_with(9)
    query.message.edit_text.assert_awaited_once()


# ── Issue 3: install blacklist entity type ───────────────────────────────────


@pytest.mark.asyncio
async def test_blacklisted_group_install_is_blocked(monkeypatch) -> None:
    install = _load_install_module()
    is_blacklisted = AsyncMock(side_effect=lambda _cid, entity_type: entity_type == "group")
    monkeypatch.setattr(install.blacklist_repo, "is_blacklisted", is_blacklisted)
    monkeypatch.setattr(install, "track_event", AsyncMock())
    client = SimpleNamespace(
        send_message=AsyncMock(),
        leave_chat=AsyncMock(),
    )

    await install._do_install(client, -1001, "Group", "group", 123)

    is_blacklisted.assert_awaited_once_with(-1001, "group")
    client.send_message.assert_awaited_once()
    client.leave_chat.assert_awaited_once_with(-1001)


@pytest.mark.asyncio
async def test_blacklisted_channel_install_is_blocked(monkeypatch) -> None:
    install = _load_install_module()
    is_blacklisted = AsyncMock(side_effect=lambda _cid, entity_type: entity_type == "channel")
    monkeypatch.setattr(install.blacklist_repo, "is_blacklisted", is_blacklisted)
    monkeypatch.setattr(install, "track_event", AsyncMock())
    client = SimpleNamespace(
        send_message=AsyncMock(),
        leave_chat=AsyncMock(),
    )

    await install._do_install(client, -1002, "Channel", "channel", 123)

    is_blacklisted.assert_awaited_once_with(-1002, "channel")
    client.send_message.assert_awaited_once()
    client.leave_chat.assert_awaited_once_with(-1002)


@pytest.mark.asyncio
async def test_non_blacklisted_channel_install_not_blocked_at_blacklist_step(monkeypatch) -> None:
    install = _load_install_module()
    is_blacklisted = AsyncMock(return_value=False)
    monkeypatch.setattr(install.blacklist_repo, "is_blacklisted", is_blacklisted)
    monkeypatch.setattr(install.settings, "MAX_CHANNEL_ADMINS", 0)
    monkeypatch.setattr(install.settings, "MAX_GROUP_MEMBERS", 0)
    monkeypatch.setattr(install.settings, "TRIAL_DAYS", 0)
    monkeypatch.setattr(install.InstallPolicyService, "determine_installer_role", AsyncMock(return_value="developer"))
    monkeypatch.setattr(install.InstallPolicyService, "compute_install_cost", AsyncMock(return_value=0))
    monkeypatch.setattr(install.channel_repo, "upsert_channel", AsyncMock())
    monkeypatch.setattr(install.settings_repo, "create_defaults", AsyncMock())
    monkeypatch.setattr(install, "track_event", AsyncMock())
    monkeypatch.setattr(install.CreditService, "activate_trial", AsyncMock(side_effect=ValueError("no trial")))
    monkeypatch.setattr(install.log_repo, "log_install", AsyncMock())
    monkeypatch.setattr(install.NotificationService, "notify_install", AsyncMock())
    monkeypatch.setattr(install, "get_guide_channel_link", AsyncMock(return_value=""))
    monkeypatch.setattr(install, "get_bot_channel_link", AsyncMock(return_value=""))
    monkeypatch.setattr(
        install.InstallPolicyService,
        "get_policy",
        AsyncMock(return_value=SimpleNamespace(policy_mode="open")),
    )
    client = SimpleNamespace(send_message=AsyncMock())

    await install._do_install(client, -1003, "Channel", "channel", 123)

    is_blacklisted.assert_awaited_once_with(-1003, "channel")
    client.send_message.assert_awaited()

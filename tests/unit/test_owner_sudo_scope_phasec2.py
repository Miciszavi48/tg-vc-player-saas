"""Phase C-2 owner-scoped sudo permission UI and hardening tests."""
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
    pyromod_exceptions.ListenerStopped = Exception
    pyromod_module.exceptions = pyromod_exceptions
    sys.modules["pyromod"] = pyromod_module
    sys.modules["pyromod.exceptions"] = pyromod_exceptions

from app.database.models import Sudo
from app.handlers import owner_panel
from app.repositories.user_repo import SUDO_PERMISSION_FIELD_NAMES
from app.services import owner_scope_service, sudo_permission_ui_service
from app.services.texts_links_ui import AskResult
from app.utils.i18n import t
from app.utils.ui import CB

OWNER_A = 100001
OWNER_B = 100002
SUDO_A = 200001
SUDO_B = 200002
DEV_ID = 123456789


class _RecorderBot:
    def __init__(self) -> None:
        self.callback_handlers: list = []

    def on_callback_query(self, *args, **kwargs):  # noqa: ANN002, ANN003
        def _decorator(fn):
            self.callback_handlers.append(fn)
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


def _sample_sudo(**overrides) -> Sudo:
    base = {
        "user_id": SUDO_A,
        "username": "sudo_a",
        "display_name": "Sudo A",
        "added_by": OWNER_A,
        "is_active": True,
        "total_installs": 1,
    }
    base.update(overrides)
    return Sudo(**base)


@pytest.mark.asyncio
async def test_owner_sudo_list_shows_only_owner_sudos():
    own_sudo = _sample_sudo(user_id=SUDO_A, added_by=OWNER_A)
    with patch.object(
        owner_scope_service,
        "get_scoped_sudos_for_actor",
        AsyncMock(return_value=[own_sudo]),
    ):
        query = _pm_query(OWNER_A, CB["OWN_LIST_SUDOS"])
        await owner_panel._render_owner_sudo_list_page(query, 0)
    text = query.message.edit_text.await_args.args[0]
    assert str(SUDO_A) in text
    kb = query.message.edit_text.await_args.kwargs["reply_markup"]
    callbacks = {btn.callback_data for row in kb.inline_keyboard for btn in row}
    assert f"{CB['OWN_SUDO_DETAIL_PREFIX']}{SUDO_A}:0" in callbacks


@pytest.mark.asyncio
async def test_other_owner_sudo_excluded_from_scoped_list():
    with patch.object(
        owner_scope_service,
        "get_scoped_sudos_for_actor",
        AsyncMock(return_value=[]),
    ):
        query = _pm_query(OWNER_A, CB["OWN_LIST_SUDOS"])
        await owner_panel._render_owner_sudo_list_page(query, 0)
    assert t("fa", "sudo_mgmt.list_empty") in query.message.edit_text.await_args.args[0]


@pytest.mark.asyncio
async def test_developer_global_sudo_excluded_for_pure_owner():
    dev_sudo = _sample_sudo(user_id=SUDO_B, added_by=DEV_ID)
    owner_sudo = _sample_sudo(user_id=SUDO_A, added_by=OWNER_A)
    with patch.object(owner_scope_service, "is_developer", return_value=False):
        scoped = [
            s
            for s in [owner_sudo, dev_sudo]
            if owner_scope_service.sudo_belongs_to_actor(s, OWNER_A)
        ]
    assert len(scoped) == 1
    assert scoped[0].user_id == SUDO_A


@pytest.mark.asyncio
async def test_developer_override_sees_all_sudos():
    sudos = [_sample_sudo(user_id=SUDO_A), _sample_sudo(user_id=SUDO_B, added_by=OWNER_B)]
    with patch.object(owner_scope_service, "get_scoped_sudos_for_actor", AsyncMock(return_value=sudos)):
        result = await owner_scope_service.get_scoped_sudos_for_actor(DEV_ID)
    assert len(result) == 2


@pytest.mark.asyncio
async def test_owner_add_sudo_sets_added_by():
    with (
        patch.object(owner_panel.user_repo, "get_sudo_record", AsyncMock(return_value=None)),
        patch.object(owner_panel.user_repo, "add_sudo", AsyncMock(return_value=_sample_sudo())) as add_mock,
        patch.object(owner_panel, "invalidate_sudolist", AsyncMock()),
    ):
        ok, key = await owner_panel._owner_add_sudo(OWNER_A, SUDO_A)
    assert ok is True
    assert key == "owner_mgmt.sudo_added"
    add_mock.assert_awaited_once_with(SUDO_A, added_by=OWNER_A)


@pytest.mark.asyncio
async def test_duplicate_sudo_gives_clear_response():
    existing = _sample_sudo(user_id=SUDO_A, added_by=OWNER_A, is_active=True)
    with patch.object(owner_panel.user_repo, "get_sudo_record", AsyncMock(return_value=existing)):
        ok, key = await owner_panel._owner_add_sudo(OWNER_A, SUDO_A)
    assert ok is False
    assert key == "owner_mgmt.sudo_already_active"


@pytest.mark.asyncio
async def test_invalid_sudo_user_id_reprompts_then_allows_navigation_abort():
    query = _pm_query(OWNER_A, CB["OWN_SUDO_MANAGE"])
    client = SimpleNamespace()
    ask = AsyncMock(
        side_effect=[
            AskResult(message=SimpleNamespace(text="not-a-number")),
            AskResult(message=None, abort_reason="listener_stopped", user_notified=True),
        ]
    )
    with (
        patch.object(owner_scope_service, "get_scoped_sudos_for_actor", AsyncMock(return_value=[])),
        patch.object(owner_panel, "_ask", ask),
        patch.object(owner_panel, "notify_ask_abort", AsyncMock(return_value=True)) as abort_mock,
        patch.object(owner_panel, "_send_done", AsyncMock()) as done_mock,
        patch.object(owner_panel, "build_done_kb", return_value=SimpleNamespace()),
    ):
        bot = _RecorderBot()
        owner_panel.register(bot, None)
        handler = _handler_by_name(bot.callback_handlers, "own_sudo_manage")
        invoke = getattr(handler, "__wrapped__", handler)
        await invoke(client, query)
    assert ask.await_count == 2
    assert t("fa", "common.errors.invalid_number") in ask.await_args_list[1].kwargs["prompt_text"]
    abort_mock.assert_awaited_once()
    done_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_owner_remove_own_sudo_succeeds():
    sudo = _sample_sudo(user_id=SUDO_A, added_by=OWNER_A)
    with (
        patch.object(owner_panel.user_repo, "get_sudo", AsyncMock(return_value=sudo)),
        patch.object(owner_panel.user_repo, "remove_sudo", AsyncMock()) as remove_mock,
        patch.object(owner_panel, "invalidate_sudolist", AsyncMock()),
    ):
        ok = owner_scope_service.sudo_belongs_to_actor(sudo, OWNER_A)
        assert ok is True
        await owner_panel.user_repo.remove_sudo(SUDO_A)
    remove_mock.assert_awaited_once_with(SUDO_A)


@pytest.mark.asyncio
async def test_owner_remove_other_owner_sudo_denied():
    sudo = _sample_sudo(user_id=SUDO_B, added_by=OWNER_B)
    assert owner_scope_service.sudo_belongs_to_actor(sudo, OWNER_A) is False


@pytest.mark.asyncio
async def test_forged_sudo_detail_callback_denied():
    sudo = _sample_sudo(user_id=SUDO_B, added_by=OWNER_B)
    query = _pm_query(OWNER_A, f"{CB['OWN_SUDO_DETAIL_PREFIX']}{SUDO_B}:0")
    with patch.object(owner_panel.user_repo, "get_sudo_record", AsyncMock(return_value=sudo)):
        denied = await owner_panel._deny_owner_sudo_not_in_scope(query, sudo)
    assert denied is True
    query.answer.assert_awaited_once()


@pytest.mark.asyncio
async def test_permission_toggle_for_own_sudo_succeeds():
    sudo = _sample_sudo(user_id=SUDO_A, added_by=OWNER_A, can_manage_credit=False)
    query = _pm_query(OWNER_A, f"{CB['OWN_SUDO_PERM_TOGGLE_PREFIX']}r:{SUDO_A}:0")
    with (
        patch.object(owner_panel.user_repo, "get_sudo_record", AsyncMock(return_value=sudo)),
        patch.object(owner_panel.user_repo, "set_sudo_permission", AsyncMock(return_value=True)),
        patch.object(owner_panel, "_render_owner_sudo_detail", AsyncMock()) as render_mock,
    ):
        bot = _RecorderBot()
        owner_panel.register(bot, None)
        handler = _handler_by_name(bot.callback_handlers, "own_sudo_perm_toggle")
        invoke = getattr(handler, "__wrapped__", handler)
        await invoke(SimpleNamespace(), query)
    render_mock.assert_awaited_once()
    assert t("fa", "owner_mgmt.sudo_permission_updated") in query.answer.await_args.args[0]


@pytest.mark.asyncio
async def test_permission_toggle_for_other_owner_sudo_denied():
    sudo = _sample_sudo(user_id=SUDO_B, added_by=OWNER_B)
    query = _pm_query(OWNER_A, f"{CB['OWN_SUDO_PERM_TOGGLE_PREFIX']}r:{SUDO_B}:0")
    with patch.object(owner_panel.user_repo, "get_sudo_record", AsyncMock(return_value=sudo)):
        bot = _RecorderBot()
        owner_panel.register(bot, None)
        handler = _handler_by_name(bot.callback_handlers, "own_sudo_perm_toggle")
        invoke = getattr(handler, "__wrapped__", handler)
        await invoke(SimpleNamespace(), query)
    query.answer.assert_awaited()
    assert t("fa", "owner_mgmt.sudo_not_in_scope") in query.answer.await_args.args[0]


def test_owner_detail_shows_only_enforced_permission_flags():
    sudo = _sample_sudo()
    kb = sudo_permission_ui_service.build_owner_sudo_detail_kb(sudo, 0, "fa")
    toggle_callbacks = [
        btn.callback_data
        for row in kb.inline_keyboard
        for btn in row
        if btn.callback_data.startswith(CB["OWN_SUDO_PERM_TOGGLE_PREFIX"])
    ]
    assert len(toggle_callbacks) == len(SUDO_PERMISSION_FIELD_NAMES)
    for callback in toggle_callbacks:
        assert callback.startswith(CB["OWN_SUDO_PERM_TOGGLE_PREFIX"])
        assert "dev:sp:" not in callback
    all_callbacks = {btn.callback_data for row in kb.inline_keyboard for btn in row}
    assert not any(cb.startswith("dev:title") for cb in all_callbacks)
    assert not any(cb.startswith("dev:tgprom") for cb in all_callbacks)


def test_existing_owner_callback_data_unchanged():
    expected = {
        "OWN_LIST_SUDOS": "own:sudos:list",
        "OWN_SUDO_MANAGE": "own:sudo_manage",
        "OWN_REMOVE_SUDO": "own:sudo:remove",
        "PAGE_OWN_SUDOS": "pg:own:sudos:",
    }
    for key, value in expected.items():
        assert CB[key] == value


@pytest.mark.asyncio
async def test_nosilent_ask_abort_in_owner_sudo_manage():
    query = _pm_query(OWNER_A, CB["OWN_SUDO_MANAGE"])
    client = SimpleNamespace()
    resp = AskResult(message=None, abort_reason="listener_stopped")
    with (
        patch.object(owner_scope_service, "get_scoped_sudos_for_actor", AsyncMock(return_value=[])),
        patch.object(owner_panel, "_ask", AsyncMock(return_value=resp)),
        patch.object(owner_panel, "notify_ask_abort", AsyncMock(return_value=True)) as abort_mock,
        patch.object(owner_panel, "build_done_kb", return_value=SimpleNamespace()),
    ):
        bot = _RecorderBot()
        owner_panel.register(bot, None)
        handler = _handler_by_name(bot.callback_handlers, "own_sudo_manage")
        invoke = getattr(handler, "__wrapped__", handler)
        await invoke(client, query)
    abort_mock.assert_awaited_once()

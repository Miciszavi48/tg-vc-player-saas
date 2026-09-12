"""Phase 2: Developer toggles for sudo permission matrix."""
from __future__ import annotations

import os
import sys
import time
from datetime import datetime, timezone
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

    class _ListenerStopped(Exception):
        pass

    pyromod_exceptions.ListenerStopped = _ListenerStopped
    pyromod_module.exceptions = pyromod_exceptions
    sys.modules["pyromod"] = pyromod_module
    sys.modules["pyromod.exceptions"] = pyromod_exceptions

from app.config.settings import settings
from app.database.models import Sudo
from app.handlers import dev_panel
from app.repositories import user_repo
from app.repositories.user_repo import (
    SUDO_PERMISSION_CALLBACK_KEYS,
    SUDO_PERMISSION_FIELD_NAMES,
    resolve_sudo_permission_field,
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


def _kb_button_texts(kb) -> list[str]:
    return [btn.text for row in kb.inline_keyboard for btn in row]


def _pm_query(user_id: int, data: str = "dummy"):
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
    base = dict(
        user_id=90001,
        username="sudo_user",
        display_name="Sudo One",
        added_by=settings.DEVELOPER_ID,
        added_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        is_active=True,
        sudo_link=None,
        total_installs=3,
        can_manage_groups=True,
        can_manage_channels=True,
        can_manage_credit=True,
        can_remove_bot=True,
        can_manage_chat_settings=True,
        auto_admin_bypass=True,
    )
    base.update(overrides)
    return Sudo(**base)


def _toggle_cb(key: str, target: int = 90001, page: int = 1) -> str:
    return f"{CB['DEV_SUDO_PERM_TOGGLE_PREFIX']}{key}:{target}:{page}"


def _confirm_do_cb(key: str, target: int, page: int, actor: int, issued_at: int) -> str:
    return f"{CB['DEV_SUDO_PERM_DO_PREFIX']}{key}:{target}:{page}:{actor}:{issued_at}"


def _confirm_no_cb(key: str, target: int, page: int, actor: int, issued_at: int) -> str:
    return f"{CB['DEV_SUDO_PERM_NO_PREFIX']}{key}:{target}:{page}:{actor}:{issued_at}"


@pytest.mark.asyncio
async def test_sudo_detail_shows_toggle_buttons_for_all_permissions():
    bot = _RecorderBot()
    dev_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "dev_sudo_detail")
    query = _pm_query(settings.DEVELOPER_ID, f"{CB['DEV_SUDO_DETAIL_PREFIX']}90001:0")
    row = _sample_sudo()

    with patch("app.handlers.dev_panel.user_repo.get_sudo_record", AsyncMock(return_value=row)):
        await handler.__wrapped__(SimpleNamespace(), query)

    cbs = _kb_callbacks(query.message.edit_text.call_args.kwargs["reply_markup"])
    for key in SUDO_PERMISSION_CALLBACK_KEYS:
        assert _toggle_cb(key, page=0) in cbs


@pytest.mark.asyncio
async def test_toggle_button_labels_reflect_enabled_state():
    bot = _RecorderBot()
    dev_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "dev_sudo_detail")
    query = _pm_query(settings.DEVELOPER_ID, f"{CB['DEV_SUDO_DETAIL_PREFIX']}90001:0")
    row = _sample_sudo(can_manage_credit=False)

    with patch("app.handlers.dev_panel.user_repo.get_sudo_record", AsyncMock(return_value=row)):
        await handler.__wrapped__(SimpleNamespace(), query)

    texts = _kb_button_texts(query.message.edit_text.call_args.kwargs["reply_markup"])
    assert t("fa", "sudo_permissions.toggle_enable_btn", label=t("fa", "sudo_permissions.perm_credit")) in texts
    assert t("fa", "sudo_permissions.toggle_disable_btn", label=t("fa", "sudo_permissions.perm_groups")) in texts


@pytest.mark.asyncio
async def test_back_to_sudo_list_still_works_from_detail():
    bot = _RecorderBot()
    dev_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "dev_sudo_detail")
    query = _pm_query(settings.DEVELOPER_ID, f"{CB['DEV_SUDO_DETAIL_PREFIX']}90001:3")
    row = _sample_sudo()

    with patch("app.handlers.dev_panel.user_repo.get_sudo_record", AsyncMock(return_value=row)):
        await handler.__wrapped__(SimpleNamespace(), query)

    cbs = _kb_callbacks(query.message.edit_text.call_args.kwargs["reply_markup"])
    assert f"{CB['DEV_SUDO_LIST_BACK_PREFIX']}3" in cbs


@pytest.mark.asyncio
async def test_enabling_disabled_permission_updates_db_immediately():
    bot = _RecorderBot()
    dev_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "dev_sudo_perm_toggle")
    query = _pm_query(settings.DEVELOPER_ID, _toggle_cb("r"))
    row = _sample_sudo(can_manage_credit=False)
    set_mock = AsyncMock(return_value=True)

    with (
        patch("app.handlers.dev_panel.user_repo.get_sudo_record", AsyncMock(return_value=row)),
        patch("app.handlers.dev_panel.user_repo.set_sudo_permission", set_mock),
        patch("app.handlers.dev_panel._render_sudo_detail", AsyncMock()) as render_mock,
    ):
        await handler.__wrapped__(SimpleNamespace(), query)

    set_mock.assert_awaited_once_with(90001, "can_manage_credit", True)
    render_mock.assert_awaited_once()
    assert t("fa", "sudo_permissions.enabled_notice") in query.answer.await_args.args[0]


@pytest.mark.asyncio
async def test_disabling_permission_shows_confirmation_without_db_update():
    bot = _RecorderBot()
    dev_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "dev_sudo_perm_toggle")
    query = _pm_query(settings.DEVELOPER_ID, _toggle_cb("g"))
    row = _sample_sudo()
    set_mock = AsyncMock(return_value=True)

    with (
        patch("app.handlers.dev_panel.user_repo.get_sudo_record", AsyncMock(return_value=row)),
        patch("app.handlers.dev_panel.user_repo.set_sudo_permission", set_mock),
    ):
        await handler.__wrapped__(SimpleNamespace(), query)

    set_mock.assert_not_awaited()
    text = query.message.edit_text.call_args.args[0]
    assert t("fa", "sudo_permissions.disable_confirm_prompt", label=t("fa", "sudo_permissions.perm_groups"), user_id=90001) in text
    cbs = _kb_callbacks(query.message.edit_text.call_args.kwargs["reply_markup"])
    assert any(cb.startswith(CB["DEV_SUDO_PERM_DO_PREFIX"]) for cb in cbs)


@pytest.mark.asyncio
async def test_confirm_disabling_updates_db_and_refreshes_detail():
    bot = _RecorderBot()
    dev_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "dev_sudo_perm_confirm")
    issued_at = int(time.time())
    query = _pm_query(
        settings.DEVELOPER_ID,
        _confirm_do_cb("g", 90001, 2, settings.DEVELOPER_ID, issued_at),
    )
    row = _sample_sudo()
    set_mock = AsyncMock(return_value=True)

    with (
        patch("app.handlers.dev_panel.user_repo.get_sudo_record", AsyncMock(return_value=row)),
        patch("app.handlers.dev_panel.user_repo.set_sudo_permission", set_mock),
        patch("app.handlers.dev_panel._render_sudo_detail", AsyncMock()) as render_mock,
    ):
        await handler.__wrapped__(SimpleNamespace(), query)

    set_mock.assert_awaited_once_with(90001, "can_manage_groups", False)
    render_mock.assert_awaited_once_with(query, 90001, 2)


@pytest.mark.asyncio
async def test_cancel_disabling_does_not_update_db():
    bot = _RecorderBot()
    dev_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "dev_sudo_perm_abort")
    issued_at = int(time.time())
    query = _pm_query(
        settings.DEVELOPER_ID,
        _confirm_no_cb("g", 90001, 2, settings.DEVELOPER_ID, issued_at),
    )
    set_mock = AsyncMock(return_value=True)

    with (
        patch("app.handlers.dev_panel.user_repo.set_sudo_permission", set_mock),
        patch("app.handlers.dev_panel._render_sudo_detail", AsyncMock()) as render_mock,
    ):
        await handler.__wrapped__(SimpleNamespace(), query)

    set_mock.assert_not_awaited()
    render_mock.assert_awaited_once_with(query, 90001, 2)


@pytest.mark.asyncio
async def test_non_developer_cannot_toggle_permission():
    bot = _RecorderBot()
    dev_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "dev_sudo_perm_toggle")
    query = _pm_query(99999, _toggle_cb("g"))
    set_mock = AsyncMock(return_value=True)

    with patch("app.handlers.dev_panel.user_repo.set_sudo_permission", set_mock):
        result = await handler(SimpleNamespace(), query)

    assert result is None
    set_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_sudo_cannot_toggle_own_permissions():
    bot = _RecorderBot()
    dev_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "dev_sudo_perm_toggle")
    query = _pm_query(90001, _toggle_cb("g", target=90001))
    set_mock = AsyncMock(return_value=True)

    with patch("app.handlers.dev_panel.user_repo.set_sudo_permission", set_mock):
        result = await handler(SimpleNamespace(), query)

    assert result is None
    set_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_wrong_user_confirm_rejected():
    bot = _RecorderBot()
    dev_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "dev_sudo_perm_confirm")
    issued_at = int(time.time())
    query = _pm_query(
        settings.DEVELOPER_ID,
        _confirm_do_cb("g", 90001, 0, 11111, issued_at),
    )
    set_mock = AsyncMock(return_value=True)

    with patch("app.handlers.dev_panel.user_repo.set_sudo_permission", set_mock):
        await handler.__wrapped__(SimpleNamespace(), query)

    set_mock.assert_not_awaited()
    assert query.answer.await_args.args[0] == t("fa", "common.errors.no_access")


@pytest.mark.asyncio
async def test_stale_confirm_rejected():
    bot = _RecorderBot()
    dev_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "dev_sudo_perm_confirm")
    stale_ts = int(time.time()) - 400
    query = _pm_query(
        settings.DEVELOPER_ID,
        _confirm_do_cb("g", 90001, 0, settings.DEVELOPER_ID, stale_ts),
    )
    set_mock = AsyncMock(return_value=True)

    with patch("app.handlers.dev_panel.user_repo.set_sudo_permission", set_mock):
        await handler.__wrapped__(SimpleNamespace(), query)

    set_mock.assert_not_awaited()
    assert t("fa", "sudo_permissions.stale_confirm") in query.answer.await_args.args[0]


@pytest.mark.asyncio
async def test_malformed_toggle_callback_rejected():
    bot = _RecorderBot()
    dev_panel.register(bot, None)
    toggle = _handler_by_name(bot.callback_handlers, "dev_sudo_perm_toggle")
    malformed = _handler_by_name(bot.callback_handlers, "dev_sudo_perm_malformed")
    query = _pm_query(settings.DEVELOPER_ID, "dev:sp:t:x:90001:0")

    await toggle.__wrapped__(SimpleNamespace(), query)
    assert t("fa", "sudo_permissions.invalid_permission") in query.answer.await_args.args[0]

    query2 = _pm_query(settings.DEVELOPER_ID, "dev:sp:t:bad")
    await malformed.__wrapped__(SimpleNamespace(), query2)
    assert t("fa", "sudo_permissions.invalid_permission") in query2.answer.await_args.args[0]


@pytest.mark.asyncio
async def test_unknown_permission_field_rejected_on_confirm():
    bot = _RecorderBot()
    dev_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "dev_sudo_perm_confirm")
    issued_at = int(time.time())
    query = _pm_query(
        settings.DEVELOPER_ID,
        f"{CB['DEV_SUDO_PERM_DO_PREFIX']}z:90001:0:{settings.DEVELOPER_ID}:{issued_at}",
    )

    await handler.__wrapped__(SimpleNamespace(), query)
    assert t("fa", "sudo_permissions.invalid_permission") in query.answer.await_args.args[0]


@pytest.mark.asyncio
async def test_missing_sudo_on_toggle_shows_not_found():
    bot = _RecorderBot()
    dev_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "dev_sudo_perm_toggle")
    query = _pm_query(settings.DEVELOPER_ID, _toggle_cb("g", target=99999))

    with patch("app.handlers.dev_panel.user_repo.get_sudo_record", AsyncMock(return_value=None)):
        await handler.__wrapped__(SimpleNamespace(), query)

    assert "99999" in query.answer.await_args.args[0]


def test_resolve_sudo_permission_field_whitelist():
    assert resolve_sudo_permission_field("g") == "can_manage_groups"
    assert resolve_sudo_permission_field("can_manage_groups") == "can_manage_groups"
    assert resolve_sudo_permission_field("evil") is None


@pytest.mark.asyncio
async def test_set_sudo_permission_rejects_unknown_field():
    ok = await user_repo.set_sudo_permission(90001, "drop_table", True)
    assert ok is False


@pytest.mark.asyncio
async def test_set_sudo_permission_returns_false_for_missing_sudo():
    with patch("app.utils.cache.invalidate_role", AsyncMock()):
        ok = await user_repo.set_sudo_permission(888880001, "can_manage_groups", False)
    assert ok is False


@pytest.mark.asyncio
async def test_set_sudo_permission_updates_only_selected_field():
    uid = 90555
    with patch("app.utils.cache.invalidate_role", AsyncMock()):
        await user_repo.add_sudo(uid)
        await user_repo.set_sudo_permission(uid, "can_manage_credit", False)
        await user_repo.set_sudo_permission(uid, "can_manage_groups", False)
    perms = await user_repo.get_sudo_permissions(uid)
    assert perms is not None
    assert perms["can_manage_credit"] is False
    assert perms["can_manage_groups"] is False
    assert perms["can_manage_channels"] is True
    await user_repo.remove_sudo(uid)

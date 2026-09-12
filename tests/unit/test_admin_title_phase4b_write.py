"""Phase 4B-write: Developer UI for stored admin title editing."""
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
from app.database.models import Owner, Sudo
from app.handlers import dev_panel
from app.utils.i18n import t
from app.utils.ui import CB, KeyboardFactory


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


def _client_with_ask(text: str):
    return SimpleNamespace(
        stop_listening=AsyncMock(),
        ask=AsyncMock(return_value=SimpleNamespace(text=text)),
        send_message=AsyncMock(),
    )


def _sample_sudo(**overrides) -> Sudo:
    base = dict(
        user_id=90001,
        username="sudo_user",
        display_name="Sudo One",
        added_by=settings.DEVELOPER_ID,
        added_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        is_active=True,
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


def test_developer_users_panel_has_admin_title_settings_entry():
    cbs = _kb_callbacks(KeyboardFactory.dev_sub_users("fa"))
    assert CB["DEV_ADMIN_TITLES"] in cbs


@pytest.mark.asyncio
async def test_admin_title_settings_shows_developer_title_and_note():
    bot = _RecorderBot()
    dev_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "dev_admin_titles")
    query = _pm_query(settings.DEVELOPER_ID, CB["DEV_ADMIN_TITLES"])

    with patch(
        "app.handlers.dev_panel.settings_repo.get_developer_admin_title",
        AsyncMock(return_value="Creator"),
    ):
        await handler.__wrapped__(SimpleNamespace(), query)

    text = query.message.edit_text.call_args.args[0]
    assert "Creator" in text
    assert t("fa", "admin_titles.note") in text


@pytest.mark.asyncio
async def test_sudo_detail_has_set_and_clear_title_buttons():
    bot = _RecorderBot()
    dev_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "dev_sudo_detail")
    query = _pm_query(settings.DEVELOPER_ID, f"{CB['DEV_SUDO_DETAIL_PREFIX']}90001:2")

    with (
        patch("app.handlers.dev_panel.user_repo.get_sudo_record", AsyncMock(return_value=_sample_sudo())),
        patch("app.handlers.dev_panel.settings_repo.get_developer_admin_title", AsyncMock(return_value=None)),
    ):
        await handler.__wrapped__(SimpleNamespace(), query)

    cbs = _kb_callbacks(query.message.edit_text.call_args.kwargs["reply_markup"])
    assert f"{CB['DEV_TITLE_SUDO_SET_PREFIX']}90001:2" in cbs
    assert f"{CB['DEV_TITLE_SUDO_CLEAR_PREFIX']}90001:2" in cbs


def test_normalize_admin_title_validation():
    assert dev_panel.normalize_admin_title("  لقب تست  ")[0] == "لقب تست"
    assert dev_panel.normalize_admin_title("")[1] == "admin_titles.invalid"
    assert dev_panel.normalize_admin_title("a" * 33)[1] == "admin_titles.too_long"
    assert dev_panel.normalize_admin_title("bad\nline")[1] == "admin_titles.invalid"
    assert dev_panel.normalize_admin_title("https://example.com")[1] == "admin_titles.invalid"


@pytest.mark.asyncio
async def test_developer_can_set_developer_title():
    bot = _RecorderBot()
    dev_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "dev_title_developer_set")
    query = _pm_query(settings.DEVELOPER_ID, CB["DEV_TITLE_DEV_SET"])
    client = _client_with_ask("Creator")

    with patch("app.handlers.dev_panel.settings_repo.set_bot_setting", AsyncMock()) as set_mock:
        await handler.__wrapped__(client, query)

    set_mock.assert_awaited_once_with(
        "developer_admin_title",
        "Creator",
        updated_by=settings.DEVELOPER_ID,
    )
    client.send_message.assert_awaited()


@pytest.mark.asyncio
async def test_developer_title_clear_first_click_does_not_clear():
    bot = _RecorderBot()
    dev_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "dev_title_developer_clear")
    query = _pm_query(settings.DEVELOPER_ID, CB["DEV_TITLE_DEV_CLEAR"])

    with patch("app.handlers.dev_panel.settings_repo.set_bot_setting", AsyncMock()) as set_mock:
        await handler.__wrapped__(SimpleNamespace(), query)

    set_mock.assert_not_awaited()
    cbs = _kb_callbacks(query.message.edit_text.call_args.kwargs["reply_markup"])
    assert any(cb.startswith(CB["DEV_TITLE_DEV_CLEAR_DO_PREFIX"]) for cb in cbs)


@pytest.mark.asyncio
async def test_developer_can_clear_developer_title_with_confirmation():
    bot = _RecorderBot()
    dev_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "dev_title_developer_clear_confirm")
    issued_at = int(time.time())
    query = _pm_query(
        settings.DEVELOPER_ID,
        f"{CB['DEV_TITLE_DEV_CLEAR_DO_PREFIX']}{settings.DEVELOPER_ID}:{issued_at}",
    )

    with (
        patch("app.handlers.dev_panel.settings_repo.set_bot_setting", AsyncMock()) as set_mock,
        patch("app.handlers.dev_panel.settings_repo.get_developer_admin_title", AsyncMock(return_value=None)),
    ):
        await handler.__wrapped__(SimpleNamespace(), query)

    set_mock.assert_awaited_once_with(
        "developer_admin_title",
        None,
        updated_by=settings.DEVELOPER_ID,
    )


@pytest.mark.asyncio
async def test_owner_title_list_has_set_and_clear_buttons():
    bot = _RecorderBot()
    dev_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "dev_title_owner_list")
    query = _pm_query(settings.DEVELOPER_ID, f"{CB['DEV_TITLE_OWNER_LIST_PREFIX']}0")
    owner = Owner(user_id=70001, username="owner", display_name="Owner", is_active=True)

    with patch("app.handlers.dev_panel.user_repo.get_all_owners", AsyncMock(return_value=[owner])):
        await handler.__wrapped__(SimpleNamespace(), query)

    cbs = _kb_callbacks(query.message.edit_text.call_args.kwargs["reply_markup"])
    assert f"{CB['DEV_TITLE_OWNER_SET_PREFIX']}70001:0" in cbs
    assert f"{CB['DEV_TITLE_OWNER_CLEAR_PREFIX']}70001:0" in cbs


@pytest.mark.asyncio
async def test_developer_can_set_owner_title():
    bot = _RecorderBot()
    dev_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "dev_title_owner_set")
    query = _pm_query(settings.DEVELOPER_ID, f"{CB['DEV_TITLE_OWNER_SET_PREFIX']}70001:0")
    client = _client_with_ask("Owner Captain")
    owner = Owner(user_id=70001, is_active=True)

    with (
        patch("app.handlers.dev_panel.user_repo.get_owner", AsyncMock(return_value=owner)),
        patch("app.handlers.dev_panel.user_repo.set_owner_admin_title", AsyncMock(return_value=True)) as set_mock,
    ):
        await handler.__wrapped__(client, query)

    set_mock.assert_awaited_once_with(70001, "Owner Captain")


@pytest.mark.asyncio
async def test_developer_can_clear_owner_title_with_confirmation():
    bot = _RecorderBot()
    dev_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "dev_title_owner_clear_confirm")
    issued_at = int(time.time())
    query = _pm_query(
        settings.DEVELOPER_ID,
        f"{CB['DEV_TITLE_OWNER_CLEAR_DO_PREFIX']}70001:0:{settings.DEVELOPER_ID}:{issued_at}",
    )

    with (
        patch("app.handlers.dev_panel.user_repo.set_owner_admin_title", AsyncMock(return_value=True)) as set_mock,
        patch("app.handlers.dev_panel.user_repo.get_all_owners", AsyncMock(return_value=[])),
    ):
        await handler.__wrapped__(SimpleNamespace(), query)

    set_mock.assert_awaited_once_with(70001, None)


@pytest.mark.asyncio
async def test_developer_can_set_sudo_title():
    bot = _RecorderBot()
    dev_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "dev_title_sudo_set")
    query = _pm_query(settings.DEVELOPER_ID, f"{CB['DEV_TITLE_SUDO_SET_PREFIX']}90001:2")
    client = _client_with_ask("Sudo Captain")

    with (
        patch("app.handlers.dev_panel.user_repo.get_sudo", AsyncMock(return_value=_sample_sudo())),
        patch("app.handlers.dev_panel.user_repo.set_sudo_admin_title", AsyncMock(return_value=True)) as set_mock,
    ):
        await handler.__wrapped__(client, query)

    set_mock.assert_awaited_once_with(90001, "Sudo Captain")


@pytest.mark.asyncio
async def test_developer_can_clear_sudo_title_with_confirmation():
    bot = _RecorderBot()
    dev_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "dev_title_sudo_clear_confirm")
    issued_at = int(time.time())
    query = _pm_query(
        settings.DEVELOPER_ID,
        f"{CB['DEV_TITLE_SUDO_CLEAR_DO_PREFIX']}90001:2:{settings.DEVELOPER_ID}:{issued_at}",
    )

    with (
        patch("app.handlers.dev_panel.user_repo.set_sudo_admin_title", AsyncMock(return_value=True)) as set_mock,
        patch("app.handlers.dev_panel._render_sudo_detail", AsyncMock()),
    ):
        await handler.__wrapped__(SimpleNamespace(), query)

    set_mock.assert_awaited_once_with(90001, None)


@pytest.mark.asyncio
async def test_missing_sudo_title_set_returns_not_found():
    bot = _RecorderBot()
    dev_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "dev_title_sudo_set")
    query = _pm_query(settings.DEVELOPER_ID, f"{CB['DEV_TITLE_SUDO_SET_PREFIX']}99999:0")

    with patch("app.handlers.dev_panel.user_repo.get_sudo", AsyncMock(return_value=None)):
        await handler.__wrapped__(SimpleNamespace(), query)

    query.answer.assert_awaited()
    assert query.message.edit_text.await_count == 0


@pytest.mark.asyncio
async def test_non_developer_cannot_set_sudo_title():
    bot = _RecorderBot()
    dev_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "dev_title_sudo_set")
    query = _pm_query(90001, f"{CB['DEV_TITLE_SUDO_SET_PREFIX']}90001:0")

    with patch("app.handlers.dev_panel.user_repo.set_sudo_admin_title", AsyncMock()) as set_mock:
        result = await handler(SimpleNamespace(), query)

    assert result is None
    set_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_wrong_user_clear_confirm_rejected():
    bot = _RecorderBot()
    dev_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "dev_title_sudo_clear_confirm")
    issued_at = int(time.time())
    query = _pm_query(
        settings.DEVELOPER_ID,
        f"{CB['DEV_TITLE_SUDO_CLEAR_DO_PREFIX']}90001:0:11111:{issued_at}",
    )

    with patch("app.handlers.dev_panel.user_repo.set_sudo_admin_title", AsyncMock()) as set_mock:
        await handler.__wrapped__(SimpleNamespace(), query)

    set_mock.assert_not_awaited()
    assert query.answer.await_args.args[0] == t("fa", "common.errors.no_access")


@pytest.mark.asyncio
async def test_stale_clear_confirm_rejected():
    bot = _RecorderBot()
    dev_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "dev_title_sudo_clear_confirm")
    stale_ts = int(time.time()) - 400
    query = _pm_query(
        settings.DEVELOPER_ID,
        f"{CB['DEV_TITLE_SUDO_CLEAR_DO_PREFIX']}90001:0:{settings.DEVELOPER_ID}:{stale_ts}",
    )

    with patch("app.handlers.dev_panel.user_repo.set_sudo_admin_title", AsyncMock()) as set_mock:
        await handler.__wrapped__(SimpleNamespace(), query)

    set_mock.assert_not_awaited()
    assert query.answer.await_args.args[0] == t("fa", "admin_titles.stale_confirm")


@pytest.mark.asyncio
async def test_malformed_clear_confirm_rejected():
    bot = _RecorderBot()
    dev_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "dev_title_sudo_clear_malformed")
    query = _pm_query(settings.DEVELOPER_ID, f"{CB['DEV_TITLE_SUDO_CLEAR_DO_PREFIX']}bad")

    await handler.__wrapped__(SimpleNamespace(), query)

    assert query.answer.await_args.args[0] == t("fa", "common.errors.invalid_callback")

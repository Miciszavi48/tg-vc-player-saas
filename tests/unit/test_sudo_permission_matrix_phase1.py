"""Phase 1: Sudo permission matrix model and read-only developer detail view."""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
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
from app.database.models import Sudo
from app.handlers import dev_panel
from app.repositories import user_repo
from app.repositories.user_repo import SUDO_PERMISSION_FIELD_NAMES, _permission_bool, sudo_permissions_from_row
from app.services.wizard_ui import TOKEN_DEV_USERS
from app.utils.i18n import t
from app.utils.ui import CB
from tests.factories import make_sudo


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
        sudo_link="https://t.me/secret",
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


def test_sudo_model_has_all_permission_fields():
    for name in SUDO_PERMISSION_FIELD_NAMES:
        assert hasattr(Sudo, name)


def test_new_sudo_defaults_all_permissions_true():
    row = make_sudo(user_id=90002)
    for name in SUDO_PERMISSION_FIELD_NAMES:
        assert getattr(row, name) is True


def test_permission_bool_treats_none_as_true():
    assert _permission_bool(None) is True
    assert _permission_bool(False) is False
    assert _permission_bool(True) is True


def test_sudo_permissions_from_row_defaults_missing_attrs_to_true():
    row = _sample_sudo()
    row.can_manage_groups = None  # type: ignore[assignment]
    perms = sudo_permissions_from_row(row)
    assert perms["can_manage_groups"] is True


def test_migration_adds_permission_columns_with_true_defaults():
    import importlib

    mod = importlib.import_module("app.database.migrations.versions.0014_sudo_permissions")
    assert mod.down_revision == "0013_media_events"
    assert set(mod._PERMISSION_COLUMNS) == set(SUDO_PERMISSION_FIELD_NAMES)


@pytest.mark.asyncio
async def test_sudo_list_includes_details_button():
    bot = _RecorderBot()
    dev_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "dev_list_sudos")
    query = _pm_query(settings.DEVELOPER_ID, CB["DEV_LIST_SUDOS"])
    row = _sample_sudo()

    with patch("app.handlers.dev_panel.user_repo.get_all_sudos", AsyncMock(return_value=[row])):
        await handler.__wrapped__(SimpleNamespace(), query)

    cbs = _kb_callbacks(query.message.edit_text.call_args.kwargs["reply_markup"])
    assert f"{CB['DEV_SUDO_DETAIL_PREFIX']}90001:0" in cbs


@pytest.mark.asyncio
async def test_developer_can_open_sudo_detail():
    bot = _RecorderBot()
    dev_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "dev_sudo_detail")
    query = _pm_query(settings.DEVELOPER_ID, f"{CB['DEV_SUDO_DETAIL_PREFIX']}90001:2")
    row = _sample_sudo()

    with patch("app.handlers.dev_panel.user_repo.get_sudo_record", AsyncMock(return_value=row)):
        await handler.__wrapped__(SimpleNamespace(), query)

    text = query.message.edit_text.call_args.args[0]
    assert "90001" in text
    assert t("fa", "sudo_permissions.perm_groups") in text
    assert t("fa", "sudo_permissions.state_enabled") in text
    cbs = _kb_callbacks(query.message.edit_text.call_args.kwargs["reply_markup"])
    assert f"{CB['DEV_SUDO_LIST_BACK_PREFIX']}2" in cbs


@pytest.mark.asyncio
async def test_sudo_detail_does_not_expose_raw_link():
    bot = _RecorderBot()
    dev_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "dev_sudo_detail")
    query = _pm_query(settings.DEVELOPER_ID, f"{CB['DEV_SUDO_DETAIL_PREFIX']}90001:0")
    row = _sample_sudo(sudo_link="https://t.me/secret")

    with patch("app.handlers.dev_panel.user_repo.get_sudo_record", AsyncMock(return_value=row)):
        await handler.__wrapped__(SimpleNamespace(), query)

    text = query.message.edit_text.call_args.args[0]
    assert "https://t.me/secret" not in text
    assert t("fa", "sudo_permissions.link_configured") in text


@pytest.mark.asyncio
async def test_sudo_detail_back_returns_same_list_page():
    bot = _RecorderBot()
    dev_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "dev_sudo_list_back")
    query = _pm_query(settings.DEVELOPER_ID, f"{CB['DEV_SUDO_LIST_BACK_PREFIX']}2")
    sudos = [_sample_sudo(user_id=90000 + i) for i in range(25)]

    with patch("app.handlers.dev_panel.user_repo.get_all_sudos", AsyncMock(return_value=sudos)):
        await handler.__wrapped__(SimpleNamespace(), query)

    cbs = _kb_callbacks(query.message.edit_text.call_args.kwargs["reply_markup"])
    assert f"{CB['PAGE_DEV_SUDOS']}1" in cbs
    assert any(cb.startswith(f"{CB['DEV_SUDO_DETAIL_PREFIX']}") and cb.endswith(":2") for cb in cbs)


@pytest.mark.asyncio
async def test_missing_sudo_shows_not_found():
    bot = _RecorderBot()
    dev_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "dev_sudo_detail")
    query = _pm_query(settings.DEVELOPER_ID, f"{CB['DEV_SUDO_DETAIL_PREFIX']}99999:0")

    with patch("app.handlers.dev_panel.user_repo.get_sudo_record", AsyncMock(return_value=None)):
        await handler.__wrapped__(SimpleNamespace(), query)

    text = query.message.edit_text.call_args.args[0]
    assert "99999" in text


@pytest.mark.asyncio
async def test_malformed_sudo_detail_callback_does_not_crash():
    bot = _RecorderBot()
    dev_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "dev_sudo_detail")
    query = _pm_query(settings.DEVELOPER_ID, "dev:sudo:detail:bad")

    await handler.__wrapped__(SimpleNamespace(), query)
    assert query.answer.await_count >= 1
    assert query.message.edit_text.await_count == 0


@pytest.mark.asyncio
async def test_non_developer_cannot_access_sudo_detail():
    bot = _RecorderBot()
    dev_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "dev_sudo_detail")
    query = _pm_query(99999, f"{CB['DEV_SUDO_DETAIL_PREFIX']}90001:0")

    with patch("app.handlers.dev_panel.user_repo.get_sudo_record", AsyncMock(return_value=_sample_sudo())):
        result = await handler(SimpleNamespace(), query)

    assert result is None
    query.message.edit_text.assert_not_awaited()


@pytest.mark.asyncio
async def test_get_sudo_permissions_returns_none_for_missing_user():
    with patch("app.repositories.user_repo.get_sudo_record", AsyncMock(return_value=None)):
        perms = await user_repo.get_sudo_permissions(88888)
    assert perms is None


@pytest.mark.asyncio
async def test_get_sudo_permissions_returns_all_flags():
    row = _sample_sudo(can_manage_credit=False)
    with patch("app.repositories.user_repo.get_sudo_record", AsyncMock(return_value=row)):
        perms = await user_repo.get_sudo_permissions(90001)
    assert perms is not None
    assert perms["can_manage_credit"] is False
    assert perms["can_manage_groups"] is True

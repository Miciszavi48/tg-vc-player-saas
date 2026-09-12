"""Phase 4B: Admin title storage and read-only Developer UI."""
from __future__ import annotations

import os
import sys
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
from app.repositories import settings_repo
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


def test_owner_and_sudo_models_have_nullable_admin_title():
    assert hasattr(Owner, "admin_title")
    assert hasattr(Sudo, "admin_title")
    assert Owner.__table__.c.admin_title.nullable is True
    assert Sudo.__table__.c.admin_title.nullable is True


def test_admin_title_migration_adds_and_drops_columns():
    import importlib

    mod = importlib.import_module("app.database.migrations.versions.0015_admin_titles")
    assert mod.down_revision == "0014_sudo_permissions"
    assert mod.revision == "0015_admin_titles"


@pytest.mark.asyncio
async def test_developer_admin_title_missing_returns_none():
    with patch("app.repositories.settings_repo.get_bot_setting", AsyncMock(return_value=None)):
        assert await settings_repo.get_developer_admin_title() is None


@pytest.mark.asyncio
async def test_developer_admin_title_setting_returns_stripped_value():
    with patch("app.repositories.settings_repo.get_bot_setting", AsyncMock(return_value="  Creator  ")):
        assert await settings_repo.get_developer_admin_title() == "Creator"


@pytest.mark.asyncio
async def test_sudo_detail_shows_admin_title_when_set():
    bot = _RecorderBot()
    dev_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "dev_sudo_detail")
    query = _pm_query(settings.DEVELOPER_ID, f"{CB['DEV_SUDO_DETAIL_PREFIX']}90001:0")
    sudo = _sample_sudo(admin_title="Sudo Captain")

    with (
        patch("app.handlers.dev_panel.user_repo.get_sudo_record", AsyncMock(return_value=sudo)),
        patch("app.handlers.dev_panel.settings_repo.get_developer_admin_title", AsyncMock(return_value="Creator")),
    ):
        await handler.__wrapped__(SimpleNamespace(), query)

    text = query.message.edit_text.call_args.args[0]
    assert t("fa", "admin_titles.sudo_admin_title") in text
    assert "Sudo Captain" in text
    assert "Creator" in text


@pytest.mark.asyncio
async def test_sudo_detail_shows_not_set_for_missing_admin_title():
    bot = _RecorderBot()
    dev_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "dev_sudo_detail")
    query = _pm_query(settings.DEVELOPER_ID, f"{CB['DEV_SUDO_DETAIL_PREFIX']}90001:0")

    with (
        patch("app.handlers.dev_panel.user_repo.get_sudo_record", AsyncMock(return_value=_sample_sudo())),
        patch("app.handlers.dev_panel.settings_repo.get_developer_admin_title", AsyncMock(return_value=None)),
    ):
        await handler.__wrapped__(SimpleNamespace(), query)

    text = query.message.edit_text.call_args.args[0]
    assert t("fa", "admin_titles.not_set") in text


@pytest.mark.asyncio
async def test_sudo_detail_notes_title_is_not_applied_to_telegram():
    bot = _RecorderBot()
    dev_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "dev_sudo_detail")
    query = _pm_query(settings.DEVELOPER_ID, f"{CB['DEV_SUDO_DETAIL_PREFIX']}90001:0")

    with (
        patch("app.handlers.dev_panel.user_repo.get_sudo_record", AsyncMock(return_value=_sample_sudo())),
        patch("app.handlers.dev_panel.settings_repo.get_developer_admin_title", AsyncMock(return_value=None)),
    ):
        await handler.__wrapped__(SimpleNamespace(), query)

    text = query.message.edit_text.call_args.args[0]
    assert t("fa", "admin_titles.note") in text
    assert "تلگرام" in text


@pytest.mark.asyncio
async def test_owner_list_displays_admin_title_read_only():
    bot = _RecorderBot()
    dev_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "dev_list_owners")
    query = _pm_query(settings.DEVELOPER_ID, CB["DEV_LIST_OWNERS"])
    owner = Owner(
        user_id=70001,
        username="owner_user",
        display_name="Owner One",
        admin_title="Owner Captain",
        is_active=True,
    )

    with patch("app.handlers.dev_panel.user_repo.get_all_owners", AsyncMock(return_value=[owner])):
        await handler.__wrapped__(SimpleNamespace(), query)

    text = query.message.edit_text.call_args.args[0]
    assert "Owner Captain" in text
    assert t("fa", "admin_titles.list_line", title="Owner Captain") in text

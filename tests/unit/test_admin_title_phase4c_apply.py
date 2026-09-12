"""Phase 4C-safe: manual Telegram admin title apply for already-admin users."""
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
from app.services.admin_title_service import AdminTitlePreflightResult, preflight_admin_title_apply
from app.utils.i18n import t
from app.utils.ui import CB, KeyboardFactory


class _FakeRedis:
    def __init__(self) -> None:
        self.data: dict[str, str] = {}

    async def set(self, key: str, value: str, ex: int | None = None) -> None:  # noqa: ARG002
        self.data[key] = value

    async def get(self, key: str) -> str | None:
        if key in self.data:
            return self.data[key]
        for k, v in self.data.items():
            if key.endswith(k) or k.endswith(key):
                return v
        return None

    async def delete(self, key: str) -> None:
        if key in self.data:
            self.data.pop(key, None)
            return
        for k in list(self.data.keys()):
            if key.endswith(k) or k.endswith(key):
                self.data.pop(k, None)


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
        set_administrator_title=AsyncMock(return_value=True),
        promote_chat_member=AsyncMock(),
    )


def _member(status: str, *, can_manage_chat: bool | None = None):
    privileges = None
    if can_manage_chat is not None:
        privileges = SimpleNamespace(can_manage_chat=can_manage_chat)
    return SimpleNamespace(status=SimpleNamespace(value=status), privileges=privileges)


def _sample_sudo(**overrides) -> Sudo:
    base = dict(
        user_id=90001,
        username="sudo_user",
        display_name="Sudo One",
        admin_title="Sudo Captain",
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


def _confirm_callback_from_send(client) -> str:
    kb = client.send_message.await_args.kwargs["reply_markup"]
    for cb in _kb_callbacks(kb):
        if cb.startswith(CB["DEV_TITLE_APPLY_DO_PREFIX"]):
            return cb
    raise AssertionError("confirm callback not found")


def test_admin_title_settings_has_apply_developer_button():
    cbs = _kb_callbacks(KeyboardFactory.dev_admin_titles("fa"))
    assert CB["DEV_TITLE_APPLY_DEV"] in cbs


@pytest.mark.asyncio
async def test_owner_title_list_rows_have_apply_button():
    bot = _RecorderBot()
    dev_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "dev_title_owner_list")
    query = _pm_query(settings.DEVELOPER_ID, f"{CB['DEV_TITLE_OWNER_LIST_PREFIX']}0")
    owner = Owner(user_id=70001, display_name="Owner", admin_title="Owner Captain", is_active=True)

    with patch("app.handlers.dev_panel.user_repo.get_all_owners", AsyncMock(return_value=[owner])):
        await handler.__wrapped__(SimpleNamespace(), query)

    cbs = _kb_callbacks(query.message.edit_text.call_args.kwargs["reply_markup"])
    assert f"{CB['DEV_TITLE_APPLY_OWNER_PREFIX']}70001:0" in cbs


@pytest.mark.asyncio
async def test_sudo_detail_has_apply_button_when_title_set():
    bot = _RecorderBot()
    dev_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "dev_sudo_detail")
    query = _pm_query(settings.DEVELOPER_ID, f"{CB['DEV_SUDO_DETAIL_PREFIX']}90001:0")

    with (
        patch("app.handlers.dev_panel.user_repo.get_sudo_record", AsyncMock(return_value=_sample_sudo())),
        patch("app.handlers.dev_panel.settings_repo.get_developer_admin_title", AsyncMock(return_value=None)),
    ):
        await handler.__wrapped__(SimpleNamespace(), query)

    cbs = _kb_callbacks(query.message.edit_text.call_args.kwargs["reply_markup"])
    assert f"{CB['DEV_TITLE_APPLY_SUDO_PREFIX']}90001:0" in cbs


@pytest.mark.asyncio
async def test_apply_title_not_set_is_unavailable():
    bot = _RecorderBot()
    dev_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "dev_title_apply_sudo")
    query = _pm_query(settings.DEVELOPER_ID, f"{CB['DEV_TITLE_APPLY_SUDO_PREFIX']}90001:0")

    with patch("app.handlers.dev_panel.user_repo.get_sudo", AsyncMock(return_value=_sample_sudo(admin_title=None))):
        await handler.__wrapped__(_client_with_ask("-100"), query)

    assert query.answer.await_args.args[0] == t("fa", "admin_titles.apply_title_not_set")


@pytest.mark.asyncio
async def test_invalid_chat_id_retries_then_shows_confirmation():
    bot = _RecorderBot()
    dev_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "dev_title_apply_developer")
    query = _pm_query(settings.DEVELOPER_ID, CB["DEV_TITLE_APPLY_DEV"])
    client = _client_with_ask("not-a-chat-id")
    client.ask.side_effect = [
        SimpleNamespace(text="not-a-chat-id"),
        SimpleNamespace(text="-100123"),
    ]
    fake_redis = _FakeRedis()

    with (
        patch("app.handlers.dev_panel.settings_repo.get_developer_admin_title", AsyncMock(return_value="Creator")),
        patch(
            "app.handlers.dev_panel.preflight_admin_title_apply",
            AsyncMock(return_value=AdminTitlePreflightResult(True, "admin_titles.apply_preflight_ok")),
        ),
        patch("app.handlers.dev_panel.get_redis", AsyncMock(return_value=fake_redis)),
    ):
        await handler.__wrapped__(client, query)

    assert client.ask.await_count == 2
    assert t("fa", "admin_titles.apply_invalid_chat_id") in client.ask.await_args_list[1].args[1]
    assert t(
        "fa",
        "admin_titles.apply_confirm",
        user_id=settings.DEVELOPER_ID,
        chat_id=-100123,
        title="Creator",
    ) in client.send_message.await_args.args[1]
    client.set_administrator_title.assert_not_awaited()


@pytest.mark.asyncio
async def test_valid_chat_id_shows_confirmation_without_api_call():
    bot = _RecorderBot()
    dev_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "dev_title_apply_developer")
    query = _pm_query(settings.DEVELOPER_ID, CB["DEV_TITLE_APPLY_DEV"])
    client = _client_with_ask("-100123")
    fake_redis = _FakeRedis()

    with (
        patch("app.handlers.dev_panel.settings_repo.get_developer_admin_title", AsyncMock(return_value="Creator")),
        patch(
            "app.handlers.dev_panel.preflight_admin_title_apply",
            AsyncMock(return_value=AdminTitlePreflightResult(True, "admin_titles.apply_preflight_ok")),
        ),
        patch("app.handlers.dev_panel.get_redis", AsyncMock(return_value=fake_redis)),
    ):
        await handler.__wrapped__(client, query)

    assert client.set_administrator_title.await_count == 0
    assert client.promote_chat_member.await_count == 0
    assert t("fa", "admin_titles.apply_confirm", user_id=settings.DEVELOPER_ID, chat_id=-100123, title="Creator") in client.send_message.await_args.args[1]


@pytest.mark.asyncio
async def test_preflight_bot_not_admin_denied():
    client = SimpleNamespace(get_chat_member=AsyncMock(return_value=_member("member")))
    result = await preflight_admin_title_apply(client, -100, 123)
    assert result.ok is False
    assert result.message_key == "admin_titles.apply_bot_not_admin"


@pytest.mark.asyncio
async def test_preflight_target_not_admin_denied():
    client = SimpleNamespace(
        get_chat_member=AsyncMock(side_effect=[_member("administrator"), _member("member")])
    )
    result = await preflight_admin_title_apply(client, -100, 123)
    assert result.ok is False
    assert result.message_key == "admin_titles.apply_target_not_admin"


@pytest.mark.asyncio
async def test_preflight_bot_and_target_admin_ok():
    client = SimpleNamespace(
        get_chat_member=AsyncMock(side_effect=[_member("administrator"), _member("administrator")])
    )
    result = await preflight_admin_title_apply(client, -100, 123)
    assert result.ok is True


@pytest.mark.asyncio
async def test_confirm_calls_set_administrator_title_never_promote():
    bot = _RecorderBot()
    dev_panel.register(bot, None)
    start = _handler_by_name(bot.callback_handlers, "dev_title_apply_sudo")
    confirm = _handler_by_name(bot.callback_handlers, "dev_title_apply_confirm")
    query = _pm_query(settings.DEVELOPER_ID, f"{CB['DEV_TITLE_APPLY_SUDO_PREFIX']}90001:0")
    client = _client_with_ask("-100123")
    fake_redis = _FakeRedis()

    with (
        patch("app.handlers.dev_panel.user_repo.get_sudo", AsyncMock(return_value=_sample_sudo())),
        patch(
            "app.handlers.dev_panel.preflight_admin_title_apply",
            AsyncMock(return_value=AdminTitlePreflightResult(True, "admin_titles.apply_preflight_ok")),
        ),
        patch("app.handlers.dev_panel.get_redis", AsyncMock(return_value=fake_redis)),
    ):
        await start.__wrapped__(client, query)
        cb = _confirm_callback_from_send(client)
        confirm_query = _pm_query(settings.DEVELOPER_ID, cb)
        await confirm.__wrapped__(client, confirm_query)

    client.set_administrator_title.assert_awaited_once_with(-100123, 90001, "Sudo Captain")
    client.promote_chat_member.assert_not_awaited()
    assert confirm_query.message.edit_text.await_args.args[0] == t("fa", "admin_titles.apply_success")


@pytest.mark.asyncio
async def test_wrong_user_confirm_rejected():
    bot = _RecorderBot()
    dev_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "dev_title_apply_confirm")
    issued_at = int(time.time())
    query = _pm_query(settings.DEVELOPER_ID, f"{CB['DEV_TITLE_APPLY_DO_PREFIX']}token_123:111:{issued_at}")

    await handler.__wrapped__(_client_with_ask("-100"), query)

    assert query.answer.await_args.args[0] == t("fa", "common.errors.no_access")


@pytest.mark.asyncio
async def test_stale_confirm_rejected_and_no_api_call():
    bot = _RecorderBot()
    dev_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "dev_title_apply_confirm")
    stale_ts = int(time.time()) - 400
    query = _pm_query(settings.DEVELOPER_ID, f"{CB['DEV_TITLE_APPLY_DO_PREFIX']}token_123:{settings.DEVELOPER_ID}:{stale_ts}")
    client = _client_with_ask("-100")

    await handler.__wrapped__(client, query)

    client.set_administrator_title.assert_not_awaited()
    assert query.answer.await_args.args[0] == t("fa", "admin_titles.stale_confirm")


@pytest.mark.asyncio
async def test_malformed_confirm_rejected():
    bot = _RecorderBot()
    dev_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "dev_title_apply_malformed")
    query = _pm_query(settings.DEVELOPER_ID, f"{CB['DEV_TITLE_APPLY_DO_PREFIX']}bad")

    await handler.__wrapped__(_client_with_ask("-100"), query)

    assert query.answer.await_args.args[0] == t("fa", "common.errors.invalid_callback")


@pytest.mark.asyncio
async def test_confirm_reruns_preflight_before_apply():
    bot = _RecorderBot()
    dev_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "dev_title_apply_confirm")
    issued_at = int(time.time())
    token = "token_123"
    fake_redis = _FakeRedis()
    await fake_redis.set("dev_title_apply:token_123", "s:90001:-100123:0")
    query = _pm_query(settings.DEVELOPER_ID, f"{CB['DEV_TITLE_APPLY_DO_PREFIX']}{token}:{settings.DEVELOPER_ID}:{issued_at}")
    client = _client_with_ask("-100")

    with (
        patch("app.handlers.dev_panel.get_redis", AsyncMock(return_value=fake_redis)),
        patch("app.handlers.dev_panel.user_repo.get_sudo", AsyncMock(return_value=_sample_sudo())),
        patch(
            "app.handlers.dev_panel.preflight_admin_title_apply",
            AsyncMock(return_value=AdminTitlePreflightResult(False, "admin_titles.apply_target_not_admin")),
        ) as preflight_mock,
    ):
        await handler.__wrapped__(client, query)

    preflight_mock.assert_awaited_once()
    client.set_administrator_title.assert_not_awaited()
    assert query.message.edit_text.await_args.args[0] == t("fa", "admin_titles.apply_target_not_admin")


@pytest.mark.asyncio
async def test_generic_api_error_handled_safely():
    bot = _RecorderBot()
    dev_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "dev_title_apply_confirm")
    issued_at = int(time.time())
    fake_redis = _FakeRedis()
    await fake_redis.set("dev_title_apply:token_123", "s:90001:-100123:0")
    query = _pm_query(settings.DEVELOPER_ID, f"{CB['DEV_TITLE_APPLY_DO_PREFIX']}token_123:{settings.DEVELOPER_ID}:{issued_at}")
    client = _client_with_ask("-100")
    client.set_administrator_title = AsyncMock(side_effect=RuntimeError("telegram down"))

    with (
        patch("app.handlers.dev_panel.get_redis", AsyncMock(return_value=fake_redis)),
        patch("app.handlers.dev_panel.user_repo.get_sudo", AsyncMock(return_value=_sample_sudo())),
        patch(
            "app.handlers.dev_panel.preflight_admin_title_apply",
            AsyncMock(return_value=AdminTitlePreflightResult(True, "admin_titles.apply_preflight_ok")),
        ),
    ):
        await handler.__wrapped__(client, query)

    assert query.message.edit_text.await_args.args[0] == t("fa", "admin_titles.apply_failed")


@pytest.mark.asyncio
async def test_title_not_set_at_confirm_time_rejected():
    bot = _RecorderBot()
    dev_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "dev_title_apply_confirm")
    issued_at = int(time.time())
    fake_redis = _FakeRedis()
    await fake_redis.set("dev_title_apply:token_123", "s:90001:-100123:0")
    query = _pm_query(settings.DEVELOPER_ID, f"{CB['DEV_TITLE_APPLY_DO_PREFIX']}token_123:{settings.DEVELOPER_ID}:{issued_at}")
    client = _client_with_ask("-100")

    with (
        patch("app.handlers.dev_panel.get_redis", AsyncMock(return_value=fake_redis)),
        patch("app.handlers.dev_panel.user_repo.get_sudo", AsyncMock(return_value=_sample_sudo(admin_title=None))),
    ):
        await handler.__wrapped__(client, query)

    client.set_administrator_title.assert_not_awaited()
    assert query.answer.await_args.args[0] == t("fa", "admin_titles.apply_title_not_set")


@pytest.mark.asyncio
async def test_non_developer_cannot_start_apply_flow():
    bot = _RecorderBot()
    dev_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "dev_title_apply_sudo")
    query = _pm_query(90001, f"{CB['DEV_TITLE_APPLY_SUDO_PREFIX']}90001:0")
    client = _client_with_ask("-100")

    result = await handler(client, query)

    assert result is None
    client.set_administrator_title.assert_not_awaited()

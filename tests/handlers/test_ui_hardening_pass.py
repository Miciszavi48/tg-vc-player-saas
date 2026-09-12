from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timezone
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

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
from app.handlers import broadcast_wizard, callbacks, group_panel, helper_otp_wizard, helper_panel, tv_radio
from app.utils.redis_keys import helper_otp_state_key, helper_proxy_state_key
from app.utils.ui import CB, KeyboardFactory


class _RecorderBot:
    def __init__(self) -> None:
        self.callback_handlers: list = []
        self.message_handlers: list = []
        self.other_handlers: list = []

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
                    self.other_handlers.append(fn)
                    return fn

                return _decorator

            return _register_other
        raise AttributeError(name)


class _FakeRedis:
    def __init__(self) -> None:
        self._store: dict[str, str] = {}

    async def set(self, key: str, value: str, ex: int | None = None):  # noqa: ARG002
        self._store[key] = value
        return True

    async def get(self, key: str):
        return self._store.get(key)

    async def delete(self, *keys: str):
        removed = 0
        for key in keys:
            if key in self._store:
                removed += 1
                self._store.pop(key, None)
        return removed


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
            chat=SimpleNamespace(id=-100100, type=SimpleNamespace(value="supergroup")),
            edit_text=AsyncMock(),
            edit_reply_markup=AsyncMock(),
            text="seed",
        ),
    )


def _pm_private_query(user_id: int, data: str):
    q = _pm_query(user_id, data)
    q.message.chat = SimpleNamespace(id=100, type=SimpleNamespace(value="private"))
    return q


def _private_message(user_id: int, text: str):
    return SimpleNamespace(
        from_user=SimpleNamespace(id=user_id, first_name="Tester"),
        text=text,
        chat=SimpleNamespace(id=100, type=SimpleNamespace(value="private")),
        reply=AsyncMock(),
        stop_propagation=MagicMock(),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("handler_name", "cb_data", "asset_data"),
    [
        ("on_tv_select", "pb:tv:t1", [{"id": "t1", "name": "TV One", "url": "https://tv.example/1"}]),
        ("on_radio_select", "pb:radio:r1", [{"id": "r1", "name": "Radio One", "url": "https://radio.example/1"}]),
        ("on_satellite_select", "pb:sat:s1", [{"id": "s1", "name": "Sat One", "url": "https://sat.example/1"}]),
    ],
)
async def test_tv_radio_sat_success_has_navigation(handler_name: str, cb_data: str, asset_data: list[dict[str, str]]):
    bot = _RecorderBot()
    tv_radio.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, handler_name)
    query = _pm_query(settings.DEVELOPER_ID, cb_data)

    with (
        patch("app.handlers.tv_radio._load_json", return_value=asset_data),
        patch("app.handlers.tv_radio.authorize_playback_action", AsyncMock(return_value=True)),
        patch("app.handlers.tv_radio.deny_free_mode_media", AsyncMock(return_value=False)),
        patch("app.handlers.tv_radio.CallService.join_voice_chat", AsyncMock(return_value=True)),
        patch("app.handlers.tv_radio.CallService.get_repeat_state", return_value=False),
    ):
        await handler(SimpleNamespace(), query)

    assert query.answer.await_count == 1
    assert query.message.edit_text.await_count == 1
    kb = query.message.edit_text.call_args.kwargs["reply_markup"]
    cbs = _kb_callbacks(kb)
    assert CB["NAV_BACK"] in cbs
    assert CB["WZ_HOME"] in cbs


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("state", "scheduler_id"),
    [
        (
            {
                "payload": {"payload_type": "text", "text_content": "Hello"},
                "targets": ["users"],
                "mode": "send",
                "schedule": "at",
                "run_at": datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc).isoformat(),
                "admin_chat_id": 100,
                "admin_msg_id": 200,
                "filter": "all",
            },
            "bc_sched_",
        ),
        (
            {
                "payload": {"payload_type": "text", "text_content": "Hello"},
                "targets": ["users"],
                "mode": "send",
                "schedule": "recurring",
                "interval_hours": 6,
                "admin_chat_id": 100,
                "admin_msg_id": 200,
                "filter": "all",
            },
            "bc_recur_",
        ),
    ],
)
async def test_broadcast_confirm_scheduled_and_recurring_end_with_nav(state: dict, scheduler_id: str):
    bot = _RecorderBot()
    broadcast_wizard.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "bcw_confirm")
    invoke = getattr(handler, "__wrapped__", handler)
    query = _pm_private_query(settings.DEVELOPER_ID, CB["BCW_CONFIRM"])

    async def _create_broadcasts(broadcasts):
        for broadcast in broadcasts:
            broadcast.id = 42
        return broadcasts

    create_mock = AsyncMock(side_effect=_create_broadcasts)
    scheduler_add_job = MagicMock()

    with (
        patch("app.handlers.broadcast_wizard._get_state", AsyncMock(return_value=state)),
        patch("app.handlers.broadcast_wizard._clear_state", AsyncMock()),
        patch("app.handlers.broadcast_wizard._claim_confirmation", AsyncMock(return_value=True)),
        patch("app.handlers.broadcast_wizard.broadcast_repo.create_many", create_mock),
        patch("app.scheduler.scheduler.add_job", scheduler_add_job),
    ):
        await invoke(SimpleNamespace(), query)

    assert query.answer.await_count == 1
    assert query.message.edit_text.await_count >= 1
    scheduler_add_job.assert_called()
    assert scheduler_add_job.call_args.kwargs["id"].startswith(scheduler_id)
    kb = query.message.edit_text.call_args.kwargs["reply_markup"]
    cbs = _kb_callbacks(kb)
    assert f"{CB['WZ_BACK_PREFIX']}dev_broadcast" in cbs
    assert CB["WZ_HOME"] in cbs


@pytest.mark.asyncio
async def test_broadcast_cancel_returns_menu_with_buttons():
    bot = _RecorderBot()
    broadcast_wizard.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "bcw_cancel")
    invoke = getattr(handler, "__wrapped__", handler)
    query = _pm_private_query(settings.DEVELOPER_ID, CB["BCW_CANCEL"])

    kb = KeyboardFactory.dev_sub_broadcast("fa")
    with (
        patch("app.handlers.broadcast_wizard._clear_state", AsyncMock()),
        patch(
            "app.handlers.broadcast_wizard.resolve_navigation_payload",
            AsyncMock(return_value=("menu", kb)),
        ),
    ):
        await invoke(SimpleNamespace(), query)

    assert query.answer.await_count == 1
    assert query.message.edit_text.await_count == 1
    out_kb = query.message.edit_text.call_args.kwargs["reply_markup"]
    cbs = _kb_callbacks(out_kb)
    assert CB["BCW_START"] in cbs
    assert CB["NAV_BACK"] in cbs


@pytest.mark.asyncio
async def test_private_language_resolution_prefers_user_setting_en():
    bot = _RecorderBot()
    callbacks.register(bot, None)
    bind_handler = _handler_by_name(bot.callback_handlers, "_bind_lang_callback")
    nav_handler = _handler_by_name(bot.callback_handlers, "nav_back")

    query = _pm_private_query(999001, CB["NAV_BACK"])
    client = SimpleNamespace(get_me=AsyncMock(return_value=SimpleNamespace(username="test_bot")))

    with (
        patch(
            "app.services.language_service.user_repo.get_user",
            AsyncMock(return_value=SimpleNamespace(language="en")),
        ),
        patch("app.services.panel_router.user_repo.is_owner", AsyncMock(return_value=False)),
        patch("app.services.panel_router.user_repo.is_sudo", AsyncMock(return_value=False)),
        patch("app.services.bot_settings_service.settings_repo.get_bot_setting", AsyncMock(return_value=None)),
    ):
        await bind_handler(client, query)
        await nav_handler(client, query)

    rendered = query.message.edit_text.call_args.args[0]
    assert "Hello" in rendered


@pytest.mark.asyncio
async def test_helper_wizard_states_are_isolated_and_cancel_scoped():
    bot_helper = _RecorderBot()
    helper_panel.register(bot_helper, None)
    proxy_start = _handler_by_name(bot_helper.callback_handlers, "hlp_set_proxy_start")
    proxy_cancel = _handler_by_name(bot_helper.callback_handlers, "hlp_proxy_cancel")

    bot_otp = _RecorderBot()
    helper_otp_wizard.register(bot_otp, None)
    otp_cancel = _handler_by_name(bot_otp.callback_handlers, "otp_cancel")

    user_id = settings.DEVELOPER_ID
    redis = _FakeRedis()

    proxy_query = _pm_private_query(user_id, f"{CB['HLP_SET_PROXY_PREFIX']}5")
    with (
        patch("app.handlers.helper_panel.get_redis", AsyncMock(return_value=redis)),
        patch("app.handlers.helper_panel.remember_return_token", AsyncMock()),
    ):
        await proxy_start.__wrapped__(SimpleNamespace(get_me=AsyncMock(return_value=SimpleNamespace(username="bot"))), proxy_query)

    with patch("app.handlers.helper_otp_wizard.get_redis", AsyncMock(return_value=redis)):
        await helper_otp_wizard._set_state(user_id, {"step": "awaiting_phone"})  # type: ignore[attr-defined]
    assert await redis.get(helper_proxy_state_key(user_id)) is not None
    assert await redis.get(helper_otp_state_key(user_id)) is not None

    otp_query = _pm_private_query(user_id, "hlp:otp:cancel")
    with patch("app.handlers.helper_otp_wizard.get_redis", AsyncMock(return_value=redis)):
        await otp_cancel.__wrapped__(SimpleNamespace(), otp_query)
    assert await redis.get(helper_otp_state_key(user_id)) is None
    assert await redis.get(helper_proxy_state_key(user_id)) is None

    proxy_cancel_query = _pm_private_query(user_id, "hlp:proxy:cancel")
    with patch("app.handlers.helper_panel.get_redis", AsyncMock(return_value=redis)):
        await proxy_cancel.__wrapped__(SimpleNamespace(), proxy_cancel_query)
    assert await redis.get(helper_proxy_state_key(user_id)) is None


def test_group_settings_regex_excludes_default_media_toggle():
    assert re.match(group_panel._GROUP_SETTING_TOGGLE_REGEX, CB["GRP_MUSIC_VIDEO"])  # noqa: SLF001
    assert not re.match(group_panel._GROUP_SETTING_TOGGLE_REGEX, CB["GRP_DEFAULT_MEDIA_TYPE"])  # noqa: SLF001


@pytest.mark.asyncio
async def test_cancel_handlers_stop_propagation_without_duplicates():
    bot_otp = _RecorderBot()
    helper_otp_wizard.register(bot_otp, None)
    otp_text_handler = _handler_by_name(bot_otp.message_handlers, "otp_text_handler")
    otp_msg = _private_message(settings.DEVELOPER_ID, "/cancel")
    redis = _FakeRedis()

    with (
        patch("app.handlers.helper_otp_wizard._get_state", AsyncMock(return_value={"step": "awaiting_phone"})),
        patch("app.handlers.helper_otp_wizard._clear_state", AsyncMock()),
        patch("app.services.wizard_ui.get_redis", AsyncMock(return_value=redis)),
    ):
        await otp_text_handler(SimpleNamespace(), otp_msg)

    assert otp_msg.reply.await_count == 0
    otp_msg.stop_propagation.assert_not_called()

    bot_proxy = _RecorderBot()
    helper_panel.register(bot_proxy, None)
    proxy_input = _handler_by_name(bot_proxy.message_handlers, "hlp_proxy_input")
    proxy_msg = _private_message(settings.DEVELOPER_ID, "/cancel")

    await redis.set(
        helper_proxy_state_key(settings.DEVELOPER_ID),
        json.dumps({"step": "awaiting_proxy", "helper_id": 7}),
    )

    with (
        patch("app.handlers.helper_panel.get_redis", AsyncMock(return_value=redis)),
        patch("app.services.wizard_ui.get_redis", AsyncMock(return_value=redis)),
    ):
        await proxy_input(SimpleNamespace(), proxy_msg)

    assert proxy_msg.reply.await_count == 0
    proxy_msg.stop_propagation.assert_not_called()

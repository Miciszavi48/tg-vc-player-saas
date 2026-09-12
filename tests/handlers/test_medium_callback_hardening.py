from __future__ import annotations

import os
import sys
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
from app.handlers import broadcast_wizard, force_join_panel, search, sudo_panel, tv_radio
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


class _FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, str] = {}
        self.expiry: dict[str, int | None] = {}

    async def get(self, key: str) -> str | None:
        return self.store.get(key)

    async def set(self, key: str, value: str, ex: int | None = None) -> None:  # noqa: ARG002
        self.store[key] = value
        self.expiry[key] = ex

    async def delete(self, *keys: str) -> int:
        deleted = 0
        for key in keys:
            if key in self.store:
                deleted += 1
            self.store.pop(key, None)
            self.expiry.pop(key, None)
        return deleted


def _handler_by_name(handlers: list, name: str):
    for fn in handlers:
        if fn.__name__ == name:
            return fn
    raise AssertionError(f"handler not found: {name}")


def _callback_data_set(kb) -> set[str]:
    return {
        btn.callback_data
        for row in getattr(kb, "inline_keyboard", [])
        for btn in row
        if getattr(btn, "callback_data", None)
    }


def _pm_query(user_id: int, data: str):
    return SimpleNamespace(
        from_user=SimpleNamespace(id=user_id, first_name="Tester"),
        data=data,
        answer=AsyncMock(),
        message=SimpleNamespace(
            chat=SimpleNamespace(id=100, type=SimpleNamespace(value="private")),
            text="menu",
            edit_text=AsyncMock(),
        ),
    )


def _group_query(user_id: int, data: str):
    return SimpleNamespace(
        from_user=SimpleNamespace(id=user_id, first_name="GroupUser"),
        data=data,
        answer=AsyncMock(),
        message=SimpleNamespace(
            chat=SimpleNamespace(id=-1001, type=SimpleNamespace(value="supergroup")),
            text="menu",
            edit_text=AsyncMock(),
        ),
    )


@pytest.mark.asyncio
async def test_sudo_leave_installs_asks_for_confirmation_before_executing():
    bot = _RecorderBot()
    sudo_panel.register(bot, SimpleNamespace())
    handler = _handler_by_name(bot.callback_handlers, "sudo_leave_installs")
    query = _pm_query(settings.DEVELOPER_ID, CB["SUDO_LEAVE_INSTALLS"])
    redis = _FakeRedis()
    client = SimpleNamespace(leave_chat=AsyncMock())

    with (
        patch("app.handlers.sudo_panel.get_redis", AsyncMock(return_value=redis)),
        patch("app.handlers.sudo_panel.log_repo.get_install_logs", AsyncMock()) as logs_mock,
        patch("app.handlers.sudo_panel.CallService.leave_voice_chat", AsyncMock()) as leave_vc_mock,
    ):
        await handler(client, query)

    logs_mock.assert_not_awaited()
    leave_vc_mock.assert_not_awaited()
    client.leave_chat.assert_not_awaited()
    query.message.edit_text.assert_awaited_once()
    kb = query.message.edit_text.await_args.kwargs["reply_markup"]
    callbacks = _callback_data_set(kb)
    assert f"{CB['SUDO_LEAVE_CONFIRM_PREFIX']}{settings.DEVELOPER_ID}" in callbacks
    assert f"{CB['SUDO_LEAVE_CANCEL_PREFIX']}{settings.DEVELOPER_ID}" in callbacks
    assert redis.expiry[f"sudo_leave_confirm:{settings.DEVELOPER_ID}"] == 300


@pytest.mark.asyncio
async def test_forged_sudo_leave_confirmation_by_another_user_is_rejected():
    bot = _RecorderBot()
    sudo_panel.register(bot, SimpleNamespace())
    open_handler = _handler_by_name(bot.callback_handlers, "sudo_leave_installs")
    confirm_handler = _handler_by_name(bot.callback_handlers, "sudo_leave_installs_confirm")
    redis = _FakeRedis()
    client = SimpleNamespace(leave_chat=AsyncMock())

    with patch("app.handlers.sudo_panel.get_redis", AsyncMock(return_value=redis)):
        await open_handler(client, _pm_query(settings.DEVELOPER_ID, CB["SUDO_LEAVE_INSTALLS"]))

    forged = _pm_query(700002, f"{CB['SUDO_LEAVE_CONFIRM_PREFIX']}{settings.DEVELOPER_ID}")
    with (
        patch("app.handlers.sudo_panel.get_redis", AsyncMock(return_value=redis)),
        patch("app.utils.decorators.user_repo.is_sudo_or_above", AsyncMock(return_value=True)),
        patch("app.handlers.sudo_panel.log_repo.get_install_logs", AsyncMock()) as logs_mock,
    ):
        await confirm_handler(client, forged)

    logs_mock.assert_not_awaited()
    forged.answer.assert_awaited_once_with(t("fa", "common.errors.no_access"), show_alert=True)


@pytest.mark.asyncio
async def test_valid_sudo_leave_confirmation_executes():
    bot = _RecorderBot()
    call_py = SimpleNamespace()
    sudo_panel.register(bot, call_py)
    open_handler = _handler_by_name(bot.callback_handlers, "sudo_leave_installs")
    confirm_handler = _handler_by_name(bot.callback_handlers, "sudo_leave_installs_confirm")
    redis = _FakeRedis()
    client = SimpleNamespace(leave_chat=AsyncMock())
    logs = [
        SimpleNamespace(chat_id=-1001, chat_type="group", action="install"),
        SimpleNamespace(chat_id=-1002, chat_type="channel", action="install"),
    ]

    with patch("app.handlers.sudo_panel.get_redis", AsyncMock(return_value=redis)):
        await open_handler(client, _pm_query(settings.DEVELOPER_ID, CB["SUDO_LEAVE_INSTALLS"]))

    query = _pm_query(settings.DEVELOPER_ID, f"{CB['SUDO_LEAVE_CONFIRM_PREFIX']}{settings.DEVELOPER_ID}")
    with (
        patch("app.handlers.sudo_panel.get_redis", AsyncMock(return_value=redis)),
        patch("app.handlers.sudo_panel.log_repo.get_install_logs", AsyncMock(return_value=logs)),
        patch(
            "app.handlers.sudo_panel._get_current_sudo_leave_targets",
            AsyncMock(return_value={-1001: "group", -1002: "channel"}),
        ),
        patch("app.handlers.sudo_panel.CallService.leave_voice_chat", AsyncMock()) as leave_vc_mock,
        patch("app.handlers.sudo_panel.group_repo.deactivate_group", AsyncMock()) as group_deactivate_mock,
        patch("app.handlers.sudo_panel.channel_repo.deactivate_channel", AsyncMock()) as channel_deactivate_mock,
    ):
        await confirm_handler(client, query)

    assert leave_vc_mock.await_count == 2
    assert client.leave_chat.await_count == 2
    group_deactivate_mock.assert_awaited_once_with(-1001)
    channel_deactivate_mock.assert_awaited_once_with(-1002)
    query.message.edit_text.assert_awaited_once()


@pytest.mark.asyncio
async def test_canceling_sudo_leave_confirmation_does_not_execute():
    bot = _RecorderBot()
    sudo_panel.register(bot, SimpleNamespace())
    open_handler = _handler_by_name(bot.callback_handlers, "sudo_leave_installs")
    cancel_handler = _handler_by_name(bot.callback_handlers, "sudo_leave_installs_cancel")
    redis = _FakeRedis()
    client = SimpleNamespace(leave_chat=AsyncMock())

    with patch("app.handlers.sudo_panel.get_redis", AsyncMock(return_value=redis)):
        await open_handler(client, _pm_query(settings.DEVELOPER_ID, CB["SUDO_LEAVE_INSTALLS"]))

    query = _pm_query(settings.DEVELOPER_ID, f"{CB['SUDO_LEAVE_CANCEL_PREFIX']}{settings.DEVELOPER_ID}")
    with (
        patch("app.handlers.sudo_panel.get_redis", AsyncMock(return_value=redis)),
        patch("app.handlers.sudo_panel.log_repo.get_install_logs", AsyncMock()) as logs_mock,
        patch("app.handlers.sudo_panel.CallService.leave_voice_chat", AsyncMock()) as leave_vc_mock,
    ):
        await cancel_handler(client, query)

    logs_mock.assert_not_awaited()
    leave_vc_mock.assert_not_awaited()
    client.leave_chat.assert_not_awaited()
    query.message.edit_text.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        "search:play:",
        "search:play:bad/id",
        f"search:play:{'a' * 64}",
    ],
)
async def test_malformed_search_play_payloads_are_rejected(payload: str):
    bot = _RecorderBot()
    search.register(bot, SimpleNamespace())
    handler = _handler_by_name(bot.callback_handlers, "on_search_select")
    query = _group_query(600001, payload)

    with (
        patch("app.handlers.search.authorize_playback_action", AsyncMock(return_value=True)) as auth_mock,
        patch("app.handlers.search.MediaService.get_stream_url", AsyncMock()) as stream_mock,
    ):
        await handler(SimpleNamespace(), query)

    auth_mock.assert_not_awaited()
    stream_mock.assert_not_awaited()
    query.answer.assert_awaited_once_with(t("fa", "common.errors.invalid_callback"), show_alert=True)


@pytest.mark.asyncio
async def test_valid_search_play_payload_still_works():
    bot = _RecorderBot()
    call_py = SimpleNamespace()
    search.register(bot, call_py)
    handler = _handler_by_name(bot.callback_handlers, "on_search_select")
    video_id = "dQw4w9WgXcQ"
    query = _group_query(600001, f"search:play:{video_id}")

    with (
        patch("app.handlers.search.authorize_playback_action", AsyncMock(return_value=True)),
        patch("app.handlers.search.validate_safe_url_with_redirects", AsyncMock(return_value=f"https://youtube.com/watch?v={video_id}")),
        patch("app.handlers.search.MediaService.get_stream_url", AsyncMock(return_value="https://cdn.example.test/a.mp3")) as stream_mock,
        patch("app.handlers.search.CallService.join_voice_chat", AsyncMock(return_value=True)) as join_mock,
    ):
        await handler(SimpleNamespace(), query)

    stream_mock.assert_awaited_once_with(f"https://youtube.com/watch?v={video_id}")
    join_mock.assert_awaited_once_with(
        call_py,
        -1001,
        "https://cdn.example.test/a.mp3",
        "audio",
        user_id=600001,
        event_source=f"https://youtube.com/watch?v={video_id}",
    )
    query.message.edit_text.assert_awaited_once_with(t("fa", "playback_cmd.playing_audio"))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("handler_name", "payload"),
    [
        ("bcw_mode", CB["BCW_MODE_SEND"]),
        ("bcw_targets_next", CB["BCW_TGT_NEXT"]),
        ("bcw_confirm", CB["BCW_CONFIRM"]),
        ("bcw_back_sched", CB["BCW_BACK_SCHED"]),
    ],
)
async def test_broadcast_wizard_missing_state_shows_expired_message(handler_name: str, payload: str):
    bot = _RecorderBot()
    broadcast_wizard.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, handler_name)
    query = _pm_query(settings.DEVELOPER_ID, payload)

    with (
        patch("app.handlers.broadcast_wizard._get_state", AsyncMock(return_value=None)),
        patch("app.handlers.broadcast_wizard._clear_state", AsyncMock()) as clear_mock,
    ):
        await handler(SimpleNamespace(), query)

    clear_mock.assert_not_awaited()
    query.answer.assert_awaited_once_with(t("fa", "broadcast.wizard.expired"), show_alert=True)
    query.message.edit_text.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", ["fm:rm:", "fm:rm:abc"])
async def test_force_join_remove_malformed_payloads_do_not_crash(payload: str):
    bot = _RecorderBot()
    force_join_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "fm_remove_invalid")
    query = _pm_query(settings.DEVELOPER_ID, payload)

    with patch("app.handlers.force_join_panel.ForcedMembershipService.remove_target", AsyncMock()) as remove_mock:
        await handler(SimpleNamespace(), query)

    remove_mock.assert_not_awaited()
    query.answer.assert_awaited_once_with(t("fa", "common.errors.invalid_callback"), show_alert=True)


@pytest.mark.asyncio
async def test_fm_remove_request_does_not_remove_immediately():
    bot = _RecorderBot()
    force_join_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "fm_remove")
    query = _pm_query(settings.DEVELOPER_ID, f"{CB['FM_REMOVE']}:-100123")

    with patch("app.handlers.force_join_panel.ForcedMembershipService.remove_target", AsyncMock()) as remove_mock:
        await handler(SimpleNamespace(), query)

    remove_mock.assert_not_awaited()
    assert query.message.edit_text.await_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", ["pb:sat:page:not_int", "pb:sat:page:-1"])
async def test_satellite_page_malformed_payloads_do_not_crash(payload: str):
    bot = _RecorderBot()
    tv_radio.register(bot, SimpleNamespace())
    handler = _handler_by_name(bot.callback_handlers, "on_satellite_page")
    query = _group_query(600001, payload)

    with patch("app.handlers.tv_radio.authorize_playback_action", AsyncMock(return_value=True)) as auth_mock:
        await handler(SimpleNamespace(), query)

    auth_mock.assert_not_awaited()
    query.answer.assert_awaited_once_with(t("fa", "common.errors.invalid_callback"), show_alert=True)
    query.message.edit_text.assert_not_awaited()


@pytest.mark.asyncio
async def test_satellite_page_out_of_range_is_rejected_safely():
    bot = _RecorderBot()
    tv_radio.register(bot, SimpleNamespace())
    handler = _handler_by_name(bot.callback_handlers, "on_satellite_page")
    query = _group_query(600001, "pb:sat:page:99")
    channels = [{"id": "c0", "name": "Channel 0", "url": "https://example.test/0.m3u8"}]

    with (
        patch("app.handlers.tv_radio.authorize_playback_action", AsyncMock(return_value=True)),
        patch("app.handlers.tv_radio._load_json", return_value=channels),
    ):
        await handler(SimpleNamespace(), query)

    query.answer.assert_awaited_once_with(t("fa", "common.errors.invalid_callback"), show_alert=True)
    query.message.edit_text.assert_not_awaited()


@pytest.mark.asyncio
async def test_valid_satellite_pagination_still_works():
    bot = _RecorderBot()
    tv_radio.register(bot, SimpleNamespace())
    handler = _handler_by_name(bot.callback_handlers, "on_satellite_page")
    query = _group_query(600001, "pb:sat:page:1")
    channels = [
        {"id": f"c{i}", "name": f"Channel {i}", "url": f"https://example.test/{i}.m3u8"}
        for i in range(9)
    ]

    with (
        patch("app.handlers.tv_radio.authorize_playback_action", AsyncMock(return_value=True)) as auth_mock,
        patch("app.handlers.tv_radio._load_json", return_value=channels),
    ):
        await handler(SimpleNamespace(), query)

    auth_mock.assert_awaited_once()
    query.answer.assert_awaited_once()
    query.message.edit_text.assert_awaited_once()
    kb = query.message.edit_text.await_args.kwargs["reply_markup"]
    assert "pb:sat:c8" in _callback_data_set(kb)

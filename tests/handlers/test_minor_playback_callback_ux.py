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

from app.handlers import callbacks
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


def _query(data: str):
    return SimpleNamespace(
        from_user=SimpleNamespace(id=600001, first_name="Tester"),
        data=data,
        answer=AsyncMock(),
        message=SimpleNamespace(
            chat=SimpleNamespace(id=-1001, type=SimpleNamespace(value="supergroup")),
            text="Now playing",
            edit_text=AsyncMock(),
            delete=AsyncMock(),
        ),
    )


class _BeginContext:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _FakeSession:
    def __init__(self, result):
        self.result = result
        self.added = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    def begin(self):
        return _BeginContext()

    async def execute(self, stmt):  # noqa: ANN001
        return self.result

    def add(self, obj):  # noqa: ANN001
        self.added.append(obj)


class _ScalarOneResult:
    def scalar_one_or_none(self):
        return None


class _ScalarsResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return self._rows


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("callback_data", "message_key"),
    [
        (CB["PB_PREV"], "playback.controls.previous_unavailable"),
    ],
)
async def test_unavailable_playback_controls_return_clear_response(
    callback_data: str,
    message_key: str,
):
    bot = _RecorderBot()
    callbacks.register(bot, SimpleNamespace())
    handler = _handler_by_name(bot.callback_handlers, "pb_playback_controls")
    query = _query(callback_data)

    with patch("app.handlers.callbacks.authorize_playback_action", AsyncMock(return_value=True)) as auth_mock:
        await handler(SimpleNamespace(), query)

    auth_mock.assert_awaited_once()
    query.answer.assert_awaited_once_with(t("fa", message_key), show_alert=True)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "callback_data",
    [
        CB["PB_PREV"],
        CB["PB_SPEED_UP"],
        CB["PB_SPEED_DOWN"],
    ],
)
async def test_unavailable_playback_controls_preserve_authorization(
    callback_data: str,
):
    bot = _RecorderBot()
    callbacks.register(bot, SimpleNamespace())
    handler = _handler_by_name(bot.callback_handlers, "pb_playback_controls")
    query = _query(callback_data)

    with patch("app.handlers.callbacks.authorize_playback_action", AsyncMock(return_value=False)) as auth_mock:
        await handler(SimpleNamespace(), query)

    auth_mock.assert_awaited_once()
    query.answer.assert_not_awaited()


@pytest.mark.asyncio
async def test_speed_callback_uses_shared_speed_change_logic():
    bot = _RecorderBot()
    callbacks.register(bot, SimpleNamespace())
    handler = _handler_by_name(bot.callback_handlers, "pb_playback_controls")
    query = _query(CB["PB_SPEED_UP"])
    result = SimpleNamespace(
        message_key="playback.controls.speed_set_position",
        speed_label="1.25",
        show_alert=False,
    )

    with (
        patch("app.handlers.callbacks.authorize_playback_action", AsyncMock(return_value=True)),
        patch(
            "app.services.CallService.change_playback_speed",
            AsyncMock(return_value=result),
        ) as change_speed,
    ):
        await handler(SimpleNamespace(), query)

    query.answer.assert_awaited_once_with(
        t("fa", "playback.controls.speed_set_position", speed="1.25"),
        show_alert=False,
    )
    change_speed.assert_awaited_once()
    assert change_speed.await_args.args[1] == query.message.chat.id
    assert change_speed.await_args.args[2] == 25


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("handler_name", "callback_data", "message_key"),
    [
        ("pb_audio", CB["PB_AUDIO"], "playback.type_hints.audio"),
        ("pb_video", CB["PB_VIDEO"], "playback.type_hints.video"),
        ("pb_download", CB["PB_DOWNLOAD"], "playback.type_hints.download"),
    ],
)
async def test_playback_type_selectors_return_useful_hints(
    handler_name: str,
    callback_data: str,
    message_key: str,
):
    bot = _RecorderBot()
    callbacks.register(bot, SimpleNamespace())
    handler = _handler_by_name(bot.callback_handlers, handler_name)
    query = _query(callback_data)

    await handler(SimpleNamespace(), query)

    query.answer.assert_awaited_once_with(t("fa", message_key), show_alert=True)


@pytest.mark.asyncio
async def test_existing_playback_controls_still_call_services_after_authorization():
    bot = _RecorderBot()
    call_py = SimpleNamespace()
    callbacks.register(bot, call_py)
    client = SimpleNamespace()

    snapshots = [
        {"is_paused": False},
        {"is_paused": True},
        {"is_paused": False},
        {"is_paused": False},
    ]

    with (
        patch("app.handlers.callbacks.authorize_playback_action", AsyncMock(return_value=True)) as auth_mock,
        patch("app.handlers.callbacks._is_playback_active", return_value=True),
        patch("app.handlers.callbacks._playback_snapshot", side_effect=snapshots),
        patch("app.services.CallService.pause", AsyncMock(return_value=True)) as pause_mock,
        patch("app.services.CallService.resume", AsyncMock(return_value=True)) as resume_mock,
        patch("app.services.CallService.play_next", AsyncMock(return_value=True)) as next_mock,
        patch("app.services.CallService.leave_voice_chat", AsyncMock(return_value=True)) as stop_mock,
        patch("app.handlers.callbacks._refresh_now_playing_callback_message", AsyncMock(return_value=True)) as refresh_mock,
    ):
        for callback_data in [
            (CB["PB_PAUSE"]),
            (CB["PB_RESUME"]),
            (CB["PB_NEXT"]),
            (CB["PB_STOP"]),
        ]:
            query = _query(callback_data)
            handler = _handler_by_name(bot.callback_handlers, "pb_playback_controls")
            await handler(client, query)

    assert auth_mock.await_count == 4
    pause_mock.assert_awaited_once_with(call_py, -1001)
    resume_mock.assert_awaited_once_with(call_py, -1001)
    next_mock.assert_awaited_once_with(call_py, -1001)
    stop_mock.assert_awaited_once_with(call_py, -1001)
    refresh_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_favorite_add_prefers_active_metadata_over_message_text():
    bot = _RecorderBot()
    callbacks.register(bot, SimpleNamespace())
    handler = _handler_by_name(bot.callback_handlers, "pb_playback_controls")
    query = _query(CB["PB_FAV_ADD"])
    query.message.text = "Old now-playing text"
    query.message.caption = "Old caption"
    fake_session = _FakeSession(_ScalarOneResult())

    with (
        patch("app.handlers.callbacks.authorize_playback_action", AsyncMock(return_value=True)),
        patch("app.services.CallService.get_active_calls", return_value={
            -1001: {
                "title": "Active Video",
                "media_type": "video",
                "duration_seconds": 123,
                "source": "https://cdn.example.test/video.mp4",
            }
        }),
        patch("app.database.engine.async_session", lambda: fake_session),
    ):
        await handler(SimpleNamespace(), query)

    assert len(fake_session.added) == 1
    favorite = fake_session.added[0]
    assert favorite.title == "Active Video"
    assert favorite.media_type == "video"
    assert favorite.duration_seconds == 123
    assert favorite.stream_url == "https://cdn.example.test/video.mp4"
    query.answer.assert_awaited_once_with(t("fa", "favorites.added"), show_alert=False)


@pytest.mark.asyncio
async def test_favorite_play_refreshes_display_after_confirmed_success():
    bot = _RecorderBot()
    call_py = SimpleNamespace()
    callbacks.register(bot, call_py)
    handler = _handler_by_name(bot.callback_handlers, "pb_playback_controls")
    query = _query(CB["PB_FAV_PLAY"])
    favorite = SimpleNamespace(
        stream_url="https://cdn.example.test/video.mp4",
        media_type="video",
        title="Saved Video",
        duration_seconds=88,
    )
    fake_session = _FakeSession(_ScalarsResult([favorite]))

    with (
        patch("app.handlers.callbacks.authorize_playback_action", AsyncMock(return_value=True)),
        patch("app.database.engine.async_session", lambda: fake_session),
        patch("app.services.CallService.join_voice_chat", AsyncMock(return_value=True)) as join_mock,
        patch("app.handlers.callbacks._refresh_now_playing_callback_message", AsyncMock(return_value=True)) as refresh_mock,
    ):
        await handler(SimpleNamespace(), query)

    join_mock.assert_awaited_once_with(
        call_py,
        -1001,
        "https://cdn.example.test/video.mp4",
        "video",
        user_id=600001,
        title="Saved Video",
        event_source="https://cdn.example.test/video.mp4",
        source_kind_hint="favorite",
        playback_feature=None,
    )
    query.answer.assert_awaited_once_with(t("fa", "favorites.playing"), show_alert=False)
    refresh_mock.assert_awaited_once_with(
        query,
        fallback_title="Saved Video",
        fallback_media_type="video",
        fallback_duration=88,
    )


@pytest.mark.asyncio
async def test_display_refresh_edit_failure_is_safe_noop_without_fallback_send():
    query = _query(CB["PB_NEXT"])
    query.message.reply = AsyncMock()

    with (
        patch("app.handlers.callbacks._playback_snapshot", return_value={
            "title": "Next Track",
            "media_type": "audio",
        }),
        patch("app.services.language_service.resolve_lang", AsyncMock(return_value="fa")),
        patch("app.services.now_playing_renderer.render_now_playing_text", AsyncMock(return_value="now playing")),
        patch("app.services.media_capability_service.build_now_playing_controls", AsyncMock(return_value="controls")),
        patch("app.handlers.callbacks.safe_edit_message", AsyncMock(return_value=False)) as edit_mock,
    ):
        ok = await callbacks._refresh_now_playing_callback_message(query)

    assert ok is False
    edit_mock.assert_awaited_once()
    query.message.reply.assert_not_awaited()


@pytest.mark.asyncio
async def test_display_refresh_uses_active_playback_feature_metadata():
    query = _query(CB["PB_NEXT"])
    seen = {}

    async def _render(chat_id, context, **kwargs):  # noqa: ANN001
        seen["title"] = context.title
        seen["media_type"] = context.media_type
        return "now playing"

    with (
        patch("app.handlers.callbacks._playback_snapshot", return_value={
            "title": "TV One",
            "media_type": "video",
            "playback_feature": "tv",
        }),
        patch("app.services.language_service.resolve_lang", AsyncMock(return_value="fa")),
        patch("app.services.now_playing_renderer.render_now_playing_text", _render),
        patch("app.services.media_capability_service.build_now_playing_controls", AsyncMock(return_value="controls")),
        patch("app.handlers.callbacks.safe_edit_message", AsyncMock(return_value=True)),
    ):
        ok = await callbacks._refresh_now_playing_callback_message(query)

    assert ok is True
    assert seen == {"title": "TV One", "media_type": "tv"}

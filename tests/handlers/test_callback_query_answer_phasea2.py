"""Phase A-2 callback handlers answer queries to clear Telegram spinners."""
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

    class _ListenerStopped(Exception):
        pass

    pyromod_exceptions.ListenerStopped = _ListenerStopped
    pyromod_module.exceptions = pyromod_exceptions
    sys.modules["pyromod"] = pyromod_module
    sys.modules["pyromod.exceptions"] = pyromod_exceptions


class _RecorderBot:
    def __init__(self) -> None:
        self.callback_handlers: list = []
        self._core_callbacks_registered = False

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
    return next(fn for fn in handlers if fn.__name__ == name)


def _group_query(data: str):
    return SimpleNamespace(
        from_user=SimpleNamespace(id=42, first_name="GroupUser"),
        data=data,
        answer=AsyncMock(),
        message=SimpleNamespace(
            chat=SimpleNamespace(id=-1001, type=SimpleNamespace(value="supergroup")),
            text="menu",
            edit_text=AsyncMock(),
        ),
    )


@pytest.mark.asyncio
async def test_search_play_callback_answers_query():
    from app.handlers import search
    from app.utils.i18n import t

    bot = _RecorderBot()
    search.register(bot, SimpleNamespace())
    handler = _handler_by_name(bot.callback_handlers, "on_search_select")
    query = _group_query("search:play:dQw4w9WgXcQ")

    with (
        patch("app.handlers.search.authorize_playback_action", AsyncMock(return_value=True)),
        patch("app.handlers.search.validate_safe_url_with_redirects", AsyncMock(return_value="https://youtube.com/watch?v=dQw4w9WgXcQ")),
        patch("app.handlers.search.MediaService.get_stream_url", AsyncMock(return_value="https://cdn.example.test/a.mp3")),
        patch("app.handlers.search.CallService.join_voice_chat", AsyncMock(return_value=True)),
        patch("app.handlers.search.CallService.get_active_calls", return_value={}),
        patch("app.handlers.search.render_now_playing_text", AsyncMock(return_value="now playing")),
        patch("app.handlers.search.build_now_playing_controls", AsyncMock(return_value="controls")),
    ):
        await handler(SimpleNamespace(), query)

    query.answer.assert_awaited_once_with(t("fa", "search.downloading"), show_alert=False)
    query.message.edit_text.assert_awaited_once_with(
        "now playing",
        reply_markup="controls",
        disable_web_page_preview=True,
    )


@pytest.mark.asyncio
async def test_search_play_display_uses_active_media_type_for_result():
    from app.handlers import search

    bot = _RecorderBot()
    search.register(bot, SimpleNamespace())
    handler = _handler_by_name(bot.callback_handlers, "on_search_select")
    query = _group_query("search:play:dQw4w9WgXcQ")
    seen = {}

    async def _render(chat_id, context, **kwargs):  # noqa: ANN001
        seen["media_type"] = context.media_type
        seen["title"] = context.title
        return "now playing"

    with (
        patch("app.handlers.search.authorize_playback_action", AsyncMock(return_value=True)),
        patch("app.handlers.search.validate_safe_url_with_redirects", AsyncMock(return_value="https://youtube.com/watch?v=dQw4w9WgXcQ")),
        patch("app.handlers.search.MediaService.get_stream_url", AsyncMock(return_value="https://cdn.example.test/v.mp4")),
        patch("app.handlers.search.CallService.join_voice_chat", AsyncMock(return_value=True)),
        patch("app.handlers.search.CallService.get_active_calls", return_value={
            -1001: {"title": "Active Video", "media_type": "video"}
        }),
        patch("app.handlers.search.render_now_playing_text", _render),
        patch("app.handlers.search.build_now_playing_controls", AsyncMock(return_value="controls")),
    ):
        await handler(SimpleNamespace(), query)

    assert seen == {"media_type": "video", "title": "Active Video"}
    query.message.edit_text.assert_awaited_once()


@pytest.mark.asyncio
async def test_tv_item_callback_answers_before_join():
    from app.handlers import tv_radio

    bot = _RecorderBot()
    tv_radio.register(bot, SimpleNamespace())
    handler = _handler_by_name(bot.callback_handlers, "on_tv_select")
    query = _group_query("pb:tv:t1")
    channels = [{"id": "t1", "name": "TV One", "url": "https://tv.example/1.m3u8"}]

    with (
        patch("app.handlers.tv_radio._load_json", return_value=channels),
        patch("app.handlers.tv_radio.authorize_playback_action", AsyncMock(return_value=True)),
        patch("app.handlers.tv_radio.deny_free_mode_media", AsyncMock(return_value=False)),
        patch("app.handlers.tv_radio.validate_safe_url_with_redirects", AsyncMock(return_value=channels[0]["url"])),
        patch("app.handlers.tv_radio.CallService.join_voice_chat", AsyncMock(return_value=True)),
        patch("app.handlers.tv_radio.resolve_lang", AsyncMock(return_value="fa")),
        patch("app.handlers.tv_radio.render_now_playing_text", AsyncMock(return_value="now playing")),
        patch("app.handlers.tv_radio.build_now_playing_controls", AsyncMock(return_value=SimpleNamespace(inline_keyboard=[]))),
    ):
        await handler(SimpleNamespace(), query)

    assert query.answer.await_count == 1


@pytest.mark.asyncio
async def test_search_play_fallback_send_is_bounded_for_stale_message():
    from app.handlers import search

    search._PLAYBACK_RESULT_FALLBACK_SENT.clear()
    query = _group_query("search:play:dQw4w9WgXcQ")
    query.message.id = 77
    query.message.reply = AsyncMock()
    query.message.edit_text = AsyncMock(side_effect=RuntimeError("message deleted"))
    query.message.edit_caption = AsyncMock(side_effect=RuntimeError("message deleted"))
    query.message.edit_reply_markup = AsyncMock(side_effect=RuntimeError("message deleted"))

    first = await search._safe_edit_search_playback_result(
        SimpleNamespace(),
        query,
        "fallback",
        reply_markup="controls",
    )
    second = await search._safe_edit_search_playback_result(
        SimpleNamespace(),
        query,
        "fallback",
        reply_markup="controls",
    )

    assert first == "fallback_sent"
    assert second == "fallback_skipped"
    query.message.reply.assert_awaited_once_with("fallback", reply_markup="controls")


@pytest.mark.asyncio
async def test_tv_radio_fallback_send_is_bounded_for_stale_message():
    from app.handlers import tv_radio

    tv_radio._PLAYBACK_RESULT_FALLBACK_SENT.clear()
    query = _group_query("pb:tv:t1")
    query.message.id = 88
    query.message.reply = AsyncMock()
    query.message.edit_text = AsyncMock(side_effect=RuntimeError("message deleted"))
    query.message.edit_caption = AsyncMock(side_effect=RuntimeError("message deleted"))
    query.message.edit_reply_markup = AsyncMock(side_effect=RuntimeError("message deleted"))

    first = await tv_radio._safe_edit_stream_playback_result(
        SimpleNamespace(),
        query,
        "fallback",
        reply_markup="controls",
    )
    second = await tv_radio._safe_edit_stream_playback_result(
        SimpleNamespace(),
        query,
        "fallback",
        reply_markup="controls",
    )

    assert first == "fallback_sent"
    assert second == "fallback_skipped"
    query.message.reply.assert_awaited_once_with("fallback", reply_markup="controls")


@pytest.mark.asyncio
async def test_pb_tv_menu_callback_answers_before_edit():
    from app.config.settings import settings
    from app.handlers import callbacks
    from app.utils.ui import CB

    bot = _RecorderBot()
    callbacks.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "pb_tv")
    query = _group_query(CB["PB_TV"])
    channels = [{"id": "t1", "name": "TV One", "url": "https://tv.example/1.m3u8"}]

    with (
        patch("app.handlers.callbacks.authorize_playback_action", AsyncMock(return_value=True)),
        patch("app.services.media_capability_service.deny_free_mode_media", AsyncMock(return_value=False)),
        patch("app.handlers.tv_radio._load_json", return_value=channels),
    ):
        await handler(SimpleNamespace(), query)

    assert query.answer.await_count == 1
    query.message.edit_text.assert_awaited_once()


@pytest.mark.asyncio
async def test_pb_audio_alert_only_single_answer():
    from app.handlers import callbacks
    from app.utils.i18n import t
    from app.utils.ui import CB

    bot = _RecorderBot()
    callbacks.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "pb_audio")
    query = _group_query(CB["PB_AUDIO"])

    await handler(SimpleNamespace(), query)

    query.answer.assert_awaited_once_with(t("fa", "playback.type_hints.audio"), show_alert=True)

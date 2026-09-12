"""TV/Radio/Satellite playback maps join failures like main playback."""
from __future__ import annotations

import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

os.environ.setdefault("TEST_MODE", "1")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./.pytest-test-mode.db")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/15")
os.environ.setdefault("BOT_TOKEN", "test")
os.environ.setdefault("API_ID", "12345")
os.environ.setdefault("API_HASH", "testhash")
os.environ.setdefault("DEVELOPER_ID", "123456789")

from app.utils.i18n import t


class _RecorderBot:
    def __init__(self) -> None:
        self.callback_handlers: list = []

    def on_callback_query(self, *args, **kwargs):  # noqa: ANN001, ANN002
        def _decorator(fn):
            self.callback_handlers.append(fn)
            return fn

        return _decorator

    def on_message(self, *args, **kwargs):  # noqa: ANN001, ANN002
        def _decorator(fn):
            return fn

        return _decorator


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


def _register_tv_radio(call_py):
    from app.handlers import tv_radio

    bot = _RecorderBot()
    tv_radio.register(bot, call_py)
    return bot


@pytest.fixture(autouse=True)
def _clear_join_failure_key():
    from app.services.call_service import CallService

    CallService.pop_join_failure_key()
    yield
    CallService.pop_join_failure_key()


@pytest.mark.asyncio
async def test_radio_play_maps_helper_unavailable():
    from app.utils.i18n import t

    bot = _register_tv_radio(MagicMock())
    handler = _handler_by_name(bot.callback_handlers, "on_radio_select")
    query = _group_query("pb:radio:r1")
    stations = [{"id": "r1", "name": "Radio One", "url": "https://93.184.216.34/radio.mp3"}]

    with (
        patch("app.handlers.tv_radio._load_json", return_value=stations),
        patch("app.handlers.tv_radio.authorize_playback_action", AsyncMock(return_value=True)),
        patch("app.handlers.tv_radio.validate_safe_url_with_redirects", AsyncMock(return_value=stations[0]["url"])),
        patch("app.services.analytics_service.track_event", AsyncMock()),
        patch(
            "app.services.media_capability_service.is_media_feature_allowed",
            AsyncMock(return_value=True),
        ),
        patch(
            "app.services.call_service.resolve_media_source_for_playback",
            AsyncMock(side_effect=lambda source, **kwargs: source),
        ),
        patch("app.services.call_service._ensure_helper_in_chat", AsyncMock(return_value=None)),
    ):
        await handler(SimpleNamespace(), query)

    query.message.edit_text.assert_awaited_once()
    assert query.message.edit_text.await_args.args[0] == t("fa", "playback_cmd.helper_unavailable")


@pytest.mark.asyncio
async def test_tv_play_maps_voice_chat_unavailable():
    from app.utils.i18n import t

    bot = _register_tv_radio(None)
    handler = _handler_by_name(bot.callback_handlers, "on_tv_select")
    query = _group_query("pb:tv:t1")
    channels = [{"id": "t1", "name": "TV One", "url": "https://93.184.216.34/tv.m3u8"}]

    with (
        patch("app.handlers.tv_radio._load_json", return_value=channels),
        patch("app.handlers.tv_radio.authorize_playback_action", AsyncMock(return_value=True)),
        patch("app.handlers.tv_radio.deny_free_mode_media", AsyncMock(return_value=False)),
        patch("app.handlers.tv_radio.validate_safe_url_with_redirects", AsyncMock(return_value=channels[0]["url"])),
        patch("app.handlers.tv_radio.CallService.join_voice_chat", AsyncMock(return_value=False)),
        patch("app.handlers.tv_radio.CallService.pop_join_failure_key", return_value="playback_cmd.voice_chat_unavailable"),
        patch("app.utils.playback_errors.track_event", AsyncMock()),
    ):
        await handler(SimpleNamespace(), query)

    query.message.edit_text.assert_awaited_once()
    assert query.message.edit_text.await_args.args[0] == t("fa", "playback_cmd.voice_chat_unavailable")


@pytest.mark.asyncio
async def test_satellite_play_maps_stream_unavailable():
    from app.utils.i18n import t

    bot = _register_tv_radio(MagicMock())
    handler = _handler_by_name(bot.callback_handlers, "on_satellite_select")
    query = _group_query("pb:sat:s1")
    channels = [{"id": "s1", "name": "Sat One", "url": "https://93.184.216.34/sat.m3u8"}]

    with (
        patch("app.handlers.tv_radio._load_json", return_value=channels),
        patch("app.handlers.tv_radio.authorize_playback_action", AsyncMock(return_value=True)),
        patch("app.handlers.tv_radio.deny_free_mode_media", AsyncMock(return_value=False)),
        patch("app.handlers.tv_radio.validate_safe_url_with_redirects", AsyncMock(return_value=channels[0]["url"])),
        patch("app.handlers.tv_radio.CallService.join_voice_chat", AsyncMock(return_value=False)),
        patch("app.handlers.tv_radio.CallService.pop_join_failure_key", return_value="playback_cmd.stream_unavailable"),
        patch("app.utils.playback_errors.track_event", AsyncMock()),
    ):
        await handler(SimpleNamespace(), query)

    query.message.edit_text.assert_awaited_once()
    assert query.message.edit_text.await_args.args[0] == t("fa", "playback_cmd.stream_unavailable")


@pytest.mark.asyncio
async def test_tv_play_free_mode_deny_uses_existing_alert():
    from app.utils.i18n import t

    bot = _register_tv_radio(MagicMock())
    handler = _handler_by_name(bot.callback_handlers, "on_tv_select")
    query = _group_query("pb:tv:t1")
    channels = [{"id": "t1", "name": "TV One", "url": "https://93.184.216.34/tv.m3u8"}]

    async def _deny(query_obj, feature, *, lang="fa"):
        await query_obj.answer(t(lang, "free_mode.blocked_tv_satellite"), show_alert=True)
        return True

    with (
        patch("app.handlers.tv_radio._load_json", return_value=channels),
        patch("app.handlers.tv_radio.authorize_playback_action", AsyncMock(return_value=True)),
        patch("app.handlers.tv_radio.deny_free_mode_media", side_effect=_deny),
        patch("app.handlers.tv_radio.CallService.join_voice_chat", AsyncMock()) as join_mock,
    ):
        await handler(SimpleNamespace(), query)

    join_mock.assert_not_awaited()
    query.answer.assert_awaited_once_with(t("fa", "free_mode.blocked_tv_satellite"), show_alert=True)


@pytest.mark.asyncio
async def test_radio_play_success_uses_renderer():
    bot = _register_tv_radio(MagicMock())
    handler = _handler_by_name(bot.callback_handlers, "on_radio_select")
    query = _group_query("pb:radio:r1")
    stations = [{"id": "r1", "name": "Radio One", "url": "https://93.184.216.34/radio.mp3"}]

    with (
        patch("app.handlers.tv_radio._load_json", return_value=stations),
        patch("app.handlers.tv_radio.authorize_playback_action", AsyncMock(return_value=True)),
        patch("app.handlers.tv_radio.validate_safe_url_with_redirects", AsyncMock(return_value=stations[0]["url"])),
        patch("app.handlers.tv_radio.CallService.join_voice_chat", AsyncMock(return_value=True)),
        patch("app.handlers.tv_radio.CallService.get_repeat_state", return_value=False),
        patch("app.handlers.tv_radio.resolve_lang", AsyncMock(return_value="fa")),
    ):
        await handler(SimpleNamespace(), query)

    query.message.edit_text.assert_awaited_once()
    text = query.message.edit_text.await_args.args[0]
    assert "Radio One" in text
    assert "رادیو" in text


@pytest.mark.asyncio
async def test_tv_join_failure_stale_message_does_not_crash():
    bot = _register_tv_radio(MagicMock())
    handler = _handler_by_name(bot.callback_handlers, "on_tv_select")
    query = _group_query("pb:tv:t1")
    query.message.edit_text = AsyncMock(side_effect=RuntimeError("message deleted"))
    query.message.edit_caption = AsyncMock(side_effect=RuntimeError("message deleted"))
    query.message.edit_reply_markup = AsyncMock(side_effect=RuntimeError("message deleted"))
    channels = [{"id": "t1", "name": "TV One", "url": "https://93.184.216.34/tv.m3u8"}]

    with (
        patch("app.handlers.tv_radio._load_json", return_value=channels),
        patch("app.handlers.tv_radio.authorize_playback_action", AsyncMock(return_value=True)),
        patch("app.handlers.tv_radio.deny_free_mode_media", AsyncMock(return_value=False)),
        patch("app.handlers.tv_radio.validate_safe_url_with_redirects", AsyncMock(return_value=channels[0]["url"])),
        patch("app.handlers.tv_radio.CallService.join_voice_chat", AsyncMock(return_value=False)),
        patch("app.utils.playback_errors.track_event", AsyncMock()),
    ):
        await handler(SimpleNamespace(), query)

    query.answer.assert_awaited_once()
    assert query.message.edit_text.await_count >= 1


@pytest.mark.asyncio
async def test_tv_blocked_url_stale_edit_fallback_is_bounded():
    from app.handlers import tv_radio

    tv_radio._PLAYBACK_RESULT_FALLBACK_SENT.clear()
    bot = _register_tv_radio(MagicMock())
    handler = _handler_by_name(bot.callback_handlers, "on_tv_select")
    query = _group_query("pb:tv:t1")
    query.message.id = 91
    query.message.reply = AsyncMock()
    query.message.edit_text = AsyncMock(side_effect=RuntimeError("message deleted"))
    query.message.edit_caption = AsyncMock(side_effect=RuntimeError("message deleted"))
    query.message.edit_reply_markup = AsyncMock(side_effect=RuntimeError("message deleted"))
    channels = [{"id": "t1", "name": "TV One", "url": "https://93.184.216.34/tv.m3u8"}]

    with (
        patch("app.handlers.tv_radio._load_json", return_value=channels),
        patch("app.handlers.tv_radio.authorize_playback_action", AsyncMock(return_value=True)),
        patch("app.handlers.tv_radio.deny_free_mode_media", AsyncMock(return_value=False)),
        patch("app.handlers.tv_radio.validate_safe_url_with_redirects", AsyncMock(return_value=None)),
    ):
        await handler(SimpleNamespace(), query)
        await handler(SimpleNamespace(), query)

    query.message.reply.assert_awaited_once()
    assert query.message.reply.await_args.args[0] == t("fa", "playback_cmd.blocked_url")


@pytest.mark.asyncio
async def test_satellite_page_message_not_modified_is_success_no_fallback():
    bot = _register_tv_radio(MagicMock())
    handler = _handler_by_name(bot.callback_handlers, "on_satellite_page")
    query = _group_query("pb:sat:page:1")
    query.message.reply = AsyncMock()
    query.message.edit_text = AsyncMock(side_effect=RuntimeError("MESSAGE_NOT_MODIFIED"))
    channels = [
        {"id": f"s{idx}", "name": f"Sat {idx}", "url": f"https://93.184.216.34/{idx}.m3u8"}
        for idx in range(9)
    ]

    with (
        patch("app.handlers.tv_radio.authorize_playback_action", AsyncMock(return_value=True)),
        patch("app.handlers.tv_radio._load_json", return_value=channels),
    ):
        await handler(SimpleNamespace(), query)

    query.message.edit_text.assert_awaited_once()
    query.message.reply.assert_not_awaited()


@pytest.mark.asyncio
async def test_radio_busy_message_stale_edit_fallback_is_bounded():
    from app.handlers import tv_radio
    from app.services.playback_dispatch_service import BusyPlaybackAction, BusyPlaybackDecision

    tv_radio._PLAYBACK_RESULT_FALLBACK_SENT.clear()
    bot = _register_tv_radio(MagicMock())
    handler = _handler_by_name(bot.callback_handlers, "on_radio_select")
    query = _group_query("pb:radio:r1")
    query.message.id = 92
    query.message.reply = AsyncMock()
    query.message.edit_text = AsyncMock(side_effect=RuntimeError("message deleted"))
    query.message.edit_caption = AsyncMock(side_effect=RuntimeError("message deleted"))
    query.message.edit_reply_markup = AsyncMock(side_effect=RuntimeError("message deleted"))
    stations = [{"id": "r1", "name": "Radio One", "url": "https://93.184.216.34/radio.mp3"}]
    decision = BusyPlaybackDecision(
        action=BusyPlaybackAction.ALREADY_PLAYING,
        message_key="playback_cmd.already_playing",
    )

    with (
        patch("app.handlers.tv_radio._load_json", return_value=stations),
        patch("app.handlers.tv_radio.authorize_playback_action", AsyncMock(return_value=True)),
        patch("app.handlers.tv_radio.validate_safe_url_with_redirects", AsyncMock(return_value=stations[0]["url"])),
        patch("app.handlers.tv_radio.decide_busy_playback", AsyncMock(return_value=decision)),
        patch("app.handlers.tv_radio.apply_busy_playback_decision", AsyncMock(return_value=True)),
    ):
        await handler(SimpleNamespace(), query)
        await handler(SimpleNamespace(), query)

    query.message.reply.assert_awaited_once()
    assert query.message.reply.await_args.args[0] == t("fa", "playback_cmd.already_playing")

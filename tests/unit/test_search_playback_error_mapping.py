"""Search result playback maps join failures like main playback."""
from __future__ import annotations

import os
from contextlib import contextmanager
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

_SEARCH_PLAY_PREFIX = "search:play:"
_VIDEO_ID = "dQw4w9WgXcQ"


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


def _register_search_handler(call_py):
    from app.handlers import search

    bot = _RecorderBot()
    search.register(bot, call_py)
    return _handler_by_name(bot.callback_handlers, "on_search_select")


@contextmanager
def _search_patches():
    url = f"https://youtube.com/watch?v={_VIDEO_ID}"
    with (
        patch("app.handlers.search.authorize_playback_action", AsyncMock(return_value=True)),
        patch("app.handlers.search.validate_safe_url_with_redirects", AsyncMock(return_value=url)),
        patch("app.handlers.search.MediaService.get_stream_url", AsyncMock(return_value="https://93.184.216.34/a.mp3")),
        patch("app.services.analytics_service.track_event", AsyncMock()),
        patch(
            "app.services.media_capability_service.is_media_feature_allowed",
            AsyncMock(return_value=True),
        ),
        patch(
            "app.services.call_service.resolve_media_source_for_playback",
            AsyncMock(side_effect=lambda source, **kwargs: source),
        ),
    ):
        yield


@pytest.fixture(autouse=True)
def _clear_join_failure_key():
    from app.services.call_service import CallService

    CallService.pop_join_failure_key()
    yield
    CallService.pop_join_failure_key()


@pytest.mark.asyncio
async def test_search_play_maps_helper_unavailable():
    from app.utils.i18n import t

    handler = _register_search_handler(MagicMock())
    query = _group_query(600001, f"{_SEARCH_PLAY_PREFIX}{_VIDEO_ID}")

    with (
        _search_patches(),
        patch("app.services.call_service._ensure_helper_in_chat", AsyncMock(return_value=None)),
    ):
        await handler(SimpleNamespace(), query)

    query.message.edit_text.assert_awaited_once()
    assert query.message.edit_text.await_args.args[0] == t("fa", "playback_cmd.helper_unavailable")
    assert query.data.startswith(_SEARCH_PLAY_PREFIX)


@pytest.mark.asyncio
async def test_search_play_maps_voice_chat_unavailable():
    from app.utils.i18n import t

    handler = _register_search_handler(None)
    query = _group_query(600002, f"{_SEARCH_PLAY_PREFIX}{_VIDEO_ID}")

    with (
        _search_patches(),
        patch("app.handlers.search.CallService.join_voice_chat", AsyncMock(return_value=False)),
        patch("app.handlers.search.playback_failure_text", return_value=t("fa", "playback_cmd.voice_chat_unavailable")),
    ):
        await handler(SimpleNamespace(), query)

    query.message.edit_text.assert_awaited_once()
    assert query.message.edit_text.await_args.args[0] == t("fa", "playback_cmd.voice_chat_unavailable")


@pytest.mark.asyncio
async def test_search_play_maps_stream_unavailable():
    from app.utils.i18n import t

    handler = _register_search_handler(MagicMock())
    query = _group_query(600003, f"{_SEARCH_PLAY_PREFIX}{_VIDEO_ID}")

    with (
        _search_patches(),
        patch("app.handlers.search.CallService.join_voice_chat", AsyncMock(return_value=False)),
        patch("app.handlers.search.playback_failure_text", return_value=t("fa", "playback_cmd.stream_unavailable")),
    ):
        await handler(SimpleNamespace(), query)

    query.message.edit_text.assert_awaited_once()
    assert query.message.edit_text.await_args.args[0] == t("fa", "playback_cmd.stream_unavailable")


@pytest.mark.asyncio
async def test_search_play_success_uses_renderer():
    call_py = SimpleNamespace()
    handler = _register_search_handler(call_py)
    query = _group_query(600004, f"{_SEARCH_PLAY_PREFIX}{_VIDEO_ID}")

    with (
        _search_patches(),
        patch("app.handlers.search.CallService.join_voice_chat", AsyncMock(return_value=True)) as join_mock,
        patch("app.handlers.search.resolve_lang", AsyncMock(return_value="fa")),
    ):
        await handler(SimpleNamespace(), query)

    join_mock.assert_awaited_once()
    query.message.edit_text.assert_awaited_once()
    text = query.message.edit_text.await_args.args[0]
    assert "موزیک در حال پخش" in text
    assert _VIDEO_ID not in text
    assert query.data == f"{_SEARCH_PLAY_PREFIX}{_VIDEO_ID}"


@pytest.mark.asyncio
async def test_search_play_callback_prefix_unchanged():
    assert _SEARCH_PLAY_PREFIX == "search:play:"

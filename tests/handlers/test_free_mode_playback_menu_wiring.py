"""Production wiring for capability-aware playback type menu."""
from __future__ import annotations

import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

os.environ.setdefault("TEST_MODE", "1")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./.pytest-test-mode.db")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/15")

from app.services.media_capability_service import (
    MediaCapabilities,
    build_playback_type_menu,
)
from app.utils.i18n import AUTO_LANG, t
from app.utils.ui import CB, KeyboardFactory

_FULL_CAPS = MediaCapabilities(is_restricted=False)
_FREE_CAPS = MediaCapabilities(
    is_restricted=True,
    audio=True,
    download=True,
    video=False,
    tv=False,
    radio=True,
    satellite=False,
)


def _menu_callbacks(kb) -> set[str]:
    return {
        btn.callback_data
        for row in kb.inline_keyboard
        for btn in row
        if btn.callback_data
    }


@pytest.mark.asyncio
async def test_build_playback_type_menu_uses_capabilities_for_paid_chat():
    with patch(
        "app.services.media_capability_service.get_chat_media_capabilities",
        AsyncMock(return_value=_FULL_CAPS),
    ):
        kb = await build_playback_type_menu("fa", -1001)

    callbacks = _menu_callbacks(kb)
    assert CB["PB_VIDEO"] in callbacks
    assert CB["PB_TV"] in callbacks
    assert CB["PB_SAT"] in callbacks


@pytest.mark.asyncio
async def test_build_playback_type_menu_free_chat_hides_blocked_types():
    with patch(
        "app.services.media_capability_service.get_chat_media_capabilities",
        AsyncMock(return_value=_FREE_CAPS),
    ):
        kb = await build_playback_type_menu("fa", -1001)

    callbacks = _menu_callbacks(kb)
    assert CB["PB_AUDIO"] in callbacks
    assert CB["PB_DOWNLOAD"] in callbacks
    assert CB["PB_RADIO"] in callbacks
    assert CB["PB_VIDEO"] not in callbacks
    assert CB["PB_TV"] not in callbacks
    assert CB["PB_SAT"] not in callbacks


@pytest.mark.asyncio
async def test_build_playback_type_menu_lookup_failure_falls_back_to_full_menu():
    with patch(
        "app.services.media_capability_service.get_chat_media_capabilities",
        AsyncMock(side_effect=RuntimeError("db unavailable")),
    ):
        kb = await build_playback_type_menu("fa", -1001)

    callbacks = _menu_callbacks(kb)
    assert CB["PB_VIDEO"] in callbacks
    assert CB["PB_TV"] in callbacks
    assert CB["PB_SAT"] in callbacks


@pytest.mark.asyncio
async def test_grp_help_play_renders_capability_aware_menu():
    from app.handlers import group_panel

    query = SimpleNamespace(
        answer=AsyncMock(),
        message=SimpleNamespace(
            chat=SimpleNamespace(id=-1001, type=SimpleNamespace(value="supergroup")),
            edit_text=AsyncMock(),
        ),
    )
    free_kb = KeyboardFactory.playback_type_menu("fa", capabilities=_FREE_CAPS)
    handlers: list = []

    class _RecorderBot:
        def on_callback_query(self, *args, **kwargs):  # noqa: ANN002, ANN003
            def _decorator(fn):
                handlers.append(fn)
                return fn

            return _decorator

        def on_message(self, *args, **kwargs):  # noqa: ANN002, ANN003
            def _decorator(fn):
                return fn

            return _decorator

        def __getattr__(self, name: str):
            if name.startswith("on_"):
                def _noop(*args, **kwargs):  # noqa: ANN002, ANN003
                    def _decorator(fn):
                        return fn

                    return _decorator

                return _noop
            raise AttributeError(name)

    group_panel.register(_RecorderBot(), SimpleNamespace())
    handler = next(fn for fn in handlers if fn.__name__ == "grp_help_play")

    with patch(
        "app.handlers.group_panel.build_playback_type_menu",
        AsyncMock(return_value=free_kb),
    ) as build_mock:
        await handler(SimpleNamespace(), query)

    build_mock.assert_awaited_once_with(AUTO_LANG, -1001)
    query.message.edit_text.assert_awaited_once()
    args, kwargs = query.message.edit_text.await_args
    assert args[0] == t(AUTO_LANG, "playback.choose_source")
    assert kwargs["reply_markup"] is free_kb
    callbacks = _menu_callbacks(kwargs["reply_markup"])
    assert CB["PB_AUDIO"] in callbacks
    assert CB["PB_VIDEO"] not in callbacks


@pytest.mark.asyncio
async def test_grp_help_play_paid_chat_shows_full_menu():
    from app.handlers import group_panel

    query = SimpleNamespace(
        answer=AsyncMock(),
        message=SimpleNamespace(
            chat=SimpleNamespace(id=-1002, type=SimpleNamespace(value="supergroup")),
            edit_text=AsyncMock(),
        ),
    )
    full_kb = KeyboardFactory.playback_type_menu("fa", capabilities=_FULL_CAPS)

    handlers: list = []

    class _RecorderBot:
        def on_callback_query(self, *args, **kwargs):  # noqa: ANN002, ANN003
            def _decorator(fn):
                handlers.append(fn)
                return fn

            return _decorator

        def on_message(self, *args, **kwargs):  # noqa: ANN002, ANN003
            def _decorator(fn):
                return fn

            return _decorator

        def __getattr__(self, name: str):
            if name.startswith("on_"):
                def _noop(*args, **kwargs):  # noqa: ANN002, ANN003
                    def _decorator(fn):
                        return fn

                    return _decorator

                return _noop
            raise AttributeError(name)

    recorder = _RecorderBot()
    group_panel.register(recorder, SimpleNamespace())
    handler = next(fn for fn in handlers if fn.__name__ == "grp_help_play")

    with patch(
        "app.handlers.group_panel.build_playback_type_menu",
        AsyncMock(return_value=full_kb),
    ):
        await handler(SimpleNamespace(), query)

    callbacks = _menu_callbacks(full_kb)
    assert CB["PB_VIDEO"] in callbacks
    assert CB["PB_TV"] in callbacks
    assert CB["PB_SAT"] in callbacks

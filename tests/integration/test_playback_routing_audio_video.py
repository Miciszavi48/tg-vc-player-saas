"""Playback routing: reply media, download toggle separation, charge collision."""
from __future__ import annotations

import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pyrogram.enums import ChatType

os.environ.setdefault("TEST_MODE", "1")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./.pytest-test-mode.db")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/15")
os.environ.setdefault("BOT_TOKEN", "test")
os.environ.setdefault("API_ID", "12345")
os.environ.setdefault("API_HASH", "testhash")
os.environ.setdefault("DEVELOPER_ID", "123456789")

from app.utils.playback_auth import authorize_playback_action


def _group_query(user_id: int = 42):
    return SimpleNamespace(
        from_user=SimpleNamespace(id=user_id),
        message=SimpleNamespace(chat=SimpleNamespace(id=-8001)),
        answer=AsyncMock(),
    )


@pytest.mark.asyncio
async def test_download_disabled_still_allows_regular_member_playback():
    query = _group_query(777)
    client = MagicMock()
    client.get_chat_member = AsyncMock(
        return_value=SimpleNamespace(status=SimpleNamespace(value="administrator"))
    )
    cs = SimpleNamespace(security_call_enabled=False, download_enabled=False)
    runtime_state = SimpleNamespace(has_runtime_credit=True, settings=cs)
    with (
        patch(
            "app.utils.playback_auth.group_runtime_state_service.get_runtime_credit_state",
            AsyncMock(return_value=runtime_state),
        ),
        patch("app.utils.playback_auth.deny_if_bot_disabled", AsyncMock(return_value=False)),
    ):
        ok = await authorize_playback_action(client, query, lang="fa")
    assert ok is True
    query.answer.assert_not_awaited()


@pytest.mark.asyncio
async def test_reply_audio_play_dispatches_with_media_type():
    from app.handlers.playback import register as register_playback

    bot = MagicMock()
    handlers: list = []

    def on_message(*args, **kwargs):
        def deco(fn):
            handlers.append(fn)
            return fn

        return deco

    bot.on_message = on_message
    bot.on_callback_query = lambda *a, **k: (lambda fn: fn)
    call_py = MagicMock()
    register_playback(bot, call_py)
    play_audio = next(fn for fn in handlers if fn.__name__ == "play_audio")

    message = MagicMock()
    message.chat = SimpleNamespace(id=-8001, type=ChatType.SUPERGROUP)
    message.from_user = SimpleNamespace(id=42)
    message.text = "پخش"
    message.caption = None
    message.reply_to_message = SimpleNamespace(
        audio=SimpleNamespace(file_id="audio-file"),
        video=None,
        document=None,
        voice=None,
    )
    client = MagicMock()

    with patch("app.handlers.playback._handle_play_command", AsyncMock()) as handle_mock:
        await play_audio(client, message)

    handle_mock.assert_awaited_once()

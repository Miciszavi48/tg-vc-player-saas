"""Reply + پخش playback command UX regression tests."""
from __future__ import annotations

import logging
import os
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pyrogram import filters
from pyrogram.enums import ChatType

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
        self.message_handlers: list = []

    def on_message(self, *args, **kwargs):  # noqa: ANN001, ANN002
        def _decorator(fn):
            self.message_handlers.append(fn)
            return fn

        return _decorator

    def on_callback_query(self, *args, **kwargs):  # noqa: ANN001, ANN002
        def _decorator(fn):
            return fn

        return _decorator


def _handlers():
    from app.handlers.playback import register as register_playback

    bot = _RecorderBot()
    register_playback(bot, MagicMock())
    return {
        "play_audio": next(fn for fn in bot.message_handlers if fn.__name__ == "play_audio"),
        "play_slash": next(fn for fn in bot.message_handlers if fn.__name__ == "play_slash_command"),
    }


def _message(text: str, *, reply_to_message=None, chat_id: int = -8001):
    message = AsyncMock()
    message.chat.id = chat_id
    message.from_user = SimpleNamespace(id=42, first_name="Tester")
    message.text = text
    message.caption = None
    message.command = None
    message.reply_to_message = reply_to_message
    message.reply = AsyncMock()
    message.continue_propagation = MagicMock()
    return message


def _group_command_message(
    text: str,
    *,
    chat_id: int = -8001,
    user_id: int = 42,
    reply_to_message=None,
):
    message = MagicMock()
    message.text = text
    message.caption = None
    message.command = None
    message.chat = SimpleNamespace(id=chat_id, type=ChatType.SUPERGROUP)
    message.from_user = SimpleNamespace(id=user_id, first_name="Tester")
    message.reply_to_message = reply_to_message
    return message


def _filter_client(username: str = "iQTESTPLAYEBOT"):
    client = MagicMock()
    client.me = SimpleNamespace(username=username)
    return client


async def _filter_matches(filt, text: str, *, username: str = "iQTESTPLAYEBOT") -> bool:
    client = _filter_client(username)
    message = _group_command_message(text)
    return await filt(client, message)


def _chat_settings(**overrides):
    base = {
        "audio_enabled": True,
        "file_enabled": True,
        "video_enabled": True,
        "download_enabled": True,
        "security_call_enabled": False,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def _success_stack(*, source: str, media_type: str = "audio"):
    from app.services.call_service import CallService

    join_mock = AsyncMock(return_value=True)
    patches = [
        patch("app.handlers.playback.authorize_playback_action", AsyncMock(return_value=True)),
        patch(
            "app.handlers.playback.settings_repo.get_chat_settings",
            AsyncMock(return_value=_chat_settings()),
        ),
        patch("app.handlers.playback.normalize_media_source", return_value=source),
        patch("app.handlers.playback.get_chat_default_media_type", AsyncMock(return_value=media_type)),
        patch(
            "app.handlers.playback.decide_busy_playback",
            AsyncMock(return_value=SimpleNamespace(action=None)),
        ),
        patch("app.handlers.playback.apply_busy_playback_decision", AsyncMock(return_value=False)),
        patch.object(CallService, "join_voice_chat", join_mock),
        patch("app.handlers.playback.track_event", AsyncMock()),
        patch("app.handlers.playback.resolve_lang", AsyncMock(return_value="fa")),
        patch("app.handlers.playback.render_now_playing_text", AsyncMock(return_value="now playing")),
        patch("app.handlers.playback.build_now_playing_controls", AsyncMock(return_value=None)),
        patch("app.handlers.playback.reply_now_playing_with_optional_cover", AsyncMock()),
        patch("app.handlers.playback.is_http_url", return_value=False),
    ]
    return patches, join_mock


@pytest.mark.asyncio
async def test_reply_audio_triggers_playback(tmp_path: Path):
    handlers = _handlers()
    local_path = str((tmp_path / "track.mp3").resolve())
    Path(local_path).write_bytes(b"mp3")
    reply = SimpleNamespace(
        audio=SimpleNamespace(),
        voice=None,
        video=None,
        video_note=None,
        animation=None,
        document=None,
        text=None,
        caption=None,
    )
    message = _message("پخش", reply_to_message=reply)
    patches, join_mock = _success_stack(source=local_path)

    with ExitStack() as stack:
        stack.enter_context(
            patch(
                "app.handlers.playback.download_trusted_telegram_media",
                AsyncMock(return_value=local_path),
            )
        )
        for cm in patches:
            stack.enter_context(cm)
        await handlers["play_audio"](AsyncMock(), message)

    join_mock.assert_awaited_once()
    assert join_mock.await_args.args[3] == "audio"


@pytest.mark.asyncio
async def test_reply_voice_triggers_audio_playback(tmp_path: Path):
    handlers = _handlers()
    local_path = str((tmp_path / "voice.ogg").resolve())
    Path(local_path).write_bytes(b"ogg")
    reply = SimpleNamespace(
        audio=None,
        voice=SimpleNamespace(),
        video=None,
        video_note=None,
        animation=None,
        document=None,
        text=None,
        caption=None,
    )
    message = _message("پخش", reply_to_message=reply)
    patches, join_mock = _success_stack(source=local_path)

    with ExitStack() as stack:
        stack.enter_context(
            patch(
                "app.handlers.playback.download_trusted_telegram_media",
                AsyncMock(return_value=local_path),
            )
        )
        for cm in patches:
            stack.enter_context(cm)
        await handlers["play_audio"](AsyncMock(), message)

    join_mock.assert_awaited_once()
    assert join_mock.await_args.args[3] == "audio"


@pytest.mark.asyncio
async def test_reply_video_triggers_video_playback(tmp_path: Path):
    handlers = _handlers()
    local_path = str((tmp_path / "clip.mp4").resolve())
    Path(local_path).write_bytes(b"mp4")
    reply = SimpleNamespace(
        audio=None,
        voice=None,
        video=SimpleNamespace(),
        video_note=None,
        animation=None,
        document=None,
        text=None,
        caption=None,
    )
    message = _message("پخش", reply_to_message=reply)
    patches, join_mock = _success_stack(source=local_path, media_type="video")

    with ExitStack() as stack:
        stack.enter_context(
            patch("app.handlers.playback.deny_video_playback", AsyncMock(return_value=False))
        )
        stack.enter_context(
            patch(
                "app.handlers.playback.download_trusted_telegram_media",
                AsyncMock(return_value=local_path),
            )
        )
        for cm in patches:
            stack.enter_context(cm)
        await handlers["play_audio"](AsyncMock(), message)

    join_mock.assert_awaited_once()
    assert join_mock.await_args.args[3] == "video"


@pytest.mark.asyncio
async def test_reply_audio_document_triggers_playback(tmp_path: Path):
    handlers = _handlers()
    local_path = str((tmp_path / "doc.mp3").resolve())
    Path(local_path).write_bytes(b"mp3")
    reply = SimpleNamespace(
        audio=None,
        voice=None,
        video=None,
        video_note=None,
        animation=None,
        document=SimpleNamespace(mime_type="audio/mpeg", file_name="song.mp3"),
        text=None,
        caption=None,
    )
    message = _message("پخش", reply_to_message=reply)
    patches, join_mock = _success_stack(source=local_path)

    with ExitStack() as stack:
        stack.enter_context(
            patch(
                "app.handlers.playback.download_trusted_telegram_media",
                AsyncMock(return_value=local_path),
            )
        )
        for cm in patches:
            stack.enter_context(cm)
        await handlers["play_audio"](AsyncMock(), message)

    join_mock.assert_awaited_once()
    assert join_mock.await_args.args[3] == "audio"


@pytest.mark.asyncio
async def test_reply_video_document_triggers_video_playback(tmp_path: Path):
    handlers = _handlers()
    local_path = str((tmp_path / "clip.mkv").resolve())
    Path(local_path).write_bytes(b"mkv")
    reply = SimpleNamespace(
        audio=None,
        voice=None,
        video=None,
        video_note=None,
        animation=None,
        document=SimpleNamespace(mime_type="video/x-matroska", file_name="clip.mkv"),
        text=None,
        caption=None,
    )
    message = _message("پخش", reply_to_message=reply)
    patches, join_mock = _success_stack(source=local_path, media_type="video")

    with ExitStack() as stack:
        stack.enter_context(
            patch("app.handlers.playback.deny_video_playback", AsyncMock(return_value=False))
        )
        stack.enter_context(
            patch(
                "app.handlers.playback.download_trusted_telegram_media",
                AsyncMock(return_value=local_path),
            )
        )
        for cm in patches:
            stack.enter_context(cm)
        await handlers["play_audio"](AsyncMock(), message)

    join_mock.assert_awaited_once()
    assert join_mock.await_args.args[3] == "video"


@pytest.mark.asyncio
async def test_reply_non_media_replies_visibly():
    handlers = _handlers()
    reply = SimpleNamespace(
        audio=None,
        voice=None,
        video=None,
        video_note=None,
        animation=None,
        document=None,
        text="سلام",
        caption=None,
    )
    message = _message("پخش", reply_to_message=reply)

    with patch("app.handlers.playback.authorize_playback_action", AsyncMock(return_value=True)):
        await handlers["play_audio"](AsyncMock(), message)

    message.reply.assert_awaited_once_with(t("fa", "playback_cmd.replay_no_media"))


@pytest.mark.asyncio
async def test_persian_play_url_routes_to_playback():
    handlers = _handlers()
    stream = "https://cdn.example.com/song.mp3"
    message = _message("پخش https://example.com/song.mp3")
    patches, join_mock = _success_stack(source=stream)

    with ExitStack() as stack:
        stack.enter_context(
            patch("app.handlers.playback.is_http_url", side_effect=lambda v: str(v).startswith("http"))
        )
        stack.enter_context(
            patch(
                "app.handlers.playback.validate_safe_url_with_redirects",
                AsyncMock(return_value="https://example.com/song.mp3"),
            )
        )
        stack.enter_context(
            patch("app.handlers.playback.MediaService.get_stream_url", AsyncMock(return_value=stream))
        )
        for cm in patches:
            stack.enter_context(cm)
        await handlers["play_audio"](AsyncMock(), message)

    join_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_persian_play_title_routes_to_search_playback():
    handlers = _handlers()
    message = _message("پخش نام آهنگ")
    patches, join_mock = _success_stack(source="normalized-query")

    with ExitStack() as stack:
        for cm in patches:
            stack.enter_context(cm)
        await handlers["play_audio"](AsyncMock(), message)

    join_mock.assert_awaited_once()
    assert join_mock.await_args.args[2] == "normalized-query"


@pytest.mark.asyncio
async def test_slash_play_reply_compatibility(tmp_path: Path):
    handlers = _handlers()
    local_path = str((tmp_path / "slash.mp3").resolve())
    Path(local_path).write_bytes(b"mp3")
    reply = SimpleNamespace(
        audio=SimpleNamespace(),
        voice=None,
        video=None,
        video_note=None,
        animation=None,
        document=None,
        text=None,
        caption=None,
    )
    message = _message("/play", reply_to_message=reply)
    message.command = ["play"]
    patches, join_mock = _success_stack(source=local_path)

    with ExitStack() as stack:
        stack.enter_context(
            patch(
                "app.handlers.playback.download_trusted_telegram_media",
                AsyncMock(return_value=local_path),
            )
        )
        for cm in patches:
            stack.enter_context(cm)
        await handlers["play_slash"](AsyncMock(), message)

    join_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_english_play_reply_compatibility(tmp_path: Path):
    handlers = _handlers()
    local_path = str((tmp_path / "en.mp3").resolve())
    Path(local_path).write_bytes(b"mp3")
    reply = SimpleNamespace(
        audio=SimpleNamespace(),
        voice=None,
        video=None,
        video_note=None,
        animation=None,
        document=None,
        text=None,
        caption=None,
    )
    message = _message("play", reply_to_message=reply)
    patches, join_mock = _success_stack(source=local_path)

    with ExitStack() as stack:
        stack.enter_context(
            patch(
                "app.handlers.playback.download_trusted_telegram_media",
                AsyncMock(return_value=local_path),
            )
        )
        for cm in patches:
            stack.enter_context(cm)
        await handlers["play_audio"](AsyncMock(), message)

    join_mock.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("text", ["پخش ", "پخش\n", "پخش\u200c"])
async def test_trailing_space_and_zwnj_still_match(text: str, tmp_path: Path):
    handlers = _handlers()
    local_path = str((tmp_path / "norm.mp3").resolve())
    Path(local_path).write_bytes(b"mp3")
    reply = SimpleNamespace(
        audio=SimpleNamespace(),
        voice=None,
        video=None,
        video_note=None,
        animation=None,
        document=None,
        text=None,
        caption=None,
    )
    message = _message(text, reply_to_message=reply)
    patches, join_mock = _success_stack(source=local_path)

    with ExitStack() as stack:
        stack.enter_context(
            patch(
                "app.handlers.playback.download_trusted_telegram_media",
                AsyncMock(return_value=local_path),
            )
        )
        for cm in patches:
            stack.enter_context(cm)
        await handlers["play_audio"](AsyncMock(), message)

    join_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_filter_words_empty_list_continues_propagation():
    from app.handlers.filter_words import register as register_filter_words

    bot = MagicMock()
    handlers = []
    bot.on_message = lambda *a, **k: (lambda fn: (handlers.append(fn), fn)[1])
    register_filter_words(bot, None)
    watcher = handlers[0]

    message = AsyncMock()
    message.from_user = SimpleNamespace(id=99)
    message.chat = SimpleNamespace(id=-1004)
    message.text = "پخش"
    message.delete = AsyncMock()
    message.continue_propagation = MagicMock()

    cs = SimpleNamespace(filter_enabled=True)
    with (
        patch("app.utils.bot_guards.is_developer", return_value=False),
        patch("app.handlers.filter_words.settings_repo.get_chat_settings", AsyncMock(return_value=cs)),
        patch("app.handlers.filter_words._load_filter_words", AsyncMock(return_value=[])),
    ):
        await watcher(AsyncMock(), message)

    message.delete.assert_not_called()
    message.continue_propagation.assert_called_once()


@pytest.mark.asyncio
async def test_blacklisted_group_guard_stops_propagation():
    from app.handlers.group_guard import register as register_group_guard

    bot = MagicMock()
    handlers = []
    bot.on_message = lambda *a, **k: (lambda fn: (handlers.append(fn), fn)[1])
    register_group_guard(bot, None)
    guard = handlers[0]

    message = AsyncMock()
    message.from_user = SimpleNamespace(id=99)
    message.chat = SimpleNamespace(id=-1005)
    message.text = "پخش"
    message.stop_propagation = MagicMock()
    message.continue_propagation = MagicMock()

    with patch(
        "app.handlers.group_guard.blacklist_repo.is_blacklisted",
        AsyncMock(side_effect=lambda _id, _kind: _kind == "group"),
    ):
        await guard(AsyncMock(), message)

    message.stop_propagation.assert_called_once()


@pytest.mark.asyncio
async def test_security_call_denial_is_visible():
    handlers = _handlers()
    message = _message("پخش")

    with patch(
        "app.handlers.playback.authorize_playback_action",
        AsyncMock(return_value=False),
    ):
        await handlers["play_audio"](AsyncMock(), message)

    message.reply.assert_not_called()


@pytest.mark.asyncio
async def test_audio_disabled_gate_is_visible():
    handlers = _handlers()
    reply = SimpleNamespace(
        audio=SimpleNamespace(),
        voice=None,
        video=None,
        video_note=None,
        animation=None,
        document=None,
        text=None,
        caption=None,
    )
    message = _message("پخش", reply_to_message=reply)

    with (
        patch("app.handlers.playback.authorize_playback_action", AsyncMock(return_value=True)),
        patch(
            "app.handlers.playback.settings_repo.get_chat_settings",
            AsyncMock(return_value=_chat_settings(audio_enabled=False)),
        ),
        patch(
            "app.handlers.playback.download_trusted_telegram_media",
            AsyncMock(return_value="/tmp/x.mp3"),
        ),
    ):
        await handlers["play_audio"](AsyncMock(), message)

    message.reply.assert_awaited_once_with(t("fa", "playback_cmd.audio_disabled_in_group"))


@pytest.mark.asyncio
async def test_file_disabled_gate_is_visible():
    handlers = _handlers()
    reply = SimpleNamespace(
        audio=SimpleNamespace(),
        voice=None,
        video=None,
        video_note=None,
        animation=None,
        document=None,
        text=None,
        caption=None,
    )
    message = _message("پخش", reply_to_message=reply)

    with (
        patch("app.handlers.playback.authorize_playback_action", AsyncMock(return_value=True)),
        patch(
            "app.handlers.playback.settings_repo.get_chat_settings",
            AsyncMock(return_value=_chat_settings(file_enabled=False)),
        ),
        patch(
            "app.handlers.playback.download_trusted_telegram_media",
            AsyncMock(return_value="/tmp/x.mp3"),
        ),
    ):
        await handlers["play_audio"](AsyncMock(), message)

    message.reply.assert_awaited_once_with(t("fa", "playback_cmd.file_disabled_in_group"))


@pytest.mark.asyncio
async def test_join_failure_replies_visibly():
    from app.services.call_service import CallService

    handlers = _handlers()
    message = _message("پخش نام آهنگ")
    patches, join_mock = _success_stack(source="query")
    join_mock.return_value = False

    with ExitStack() as stack:
        fail_mock = stack.enter_context(
            patch(
                "app.handlers.playback.reply_playback_join_failure",
                AsyncMock(),
            )
        )
        for cm in patches:
            stack.enter_context(cm)
        await handlers["play_audio"](AsyncMock(), message)

    fail_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_no_silent_return_on_recognized_command_without_source():
    handlers = _handlers()
    message = _message("پخش")

    with patch("app.handlers.playback.authorize_playback_action", AsyncMock(return_value=True)):
        await handlers["play_audio"](AsyncMock(), message)

    message.reply.assert_awaited_once_with(t("fa", "playback_cmd.provide_source"))


def test_parse_playback_command_rejects_embedded_persian_play():
    from app.utils.playback_commands import parse_playback_command

    assert parse_playback_command("سلام پخش") is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "text",
    ["/play", "/play@iQTESTPLAYEBOT", "/play query"],
)
async def test_native_play_filter_matches(text: str):
    filt = filters.command("play") & filters.group
    assert await _filter_matches(filt, text) is True


@pytest.mark.asyncio
@pytest.mark.parametrize("text", ["/playlist", "/playback"])
async def test_native_play_filter_rejects_similar_commands(text: str):
    filt = filters.command("play") & filters.group
    assert await _filter_matches(filt, text) is False


@pytest.mark.asyncio
async def test_typed_playback_filter_matches_persian():
    from app.utils.playback_commands import playback_command_filter

    filt = playback_command_filter() & filters.group
    assert await _filter_matches(filt, "پخش") is True


@pytest.mark.asyncio
@pytest.mark.parametrize("text", ["پخش ", "پخش\n", "پخش\u200c"])
async def test_typed_playback_filter_matches_persian_variants(text: str):
    from app.utils.playback_commands import playback_command_filter

    filt = playback_command_filter() & filters.group
    assert await _filter_matches(filt, text) is True


@pytest.mark.asyncio
async def test_typed_playback_filter_matches_english_play():
    from app.utils.playback_commands import playback_command_filter

    filt = playback_command_filter() & filters.group
    assert await _filter_matches(filt, "play") is True


@pytest.mark.asyncio
async def test_typed_playback_filter_rejects_slash_play():
    from app.utils.playback_commands import playback_command_filter

    filt = playback_command_filter() & filters.group
    assert await _filter_matches(filt, "/play") is False


def test_parse_slash_play_command_at_bot_has_no_dedication():
    from app.utils.playback_commands import parse_slash_play_command

    message = _group_command_message("/play@iQTESTPLAYEBOT")
    message.command = ["play"]
    parsed = parse_slash_play_command(message)
    assert parsed is not None
    assert parsed.remainder == ""
    assert parsed.dedication is None


def test_parse_slash_play_command_at_bot_with_query():
    from app.utils.playback_commands import parse_slash_play_command

    message = _group_command_message("/play@iQTESTPLAYEBOT song name")
    message.command = ["play", "song", "name"]
    parsed = parse_slash_play_command(message)
    assert parsed is not None
    assert parsed.remainder == "song name"
    assert parsed.dedication is None


@pytest.mark.asyncio
async def test_slash_handler_logs_entered_at_info(caplog):
    handlers = _handlers()
    message = _message("/play")
    message.command = ["play"]
    caplog.set_level(logging.INFO, logger="app.utils.playback_commands")

    with patch("app.handlers.playback.authorize_playback_action", AsyncMock(return_value=True)):
        await handlers["play_slash"](AsyncMock(), message)

    assert any("playback_slash_handler_entered" in record.message for record in caplog.records)


@pytest.mark.asyncio
async def test_slash_play_reply_non_media_replies_visibly():
    handlers = _handlers()
    reply = SimpleNamespace(
        audio=None,
        voice=None,
        video=None,
        video_note=None,
        animation=None,
        document=None,
        text="سلام",
        caption=None,
    )
    message = _message("/play", reply_to_message=reply)
    message.command = ["play"]

    with patch("app.handlers.playback.authorize_playback_action", AsyncMock(return_value=True)):
        await handlers["play_slash"](AsyncMock(), message)

    message.reply.assert_awaited_once_with(t("fa", "playback_cmd.replay_no_media"))


@pytest.mark.asyncio
async def test_slash_play_bare_command_replies_provide_source():
    handlers = _handlers()
    message = _message("/play")
    message.command = ["play"]

    with patch("app.handlers.playback.authorize_playback_action", AsyncMock(return_value=True)):
        await handlers["play_slash"](AsyncMock(), message)

    message.reply.assert_awaited_once_with(t("fa", "playback_cmd.provide_source"))

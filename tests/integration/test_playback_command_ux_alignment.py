"""Playback command UX alignment tests for Persian typed-first usage."""
from __future__ import annotations

import os
from contextlib import ExitStack
from pathlib import Path
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
from app.utils.ui import CB


class _RecorderBot:
    def __init__(self) -> None:
        self.message_handlers: list = []
        self.callback_handlers: list = []
        self.message_registrations: list[dict] = []
        self.callback_registrations: list[dict] = []

    def on_message(self, *args, **kwargs):  # noqa: ANN001, ANN002
        def _decorator(fn):
            self.message_handlers.append(fn)
            self.message_registrations.append({"fn": fn, "args": args, "kwargs": kwargs})
            return fn

        return _decorator

    def on_callback_query(self, *args, **kwargs):  # noqa: ANN001, ANN002
        def _decorator(fn):
            self.callback_handlers.append(fn)
            self.callback_registrations.append({"fn": fn, "args": args, "kwargs": kwargs})
            return fn

        return _decorator


def _play_audio_handler():
    from app.handlers.playback import register as register_playback

    bot = _RecorderBot()
    register_playback(bot, MagicMock())
    return next(fn for fn in bot.message_handlers if fn.__name__ == "play_audio")


def _play_auto_music_handler():
    from app.handlers.playback import register as register_playback

    bot = _RecorderBot()
    register_playback(bot, MagicMock())
    return next(fn for fn in bot.message_handlers if fn.__name__ == "play_auto_music")


def _play_auto_video_handler():
    from app.handlers.playback import register as register_playback

    bot = _RecorderBot()
    register_playback(bot, MagicMock())
    return next(fn for fn in bot.message_handlers if fn.__name__ == "play_auto_video")


def _playback_message_handler(name: str):
    from app.handlers.playback import register as register_playback

    bot = _RecorderBot()
    register_playback(bot, MagicMock())
    return next(fn for fn in bot.message_handlers if fn.__name__ == name)


def _playback_message_registration(name: str) -> dict:
    from app.handlers.playback import register as register_playback

    bot = _RecorderBot()
    register_playback(bot, MagicMock())
    return next(
        registration
        for registration in bot.message_registrations
        if registration["fn"].__name__ == name
    )


def _registration_regex_matches(handler_name: str, text: str) -> bool:
    registration = _playback_message_registration(handler_name)
    combined_filter = registration["args"][0]
    regex_filter = getattr(combined_filter, "base", combined_filter)
    pattern = getattr(regex_filter, "p", None)
    assert pattern is not None, f"{handler_name} is not registered with a regex filter"
    return bool(pattern.search(text))


def _tv_radio_message_handler(name: str):
    from app.handlers.tv_radio import register as register_tv_radio

    bot = _RecorderBot()
    register_tv_radio(bot, MagicMock())
    return next(fn for fn in bot.message_handlers if fn.__name__ == name)


def _inline_callbacks(markup):
    return [
        btn.callback_data
        for row in markup.inline_keyboard
        for btn in row
        if getattr(btn, "callback_data", None)
    ]


def _group_message(text: str, *, reply_to_message=None):
    message = AsyncMock()
    message.chat.id = -7001
    message.from_user = SimpleNamespace(id=42, first_name="Tester")
    message.text = text
    message.caption = None
    message.reply_to_message = reply_to_message
    message.reply = AsyncMock()
    return message


def _success_patches(*, source: str, media_type: str = "audio"):
    from app.services.call_service import CallService

    join_mock = AsyncMock(return_value=True)
    patches = (
        patch("app.handlers.playback.authorize_playback_action", AsyncMock(return_value=True)),
        patch(
            "app.handlers.playback.settings_repo.get_chat_settings",
            AsyncMock(
                return_value=SimpleNamespace(
                    audio_enabled=True,
                    file_enabled=True,
                    video_enabled=True,
                )
            ),
        ),
        patch("app.handlers.playback.normalize_media_source", return_value=source),
        patch("app.handlers.playback.get_chat_default_media_type", AsyncMock(return_value=media_type)),
        patch("app.handlers.playback.decide_busy_playback", AsyncMock(return_value=SimpleNamespace(action=None))),
        patch("app.handlers.playback.apply_busy_playback_decision", AsyncMock(return_value=False)),
        patch.object(CallService, "join_voice_chat", join_mock),
        patch("app.handlers.playback.track_event", AsyncMock()),
        patch("app.handlers.playback.resolve_lang", AsyncMock(return_value="fa")),
        patch("app.handlers.playback.render_now_playing_text", AsyncMock(return_value="now playing")),
        patch("app.handlers.playback.build_now_playing_controls", AsyncMock(return_value=None)),
        patch("app.handlers.playback.reply_now_playing_with_optional_cover", AsyncMock()),
    )
    return patches, join_mock


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("text", "query"),
    [("پخش نام آهنگ", "نام آهنگ"), ("play song name", "song name")],
)
async def test_play_query_uses_first_youtube_result(text: str, query: str):
    play_audio = _play_audio_handler()
    url = "https://youtube.com/watch?v=dQw4w9WgXcQ"
    stream = "https://cdn.example.com/query-audio"
    message = _group_message(text)
    patches, join_mock = _success_patches(source=stream)

    with ExitStack() as stack:
        stack.enter_context(
            patch(
                "app.handlers.playback.is_http_url",
                side_effect=lambda value: str(value).startswith("http"),
            )
        )
        resolver = stack.enter_context(
            patch(
                "app.handlers.search.resolve_youtube_first_result",
                AsyncMock(
                    return_value={
                        "id": "dQw4w9WgXcQ",
                        "title": "first result",
                        "duration": "213",
                        "url": url,
                    }
                ),
            )
        )
        stream_resolver = stack.enter_context(
            patch(
                "app.handlers.playback.MediaService.get_stream_url",
                AsyncMock(return_value=stream),
            )
        )
        stack.enter_context(
            patch(
                "app.handlers.playback.validate_safe_url_with_redirects",
                AsyncMock(return_value=url),
            )
        )
        for cm in patches:
            stack.enter_context(cm)
        await play_audio(AsyncMock(), message)

    resolver.assert_awaited_once_with(query)
    stream_resolver.assert_awaited_once_with(url, media_type="audio")
    join_mock.assert_awaited_once()
    assert join_mock.await_args.args[2] == stream
    assert join_mock.await_args.args[3] == "audio"


@pytest.mark.asyncio
async def test_persian_play_link_is_accepted():
    play_audio = _play_audio_handler()
    message = _group_message("پخش https://example.com/song.mp3")
    stream = "https://cdn.example.com/song.mp3"
    patches, join_mock = _success_patches(source=stream)

    with ExitStack() as stack:
        stack.enter_context(patch("app.handlers.playback.is_http_url", side_effect=lambda value: str(value).startswith("http")))
        stack.enter_context(patch("app.handlers.playback.validate_safe_url_with_redirects", AsyncMock(return_value="https://example.com/song.mp3")))
        stack.enter_context(patch("app.handlers.playback.MediaService.get_stream_url", AsyncMock(return_value=stream)))
        search_resolver = stack.enter_context(
            patch("app.handlers.search.resolve_youtube_first_result", AsyncMock())
        )
        for cm in patches:
            stack.enter_context(cm)
        await play_audio(AsyncMock(), message)

    search_resolver.assert_not_awaited()
    join_mock.assert_awaited_once()
    assert join_mock.await_args.args[2] == stream
    assert join_mock.await_args.args[3] == "audio"


@pytest.mark.asyncio
async def test_persian_play_query_without_youtube_result_does_not_join_voice_chat():
    play_audio = _play_audio_handler()
    message = _group_message("پخش نام ناشناخته")
    patches, join_mock = _success_patches(source="unused")

    with ExitStack() as stack:
        stack.enter_context(patch("app.handlers.playback.is_http_url", return_value=False))
        resolver = stack.enter_context(
            patch(
                "app.handlers.search.resolve_youtube_first_result",
                AsyncMock(return_value=None),
            )
        )
        for cm in patches:
            stack.enter_context(cm)
        await play_audio(AsyncMock(), message)

    resolver.assert_awaited_once_with("نام ناشناخته")
    message.reply.assert_awaited_once_with(t("fa", "search.no_results"))
    join_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_soundcloud_track_link_resolves_then_joins_voice_chat():
    play_audio = _play_audio_handler()
    url = "https://soundcloud.com/artist-name/track-name"
    stream = "https://cf-media.sndcdn.com/stream-token.ogg"
    message = _group_message(f"پخش {url}")
    patches, join_mock = _success_patches(source=stream)

    with ExitStack() as stack:
        stack.enter_context(
            patch(
                "app.handlers.playback.is_http_url",
                side_effect=lambda value: str(value).startswith("http"),
            )
        )
        stack.enter_context(
            patch(
                "app.handlers.playback.validate_safe_url_with_redirects",
                AsyncMock(return_value=url),
            )
        )
        resolver = stack.enter_context(
            patch(
                "app.handlers.playback.MediaService.get_stream_url",
                AsyncMock(return_value=stream),
            )
        )
        for cm in patches:
            stack.enter_context(cm)
        await play_audio(AsyncMock(), message)

    resolver.assert_awaited_once_with(url, media_type="audio")
    join_mock.assert_awaited_once()
    assert join_mock.await_args.args[2] == stream
    assert join_mock.await_args.args[3] == "audio"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url",
    [
        "https://soundcloud.com/artist-name",
        "https://soundcloud.com/artist-name/sets/release-name",
        "https://soundcloud.com/artist-name%2Fsets/release-name",
        "https://soundcloud.com/discover",
    ],
)
async def test_soundcloud_profile_or_collection_is_rejected_before_stream_resolution(
    url: str,
):
    play_audio = _play_audio_handler()
    message = _group_message(f"پخش {url}")
    resolver = AsyncMock()

    with (
        patch(
            "app.handlers.playback.authorize_playback_action",
            AsyncMock(return_value=True),
        ),
        patch(
            "app.handlers.playback.settings_repo.get_chat_settings",
            AsyncMock(
                return_value=SimpleNamespace(
                    audio_enabled=True,
                    file_enabled=True,
                    video_enabled=True,
                )
            ),
        ),
        patch("app.handlers.playback.get_chat_default_media_type", AsyncMock(return_value="audio")),
        patch("app.handlers.playback.is_http_url", return_value=True),
        patch(
            "app.handlers.playback.validate_safe_url_with_redirects",
            AsyncMock(return_value=url),
        ),
        patch("app.handlers.playback.MediaService.get_stream_url", resolver),
    ):
        await play_audio(AsyncMock(), message)

    message.reply.assert_awaited_once_with(t("fa", "playback_cmd.soundcloud_track_only"))
    resolver.assert_not_awaited()


@pytest.mark.asyncio
async def test_reply_plus_persian_play_is_accepted(tmp_path: Path):
    play_audio = _play_audio_handler()
    local_path = str((tmp_path / "voice.ogg").resolve())
    Path(local_path).write_bytes(b"audio")
    reply = SimpleNamespace(
        audio=SimpleNamespace(),
        voice=None,
        document=None,
        text=None,
    )
    message = _group_message("پخش", reply_to_message=reply)
    patches, join_mock = _success_patches(source=local_path, media_type="video")

    with ExitStack() as stack:
        stack.enter_context(patch("app.handlers.playback.download_trusted_telegram_media", AsyncMock(return_value=local_path)))
        stack.enter_context(patch("app.handlers.playback.is_http_url", return_value=False))
        for cm in patches:
            stack.enter_context(cm)
        await play_audio(AsyncMock(), message)

    join_mock.assert_awaited_once()
    assert join_mock.await_args.args[2] == local_path
    assert join_mock.await_args.args[3] == "audio"


def test_persian_help_prioritizes_typed_commands_and_not_slash_play():
    fa_commands = t("fa", "help_content.play_commands")
    fa_playback = t("fa", "help.playback")
    fa_hint = t("fa", "playback.type_hints.audio")

    assert fa_commands.splitlines()[1] == "• پخش نام آهنگ"
    assert "پخش لینک مستقیم" in fa_commands
    assert "بنویسید: پخش" in fa_commands
    assert "/play" not in fa_commands
    assert "/play" not in fa_playback
    assert "/play" not in fa_hint


def test_playback_help_has_english_parity_without_slash_first_flow():
    en_commands = t("en", "help_content.play_commands")

    assert en_commands.splitlines()[1] == "• play song name"
    assert "play direct link" in en_commands
    assert "/play" not in en_commands


def test_existing_playback_callbacks_still_exist():
    from app.handlers.search import _SEARCH_PLAY_PREFIX

    assert CB["PB_AUDIO"] == "pb:type:audio"
    assert CB["PB_VIDEO"] == "pb:type:video"
    assert CB["PB_TV"] == "pb:type:tv"
    assert CB["PB_RADIO"] == "pb:type:radio"
    assert CB["PB_SAT"] == "pb:type:satellite"
    assert CB["PB_STOP"] == "pb:stop"
    assert _SEARCH_PLAY_PREFIX == "search:play:"


def test_help_reference_playback_aliases_route_through_existing_lists():
    from app.handlers.playback import (
        _AUTO_MUSIC_CMDS,
        _AUTO_VIDEO_CMDS,
        _MUSIC_VOL_CMDS,
        _MUTE_CMDS,
        _PAUSE_CMDS,
        _PLAY_TV_CMDS,
        _PLAY_VIDEO_CMDS,
        _RESUME_CMDS,
        _SEEK_BACK_CMDS,
        _SEEK_FRONT_CMDS,
        _SERIAL_CMDS,
        _SPEED_DOWN_CMDS,
        _SPEED_UP_CMDS,
        _STOP_AUDIO_CMDS,
        _UNMUTE_CMDS,
        _VIDEO_VOL_CMDS,
        _VOLUME_DOWN_CMDS,
        _VOLUME_UP_CMDS,
    )

    assert "پخش خودکار موزیک" in _AUTO_MUSIC_CMDS
    assert "Play Auto Music" in _AUTO_MUSIC_CMDS
    assert "پخش خودکار ویدئو" in _AUTO_VIDEO_CMDS
    assert "Play Auto Video" in _AUTO_VIDEO_CMDS
    assert "Stop Play" in _STOP_AUDIO_CMDS
    assert "مکث پلیر" in _PAUSE_CMDS
    assert "Pause Play" in _PAUSE_CMDS
    assert "Resume Play" in _RESUME_CMDS
    assert "پخش بیصدا" in _MUTE_CMDS
    assert "Mute Play" in _MUTE_CMDS
    assert "پخش باصدا" in _UNMUTE_CMDS
    assert "UnMute Play" in _UNMUTE_CMDS
    assert "تنظیم صدا" in _MUSIC_VOL_CMDS
    assert "Set Volume" in _MUSIC_VOL_CMDS
    assert "پخش تلویزیون" in _PLAY_TV_CMDS
    assert "Tv Play" in _PLAY_TV_CMDS
    assert "پخش سریال" in _SERIAL_CMDS
    assert "Serial Play" in _SERIAL_CMDS
    assert "کاهش سرعت" in _SPEED_DOWN_CMDS
    assert "Speed Down" in _SPEED_DOWN_CMDS
    assert "افزایش سرعت" in _SPEED_UP_CMDS
    assert "Speed Up" in _SPEED_UP_CMDS
    assert "کاهش صدا" in _VOLUME_DOWN_CMDS
    assert "Volume-" in _VOLUME_DOWN_CMDS
    assert "افزایش صدا" in _VOLUME_UP_CMDS
    assert "Volume+" in _VOLUME_UP_CMDS
    assert "جلو" in _SEEK_FRONT_CMDS
    assert "Front" in _SEEK_FRONT_CMDS
    assert "عقب" in _SEEK_BACK_CMDS
    assert "Back" in _SEEK_BACK_CMDS

    assert "playvideo" in _PLAY_VIDEO_CMDS
    assert "پخش ویدیو" in _PLAY_VIDEO_CMDS
    assert "Play Auto Video" not in _PLAY_VIDEO_CMDS
    assert "پخش خودکار ویدئو" not in _PLAY_VIDEO_CMDS
    assert "Volume+" not in _MUSIC_VOL_CMDS + _VIDEO_VOL_CMDS
    assert "Volume-" not in _MUSIC_VOL_CMDS + _VIDEO_VOL_CMDS


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "text",
    [
        "توقف پخش",
        "Stop Play",
        "stopmusic",
        "  توقف پخش",
        "توقف پخش  ",
    ],
)
async def test_stop_help_commands_call_leave_voice_chat(text: str):
    stop_handler = _playback_message_handler("stop_audio")
    message = _group_message(text)

    with (
        patch("app.handlers.playback._check_prerequisites", AsyncMock(return_value=True)) as gate,
        patch("app.handlers.playback.CallService.leave_voice_chat", AsyncMock(return_value=True)) as leave,
        patch("app.handlers.playback.track_event", AsyncMock()) as track,
    ):
        await stop_handler(AsyncMock(), message)

    gate.assert_awaited_once()
    leave.assert_awaited_once()
    assert leave.await_args.args[1] == message.chat.id
    track.assert_awaited_once()
    message.reply.assert_awaited_once_with(t("fa", "playback_cmd.stopped_audio"))


@pytest.mark.asyncio
async def test_stop_help_command_no_active_returns_visible_feedback_without_fake_success():
    stop_handler = _playback_message_handler("stop_audio")
    message = _group_message("Stop Play")

    with (
        patch("app.handlers.playback._check_prerequisites", AsyncMock(return_value=True)),
        patch("app.handlers.playback.CallService.leave_voice_chat", AsyncMock(return_value=False)) as leave,
        patch("app.handlers.playback.track_event", AsyncMock()) as track,
    ):
        await stop_handler(AsyncMock(), message)

    leave.assert_awaited_once()
    track.assert_not_awaited()
    message.reply.assert_awaited_once_with(t("fa", "playback_cmd.no_voice_chat"))


@pytest.mark.asyncio
async def test_stop_help_command_service_failure_returns_visible_feedback():
    stop_handler = _playback_message_handler("stop_audio")
    message = _group_message("توقف پخش")

    with (
        patch("app.handlers.playback._check_prerequisites", AsyncMock(return_value=True)),
        patch(
            "app.handlers.playback.CallService.leave_voice_chat",
            AsyncMock(side_effect=RuntimeError("leave failed")),
        ) as leave,
        patch("app.handlers.playback.track_event", AsyncMock()) as track,
    ):
        await stop_handler(AsyncMock(), message)

    leave.assert_awaited_once()
    track.assert_not_awaited()
    message.reply.assert_awaited_once_with(t("fa", "playback_cmd.failed"))


@pytest.mark.asyncio
async def test_stop_help_command_false_service_failure_uses_failure_key():
    stop_handler = _playback_message_handler("stop_audio")
    message = _group_message("توقف پخش")

    with (
        patch("app.handlers.playback._check_prerequisites", AsyncMock(return_value=True)),
        patch("app.handlers.playback.CallService.leave_voice_chat", AsyncMock(return_value=False)) as leave,
        patch("app.handlers.playback.CallService.pop_leave_failure_key", return_value="playback_cmd.failed"),
        patch("app.handlers.playback.track_event", AsyncMock()) as track,
    ):
        await stop_handler(AsyncMock(), message)

    leave.assert_awaited_once()
    track.assert_not_awaited()
    message.reply.assert_awaited_once_with(t("fa", "playback_cmd.failed"))


@pytest.mark.asyncio
async def test_stop_help_command_permission_denial_returns_visible_feedback_without_mutation():
    stop_handler = _playback_message_handler("stop_audio")
    message = _group_message("توقف پخش")

    async def _deny(_client, msg):
        await msg.reply(t("fa", "playback_cmd.no_permission"))
        return False

    with (
        patch("app.handlers.playback._check_prerequisites", AsyncMock(side_effect=_deny)) as gate,
        patch("app.handlers.playback.CallService.leave_voice_chat", AsyncMock()) as leave,
        patch("app.handlers.playback.track_event", AsyncMock()) as track,
    ):
        await stop_handler(AsyncMock(), message)

    gate.assert_awaited_once()
    leave.assert_not_awaited()
    track.assert_not_awaited()
    message.reply.assert_awaited_once_with(t("fa", "playback_cmd.no_permission"))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "handler_name,text,service_name,success_key",
    [
        ("pause_playback", "مکث پلیر", "pause", "playback_cmd.paused"),
        ("pause_playback", "Pause Play", "pause", "playback_cmd.paused"),
        ("resume_playback", "ازسرگیری", "resume", "playback_cmd.resumed"),
        ("resume_playback", "Resume Play", "resume", "playback_cmd.resumed"),
    ],
)
async def test_pause_resume_help_commands_work_exactly(
    handler_name: str,
    text: str,
    service_name: str,
    success_key: str,
):
    handler = _playback_message_handler(handler_name)
    message = _group_message(text)

    with (
        patch("app.handlers.playback._check_prerequisites", AsyncMock(return_value=True)) as gate,
        patch(f"app.handlers.playback.CallService.{service_name}", AsyncMock(return_value=True)) as service,
        patch("app.handlers.playback.track_event", AsyncMock()),
    ):
        await handler(AsyncMock(), message)

    gate.assert_awaited_once()
    service.assert_awaited_once()
    service_args = service.await_args.args
    assert service_args[1] == message.chat.id
    message.reply.assert_awaited_once_with(t("fa", success_key))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "handler_name,text,volume,success_key",
    [
        ("mute", "پخش بیصدا", 1, "playback_cmd.muted"),
        ("mute", "Mute Play", 1, "playback_cmd.muted"),
        ("unmute", "پخش باصدا", 100, "playback_cmd.unmuted"),
        ("unmute", "UnMute Play", 100, "playback_cmd.unmuted"),
    ],
)
async def test_mute_unmute_help_commands_work_exactly(
    handler_name: str,
    text: str,
    volume: int,
    success_key: str,
):
    handler = _playback_message_handler(handler_name)
    message = _group_message(text)

    with (
        patch("app.handlers.playback._check_prerequisites", AsyncMock(return_value=True)),
        patch("app.handlers.playback.CallService.is_chat_playing", return_value=True),
        patch("app.handlers.playback.CallService.set_volume", AsyncMock(return_value=True)) as set_volume,
    ):
        await handler(AsyncMock(), message)

    set_volume.assert_awaited_once()
    assert set_volume.await_args.args[1] == message.chat.id
    assert set_volume.await_args.args[2] == volume
    message.reply.assert_awaited_once_with(t("fa", success_key))


@pytest.mark.asyncio
@pytest.mark.parametrize("handler_name,text", [("mute", "Mute Play"), ("unmute", "UnMute Play")])
async def test_mute_unmute_volume_failure_returns_visible_feedback(handler_name: str, text: str):
    handler = _playback_message_handler(handler_name)
    message = _group_message(text)

    with (
        patch("app.handlers.playback._check_prerequisites", AsyncMock(return_value=True)),
        patch("app.handlers.playback.CallService.is_chat_playing", return_value=True),
        patch("app.handlers.playback.CallService.set_volume", AsyncMock(return_value=False)),
    ):
        await handler(AsyncMock(), message)

    message.reply.assert_awaited_once_with(t("fa", "playback_cmd.failed"))


@pytest.mark.parametrize(
    "handler_name,valid_text,invalid_text",
    [
        ("stop_audio", "Stop Play", "Stop Play now"),
        ("stop_audio", "توقف پخش", "توقف پخش الان"),
        ("stop_audio", "stopmusic", "stopmusic now"),
        ("stop_audio", "  توقف پخش  ", "توقف پخش الان"),
        ("pause_playback", "Pause Play", "Pause Play x"),
        ("pause_playback", "مکث پلیر", "مکث پلیر x"),
        ("resume_playback", "Resume Play", "Resume Play x"),
        ("resume_playback", "ازسرگیری", "ازسرگیری x"),
        ("mute", "Mute Play", "Mute Play x"),
        ("mute", "پخش بیصدا", "پخش بیصدا x"),
        ("unmute", "UnMute Play", "UnMute Play x"),
        ("unmute", "پخش باصدا", "پخش باصدا x"),
        ("speed_up_control", "Speed Up", "Speed Up fast"),
        ("speed_up_control", "افزایش سرعت", "افزایش سرعت سریع"),
        ("speed_down_control", "Speed Down", "Speed Down fast"),
        ("speed_down_control", "کاهش سرعت", "کاهش سرعت سریع"),
        ("volume_up_step", "Volume+", "Volume+ 10"),
        ("volume_up_step", "افزایش صدا", "افزایش صدا 10"),
        ("volume_down_step", "Volume-", "Volume- 10"),
        ("volume_down_step", "کاهش صدا", "کاهش صدا 10"),
    ],
)
def test_no_argument_control_filters_reject_invalid_suffixes(
    handler_name: str,
    valid_text: str,
    invalid_text: str,
):
    assert _registration_regex_matches(handler_name, valid_text)
    assert not _registration_regex_matches(handler_name, invalid_text)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "handler_name,text,expected_volume",
    [
        ("music_volume", "تنظیم صدا 1", 1),
        ("music_volume", "Set Volume 100", 100),
        ("music_volume", "Set Volume 200", 200),
        ("music_volume", "musicsound 100", 100),
        ("music_volume", "صدای موزیک 100", 100),
        ("video_volume", "videosound 100", 100),
        ("video_volume", "صدای ویدیو 100", 100),
    ],
)
async def test_absolute_volume_help_commands_require_valid_range_and_apply_actual_value(
    handler_name: str,
    text: str,
    expected_volume: int,
):
    handler = _playback_message_handler(handler_name)
    message = _group_message(text)

    with (
        patch("app.handlers.playback._check_prerequisites", AsyncMock(return_value=True)) as gate,
        patch("app.handlers.playback.CallService.set_volume", AsyncMock(return_value=True)) as set_volume,
    ):
        await handler(AsyncMock(), message)

    gate.assert_awaited_once()
    set_volume.assert_awaited_once()
    assert set_volume.await_args.args[1] == message.chat.id
    assert set_volume.await_args.args[2] == expected_volume
    message.reply.assert_awaited_once_with(
        t("fa", "playback_cmd.volume_set", volume=expected_volume)
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "handler_name,text,expected_key",
    [
        ("music_volume", "Set Volume", "playback_cmd.volume_usage"),
        ("music_volume", "تنظیم صدا", "playback_cmd.volume_usage"),
        ("music_volume", "Set Volume abc", "playback_cmd.volume_invalid"),
        ("music_volume", "تنظیم صدا abc", "playback_cmd.volume_invalid"),
        ("music_volume", "Set Volume 0", "playback_cmd.volume_invalid"),
        ("music_volume", "Set Volume 201", "playback_cmd.volume_invalid"),
        ("music_volume", "تنظیم صدا ۲۰۱", "playback_cmd.volume_invalid"),
    ],
)
async def test_absolute_volume_invalid_input_rejects_without_auth_or_mutation(
    handler_name: str,
    text: str,
    expected_key: str,
):
    handler = _playback_message_handler(handler_name)
    message = _group_message(text)

    with (
        patch("app.handlers.playback._check_prerequisites", AsyncMock()) as gate,
        patch("app.handlers.playback.CallService.set_volume", AsyncMock()) as set_volume,
    ):
        await handler(AsyncMock(), message)

    gate.assert_not_awaited()
    set_volume.assert_not_awaited()
    message.reply.assert_awaited_once_with(t("fa", expected_key))


@pytest.mark.asyncio
async def test_absolute_volume_denied_user_does_not_mutate():
    handler = _playback_message_handler("music_volume")
    message = _group_message("Set Volume 100")

    with (
        patch("app.handlers.playback._check_prerequisites", AsyncMock(return_value=False)) as gate,
        patch("app.handlers.playback.CallService.set_volume", AsyncMock()) as set_volume,
    ):
        await handler(AsyncMock(), message)

    gate.assert_awaited_once()
    set_volume.assert_not_awaited()
    message.reply.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("text", ["پخش سریال", "Serial Play"])
async def test_serial_text_command_returns_coming_soon_without_playback(text: str):
    play_serial_placeholder = _playback_message_handler("play_serial_placeholder")
    message = _group_message(text)

    with (
        patch("app.handlers.playback._check_prerequisites", AsyncMock()) as gate,
        patch("app.handlers.search.resolve_youtube_first_result", AsyncMock()) as resolver,
        patch("app.handlers.playback._handle_play_command", AsyncMock()) as handle_play,
        patch("app.handlers.playback.CallService.join_voice_chat", AsyncMock()) as join,
    ):
        await play_serial_placeholder(AsyncMock(), message)

    message.reply.assert_awaited_once_with(t("fa", "playback.controls.serial_coming_soon"))
    gate.assert_not_awaited()
    resolver.assert_not_awaited()
    handle_play.assert_not_awaited()
    join.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "handler_name,text",
    [
        ("speed_down_control", "کاهش سرعت"),
        ("speed_down_control", "Speed Down"),
        ("speed_up_control", "افزایش سرعت"),
        ("speed_up_control", "Speed Up"),
    ],
)
async def test_speed_text_commands_call_shared_speed_change(
    handler_name: str,
    text: str,
):
    speed_handler = _playback_message_handler(handler_name)
    message = _group_message(text)
    expected_delta = -25 if "down" in handler_name else 25
    result = SimpleNamespace(
        message_key="playback.controls.speed_set_position",
        speed_label="1.25" if expected_delta > 0 else "0.75",
    )

    with (
        patch("app.handlers.playback._check_prerequisites", AsyncMock(return_value=True)) as gate,
        patch(
            "app.handlers.playback.CallService.change_playback_speed",
            AsyncMock(return_value=result),
        ) as change_speed,
        patch("app.handlers.playback.CallService.set_volume", AsyncMock()) as set_volume,
        patch("app.handlers.playback.CallService.join_voice_chat", AsyncMock()) as join,
    ):
        await speed_handler(AsyncMock(), message)

    gate.assert_awaited_once()
    change_speed.assert_awaited_once()
    assert change_speed.await_args.args[1] == message.chat.id
    assert change_speed.await_args.args[2] == expected_delta
    message.reply.assert_awaited_once_with(
        t("fa", "playback.controls.speed_set_position", speed=result.speed_label)
    )
    set_volume.assert_not_awaited()
    join.assert_not_awaited()


@pytest.mark.asyncio
async def test_speed_text_auth_denial_does_not_mutate_speed():
    speed_handler = _playback_message_handler("speed_up_control")
    message = _group_message("Speed Up")

    with (
        patch("app.handlers.playback._check_prerequisites", AsyncMock(return_value=False)) as gate,
        patch("app.handlers.playback.CallService.change_playback_speed", AsyncMock()) as change_speed,
    ):
        await speed_handler(AsyncMock(), message)

    gate.assert_awaited_once()
    change_speed.assert_not_awaited()
    message.reply.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "handler_name,text,expected_volume",
    [
        ("volume_down_step", "کاهش صدا", 80),
        ("volume_down_step", "Volume-", 80),
        ("volume_up_step", "افزایش صدا", 120),
        ("volume_up_step", "Volume+", 120),
    ],
)
async def test_volume_step_text_commands_use_existing_volume_state(
    handler_name: str,
    text: str,
    expected_volume: int,
):
    from app.handlers import callbacks as playback_callbacks

    playback_callbacks._pb_volume_state.clear()
    volume_handler = _playback_message_handler(handler_name)
    message = _group_message(text)

    with (
        patch("app.handlers.playback._check_prerequisites", AsyncMock(return_value=True)) as gate,
        patch("app.handlers.callbacks._is_playback_active", return_value=True) as active,
        patch("app.handlers.playback.CallService.set_volume", AsyncMock(return_value=True)) as set_volume,
        patch("app.handlers.playback.CallService.play_next", AsyncMock()) as play_next,
        patch("app.handlers.playback.CallService.play_previous", AsyncMock()) as play_previous,
    ):
        await volume_handler(AsyncMock(), message)

    gate.assert_awaited_once()
    active.assert_called_once_with(message.chat.id)
    set_volume.assert_awaited_once()
    assert set_volume.await_args.args[1] == message.chat.id
    assert set_volume.await_args.args[2] == expected_volume
    assert playback_callbacks._pb_volume_state[message.chat.id] == expected_volume
    message.reply.assert_awaited_once_with(
        t("fa", "playback_cmd.volume_set", volume=expected_volume)
    )
    play_next.assert_not_awaited()
    play_previous.assert_not_awaited()


@pytest.mark.asyncio
async def test_volume_step_auth_denial_does_not_mutate_volume():
    volume_handler = _playback_message_handler("volume_up_step")
    message = _group_message("Volume+")

    with (
        patch("app.handlers.playback._check_prerequisites", AsyncMock(return_value=False)) as gate,
        patch("app.handlers.playback.CallService.set_volume", AsyncMock()) as set_volume,
    ):
        await volume_handler(AsyncMock(), message)

    gate.assert_awaited_once()
    set_volume.assert_not_awaited()
    message.reply.assert_not_awaited()


@pytest.mark.asyncio
async def test_volume_step_without_active_playback_does_not_mutate_volume():
    volume_handler = _playback_message_handler("volume_down_step")
    message = _group_message("Volume-")

    with (
        patch("app.handlers.playback._check_prerequisites", AsyncMock(return_value=True)) as gate,
        patch("app.handlers.callbacks._is_playback_active", return_value=False) as active,
        patch("app.handlers.playback.CallService.set_volume", AsyncMock()) as set_volume,
    ):
        await volume_handler(AsyncMock(), message)

    gate.assert_awaited_once()
    active.assert_called_once_with(message.chat.id)
    set_volume.assert_not_awaited()
    message.reply.assert_awaited_once_with(t("fa", "playback_cmd.no_voice_chat"))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "handler_name,text,expected_delta",
    [
        ("seek_front_control", "جلو", 10),
        ("seek_front_control", "Front", 10),
        ("seek_front_control", "Front 25", 25),
        ("seek_back_control", "عقب", -10),
        ("seek_back_control", "Back", -10),
        ("seek_back_control", "Back 15", -15),
    ],
)
async def test_front_back_text_commands_call_real_seek_without_queue_navigation(
    handler_name: str,
    text: str,
    expected_delta: int,
):
    seek_handler = _playback_message_handler(handler_name)
    message = _group_message(text)
    result = SimpleNamespace(
        message_key="playback.controls.seek_set_position",
        position_label="0:25",
        show_alert=False,
    )

    with (
        patch("app.handlers.playback._check_prerequisites", AsyncMock(return_value=True)) as gate,
        patch("app.handlers.playback.CallService.seek_playback", AsyncMock(return_value=result)) as seek,
        patch("app.handlers.playback.CallService.play_next", AsyncMock()) as play_next,
        patch("app.handlers.playback.CallService.play_previous", AsyncMock()) as play_previous,
        patch("app.handlers.playback.CallService.set_volume", AsyncMock()) as set_volume,
    ):
        await seek_handler(AsyncMock(), message)

    gate.assert_awaited_once()
    seek.assert_awaited_once()
    assert seek.await_args.args[1] == message.chat.id
    assert seek.await_args.args[2] == expected_delta
    message.reply.assert_awaited_once_with(
        t("fa", "playback.controls.seek_set_position", position=result.position_label)
    )
    play_next.assert_not_awaited()
    play_previous.assert_not_awaited()
    set_volume.assert_not_awaited()


@pytest.mark.asyncio
async def test_front_back_auth_denial_does_not_seek():
    seek_handler = _playback_message_handler("seek_front_control")
    message = _group_message("Front")

    with (
        patch("app.handlers.playback._check_prerequisites", AsyncMock(return_value=False)) as gate,
        patch("app.handlers.playback.CallService.seek_playback", AsyncMock()) as seek,
        patch("app.handlers.playback.CallService.play_next", AsyncMock()) as play_next,
    ):
        await seek_handler(AsyncMock(), message)

    gate.assert_awaited_once()
    seek.assert_not_awaited()
    play_next.assert_not_awaited()
    message.reply.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "handler_name,text",
    [
        ("seek_front_control", "Front test"),
        ("seek_back_control", "Back test"),
        ("seek_front_control", "جلو تست"),
        ("seek_back_control", "عقب تست"),
    ],
)
async def test_front_back_invalid_seconds_does_not_seek_or_auth(handler_name: str, text: str):
    seek_handler = _playback_message_handler(handler_name)
    message = _group_message(text)

    with (
        patch("app.handlers.playback._check_prerequisites", AsyncMock()) as gate,
        patch("app.handlers.playback.CallService.seek_playback", AsyncMock()) as seek,
    ):
        await seek_handler(AsyncMock(), message)

    gate.assert_not_awaited()
    seek.assert_not_awaited()
    message.reply.assert_awaited_once_with(t("fa", "playback.controls.seek_invalid_seconds"))


def test_radio_satellite_text_commands_are_exact_no_argument_aliases():
    from app.handlers.tv_radio import (
        _RADIO_TEXT_CMDS,
        _SATELLITE_TEXT_CMDS,
        _matches_exact_text_command,
    )

    assert _matches_exact_text_command(" پخش رادیو ", _RADIO_TEXT_CMDS)
    assert _matches_exact_text_command("Radio Play", _RADIO_TEXT_CMDS)
    assert _matches_exact_text_command("پخش ماهواره", _SATELLITE_TEXT_CMDS)
    assert _matches_exact_text_command("Satellite Play", _SATELLITE_TEXT_CMDS)

    assert not _matches_exact_text_command("Radio Play BBC", _RADIO_TEXT_CMDS)
    assert not _matches_exact_text_command("Radio    Play", _RADIO_TEXT_CMDS)
    assert not _matches_exact_text_command("پخش رادیو جوان", _RADIO_TEXT_CMDS)
    assert not _matches_exact_text_command("Satellite Play CNN", _SATELLITE_TEXT_CMDS)
    assert not _matches_exact_text_command("پخش ماهواره خبر", _SATELLITE_TEXT_CMDS)


def test_radio_satellite_text_handlers_use_priority_command_group():
    from app.handlers.priority import PRIORITY_COMMAND_GROUP
    from app.handlers.tv_radio import register as register_tv_radio

    bot = _RecorderBot()
    register_tv_radio(bot, MagicMock())
    groups = {
        registration["fn"].__name__: registration["kwargs"].get("group")
        for registration in bot.message_registrations
    }

    assert groups["play_radio_menu"] == PRIORITY_COMMAND_GROUP
    assert groups["play_satellite_menu"] == PRIORITY_COMMAND_GROUP


def test_serial_speed_text_handlers_register_before_generic_playback():
    from app.handlers.priority import PRIORITY_COMMAND_GROUP
    from app.handlers.playback import register as register_playback

    bot = _RecorderBot()
    register_playback(bot, MagicMock())
    names = [registration["fn"].__name__ for registration in bot.message_registrations]
    groups = {
        registration["fn"].__name__: registration["kwargs"].get("group")
        for registration in bot.message_registrations
    }

    assert names.index("play_serial_placeholder") < names.index("play_audio")
    assert names.index("speed_down_control") < names.index("play_audio")
    assert names.index("speed_up_control") < names.index("play_audio")
    assert names.index("volume_down_step") < names.index("play_audio")
    assert names.index("volume_up_step") < names.index("play_audio")
    assert names.index("seek_front_control") < names.index("play_audio")
    assert names.index("seek_back_control") < names.index("play_audio")
    assert groups["play_serial_placeholder"] == PRIORITY_COMMAND_GROUP
    assert groups["speed_down_control"] == PRIORITY_COMMAND_GROUP
    assert groups["speed_up_control"] == PRIORITY_COMMAND_GROUP
    assert groups["volume_down_step"] == PRIORITY_COMMAND_GROUP
    assert groups["volume_up_step"] == PRIORITY_COMMAND_GROUP
    assert groups["seek_front_control"] == PRIORITY_COMMAND_GROUP
    assert groups["seek_back_control"] == PRIORITY_COMMAND_GROUP


@pytest.mark.asyncio
@pytest.mark.parametrize("text", ["پخش رادیو", "Radio Play"])
async def test_radio_text_command_opens_existing_radio_menu(text: str):
    play_radio_menu = _tv_radio_message_handler("play_radio_menu")
    message = _group_message(text)
    stations = [
        {"id": "r1", "name": "Radio One", "url": "https://example.com/r1.mp3"},
        {"id": "bad", "name": "Broken"},
    ]

    with (
        patch("app.handlers.tv_radio.authorize_playback_action", AsyncMock(return_value=True)) as auth,
        patch("app.handlers.tv_radio._load_json", return_value=stations) as load_json,
        patch("app.handlers.tv_radio.CallService.join_voice_chat", AsyncMock()) as join,
    ):
        await play_radio_menu(AsyncMock(), message)

    auth.assert_awaited_once()
    load_json.assert_called_once()
    join.assert_not_awaited()
    message.reply.assert_awaited_once()
    # CONTENT-05: the command now opens the language/country step first; the
    # station buttons live behind the chosen group.
    assert message.reply.await_args.args[0] == t("fa", "tv_radio.choose_radio_group")
    callbacks = _inline_callbacks(message.reply.await_args.kwargs["reply_markup"])
    assert any(cb.startswith("pb:radio:c:") for cb in callbacks)
    # The unusable entry must not create a group of its own beyond "other".
    assert "pb:radio:bad" not in callbacks
    assert "pb:radio:r1" not in callbacks


@pytest.mark.asyncio
@pytest.mark.parametrize("text", ["پخش ماهواره", "Satellite Play"])
async def test_satellite_text_command_opens_existing_satellite_menu(text: str):
    from app.handlers.tv_radio import _LANG

    play_satellite_menu = _tv_radio_message_handler("play_satellite_menu")
    message = _group_message(text)
    channels = [
        {"id": f"s{i}", "name": f"Satellite {i}", "url": f"https://example.com/s{i}.m3u8"}
        for i in range(1, 10)
    ]
    channels.append({"id": "broken", "name": "Broken"})

    with (
        patch("app.handlers.tv_radio.authorize_playback_action", AsyncMock(return_value=True)) as auth,
        patch("app.handlers.tv_radio.deny_free_mode_media", AsyncMock(return_value=False)) as deny,
        patch("app.handlers.tv_radio._load_json", return_value=channels) as load_json,
        patch("app.handlers.tv_radio.CallService.join_voice_chat", AsyncMock()) as join,
    ):
        await play_satellite_menu(AsyncMock(), message)

    auth.assert_awaited_once()
    deny.assert_awaited_once_with(message, "satellite", lang=_LANG)
    load_json.assert_called_once()
    join.assert_not_awaited()
    message.reply.assert_awaited_once()
    # CONTENT-03: the command now opens the topic-category step first; channel
    # buttons and pagination live behind the chosen group.
    assert message.reply.await_args.args[0] == t("fa", "tv_radio.choose_sat_group")
    callbacks = _inline_callbacks(message.reply.await_args.kwargs["reply_markup"])
    assert any(callback.startswith("pb:sat:g:") for callback in callbacks)
    assert "pb:sat:s1" not in callbacks
    assert "pb:sat:broken" not in callbacks


@pytest.mark.asyncio
async def test_radio_text_auth_denial_renders_no_menu():
    play_radio_menu = _tv_radio_message_handler("play_radio_menu")
    message = _group_message("پخش رادیو")

    with (
        patch("app.handlers.tv_radio.authorize_playback_action", AsyncMock(return_value=False)) as auth,
        patch("app.handlers.tv_radio._load_json") as load_json,
    ):
        await play_radio_menu(AsyncMock(), message)

    auth.assert_awaited_once()
    load_json.assert_not_called()
    message.reply.assert_not_awaited()


@pytest.mark.asyncio
async def test_satellite_text_auth_denial_renders_no_menu():
    play_satellite_menu = _tv_radio_message_handler("play_satellite_menu")
    message = _group_message("پخش ماهواره")

    with (
        patch("app.handlers.tv_radio.authorize_playback_action", AsyncMock(return_value=False)) as auth,
        patch("app.handlers.tv_radio.deny_free_mode_media", AsyncMock()) as deny,
        patch("app.handlers.tv_radio._load_json") as load_json,
    ):
        await play_satellite_menu(AsyncMock(), message)

    auth.assert_awaited_once()
    deny.assert_not_awaited()
    load_json.assert_not_called()
    message.reply.assert_not_awaited()


@pytest.mark.asyncio
async def test_satellite_text_free_mode_denial_happens_before_catalog_load():
    from app.handlers.tv_radio import _LANG

    play_satellite_menu = _tv_radio_message_handler("play_satellite_menu")
    message = _group_message("Satellite Play")

    with (
        patch("app.handlers.tv_radio.authorize_playback_action", AsyncMock(return_value=True)) as auth,
        patch("app.handlers.tv_radio.deny_free_mode_media", AsyncMock(return_value=True)) as deny,
        patch("app.handlers.tv_radio._load_json") as load_json,
    ):
        await play_satellite_menu(AsyncMock(), message)

    auth.assert_awaited_once()
    deny.assert_awaited_once_with(message, "satellite", lang=_LANG)
    load_json.assert_not_called()
    message.reply.assert_not_awaited()


@pytest.mark.asyncio
async def test_satellite_text_free_mode_real_message_gate_replies_before_catalog_load():
    from app.services.media_capability_service import denial_message_key

    play_satellite_menu = _tv_radio_message_handler("play_satellite_menu")
    message = _group_message("پخش ماهواره")

    with (
        patch("app.handlers.tv_radio.authorize_playback_action", AsyncMock(return_value=True)) as auth,
        patch(
            "app.services.media_capability_service.is_media_feature_allowed",
            AsyncMock(return_value=False),
        ) as allowed,
        patch("app.handlers.tv_radio._load_json") as load_json,
    ):
        await play_satellite_menu(AsyncMock(), message)

    auth.assert_awaited_once()
    allowed.assert_awaited_once_with(message.chat.id, "satellite")
    load_json.assert_not_called()
    message.reply.assert_awaited_once_with(t("fa", denial_message_key("satellite")))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "catalog_text",
    [
        None,
        "[]",
        "{not valid json",
        '[{"id": "missing-url", "name": "No Url"}]',
    ],
)
async def test_radio_text_catalog_failures_reply_safely(tmp_path: Path, catalog_text: str | None):
    play_radio_menu = _tv_radio_message_handler("play_radio_menu")
    message = _group_message("Radio Play")
    catalog_path = tmp_path / "radio_stations.json"
    if catalog_text is not None:
        catalog_path.write_text(catalog_text, encoding="utf-8")

    with (
        patch("app.handlers.tv_radio.authorize_playback_action", AsyncMock(return_value=True)),
        patch("app.handlers.tv_radio._RADIO_STATIONS_PATH", catalog_path),
        patch("app.handlers.tv_radio.CallService.join_voice_chat", AsyncMock()) as join,
    ):
        await play_radio_menu(AsyncMock(), message)

    join.assert_not_awaited()
    message.reply.assert_awaited_once()
    assert message.reply.await_args.args[0] == t("fa", "tv_radio.no_channels")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "catalog_text",
    [
        None,
        "[]",
        "{not valid json",
        '[{"id": "missing-url", "name": "No Url"}]',
    ],
)
async def test_satellite_text_catalog_failures_reply_safely(tmp_path: Path, catalog_text: str | None):
    play_satellite_menu = _tv_radio_message_handler("play_satellite_menu")
    message = _group_message("Satellite Play")
    catalog_path = tmp_path / "satellite_channels.json"
    if catalog_text is not None:
        catalog_path.write_text(catalog_text, encoding="utf-8")

    with (
        patch("app.handlers.tv_radio.authorize_playback_action", AsyncMock(return_value=True)),
        patch("app.handlers.tv_radio.deny_free_mode_media", AsyncMock(return_value=False)),
        patch("app.handlers.tv_radio._SAT_CHANNELS_PATH", catalog_path),
        patch("app.handlers.tv_radio.CallService.join_voice_chat", AsyncMock()) as join,
    ):
        await play_satellite_menu(AsyncMock(), message)

    join.assert_not_awaited()
    message.reply.assert_awaited_once()
    assert message.reply.await_args.args[0] == t("fa", "tv_radio.no_satellite")


@pytest.mark.asyncio
@pytest.mark.parametrize("text", ["سرچ یوتیوب lo-fi", "Search Youtube lo-fi"])
async def test_help_reference_search_aliases_reuse_search_flow(text: str):
    from app.handlers import search

    bot = _RecorderBot()
    search.register(bot, None)
    handler = next(fn for fn in bot.message_handlers if fn.__name__ == "search_text_alias")
    message = _group_message(text)
    status = AsyncMock()
    message.reply = AsyncMock(return_value=status)

    with patch(
        "app.handlers.search._yt_search",
        AsyncMock(
            return_value=[
                {
                    "id": "dQw4w9WgXcQ",
                    "title": "lo-fi result",
                    "duration": "213",
                }
            ]
        ),
    ) as yt_search:
        await handler(AsyncMock(), message)

    yt_search.assert_awaited_once_with("lo-fi")
    message.reply.assert_awaited_once_with(t("fa", "search.searching"))
    status.edit_text.assert_awaited_once()
    markup = status.edit_text.await_args.kwargs["reply_markup"]
    callbacks = [
        btn.callback_data
        for row in markup.inline_keyboard
        for btn in row
        if getattr(btn, "callback_data", None)
    ]
    assert "search:play:dQw4w9WgXcQ" in callbacks


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "text,query,prefix",
    [
        ("پخش خودکار موزیک lo-fi beats", "lo-fi beats", "پخش خودکار موزیک"),
        ("Play Auto Music lo-fi beats", "lo-fi beats", "Play Auto Music"),
    ],
)
async def test_auto_music_help_alias_routes_first_youtube_result_to_audio_playback(
    text: str,
    query: str,
    prefix: str,
):
    play_auto_music = _play_auto_music_handler()
    message = _group_message(text)
    resolved = {
        "id": "dQw4w9WgXcQ",
        "title": "lo-fi beats",
        "duration": "213",
        "url": "https://youtube.com/watch?v=dQw4w9WgXcQ",
    }

    with (
        patch("app.handlers.playback._check_prerequisites", AsyncMock(return_value=True)) as gate,
        patch(
            "app.handlers.search.resolve_youtube_first_result",
            AsyncMock(return_value=resolved),
        ) as resolver,
        patch("app.handlers.playback._handle_play_command", AsyncMock()) as handle_play,
    ):
        await play_auto_music(AsyncMock(), message)

    gate.assert_awaited_once()
    resolver.assert_awaited_once_with(query)
    handle_play.assert_awaited_once()
    parsed = handle_play.await_args.args[3]
    assert parsed.prefix == prefix
    assert parsed.remainder == resolved["url"]
    assert handle_play.await_args.kwargs == {
        "forced_media_type": "audio",
        "prerequisites_checked": True,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("text", ["پخش خودکار موزیک", "Play Auto Music"])
async def test_auto_music_missing_query_uses_search_query_guidance(text: str):
    play_auto_music = _play_auto_music_handler()
    message = _group_message(text)

    with (
        patch("app.handlers.playback._check_prerequisites", AsyncMock()) as gate,
        patch("app.handlers.search.resolve_youtube_first_result", AsyncMock()) as resolver,
        patch("app.handlers.playback._handle_play_command", AsyncMock()) as handle_play,
    ):
        await play_auto_music(AsyncMock(), message)

    message.reply.assert_awaited_once_with(t("fa", "ask.search_query"))
    gate.assert_not_awaited()
    resolver.assert_not_awaited()
    handle_play.assert_not_awaited()


@pytest.mark.asyncio
async def test_auto_music_no_youtube_result_does_not_fake_playback():
    play_auto_music = _play_auto_music_handler()
    message = _group_message("Play Auto Music no results")

    with (
        patch("app.handlers.playback._check_prerequisites", AsyncMock(return_value=True)) as gate,
        patch(
            "app.handlers.search.resolve_youtube_first_result",
            AsyncMock(return_value=None),
        ) as resolver,
        patch("app.handlers.playback._handle_play_command", AsyncMock()) as handle_play,
    ):
        await play_auto_music(AsyncMock(), message)

    gate.assert_awaited_once()
    resolver.assert_awaited_once_with("no results")
    message.reply.assert_awaited_once_with(t("fa", "search.no_results"))
    handle_play.assert_not_awaited()


@pytest.mark.asyncio
async def test_auto_music_auth_denial_does_not_run_youtube_resolver():
    play_auto_music = _play_auto_music_handler()
    message = _group_message("پخش خودکار موزیک lo-fi")

    with (
        patch("app.handlers.playback._check_prerequisites", AsyncMock(return_value=False)) as gate,
        patch("app.handlers.search.resolve_youtube_first_result", AsyncMock()) as resolver,
        patch("app.handlers.playback._handle_play_command", AsyncMock()) as handle_play,
    ):
        await play_auto_music(AsyncMock(), message)

    gate.assert_awaited_once()
    resolver.assert_not_awaited()
    handle_play.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "text,query,prefix",
    [
        ("پخش خودکار ویدئو lo-fi video", "lo-fi video", "پخش خودکار ویدئو"),
        ("Play Auto Video lo-fi video", "lo-fi video", "Play Auto Video"),
    ],
)
async def test_auto_video_help_alias_routes_first_youtube_result_to_video_playback(
    text: str,
    query: str,
    prefix: str,
):
    play_auto_video = _play_auto_video_handler()
    message = _group_message(text)
    resolved = {
        "id": "dQw4w9WgXcQ",
        "title": "lo-fi video",
        "duration": "213",
        "url": "https://youtube.com/watch?v=dQw4w9WgXcQ",
    }

    with (
        patch("app.handlers.playback._check_prerequisites", AsyncMock(return_value=True)) as gate,
        patch("app.handlers.playback.deny_video_playback", AsyncMock(return_value=False)) as video_gate,
        patch(
            "app.handlers.search.resolve_youtube_first_result",
            AsyncMock(return_value=resolved),
        ) as resolver,
        patch("app.handlers.playback._handle_play_command", AsyncMock()) as handle_play,
    ):
        await play_auto_video(AsyncMock(), message)

    gate.assert_awaited_once()
    video_gate.assert_awaited_once()
    resolver.assert_awaited_once_with(query)
    handle_play.assert_awaited_once()
    parsed = handle_play.await_args.args[3]
    assert parsed.prefix == prefix
    assert parsed.remainder == resolved["url"]
    assert handle_play.await_args.kwargs == {
        "forced_media_type": "video",
        "prerequisites_checked": True,
        "require_stream_resolution": True,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("text", ["پخش خودکار ویدئو", "Play Auto Video"])
async def test_auto_video_missing_query_uses_search_query_guidance(text: str):
    play_auto_video = _play_auto_video_handler()
    message = _group_message(text)

    with (
        patch("app.handlers.playback._check_prerequisites", AsyncMock()) as gate,
        patch("app.handlers.playback.deny_video_playback", AsyncMock()) as video_gate,
        patch("app.handlers.search.resolve_youtube_first_result", AsyncMock()) as resolver,
        patch("app.handlers.playback._handle_play_command", AsyncMock()) as handle_play,
    ):
        await play_auto_video(AsyncMock(), message)

    message.reply.assert_awaited_once_with(t("fa", "ask.search_query"))
    gate.assert_not_awaited()
    video_gate.assert_not_awaited()
    resolver.assert_not_awaited()
    handle_play.assert_not_awaited()


@pytest.mark.asyncio
async def test_auto_video_no_youtube_result_does_not_fake_playback():
    play_auto_video = _play_auto_video_handler()
    message = _group_message("Play Auto Video no results")

    with (
        patch("app.handlers.playback._check_prerequisites", AsyncMock(return_value=True)) as gate,
        patch("app.handlers.playback.deny_video_playback", AsyncMock(return_value=False)) as video_gate,
        patch(
            "app.handlers.search.resolve_youtube_first_result",
            AsyncMock(return_value=None),
        ) as resolver,
        patch("app.handlers.playback._handle_play_command", AsyncMock()) as handle_play,
    ):
        await play_auto_video(AsyncMock(), message)

    gate.assert_awaited_once()
    video_gate.assert_awaited_once()
    resolver.assert_awaited_once_with("no results")
    message.reply.assert_awaited_once_with(t("fa", "search.no_results"))
    handle_play.assert_not_awaited()


@pytest.mark.asyncio
async def test_auto_video_auth_denial_does_not_run_youtube_resolver():
    play_auto_video = _play_auto_video_handler()
    message = _group_message("پخش خودکار ویدئو lo-fi")

    with (
        patch("app.handlers.playback._check_prerequisites", AsyncMock(return_value=False)) as gate,
        patch("app.handlers.playback.deny_video_playback", AsyncMock()) as video_gate,
        patch("app.handlers.search.resolve_youtube_first_result", AsyncMock()) as resolver,
        patch("app.handlers.playback._handle_play_command", AsyncMock()) as handle_play,
    ):
        await play_auto_video(AsyncMock(), message)

    gate.assert_awaited_once()
    video_gate.assert_not_awaited()
    resolver.assert_not_awaited()
    handle_play.assert_not_awaited()


@pytest.mark.asyncio
async def test_auto_video_disabled_gate_does_not_run_youtube_resolver():
    play_auto_video = _play_auto_video_handler()
    message = _group_message("Play Auto Video lo-fi")

    with (
        patch("app.handlers.playback._check_prerequisites", AsyncMock(return_value=True)) as gate,
        patch("app.handlers.playback.deny_video_playback", AsyncMock(return_value=True)) as video_gate,
        patch("app.handlers.search.resolve_youtube_first_result", AsyncMock()) as resolver,
        patch("app.handlers.playback._handle_play_command", AsyncMock()) as handle_play,
    ):
        await play_auto_video(AsyncMock(), message)

    gate.assert_awaited_once()
    video_gate.assert_awaited_once()
    resolver.assert_not_awaited()
    handle_play.assert_not_awaited()


@pytest.mark.asyncio
async def test_media_service_audio_stream_format_default_is_preserved():
    from app.services.media_service import MediaService

    with (
        patch(
            "app.services.media_service.validate_safe_url_with_redirects",
            AsyncMock(side_effect=lambda value: value),
        ),
        patch(
            "app.services.media_service._get_output",
            return_value="https://cdn.example.com/audio\n",
        ) as get_output,
    ):
        stream_url = await MediaService.get_stream_url("https://youtube.com/watch?v=abc")

    cmd = get_output.call_args.args[0]
    assert cmd[cmd.index("-f") + 1] == "bestaudio/best"
    assert stream_url == "https://cdn.example.com/audio"


@pytest.mark.asyncio
async def test_media_service_video_stream_format_requires_video_and_audio_codecs():
    from app.services.media_service import MediaService

    with (
        patch(
            "app.services.media_service.validate_safe_url_with_redirects",
            AsyncMock(side_effect=lambda value: value),
        ),
        patch(
            "app.services.media_service._get_output",
            return_value="https://cdn.example.com/video.mp4\n",
        ) as get_output,
    ):
        stream_url = await MediaService.get_stream_url(
            "https://youtube.com/watch?v=abc",
            media_type="video",
        )

    cmd = get_output.call_args.args[0]
    fmt = cmd[cmd.index("-f") + 1]
    assert "vcodec!=none" in fmt
    assert "acodec!=none" in fmt
    assert "bestaudio/best" not in fmt
    assert stream_url == "https://cdn.example.com/video.mp4"


@pytest.mark.asyncio
async def test_media_service_video_stream_empty_output_returns_none():
    from app.services.media_service import MediaService

    with (
        patch(
            "app.services.media_service.validate_safe_url_with_redirects",
            AsyncMock(side_effect=lambda value: value),
        ),
        patch("app.services.media_service._get_output", return_value="") as get_output,
    ):
        stream_url = await MediaService.get_stream_url(
            "https://youtube.com/watch?v=abc",
            media_type="video",
        )

    cmd = get_output.call_args.args[0]
    assert "vcodec!=none" in cmd[cmd.index("-f") + 1]
    assert stream_url is None


@pytest.mark.asyncio
async def test_auto_music_first_result_resolver_returns_canonical_watch_url():
    from app.handlers.search import resolve_youtube_first_result

    with patch(
        "app.handlers.search._yt_search",
        AsyncMock(
            return_value=[
                {"id": "", "title": "invalid", "duration": "1"},
                {"id": "dQw4w9WgXcQ", "title": "valid", "duration": "213"},
            ]
        ),
    ) as yt_search:
        result = await resolve_youtube_first_result("lo-fi")

    yt_search.assert_awaited_once_with("lo-fi")
    assert result == {
        "id": "dQw4w9WgXcQ",
        "title": "valid",
        "duration": "213",
        "url": "https://youtube.com/watch?v=dQw4w9WgXcQ",
    }


@pytest.mark.asyncio
async def test_empty_persian_play_gets_visible_guidance():
    play_audio = _play_audio_handler()
    message = _group_message("پخش")

    with patch("app.handlers.playback.authorize_playback_action", AsyncMock(return_value=True)):
        await play_audio(AsyncMock(), message)

    message.reply.assert_awaited_once_with(t("fa", "playback_cmd.provide_source"))

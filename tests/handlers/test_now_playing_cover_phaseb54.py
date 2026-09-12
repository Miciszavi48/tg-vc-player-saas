"""Phase B5-4: Telegram-native now-playing cover display."""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

os.environ.setdefault("TEST_MODE", "1")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./.pytest-test-mode.db")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/15")
os.environ.setdefault("BOT_TOKEN", "test")
os.environ.setdefault("API_ID", "12345")
os.environ.setdefault("API_HASH", "testhash")
os.environ.setdefault("DEVELOPER_ID", "123456789")


def _thumb(file_id: str) -> SimpleNamespace:
    return SimpleNamespace(file_id=file_id)


def test_extract_audio_thumbnail_file_id():
    from app.services.cover_art_service import extract_telegram_cover_from_reply

    reply = SimpleNamespace(
        audio=SimpleNamespace(thumb=_thumb("audio-thumb-id")),
        video=None,
        document=None,
    )
    cover = extract_telegram_cover_from_reply(reply)
    assert cover is not None
    assert cover.file_id == "audio-thumb-id"
    assert cover.source == "telegram_audio"


def test_extract_video_thumbnail_file_id():
    from app.services.cover_art_service import extract_telegram_cover_from_reply

    reply = SimpleNamespace(
        audio=None,
        video=SimpleNamespace(thumbnail=_thumb("video-thumb-id")),
        document=None,
    )
    cover = extract_telegram_cover_from_reply(reply)
    assert cover is not None
    assert cover.file_id == "video-thumb-id"
    assert cover.source == "telegram_video"


def test_extract_document_thumbnail_file_id():
    from app.services.cover_art_service import extract_telegram_cover_from_reply

    reply = SimpleNamespace(
        audio=None,
        video=None,
        document=SimpleNamespace(
            mime_type="audio/mpeg",
            thumbs=[_thumb("small"), _thumb("doc-thumb-id")],
        ),
    )
    cover = extract_telegram_cover_from_reply(reply)
    assert cover is not None
    assert cover.file_id == "doc-thumb-id"
    assert cover.source == "telegram_document"


def test_extract_returns_none_without_thumbnail():
    from app.services.cover_art_service import extract_telegram_cover_from_reply

    reply = SimpleNamespace(
        audio=SimpleNamespace(thumb=None, thumbnail=None, thumbs=[]),
        video=None,
        document=None,
    )
    assert extract_telegram_cover_from_reply(reply) is None
    assert extract_telegram_cover_from_reply(None) is None


def test_extract_supports_thumb_and_thumbnail_variants():
    from app.services.cover_art_service import extract_telegram_cover_from_reply

    via_thumb = SimpleNamespace(
        audio=SimpleNamespace(thumb=_thumb("via-thumb"), thumbnail=None, thumbs=[]),
        video=None,
        document=None,
    )
    via_thumbnail = SimpleNamespace(
        audio=None,
        video=SimpleNamespace(thumb=None, thumbnail=_thumb("via-thumbnail"), thumbs=[]),
        document=None,
    )
    assert extract_telegram_cover_from_reply(via_thumb).file_id == "via-thumb"
    assert extract_telegram_cover_from_reply(via_thumbnail).file_id == "via-thumbnail"


def test_cover_service_has_no_external_download():
    source = Path("app/services/cover_art_service.py").read_text(encoding="utf-8").lower()
    for token in ("aiohttp", "httpx", "requests", "urllib", "validate_safe_url", "yt_dlp", "yt-dlp"):
        assert token not in source


@pytest.mark.asyncio
async def test_show_cover_false_skips_extraction_and_photo():
    from app.services.cover_art_service import reply_now_playing_with_optional_cover

    message = AsyncMock()
    message.reply_to_message = SimpleNamespace(
        audio=SimpleNamespace(thumb=_thumb("hidden-id")),
        video=None,
        document=None,
    )
    keyboard = object()

    with patch(
        "app.services.cover_art_service.is_show_cover_enabled",
        AsyncMock(return_value=False),
    ), patch(
        "app.services.cover_art_service.extract_telegram_cover_from_reply",
    ) as extract_mock:
        await reply_now_playing_with_optional_cover(
            message,
            chat_id=-1001,
            text="Playing",
            reply_markup=keyboard,
        )

    extract_mock.assert_not_called()
    message.reply_photo.assert_not_awaited()
    message.reply.assert_awaited_once_with("Playing", reply_markup=keyboard)


@pytest.mark.asyncio
async def test_show_cover_true_sends_photo_with_caption():
    from app.services.cover_art_service import reply_now_playing_with_optional_cover

    message = AsyncMock()
    message.reply_to_message = SimpleNamespace(
        audio=SimpleNamespace(thumb=_thumb("cover-abc")),
        video=None,
        document=None,
    )
    keyboard = object()

    with patch(
        "app.services.cover_art_service.is_show_cover_enabled",
        AsyncMock(return_value=True),
    ):
        await reply_now_playing_with_optional_cover(
            message,
            chat_id=-1002,
            text="موزیک در حال پخش است",
            reply_markup=keyboard,
            reply_to_message=message.reply_to_message,
        )

    message.reply_photo.assert_awaited_once_with(
        "cover-abc",
        caption="موزیک در حال پخش است",
        reply_markup=keyboard,
    )
    message.reply.assert_not_awaited()


@pytest.mark.asyncio
async def test_photo_send_failure_falls_back_to_text():
    from app.services.cover_art_service import reply_now_playing_with_optional_cover

    message = AsyncMock()
    message.reply_photo = AsyncMock(side_effect=RuntimeError("photo failed"))
    message.reply_to_message = SimpleNamespace(
        video=SimpleNamespace(thumb=_thumb("vid-cover")),
        audio=None,
        document=None,
    )
    keyboard = object()

    with patch(
        "app.services.cover_art_service.is_show_cover_enabled",
        AsyncMock(return_value=True),
    ):
        await reply_now_playing_with_optional_cover(
            message,
            chat_id=-1003,
            text="Now playing",
            reply_markup=keyboard,
            reply_to_message=message.reply_to_message,
        )

    message.reply_photo.assert_awaited_once()
    message.reply.assert_awaited_once_with("Now playing", reply_markup=keyboard)


@pytest.mark.asyncio
async def test_text_fallback_preserves_keyboard():
    from app.services.cover_art_service import reply_now_playing_with_optional_cover

    message = AsyncMock()
    message.reply_to_message = None
    keyboard = SimpleNamespace(label="kb")

    with patch(
        "app.services.cover_art_service.is_show_cover_enabled",
        AsyncMock(return_value=True),
    ):
        await reply_now_playing_with_optional_cover(
            message,
            chat_id=-1004,
            text="Text only",
            reply_markup=keyboard,
        )

    message.reply.assert_awaited_once_with("Text only", reply_markup=keyboard)


@pytest.mark.asyncio
async def test_caption_never_includes_file_id():
    from app.services.cover_art_service import reply_now_playing_with_optional_cover

    file_id = "AgACAgIAAxkBAAI-secret-thumb"
    message = AsyncMock()
    message.reply_to_message = SimpleNamespace(
        audio=SimpleNamespace(thumb=_thumb(file_id)),
        video=None,
        document=None,
    )

    with patch(
        "app.services.cover_art_service.is_show_cover_enabled",
        AsyncMock(return_value=True),
    ):
        await reply_now_playing_with_optional_cover(
            message,
            chat_id=-1005,
            text="موزیک در حال پخش است",
            reply_to_message=message.reply_to_message,
        )

    _args, kwargs = message.reply_photo.await_args
    assert file_id not in kwargs.get("caption", "")
    assert _args[0] == file_id


def test_search_remains_text_only():
    source = Path("app/handlers/search.py").read_text(encoding="utf-8")
    assert "reply_photo" not in source
    assert "cover_art_service" not in source
    assert "edit_text" in source


def test_tv_radio_remains_text_only():
    source = Path("app/handlers/tv_radio.py").read_text(encoding="utf-8")
    assert "reply_photo" not in source
    assert "cover_art_service" not in source
    assert "edit_text" in source


def test_queue_on_busy_unchanged():
    from app.services.playback_dispatch_service import BusyPlaybackAction, _MESSAGE_KEYS

    assert _MESSAGE_KEYS[BusyPlaybackAction.QUEUED] == "playback_cmd.queued"
    assert "cover_art_service" not in Path(
        "app/services/playback_dispatch_service.py"
    ).read_text(encoding="utf-8")


def test_show_cover_toggle_visible_and_callback_unchanged():
    from app.utils.ui import _GRP_VISIBLE_SETTING_TOGGLES
    from app.handlers.group_panel import _SETTING_TOGGLE_MAP
    from app.utils.ui import CB

    assert "show_photo" in _GRP_VISIBLE_SETTING_TOGGLES
    assert _SETTING_TOGGLE_MAP[CB["GRP_SHOW_PHOTO"]] == "show_cover"


@pytest.mark.asyncio
async def test_is_show_cover_enabled_defaults_true_when_missing_row():
    from app.services.cover_art_service import is_show_cover_enabled

    with patch(
        "app.repositories.settings_repo.get_chat_settings",
        AsyncMock(return_value=None),
    ):
        assert await is_show_cover_enabled(-9999) is True


@pytest.mark.asyncio
async def test_is_show_cover_enabled_reads_chat_settings():
    from app.services.cover_art_service import is_show_cover_enabled

    cs = SimpleNamespace(show_cover=False)
    with patch(
        "app.repositories.settings_repo.get_chat_settings",
        AsyncMock(return_value=cs),
    ):
        assert await is_show_cover_enabled(-1006) is False


@pytest.mark.asyncio
async def test_playback_wires_cover_helper_on_success():
    source = Path("app/handlers/playback.py").read_text(encoding="utf-8")
    assert "reply_now_playing_with_optional_cover" in source
    assert source.count("reply_now_playing_with_optional_cover") >= 3

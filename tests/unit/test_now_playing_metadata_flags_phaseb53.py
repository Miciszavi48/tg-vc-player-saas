"""Phase B5-3: metadata flags wired to group settings UI and renderer."""
from __future__ import annotations

import os
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


METADATA_VISIBLE_KEYS = frozenset({"show_id", "show_photo", "show_text"})


def _settings_dict(**overrides) -> dict:
    base = {
        "music_video": True,
        "security_call": False,
        "repeat": False,
        "download_users": True,
        "call_message": True,
        "auto_clean": False,
        "queue": False,
        "auto_ready_call": False,
        "call_report": True,
        "record_call": True,
        "show_id": False,
        "show_photo": True,
        "show_text": True,
        "default_media_type": "audio",
        "language": "fa",
    }
    base.update(overrides)
    return base


class _RecorderBot:
    def __init__(self) -> None:
        self.callback_handlers: list = []

    def on_message(self, *args, **kwargs):  # noqa: ANN001, ANN002
        def _decorator(fn):
            return fn

        return _decorator

    def on_callback_query(self, *args, **kwargs):  # noqa: ANN001, ANN002
        def _decorator(fn):
            self.callback_handlers.append(fn)
            return fn

        return _decorator


def _handler_by_name(handlers: list, name: str):
    return next(fn for fn in handlers if fn.__name__ == name)


def test_group_settings_shows_metadata_toggles():
    from app.utils.ui import CB, KeyboardFactory, _GRP_VISIBLE_SETTING_TOGGLES

    assert METADATA_VISIBLE_KEYS <= set(_GRP_VISIBLE_SETTING_TOGGLES.keys())
    kb = KeyboardFactory.group_settings("fa", _settings_dict())
    toggle_cbs = {
        btn.callback_data
        for row in kb.inline_keyboard
        for btn in row
        if btn.callback_data and btn.callback_data.startswith("grp:set:")
    }
    assert CB["GRP_SHOW_ID"] in toggle_cbs
    assert CB["GRP_SHOW_PHOTO"] in toggle_cbs
    assert CB["GRP_SHOW_TEXT"] in toggle_cbs


@pytest.mark.parametrize(
    ("callback_key", "db_field", "legacy_field"),
    [
        ("GRP_SHOW_ID", "show_track_id", "inline_enabled"),
        ("GRP_SHOW_PHOTO", "show_cover", "soundcloud_enabled"),
        ("GRP_SHOW_TEXT", "show_now_playing_text", "spotify_enabled"),
    ],
)
@pytest.mark.asyncio
async def test_metadata_toggle_writes_new_column_not_legacy(
    callback_key: str,
    db_field: str,
    legacy_field: str,
):
    from app.handlers import group_panel
    from app.handlers.group_panel import _SETTING_TOGGLE_MAP
    from app.utils.ui import CB

    assert _SETTING_TOGGLE_MAP[CB[callback_key]] == db_field
    assert _SETTING_TOGGLE_MAP[CB[callback_key]] != legacy_field

    bot = _RecorderBot()
    group_panel.register(bot, None)
    toggle_handler = _handler_by_name(bot.callback_handlers, "grp_toggle_setting")

    query = AsyncMock()
    query.data = CB[callback_key]
    query.from_user = SimpleNamespace(id=42)
    query.message = AsyncMock()
    query.message.chat = SimpleNamespace(id=-1001)
    query.message.edit_text = AsyncMock()
    query.answer = AsyncMock()

    cs = SimpleNamespace(**{db_field: False})
    with (
        patch("app.utils.decorators.is_developer", return_value=True),
        patch("app.handlers.group_panel.group_runtime_state_service.require_active_group", AsyncMock(return_value=True)),
        patch("app.handlers.group_panel.allow_group_chat_settings_change", AsyncMock(return_value=True)),
        patch("app.handlers.group_panel.settings_repo.get_chat_settings", AsyncMock(return_value=cs)),
        patch("app.handlers.group_panel.settings_repo.update_setting", AsyncMock()) as update_mock,
        patch("app.handlers.group_panel.invalidate_chat_settings", AsyncMock()),
        patch("app.handlers.group_panel._get_settings_dict", AsyncMock(return_value=_settings_dict())),
    ):
        await toggle_handler(AsyncMock(), query)

    update_mock.assert_awaited_once_with(-1001, db_field, True)
    assert legacy_field not in {call.args[1] for call in update_mock.await_args_list}


@pytest.mark.asyncio
async def test_ordinary_user_cannot_toggle_metadata():
    from app.handlers import group_panel
    from app.utils.ui import CB

    bot = _RecorderBot()
    group_panel.register(bot, None)
    toggle_handler = _handler_by_name(bot.callback_handlers, "grp_toggle_setting")

    query = AsyncMock()
    query.data = CB["GRP_SHOW_ID"]
    query.from_user = SimpleNamespace(id=999)
    query.message = AsyncMock()
    query.message.chat = SimpleNamespace(id=-1002)
    query.message.edit_text = AsyncMock()
    query.answer = AsyncMock()

    with (
        patch("app.utils.decorators.is_developer", return_value=False),
        patch("app.utils.decorators.deny_if_bot_disabled", AsyncMock(return_value=True)),
        patch("app.handlers.group_panel.settings_repo.update_setting", AsyncMock()) as update_mock,
    ):
        await toggle_handler(AsyncMock(), query)

    update_mock.assert_not_awaited()
    query.message.edit_text.assert_not_awaited()


@pytest.mark.asyncio
async def test_sudo_without_chat_settings_permission_denied():
    from app.handlers import group_panel
    from app.utils.ui import CB

    bot = _RecorderBot()
    group_panel.register(bot, None)
    toggle_handler = _handler_by_name(bot.callback_handlers, "grp_toggle_setting")

    query = AsyncMock()
    query.data = CB["GRP_SHOW_TEXT"]
    query.from_user = SimpleNamespace(id=77)
    query.message = AsyncMock()
    query.message.chat = SimpleNamespace(id=-1003)
    query.message.edit_text = AsyncMock()
    query.answer = AsyncMock()

    with (
        patch("app.utils.decorators.is_developer", return_value=False),
        patch("app.utils.decorators.deny_if_bot_disabled", AsyncMock(return_value=False)),
        patch("app.utils.decorators.user_repo.is_owner", AsyncMock(return_value=False)),
        patch("app.utils.decorators.user_repo.is_sudo", AsyncMock(return_value=True)),
        patch("app.utils.decorators.can_use_sudo_admin_bypass", AsyncMock(return_value=True)),
        patch("app.utils.decorators.admin_repo.is_music_admin_or_above", AsyncMock(return_value=True)),
        patch("app.utils.player_permissions.can_open_group_settings", AsyncMock(return_value=False)),
        patch("app.handlers.group_panel.group_runtime_state_service.require_active_group", AsyncMock(return_value=True)),
        patch("app.handlers.group_panel.allow_group_chat_settings_change", AsyncMock(return_value=False)),
        patch("app.handlers.group_panel.notify_sudo_permission_denied", AsyncMock()) as deny_mock,
        patch("app.handlers.group_panel.settings_repo.update_setting", AsyncMock()) as update_mock,
    ):
        await toggle_handler(AsyncMock(), query)

    deny_mock.assert_awaited_once()
    update_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_developer_toggle_metadata_works():
    from app.handlers import group_panel
    from app.utils.ui import CB

    bot = _RecorderBot()
    group_panel.register(bot, None)
    toggle_handler = _handler_by_name(bot.callback_handlers, "grp_toggle_setting")

    query = AsyncMock()
    query.data = CB["GRP_SHOW_PHOTO"]
    query.from_user = SimpleNamespace(id=1)
    query.message = AsyncMock()
    query.message.chat = SimpleNamespace(id=-1004)
    query.message.edit_text = AsyncMock()
    query.answer = AsyncMock()

    cs = SimpleNamespace(show_cover=True)
    with (
        patch("app.utils.decorators.is_developer", return_value=True),
        patch("app.handlers.group_panel.group_runtime_state_service.require_active_group", AsyncMock(return_value=True)),
        patch("app.handlers.group_panel.allow_group_chat_settings_change", AsyncMock(return_value=True)),
        patch("app.handlers.group_panel.settings_repo.get_chat_settings", AsyncMock(return_value=cs)),
        patch("app.handlers.group_panel.settings_repo.update_setting", AsyncMock()) as update_mock,
        patch("app.handlers.group_panel.invalidate_chat_settings", AsyncMock()),
        patch("app.handlers.group_panel._get_settings_dict", AsyncMock(return_value=_settings_dict())),
    ):
        await toggle_handler(AsyncMock(), query)

    update_mock.assert_awaited_once_with(-1004, "show_cover", False)


@pytest.mark.asyncio
async def test_inactive_group_cannot_toggle_metadata():
    from app.handlers import group_panel
    from app.utils.i18n import t
    from app.utils.ui import CB

    bot = _RecorderBot()
    group_panel.register(bot, None)
    toggle_handler = _handler_by_name(bot.callback_handlers, "grp_toggle_setting")

    query = SimpleNamespace(
        data=CB["GRP_SHOW_TEXT"],
        from_user=SimpleNamespace(id=1),
        message=SimpleNamespace(
            chat=SimpleNamespace(id=-1005),
            edit_text=AsyncMock(),
        ),
        answer=AsyncMock(),
    )

    with (
        patch("app.utils.decorators.is_developer", return_value=True),
        patch("app.handlers.group_panel.CallbackQuery", SimpleNamespace),
        patch("app.handlers.group_panel.group_runtime_state_service.require_active_group", AsyncMock(return_value=False)),
        patch("app.handlers.group_panel.settings_repo.update_setting", AsyncMock()) as update_mock,
    ):
        await toggle_handler(AsyncMock(), query)

    query.answer.assert_awaited_once_with(t("fa", "panels.group.not_managed"), show_alert=True)
    update_mock.assert_not_awaited()
    query.message.edit_text.assert_not_awaited()


@pytest.mark.asyncio
async def test_renderer_minimal_when_show_now_playing_text_false():
    from app.services.now_playing_renderer import (
        NowPlayingContext,
        NowPlayingDisplayFlags,
        render_now_playing_text,
    )

    text = await render_now_playing_text(
        -1001,
        NowPlayingContext(
            title="Song",
            media_type="audio",
            duration=120,
            requester_name="Ali",
            track_id="dQw4w9WgXcQ",
        ),
        lang="fa",
        flags=NowPlayingDisplayFlags(
            show_track_id=True,
            show_cover=True,
            show_now_playing_text=False,
        ),
    )

    assert "در حال پخش" in text
    assert "عنوان: Song" in text
    assert "مدت" not in text
    assert "نوع" not in text
    assert "درخواست" not in text
    assert "شناسه: dQw4w9WgXcQ" in text


@pytest.mark.asyncio
async def test_renderer_structured_when_show_now_playing_text_true():
    from app.services.now_playing_renderer import (
        NowPlayingContext,
        NowPlayingDisplayFlags,
        render_now_playing_text,
    )

    text = await render_now_playing_text(
        -1001,
        NowPlayingContext(title="Song", media_type="audio", duration=90),
        lang="en",
        flags=NowPlayingDisplayFlags(show_now_playing_text=True),
    )

    assert "Music Playing" in text
    assert "Title: Song" in text
    assert "Duration: 1:30" in text


@pytest.mark.asyncio
async def test_renderer_shows_track_id_only_when_enabled():
    from app.services.now_playing_renderer import (
        NowPlayingContext,
        NowPlayingDisplayFlags,
        render_now_playing_text,
    )

    off = await render_now_playing_text(
        -1001,
        NowPlayingContext(title="Song", media_type="audio", track_id="dQw4w9WgXcQ"),
        lang="fa",
        flags=NowPlayingDisplayFlags(show_track_id=False, show_now_playing_text=True),
    )
    on = await render_now_playing_text(
        -1001,
        NowPlayingContext(title="Song", media_type="audio", track_id="dQw4w9WgXcQ"),
        lang="fa",
        flags=NowPlayingDisplayFlags(show_track_id=True, show_now_playing_text=True),
    )

    assert "dQw4w9WgXcQ" not in off
    assert "شناسه: dQw4w9WgXcQ" in on


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "unsafe_id",
    [
        "https://youtube.com/watch?v=abc",
        r"C:\downloads\track.ogg",
        "BQACAgQAAxkBAAISampleTelegramFileIdValue1234567890",
    ],
)
async def test_renderer_suppresses_unsafe_track_ids(unsafe_id: str):
    from app.services.now_playing_renderer import (
        NowPlayingContext,
        NowPlayingDisplayFlags,
        render_now_playing_text,
    )

    text = await render_now_playing_text(
        -1001,
        NowPlayingContext(title="Song", media_type="audio", track_id=unsafe_id),
        lang="en",
        flags=NowPlayingDisplayFlags(show_track_id=True, show_now_playing_text=True),
    )

    assert unsafe_id not in text
    assert "Track ID" not in text


@pytest.mark.asyncio
async def test_renderer_does_not_show_requester_id():
    from app.services.now_playing_renderer import (
        NowPlayingContext,
        NowPlayingDisplayFlags,
        render_now_playing_text,
    )

    text = await render_now_playing_text(
        -1001,
        NowPlayingContext(
            title="Song",
            media_type="audio",
            requester_name="Ali",
            requester_id=987654321,
        ),
        lang="en",
        flags=NowPlayingDisplayFlags(show_now_playing_text=True),
    )

    assert "987654321" not in text
    assert "Ali" in text


def test_show_cover_renderer_stays_text_only():
    from pathlib import Path

    renderer = Path("app/services/now_playing_renderer.py").read_text(encoding="utf-8")
    assert "send_photo" not in renderer
    assert "reply_photo" not in renderer
    assert "thumbnail_url" not in renderer
    assert "show_cover" in renderer

    cover = Path("app/services/cover_art_service.py").read_text(encoding="utf-8")
    assert "aiohttp" not in cover
    assert "validate_safe_url" not in cover


def test_search_stays_audio_only():
    source = open("app/handlers/search.py", encoding="utf-8").read()
    assert 'media_type = "audio"' in source
    assert 'media_type = "video"' not in source


def test_queue_on_busy_keys_unchanged():
    from app.services.playback_dispatch_service import BusyPlaybackAction, _MESSAGE_KEYS

    assert _MESSAGE_KEYS[BusyPlaybackAction.QUEUED] == "playback_cmd.queued"


@pytest.mark.asyncio
async def test_buttons_enabled_independent_of_metadata_text():
    from app.services.media_capability_service import build_now_playing_controls
    from app.services.now_playing_renderer import (
        NowPlayingContext,
        NowPlayingDisplayFlags,
        render_now_playing_text,
    )
    from app.utils.ui import CB

    text = await render_now_playing_text(
        -1001,
        NowPlayingContext(title="Song", media_type="audio"),
        lang="fa",
        flags=NowPlayingDisplayFlags(show_now_playing_text=False),
    )

    cs_on = SimpleNamespace(buttons_enabled=True)
    cs_off = SimpleNamespace(buttons_enabled=False)
    with patch(
        "app.repositories.settings_repo.get_chat_settings",
        AsyncMock(side_effect=[cs_on, cs_off]),
    ):
        kb_on = await build_now_playing_controls("fa", -1001)
        kb_off = await build_now_playing_controls("fa", -1001)

    on_cbs = {b.callback_data for row in kb_on.inline_keyboard for b in row}
    off_cbs = {b.callback_data for row in kb_off.inline_keyboard for b in row}
    assert CB["PB_VOL_DOWN"] in on_cbs
    assert CB["PB_VOL_DOWN"] not in off_cbs
    assert "Song" in text


def test_legacy_columns_untouched_in_toggle_map():
    from app.handlers.group_panel import _SETTING_TOGGLE_MAP

    values = set(_SETTING_TOGGLE_MAP.values())
    assert "inline_enabled" not in values
    assert "soundcloud_enabled" not in values
    assert "spotify_enabled" not in values
    assert "show_track_id" in values
    assert "show_cover" in values
    assert "show_now_playing_text" in values

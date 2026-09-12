"""Text command normalization and priority routing regression tests."""
from __future__ import annotations

import os
import re
import sys
from types import ModuleType, SimpleNamespace
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

if "pyromod" not in sys.modules:
    pyromod_module = ModuleType("pyromod")
    pyromod_exceptions = ModuleType("pyromod.exceptions")
    pyromod_exceptions.ListenerStopped = type("ListenerStopped", (Exception,), {})
    pyromod_module.exceptions = pyromod_exceptions
    sys.modules["pyromod"] = pyromod_module
    sys.modules["pyromod.exceptions"] = pyromod_exceptions

from app.handlers import credit_commands, group_panel, help_center
from app.handlers.priority import PRIORITY_COMMAND_GROUP
from app.utils.i18n import t
from app.utils.text_commands import (
    help_command_filter,
    is_escape_command,
    is_help_command,
    normalize_command_text,
    normalize_digits,
)


def _msg(text: str, *, caption: str | None = None):
    return SimpleNamespace(text=text, caption=caption)


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("راهنما", True),
        ("  راهنما  ", True),
        ("help", True),
        ("/help", True),
        ("کمک", True),
        ("کمک\u200c", True),
        ("راهنما\u200c", True),
        ("پخش", False),
        ("آپدیت شارژ ۳۰", False),
    ],
)
def test_is_help_command_normalization(raw: str, expected: bool):
    assert is_help_command(raw) is expected


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("/help", True),
        ("راهنما", True),
        ("/panel", True),
        ("پنل", True),
        ("/cancel", True),
        ("لغو", True),
        ("random", False),
    ],
)
def test_is_escape_command(raw: str, expected: bool):
    assert is_escape_command(raw) is expected


def test_normalize_digits_persian():
    assert normalize_digits("آپدیت شارژ ۳۰") == "آپدیت شارژ 30"
    assert normalize_digits("آپدیت شارژ ویدیو ۱۵") == "آپدیت شارژ ویدیو 15"


@pytest.mark.parametrize(
    "raw,days",
    [
        ("آپدیت شارژ 30", "30"),
        ("آپدیت شارژ ۳۰", "30"),
        ("update charge 30", "30"),
    ],
)
def test_charge_music_aliases_parse_without_matching_video(raw: str, days: str):
    text = normalize_digits(normalize_command_text(_msg(raw)))
    match = credit_commands._CHARGE_PATTERN.fullmatch(text)
    assert match
    assert match.group(1) == days
    assert not credit_commands._CHARGE_VIDEO_PATTERN.fullmatch(text)


@pytest.mark.parametrize(
    "raw,days",
    [
        ("آپدیت شارژ ویدیو 15", "15"),
        ("آپدیت شارژ ویدیو ۱۵", "15"),
        ("update charge video 15", "15"),
    ],
)
def test_charge_video_aliases_parse_without_matching_music(raw: str, days: str):
    text = normalize_digits(normalize_command_text(_msg(raw)))
    match = credit_commands._CHARGE_VIDEO_PATTERN.fullmatch(text)
    assert match
    assert match.group(1) == days
    assert not credit_commands._CHARGE_PATTERN.fullmatch(text)


def test_charge_video_before_music_regex_order():
    video = credit_commands._CHARGE_VIDEO_PATTERN
    music = credit_commands._CHARGE_PATTERN
    text = normalize_digits(normalize_command_text(_msg("آپدیت شارژ ویدیو ۵")))
    assert video.fullmatch(text)
    assert not music.fullmatch(text)


@pytest.mark.asyncio
async def test_charge_persian_digits_parsed():
    message = MagicMock()
    message.from_user = SimpleNamespace(id=999)
    message.chat = SimpleNamespace(id=-100, type=SimpleNamespace(value="supergroup"))
    message.text = "آپدیت شارژ ۳۰"
    message.reply_text = AsyncMock()
    client = MagicMock()

    with (
        patch("app.handlers.credit_commands.user_repo.is_sudo_or_above", AsyncMock(return_value=True)),
        patch("app.handlers.credit_commands.user_repo.is_owner", AsyncMock(return_value=True)),
        patch("app.handlers.credit_commands.CreditService.charge_managed_chat", AsyncMock()) as charge,
        patch("app.handlers.credit_commands.NotificationService.notify_credit_charge", AsyncMock()),
    ):
        await credit_commands._handle_charge(client, message, is_video=False)

    charge.assert_awaited_once()
    assert charge.await_args.args[:3] == (-100, "group", 30)
    message.reply_text.assert_awaited()
    assert "30" in str(message.reply_text.await_args)


@pytest.mark.asyncio
async def test_help_handler_priority_group_registered():
    bot = MagicMock()
    captured: list[dict] = []

    def on_message(*args, **kwargs):
        captured.append({"args": args, "kwargs": kwargs})

        def deco(fn):
            return fn

        return deco

    bot.on_message = on_message
    bot.on_callback_query = lambda *a, **k: (lambda fn: fn)
    help_center.register(bot, None)
    assert captured
    assert captured[0]["kwargs"].get("group") == PRIORITY_COMMAND_GROUP


def test_panel_normalized_alias_matches_zwnj():
    from app.handlers.group_panel import _PANEL_TEXT_CMDS

    pattern = re.compile(
        rf"^(?:{'|'.join(re.escape(c) for c in _PANEL_TEXT_CMDS)})\s*$",
        re.IGNORECASE,
    )
    text = normalize_command_text(_msg("پنل\u200c"))
    assert pattern.fullmatch(text)


def test_panel_exact_alias_matches_without_help_conflict():
    from app.handlers.group_panel import _PANEL_TEXT_CMDS

    pattern = re.compile(
        rf"^(?:{'|'.join(re.escape(c) for c in _PANEL_TEXT_CMDS)})\s*$",
        re.IGNORECASE,
    )
    assert pattern.fullmatch(normalize_command_text(_msg("پنل")))
    assert not is_help_command("پنل")
    assert is_help_command("راهنما")
    assert is_help_command("کمک")


def test_playback_filter_does_not_match_charge_command():
    from app.utils.playback_commands import parse_playback_command

    text = normalize_command_text(_msg("آپدیت شارژ 30"))
    assert parse_playback_command(text) is None


@pytest.mark.parametrize("raw", ["/play test", "play test", "پخش test"])
def test_playback_command_aliases_still_route(raw: str):
    from app.utils.playback_commands import parse_playback_command

    parsed = parse_playback_command(normalize_command_text(_msg(raw)))
    assert parsed is not None
    assert parsed.remainder == "test"


def test_radio_satellite_module_registers_before_generic_playback_module():
    from app.handlers import _MODULES, playback, tv_radio

    assert _MODULES.index(tv_radio) < _MODULES.index(playback)


@pytest.mark.parametrize(
    "raw",
    [
        "Play Auto Music test",
        "Play Auto Video test",
        "پخش خودکار موزیک test",
        "پخش خودکار ویدئو test",
        "پخش خودکار ویدیو test",
        "Radio Play",
        "Radio Play BBC",
        "Satellite Play",
        "Satellite Play CNN",
        "Serial Play",
        "Serial Play test",
        "Speed Up",
        "Speed Up fast",
        "Speed Down",
        "Speed Down slow",
        "Volume+",
        "Volume+ 10",
        "Volume+ test",
        "Volume-",
        "Volume- 10",
        "Volume- test",
        "Front",
        "Front abc",
        "Front test",
        "Back",
        "Back abc",
        "Back test",
        "Stop Play",
        "Stop Play now",
        "stopmusic",
        "stopmusic now",
        "Pause Play",
        "Pause Play x",
        "Resume Play",
        "Resume Play x",
        "Mute Play",
        "Mute Play x",
        "UnMute Play",
        "UnMute Play x",
        "Set Volume abc",
        "Start Call",
        "End Call",
        "End Call now",
        "پایان کال",
        "پایان کال الان",
        "Link Call",
        "Link Call x",
    ],
)
def test_auto_play_help_commands_do_not_route_as_generic_play(raw: str):
    from app.utils.playback_commands import parse_playback_command

    assert parse_playback_command(normalize_command_text(_msg(raw))) is None


@pytest.mark.parametrize(
    "raw",
    [
        "پخش ویدیو https://example.com/v.mp4",
        "playvideo https://example.com/v.mp4",
        "پخش تیوی",
        "پخش تلویزیون",
        "playtv",
        "توقف پخش",
        "توقف پخش الان",
        "مکث پلیر",
        "مکث پلیر x",
        "ازسرگیری",
        "ازسرگیری x",
        "پخش بیصدا",
        "پخش بیصدا x",
        "پخش باصدا",
        "پخش باصدا x",
        "پخش رادیو",
        "پخش رادیو جوان",
        "پخش ماهواره",
        "پخش ماهواره خبر",
        "پخش سریال",
        "پخش سریال تست",
        "کاهش سرعت",
        "کاهش سرعت سریع",
        "افزایش سرعت",
        "افزایش سرعت سریع",
        "کاهش صدا",
        "کاهش صدا 10",
        "کاهش صدا تست",
        "افزایش صدا",
        "افزایش صدا 10",
        "افزایش صدا تست",
        "جلو",
        "جلو abc",
        "جلو تست",
        "عقب",
        "عقب abc",
        "عقب تست",
        "تنظیم صدا abc",
        "شروع کال",
        "پایان کال الان",
        "لینک کال",
        "لینک کال x",
    ],
)
def test_specific_or_later_phase_play_prefixes_do_not_route_as_generic_play(raw: str):
    from app.utils.playback_commands import parse_playback_command

    assert parse_playback_command(normalize_command_text(_msg(raw))) is None

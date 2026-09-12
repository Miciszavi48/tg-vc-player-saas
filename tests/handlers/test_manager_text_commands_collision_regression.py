from __future__ import annotations

import pytest

from app.handlers import credit_commands
from app.utils.group_text_commands import parse_group_text_command
from app.utils.manager_text_commands import parse_manager_text_command
from app.utils.playback_commands import parse_playback_command
from app.utils.text_commands import is_escape_command, normalize_command_text, normalize_digits


@pytest.mark.parametrize(
    "raw",
    [
        "شروع کال",
        "عنوان کال تست",
        "آمار کال",
    ],
)
def test_manager_parser_ignores_group_call_commands_while_group_parser_matches(raw: str):
    assert parse_manager_text_command(raw) is None
    assert parse_group_text_command(raw) is not None


def test_manager_parser_ignores_existing_update_charge_command():
    raw = "آپدیت شارژ ۳۰"
    normalized = normalize_digits(normalize_command_text(raw))

    assert parse_manager_text_command(raw) is None
    assert credit_commands._CHARGE_PATTERN.fullmatch(normalized) is not None


@pytest.mark.parametrize("raw", ["پخش", "پخش آهنگ", "play test"])
def test_manager_parser_ignores_playback_commands_while_playback_parser_matches(raw: str):
    assert parse_manager_text_command(raw) is None
    assert parse_playback_command(normalize_command_text(raw)) is not None


@pytest.mark.parametrize("raw", ["/cancel", "cancel", "لغو"])
def test_manager_parser_ignores_escape_commands(raw: str):
    assert parse_manager_text_command(raw) is None
    assert is_escape_command(raw) is True


@pytest.mark.parametrize(
    "raw",
    [
        "سلام بچه‌ها",
        "یک متن عادی درباره شارژ موزیک",
        "AddMusic لطفا",
        "/AddMusic",
    ],
)
def test_manager_parser_ignores_normal_chat_and_unanchored_manager_words(raw: str):
    assert parse_manager_text_command(raw) is None

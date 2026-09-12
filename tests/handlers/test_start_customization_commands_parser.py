from __future__ import annotations

import pytest

from app.utils.manager_text_commands import parse_manager_text_command
from app.utils.start_customization_commands import parse_start_customization_command


@pytest.mark.parametrize(
    ("raw", "action", "category"),
    [
        ("addstartMsg", "add", "start"),
        ("cleanstartMsg", "clean", "start"),
        ("addabilityMsg", "add", "ability"),
        ("cleanabilityMsg", "clean", "ability"),
        ("addtestMsg", "add", "test"),
        ("cleantestMsg", "clean", "test"),
        ("adduseMsg", "add", "use"),
        ("cleanuseMsg", "clean", "use"),
        ("addhistoryMsg", "add", "history"),
        ("cleanhistoryMsg", "clean", "history"),
        ("addnoteMsg", "add", "note"),
        ("cleannoteMsg", "clean", "note"),
        ("افزودن پیام استارت", "add", "start"),
        ("پاکسازی پیام استارت", "clean", "start"),
        ("افزودن پیام امکانات", "add", "ability"),
        ("پاکسازی پیام امکانات", "clean", "ability"),
        ("افزودن پیام تست", "add", "test"),
        ("پاکسازی پیام تست", "clean", "test"),
        ("افزودن پیام استفاده", "add", "use"),
        ("پاکسازی پیام استفاده", "clean", "use"),
        ("افزودن پیام تاریخچه", "add", "history"),
        ("پاکسازی پیام تاریخچه", "clean", "history"),
        ("افزودن پیام نکات", "add", "note"),
        ("پاکسازی پیام نکات", "clean", "note"),
    ],
)
def test_message_command_aliases(raw: str, action: str, category: str) -> None:
    parsed = parse_start_customization_command(raw)

    assert parsed is not None
    assert parsed.action == action
    assert parsed.target == "message"
    assert parsed.category == category


@pytest.mark.parametrize(
    ("raw", "action", "part"),
    [
        ("addstart keycolor", "add", "color"),
        ("addstart key color", "add", "color"),
        ("cleanstart keycolor", "clean", "color"),
        ("cleanstart key color", "clean", "color"),
        ("addstart keyemoji", "add", "emoji"),
        ("addstart key emoji", "add", "emoji"),
        ("cleanstart keyemoji", "clean", "emoji"),
        ("cleanstart key emoji", "clean", "emoji"),
        ("addstart keytext", "add", "text"),
        ("addstart key text", "add", "text"),
        ("cleanstart keytext", "clean", "text"),
        ("cleanstart key text", "clean", "text"),
        ("افزودن رنگ دکمه استارت", "add", "color"),
        ("پاکسازی رنگ دکمه استارت", "clean", "color"),
        ("افزودن ایموجی دکمه استارت", "add", "emoji"),
        ("پاکسازی ایموجی دکمه استارت", "clean", "emoji"),
        ("افزودن متن دکمه استارت", "add", "text"),
        ("پاکسازی متن دکمه استارت", "clean", "text"),
    ],
)
def test_button_command_aliases(raw: str, action: str, part: str) -> None:
    parsed = parse_start_customization_command(raw)

    assert parsed is not None
    assert parsed.action == action
    assert parsed.target == "button"
    assert parsed.button_part == part


def test_parser_normalizes_case_whitespace_and_zero_width() -> None:
    parsed = parse_start_customization_command("  ADDSTART\u200c   KEY COLOR  ")

    assert parsed is not None
    assert parsed.action == "add"
    assert parsed.button_part == "color"


@pytest.mark.parametrize(
    "raw",
    [
        "متن عادی",
        "AddMusic",
        "ChargeMusic +10",
        "StartCall",
        "/cancel",
        "addstart keycolor لطفا",
        "/addstartMsg",
    ],
)
def test_parser_ignores_unrelated_and_unanchored_text(raw: str) -> None:
    assert parse_start_customization_command(raw) is None


def test_start_customization_parser_does_not_collide_with_manager_parser() -> None:
    assert parse_start_customization_command("AddMusic") is None
    assert parse_manager_text_command("AddMusic") is not None
    assert parse_start_customization_command("addstartMsg") is not None
    assert parse_manager_text_command("addstartMsg") is None

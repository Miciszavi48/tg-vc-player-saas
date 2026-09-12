"""Parser for reply-based start customization commands."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pyrogram import filters

from app.utils.manager_text_commands import normalize_manager_text


@dataclass(frozen=True)
class StartCustomizationCommand:
    action: str
    target: str
    raw_text: str
    normalized_text: str
    category: str | None = None
    button_part: str | None = None


_MESSAGE_ALIASES: dict[tuple[str, str], tuple[str, ...]] = {
    ("add", "start"): ("addstartmsg", "افزودن پیام استارت"),
    ("clean", "start"): ("cleanstartmsg", "پاکسازی پیام استارت"),
    ("add", "ability"): ("addabilitymsg", "افزودن پیام امکانات"),
    ("clean", "ability"): ("cleanabilitymsg", "پاکسازی پیام امکانات"),
    ("add", "test"): ("addtestmsg", "افزودن پیام تست"),
    ("clean", "test"): ("cleantestmsg", "پاکسازی پیام تست"),
    ("add", "use"): ("addusemsg", "افزودن پیام استفاده"),
    ("clean", "use"): ("cleanusemsg", "پاکسازی پیام استفاده"),
    ("add", "history"): ("addhistorymsg", "افزودن پیام تاریخچه"),
    ("clean", "history"): ("cleanhistorymsg", "پاکسازی پیام تاریخچه"),
    ("add", "note"): ("addnotemsg", "افزودن پیام نکات"),
    ("clean", "note"): ("cleannotemsg", "پاکسازی پیام نکات"),
}

_BUTTON_ALIASES: dict[tuple[str, str], tuple[str, ...]] = {
    ("add", "color"): (
        "addstart keycolor",
        "addstart key color",
        "افزودن رنگ دکمه استارت",
    ),
    ("clean", "color"): (
        "cleanstart keycolor",
        "cleanstart key color",
        "پاکسازی رنگ دکمه استارت",
    ),
    ("add", "emoji"): (
        "addstart keyemoji",
        "addstart key emoji",
        "افزودن ایموجی دکمه استارت",
    ),
    ("clean", "emoji"): (
        "cleanstart keyemoji",
        "cleanstart key emoji",
        "پاکسازی ایموجی دکمه استارت",
    ),
    ("add", "text"): (
        "addstart keytext",
        "addstart key text",
        "افزودن متن دکمه استارت",
    ),
    ("clean", "text"): (
        "cleanstart keytext",
        "cleanstart key text",
        "پاکسازی متن دکمه استارت",
    ),
}


def _raw_text(text_or_message: Any | None) -> str:
    if isinstance(text_or_message, str):
        return text_or_message
    return str(
        getattr(text_or_message, "text", None)
        or getattr(text_or_message, "caption", None)
        or ""
    )


def _normalized_aliases(aliases: tuple[str, ...]) -> set[str]:
    return {normalize_manager_text(alias).lower() for alias in aliases}


def parse_start_customization_command(
    text_or_message: Any | None,
) -> StartCustomizationCommand | None:
    raw = _raw_text(text_or_message)
    normalized = normalize_manager_text(text_or_message)
    if not normalized:
        return None
    lowered = normalized.lower()

    for (action, category), aliases in _MESSAGE_ALIASES.items():
        if lowered in _normalized_aliases(aliases):
            return StartCustomizationCommand(
                action=action,
                target="message",
                category=category,
                raw_text=raw,
                normalized_text=normalized,
            )

    for (action, part), aliases in _BUTTON_ALIASES.items():
        if lowered in _normalized_aliases(aliases):
            return StartCustomizationCommand(
                action=action,
                target="button",
                button_part=part,
                raw_text=raw,
                normalized_text=normalized,
            )

    return None


def start_customization_command_filter():
    """Pyrogram filter for reply-based start customization commands."""

    async def func(_flt, _client, message) -> bool:
        return parse_start_customization_command(message) is not None

    return filters.create(func, name="StartCustomizationCommandFilter")

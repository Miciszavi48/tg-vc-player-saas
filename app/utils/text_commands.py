"""Shared normalization and escape detection for text commands."""

from __future__ import annotations

import re
import unicodedata

from pyrogram import filters
from pyrogram.types import Message

from app.utils.playback_commands import normalize_playback_text

_PERSIAN_DIGIT_MAP = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")

_ESCAPE_COMMANDS = frozenset({
    "/cancel",
    "cancel",
    "لغو",
    "/help",
    "help",
    "راهنما",
    "کمک",
    "/panel",
    "پنل",
    "/start",
    "start",
})

_HELP_ALIASES = frozenset({"/help", "help", "راهنما", "کمک"})


def normalize_command_text(message: Message | str | None) -> str:
    """Normalize message text/caption for anchored command matching."""
    return normalize_playback_text(message)


def normalize_digits(text: str) -> str:
    """Map Persian/Arabic-Indic digits to ASCII ``0-9``."""
    return (text or "").translate(_PERSIAN_DIGIT_MAP)


def is_escape_command(text: str | None) -> bool:
    """Return True when *text* is a global escape (help/panel/cancel/start)."""
    normalized = normalize_playback_text(text or "").strip().lower()
    if not normalized:
        return False
    if normalized in _ESCAPE_COMMANDS:
        return True
    first = normalized.split(maxsplit=1)[0]
    return first in _ESCAPE_COMMANDS


def is_help_command(text: str | None) -> bool:
    """Return True for help text aliases (normalized, anchored)."""
    normalized = normalize_playback_text(text or "").strip()
    if not normalized:
        return False
    return normalized.lower() in _HELP_ALIASES


def help_command_filter():
    """Pyrogram filter: normalized help aliases at start of message."""

    async def func(_flt, _client, message: Message) -> bool:
        return is_help_command(message.text or message.caption)

    return filters.create(func, name="HelpCommandFilter")


def anchored_regex_filter(pattern: re.Pattern[str]):
    """Match *pattern* against normalized command text (full string)."""

    async def func(_flt, _client, message: Message) -> bool:
        text = normalize_command_text(message)
        if not text:
            return False
        return pattern.fullmatch(text) is not None

    return filters.create(func, name=f"AnchoredRegex:{pattern.pattern[:32]}")

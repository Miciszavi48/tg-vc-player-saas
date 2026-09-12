"""Parser for slash-free group call text commands."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import re
import unicodedata
from typing import Any

from pyrogram import filters

_PERSIAN_DIGIT_MAP = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
_ZERO_WIDTH_CHARS = "\u200b\u200c\u200d\ufeff"


class GroupTextCommandType(str, Enum):
    GET_PANEL = "get_panel"
    END_CALL = "end_call"
    START_CALL = "start_call"
    MUTE_CALL = "mute_call"
    UNMUTE_CALL = "unmute_call"
    INVITE_CALL = "invite_call"
    AUTO_CALL_STATS = "auto_call_stats"
    CALL_STATS_PANEL = "call_stats_panel"
    CALL_MUTE = "call_mute"
    CALL_COMMENT = "call_comment"
    SET_TITLE = "set_title"
    GET_CALL_LINK = "get_call_link"
    REPEAT = "repeat"
    AUTO_CLEAR = "auto_clear"
    RESET_CALL_STATS = "reset_call_stats"
    EQUALIZER = "equalizer"
    CALL_REPORT = "call_report"
    SET_CHANNEL = "set_channel"
    CHANNEL_PLAYBACK = "channel_playback"


@dataclass(frozen=True)
class ParsedGroupTextCommand:
    command: GroupTextCommandType
    raw_text: str
    normalized_text: str
    minutes: int | None = None
    target_text: str | None = None
    invite_scope: str | None = None
    day: str | None = None
    mode: bool | None = None
    title: str | None = None
    cadence: str | None = None
    target_chat_id: int | None = None
    error: str | None = None


def normalize_group_text(text_or_message: Any | None) -> str:
    """Normalize user text for exact command matching."""
    if text_or_message is None:
        raw = ""
    elif isinstance(text_or_message, str):
        raw = text_or_message
    else:
        raw = getattr(text_or_message, "text", None) or getattr(
            text_or_message, "caption", None
        ) or ""
    text = unicodedata.normalize("NFKC", str(raw))
    text = text.translate(_PERSIAN_DIGIT_MAP)
    text = text.replace("ي", "ی").replace("ك", "ک")
    text = text.replace("\r", " ").replace("\n", " ").replace("\t", " ")
    text = re.sub(f"[{re.escape(_ZERO_WIDTH_CHARS)}]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _alias_map(aliases: list[str]) -> list[tuple[str, str]]:
    normalized = [(normalize_group_text(alias), normalize_group_text(alias).lower()) for alias in aliases]
    return sorted(normalized, key=lambda item: len(item[0]), reverse=True)


def _after_alias(text: str, aliases: list[str]) -> str | None:
    lowered = text.lower()
    for alias, alias_lower in _alias_map(aliases):
        if lowered == alias_lower:
            return ""
        if lowered.startswith(alias_lower + " "):
            return text[len(alias):].strip()
    return None


def _exact(text: str, aliases: list[str]) -> bool:
    lowered = text.lower()
    return any(lowered == alias_lower for _alias, alias_lower in _alias_map(aliases))


def _bool_mode(value: str) -> tuple[bool | None, str | None]:
    lowered = value.strip().lower()
    active = {
        normalize_group_text("فعال").lower(),
        "active",
        "on",
        "enable",
        "enabled",
    }
    inactive = {
        normalize_group_text("غیرفعال").lower(),
        normalize_group_text("غیر فعال").lower(),
        "inactive",
        "off",
        "disable",
        "disabled",
    }
    if lowered in active:
        return True, None
    if lowered in inactive:
        return False, None
    if not lowered:
        return None, "missing_mode"
    return None, "invalid_mode"


_DAYS: dict[str, str] = {
    "saturday": "saturday",
    "شنبه": "saturday",
    "sunday": "sunday",
    "یکشنبه": "sunday",
    "یک شنبه": "sunday",
    "monday": "monday",
    "دوشنبه": "monday",
    "دو شنبه": "monday",
    "tuesday": "tuesday",
    "سه شنبه": "tuesday",
    "wednesday": "wednesday",
    "چهارشنبه": "wednesday",
    "چهار شنبه": "wednesday",
    "thursday": "thursday",
    "پنجشنبه": "thursday",
    "پنج شنبه": "thursday",
    "friday": "friday",
    "جمعه": "friday",
}

_GET_PANEL_ALIASES = ["دریافت پنل", "GetPanel"]
_END_CALL_ALIASES = ["پایان کال", "بستن کال", "EndCall", "End Call", "DiscardCall"]
_START_CALL_ALIASES = ["شروع ویس چت", "شروع کال", "StartCall", "Start Call"]
_MUTE_CALL_ALIASES = ["بیصدا کال", "MuteCall"]
_UNMUTE_CALL_ALIASES = ["حذف بیصدا کال", "UnmuteCall"]
_INVITE_CALL_ALIASES = ["دعوت کال", "InviteCall"]
_AUTO_STATS_ALIASES = ["آمار خودکار کال", "AutoCallStatis"]
_CALL_STATS_PANEL_ALIASES = [
    "امار کال",
    "آمار کال",
    "Call Stats",
    "Voice Call Stats",
    "CallStatis",
]
_CALL_MUTE_ALIASES = ["سکوت کال", "CallMute"]
_CALL_COMMENT_ALIASES = [
    "کامنت کال",
    "پیام کال",
    "مسیج کال",
    "CallComment",
    "Call comment",
    "Call message",
]
_REPEAT_ALIASES = ["تکرار", "Repeat", "Repaet"]
_AUTO_CLEAR_ALIASES = ["پاکسازی خودکار", "Clearauto", "Clear auto"]
_RESET_CALL_STATS_ALIASES = [
    "ریست آمار کال",
    "ریست امار کال",
    "Reset stats call",
    "ResetStatsCall",
]
_EQUALIZER_ALIASES = ["اکولایزر", "Equalizer"]
# CALLSEC-01: owner-DM call report. Must be matched before the shorter
# "آمار کال" family so "گزارش کال فعال" is never mis-parsed.
_CALL_REPORT_ALIASES = ["گزارش کال", "Call report", "CallReport"]
# CALLMGMT-01/03. "پخش کانال" must be matched before the generic "پخش <query>"
# playback parser, which is why it is also listed in
# app/utils/playback_commands.py::_NON_GENERIC_PLAY_COMMAND_PREFIXES.
_SET_CHANNEL_ALIASES = ["تنظیم کانال پلیر", "تنظیم کانال", "SetChannel", "Set Channel"]
_CHANNEL_PLAYBACK_ALIASES = ["پخش کانال", "Play channel", "PlayChannel"]

_CADENCES: dict[str, str] = {
    normalize_group_text("روزانه").lower(): "daily",
    "daily": "daily",
    normalize_group_text("ماهیانه").lower(): "monthly",
    normalize_group_text("ماهانه").lower(): "monthly",
    "monthly": "monthly",
}


def _parse_cadence(value: str) -> tuple[str | None, str | None]:
    lowered = value.strip().lower()
    if not lowered:
        return None, "missing_cadence"
    cadence = _CADENCES.get(lowered)
    if cadence is None:
        return None, "invalid_cadence"
    return cadence, None


def _parse_chat_id_token(value: str) -> int | None:
    """Parse a Telegram channel id such as ``-1001234567890``."""
    token = value.strip().split()[0] if value.strip() else ""
    if not re.fullmatch(r"-?\d{5,20}", token):
        return None
    return int(token)
_SET_TITLE_ALIASES = [
    "تنظیم عنوان کال",
    "تنظیم تایتل",
    "عنوان کال",
    "SetTitleCall",
    "SetTitle",
]
_GET_LINK_ALIASES = ["دریافت لینک کال", "لینک کال", "GetCallLink", "Link Call"]

_SPECIAL_INVITE_SCOPES: dict[str, str] = {
    normalize_group_text("مدیران").lower(): "admins",
    "admins": "admins",
    normalize_group_text("اخیر").lower(): "recent",
    "recent": "recent",
    normalize_group_text("ویژه").lower(): "special",
    "special": "special",
}


def _parse_minutes(value: str) -> tuple[int | None, str | None]:
    if not value:
        return None, "missing_duration"
    if not re.fullmatch(r"-?\d+", value):
        return None, "invalid_duration"
    minutes = int(value)
    if minutes <= 0:
        return minutes, "invalid_duration"
    if minutes > 1440:
        return minutes, "duration_too_large"
    return minutes, None


def parse_group_text_command(text_or_message: Any | None) -> ParsedGroupTextCommand | None:
    """Return a structured group text command, or None for unrelated text."""
    raw = (
        text_or_message
        if isinstance(text_or_message, str)
        else (getattr(text_or_message, "text", None) or getattr(text_or_message, "caption", None) or "")
    )
    text = normalize_group_text(text_or_message)
    if not text:
        return None

    if _exact(text, _GET_PANEL_ALIASES):
        return ParsedGroupTextCommand(GroupTextCommandType.GET_PANEL, str(raw), text)

    rest = _after_alias(text, _END_CALL_ALIASES)
    if rest is not None:
        minutes, error = (None, None) if not rest else _parse_minutes(rest)
        return ParsedGroupTextCommand(
            GroupTextCommandType.END_CALL,
            str(raw),
            text,
            minutes=minutes,
            error=error,
        )

    if _exact(text, _START_CALL_ALIASES):
        return ParsedGroupTextCommand(GroupTextCommandType.START_CALL, str(raw), text)

    rest = _after_alias(text, _UNMUTE_CALL_ALIASES)
    if rest is not None:
        return ParsedGroupTextCommand(
            GroupTextCommandType.UNMUTE_CALL,
            str(raw),
            text,
            target_text=rest or None,
        )

    rest = _after_alias(text, _MUTE_CALL_ALIASES)
    if rest is not None:
        return ParsedGroupTextCommand(
            GroupTextCommandType.MUTE_CALL,
            str(raw),
            text,
            target_text=rest or None,
        )

    rest = _after_alias(text, _INVITE_CALL_ALIASES)
    if rest is not None:
        lowered = rest.lower()
        scope = _SPECIAL_INVITE_SCOPES.get(lowered)
        return ParsedGroupTextCommand(
            GroupTextCommandType.INVITE_CALL,
            str(raw),
            text,
            target_text=None if scope else (rest or None),
            invite_scope=scope or ("user" if rest else None),
            error=None if rest else "missing_target",
        )

    # Must run before the shorter _AUTO_STATS_ALIASES / _CALL_STATS_PANEL_ALIASES
    # checks so "ریست آمار کال روزانه" is not mistaken for a stats command.
    rest = _after_alias(text, _RESET_CALL_STATS_ALIASES)
    if rest is not None:
        cadence, error = _parse_cadence(rest)
        return ParsedGroupTextCommand(
            GroupTextCommandType.RESET_CALL_STATS,
            str(raw),
            text,
            cadence=cadence,
            error=error,
        )

    rest = _after_alias(text, _CALL_REPORT_ALIASES)
    if rest is not None:
        mode, error = _bool_mode(rest)
        return ParsedGroupTextCommand(
            GroupTextCommandType.CALL_REPORT,
            str(raw),
            text,
            mode=mode,
            error=error,
        )

    rest = _after_alias(text, _REPEAT_ALIASES)
    if rest is not None:
        mode, error = _bool_mode(rest)
        return ParsedGroupTextCommand(
            GroupTextCommandType.REPEAT,
            str(raw),
            text,
            mode=mode,
            error=error,
        )

    rest = _after_alias(text, _AUTO_CLEAR_ALIASES)
    if rest is not None:
        mode, error = _bool_mode(rest)
        return ParsedGroupTextCommand(
            GroupTextCommandType.AUTO_CLEAR,
            str(raw),
            text,
            mode=mode,
            error=error,
        )

    if _exact(text, _EQUALIZER_ALIASES):
        return ParsedGroupTextCommand(GroupTextCommandType.EQUALIZER, str(raw), text)

    # "پخش کانال فعال" must win over the "تنظیم کانال" prefix check below and
    # over the generic play parser.
    rest = _after_alias(text, _CHANNEL_PLAYBACK_ALIASES)
    if rest is not None:
        mode, error = _bool_mode(rest)
        return ParsedGroupTextCommand(
            GroupTextCommandType.CHANNEL_PLAYBACK,
            str(raw),
            text,
            mode=mode,
            error=error,
        )

    rest = _after_alias(text, _SET_CHANNEL_ALIASES)
    if rest is not None:
        target = _parse_chat_id_token(rest)
        return ParsedGroupTextCommand(
            GroupTextCommandType.SET_CHANNEL,
            str(raw),
            text,
            target_text=rest or None,
            target_chat_id=target,
            error=None if target is not None else (
                "missing_channel" if not rest else "invalid_channel"
            ),
        )

    rest = _after_alias(text, _AUTO_STATS_ALIASES)
    if rest is not None:
        mode, error = _bool_mode(rest)
        return ParsedGroupTextCommand(
            GroupTextCommandType.AUTO_CALL_STATS,
            str(raw),
            text,
            mode=mode,
            error=error,
        )

    if _exact(text, _CALL_STATS_PANEL_ALIASES):
        return ParsedGroupTextCommand(
            GroupTextCommandType.CALL_STATS_PANEL,
            str(raw),
            text,
        )

    rest = _after_alias(text, _CALL_MUTE_ALIASES)
    if rest is not None:
        mode, error = _bool_mode(rest)
        return ParsedGroupTextCommand(
            GroupTextCommandType.CALL_MUTE,
            str(raw),
            text,
            mode=mode,
            error=error,
        )

    rest = _after_alias(text, _CALL_COMMENT_ALIASES)
    if rest is not None:
        mode, error = _bool_mode(rest)
        return ParsedGroupTextCommand(
            GroupTextCommandType.CALL_COMMENT,
            str(raw),
            text,
            mode=mode,
            error=error,
        )

    rest = _after_alias(text, _SET_TITLE_ALIASES)
    if rest is not None:
        return ParsedGroupTextCommand(
            GroupTextCommandType.SET_TITLE,
            str(raw),
            text,
            title=rest or None,
            error=None if rest else "missing_title",
        )

    if _exact(text, _GET_LINK_ALIASES):
        return ParsedGroupTextCommand(GroupTextCommandType.GET_CALL_LINK, str(raw), text)

    return None


def group_text_command_filter():
    """Pyrogram filter for the slash-free group call text command surface."""

    async def func(_flt, _client, message) -> bool:
        return parse_group_text_command(message) is not None

    return filters.create(func, name="GroupTextCallCommandFilter")

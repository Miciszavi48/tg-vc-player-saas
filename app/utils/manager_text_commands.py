"""Parser for slash-free manager and group management text commands."""

from __future__ import annotations

from dataclasses import dataclass
import re
import unicodedata
from typing import Any

from pyrogram import filters

_PERSIAN_DIGIT_MAP = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
_ZERO_WIDTH_CHARS = "\u200b\u200c\u200d\ufeff"


@dataclass(frozen=True)
class ManagerTextCommand:
    family: str
    scope: str
    raw_text: str
    normalized_text: str
    amount: int | None = None
    charge_mode: str | None = None
    target_chat_id: int | None = None
    target_user: str | None = None
    duration: str | None = None
    error: str | None = None


def normalize_manager_text(text_or_message: Any | None) -> str:
    """Normalize user text for exact manager-command matching."""
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


def _raw_text(text_or_message: Any | None) -> str:
    if isinstance(text_or_message, str):
        return text_or_message
    return str(
        getattr(text_or_message, "text", None)
        or getattr(text_or_message, "caption", None)
        or ""
    )


def _alias_map(aliases: list[str]) -> list[tuple[str, str]]:
    normalized = [
        (normalize_manager_text(alias), normalize_manager_text(alias).lower())
        for alias in aliases
    ]
    return sorted(normalized, key=lambda item: len(item[0]), reverse=True)


def _exact(text: str, aliases: list[str]) -> bool:
    lowered = text.lower()
    return any(lowered == alias_lower for _alias, alias_lower in _alias_map(aliases))


def _after_alias(text: str, aliases: list[str]) -> str | None:
    lowered = text.lower()
    for alias, alias_lower in _alias_map(aliases):
        if lowered == alias_lower:
            return ""
        if lowered.startswith(alias_lower + " "):
            return text[len(alias):].strip()
    return None


def _parse_chat_id(value: str) -> int | None:
    token = value.strip()
    if re.fullmatch(r"\d+-", token):
        return -int(token[:-1])
    if re.fullmatch(r"-?\d+", token):
        return int(token)
    return None


def _parse_amount(value: str) -> tuple[str | None, int | None, str | None]:
    token = value.strip()
    if not token:
        return None, None, "missing_amount"
    if token.lower() in {"unlimit", "unlimited", "نامحدود"}:
        return "unlimited", None, None
    if re.fullmatch(r"\+\d+", token):
        return "increase", int(token[1:]), None
    if re.fullmatch(r"-\d+", token):
        return "decrease", int(token[1:]), None
    if re.fullmatch(r"\d+\+", token):
        return "increase", int(token[:-1]), None
    if re.fullmatch(r"\d+-", token):
        return "decrease", int(token[:-1]), None
    if re.fullmatch(r"\d+", token):
        return "increase", int(token), None
    return None, None, "invalid_amount"


_INSTALL_ALIASES = ["افزودن موزیک", "نصب موزیک", "نصب پلیر", "Addm", "AddMusic"]
_UNINSTALL_ALIASES = [
    "حذف نصب موزیک",
    "حذف نصب پلیر",
    "حذف موزیک",
    "RemM",
    "RemMusic",
    "RemPlayer",
]
_LEAVE_ALIASES = [
    "خروج موزیک",
    "ترک گروه موزیک",
    "LeaveM",
    "LeaveMusic",
    "LeavePlayer",
]
_CHARGE_ALIASES = ["شارژ موزیک", "شارژ پلیر", "ChargeMusic", "ChargePlayer", "ChargeM"]
_ADD_HELPER_ALIASES = [
    "افزودن کمکی موزیک",
    "افزودن کمکی پلیر",
    "AddhelperMusic",
    "AddhelperM",
]
_CONFIG_ALIASES = ["پیکربندی پلیر", "پیکربندی موزیک", "ConfigMusic", "ConfigPlayer", "Config Player"]
_EXPIRE_ALIASES = ["اعتبار موزیک", "اعتبار پلیر", "MusicExpire", "ExpirePlayer", "Expire Player"]

_OWNER_ADD_ALIASES = [
    "ارتقا مالک موزیک",
    "ارتقا مالک پلیر",
    "افزودن مالک پلیر",
    "AddOwnerM",
    "SetOwnerMusic",
    "SetOwner Player",
    "SetOwnerPlayer",
]
_OWNER_REMOVE_ALIASES = [
    "عزل مالک موزیک",
    "عزل مالک پلیر",
    "حذف مالک پلیر",
    "RemOwnerMusic",
    "RemOwner Player",
    "RemOwnerPlayer",
    "DemOwnerpPlayer",
    "DemOwnerPlayer",
]
_OWNER_LIST_ALIASES = [
    "لیست مالک پلیر",
    "لیست مالکان پلیر",
    "لیست مالکان موزیک",
    "OwnerListM",
    "OwnerListMusic",
    "OwnerListPlayer",
    "ListOwner Player",
]
_OWNER_CLEAR_ALIASES = [
    "پاکسازی لیست مالک موزیک",
    "پاکسازی لیست مالکان پلیر",
    "ClearOwnerListM",
    "ClearOwnerListMusic",
    "ClearOwnerListPlayer",
]

_DEPUTY_ADD_ALIASES = [
    "ارتقا معاون موزیک",
    "ارتقا معاون پلیر",
    "افزودن معاون پلیر",
    "AddDeputyM",
    "SetDeputyMusic",
    "SetDeputy Player",
]
_DEPUTY_REMOVE_ALIASES = [
    "عزل معاون موزیک",
    "عزل معاون پلیر",
    "حذف معاون پلیر",
    "RemDeputyMusic",
    "RemDeputy Player",
    "DemDeputypPlayer",
    "DemDeputyPlayer",
]
_DEPUTY_LIST_ALIASES = [
    "لیست معاون پلیر",
    "لیست معاونان پلیر",
    "لیست معاونین موزیک",
    "DeputyListM",
    "DeputyListMusic",
    "DeputyListPlayer",
    "ListDeputy Player",
]
_DEPUTY_CLEAR_ALIASES = [
    "پاکسازی لیست معاون موزیک",
    "پاکسازی لیست معاونان پلیر",
    "پاکسازی لیست معاونین پلیر",
    "ClearDeputyListM",
    "ClearDeputyListMusic",
    "ClearDeputyListPlayer",
    "ClearListDeputy Player",
]

_MOD_ADD_ALIASES = [
    "ترفیع موزیک",
    "ترفیع پلیر",
    "ارتقا مقام پلیر",
    "PromoteM",
    "PromoteMusic",
    "PromotePLayer",
    "PromotePlayer",
    "Promote Player",
]
_MOD_REMOVE_ALIASES = [
    "عزل موزیک",
    "عزل مقام پلیر",
    "DemoteM",
    "DemoteMusic",
    "DemotePlayer",
    "Demote Player",
]
_MOD_LIST_ALIASES = [
    "لیست مدیر موزیک",
    "لیست مدیران پلیر",
    "ModListM",
    "ModListMusic",
    "ModListPlayer",
    "ListAdmin Player",
]
_MOD_CLEAR_ALIASES = [
    "پاکسازی لیست مدیران موزیک",
    "پاکسازی لیست مدیران پلیر",
    "ClearModListM",
    "ClearModListPlayer",
    "ClearListAdmin Player",
]
_VIP_ADD_ALIASES = [
    "ترفیع ویژه",
    "ارتقا ویژه پلیر",
    "افزودن ویژه پلیر",
    "SetVip Player",
    "AddVip Player",
    "PromoteVip",
]
_VIP_REMOVE_ALIASES = [
    "عزل ویژه",
    "عزل ویژه پلیر",
    "حذف ویژه پلیر",
    "RemVip Player",
    "DemVip Player",
    "DemoteVip",
]
_VIP_LIST_ALIASES = [
    "لیست ویژه پلیر",
    "لیست ویژه‌های پلیر",
    "ListVip Player",
    "VipListPlayer",
]
_VIP_CLEAR_ALIASES = [
    "پاکسازی لیست ویژه پلیر",
    "ClearListVip Player",
    "ClearVipListPlayer",
]


def _cmd(
    family: str,
    scope: str,
    raw: str,
    text: str,
    **kwargs: object,
) -> ManagerTextCommand:
    return ManagerTextCommand(family, scope, raw, text, **kwargs)


def _parse_charge(raw: str, text: str, rest: str) -> ManagerTextCommand:
    if not rest:
        return _cmd("charge", "both", raw, text, error="missing_amount")
    parts = rest.split()
    if len(parts) == 1:
        mode, amount, error = _parse_amount(parts[0])
        return _cmd(
            "charge",
            "both",
            raw,
            text,
            amount=amount,
            charge_mode=mode,
            error=error,
        )
    if len(parts) == 2:
        target_chat_id = _parse_chat_id(parts[0])
        mode, amount, error = _parse_amount(parts[1])
        if target_chat_id is None:
            return _cmd("charge", "both", raw, text, error="invalid_chat_id")
        return _cmd(
            "charge",
            "both",
            raw,
            text,
            amount=amount,
            charge_mode=mode,
            target_chat_id=target_chat_id,
            error=error,
        )
    return _cmd("charge", "both", raw, text, error="invalid_amount")


_DURATION_UNITS = {
    "d": "d",
    "day": "d",
    "days": "d",
    "روز": "d",
    "h": "h",
    "hour": "h",
    "hours": "h",
    "ساعت": "h",
}
_DURATION_RE = re.compile(r"^(\d{1,5})\s*([A-Za-z؀-ۿ]*)$")


def parse_duration_token(token: str) -> tuple[str | None, bool]:
    """Parse a VIP duration token.

    Returns ``(normalized, explicit_unit)`` where ``normalized`` is ``"<n>d"`` or
    ``"<n>h"``. ``explicit_unit`` is True only when the token carried a unit
    suffix, which is what disambiguates a bare number from a numeric user id.
    """
    match = _DURATION_RE.match(token.strip())
    if match is None:
        return None, False
    value, suffix = match.group(1), match.group(2).lower()
    if not suffix:
        return f"{int(value)}d", False
    unit = _DURATION_UNITS.get(suffix)
    if unit is None:
        return None, False
    return f"{int(value)}{unit}", True


def _split_target_and_duration(rest: str) -> tuple[str | None, str | None]:
    """Split ``<target> [duration]`` for duration-aware role commands."""
    tokens = rest.split()
    if not tokens:
        return None, None
    if len(tokens) == 1:
        duration, explicit = parse_duration_token(tokens[0])
        if duration is not None and explicit:
            return None, duration
        return tokens[0], None
    duration, _explicit = parse_duration_token(tokens[-1])
    if duration is not None:
        return " ".join(tokens[:-1]) or None, duration
    return rest, None


def _parse_target_command(
    raw: str,
    text: str,
    family: str,
    aliases: list[str],
) -> ManagerTextCommand | None:
    rest = _after_alias(text, aliases)
    if rest is None:
        return None
    if family == "vip_add":
        target, duration = _split_target_and_duration(rest)
        return _cmd(family, "group", raw, text, target_user=target, duration=duration)
    return _cmd(family, "group", raw, text, target_user=rest or None)


def parse_manager_text_command(text_or_message: Any | None) -> ManagerTextCommand | None:
    """Return a structured manager text command, or None for unrelated text."""
    raw = _raw_text(text_or_message)
    text = normalize_manager_text(text_or_message)
    if not text:
        return None

    for family, aliases in (
        ("uninstall", _UNINSTALL_ALIASES),
        ("install", _INSTALL_ALIASES),
        ("leave", _LEAVE_ALIASES),
        ("add_helper", _ADD_HELPER_ALIASES),
        ("config", _CONFIG_ALIASES),
        ("expire", _EXPIRE_ALIASES),
        ("owner_list", _OWNER_LIST_ALIASES),
        ("owner_clear", _OWNER_CLEAR_ALIASES),
        ("deputy_list", _DEPUTY_LIST_ALIASES),
        ("deputy_clear", _DEPUTY_CLEAR_ALIASES),
        ("mod_list", _MOD_LIST_ALIASES),
        ("mod_clear", _MOD_CLEAR_ALIASES),
        ("vip_list", _VIP_LIST_ALIASES),
        ("vip_clear", _VIP_CLEAR_ALIASES),
    ):
        if _exact(text, aliases):
            scope = "both" if family == "charge" else "group"
            return _cmd(family, scope, raw, text)

    rest = _after_alias(text, _CHARGE_ALIASES)
    if rest is not None:
        return _parse_charge(raw, text, rest)

    for family, aliases in (
        ("owner_add", _OWNER_ADD_ALIASES),
        ("owner_remove", _OWNER_REMOVE_ALIASES),
        ("deputy_add", _DEPUTY_ADD_ALIASES),
        ("deputy_remove", _DEPUTY_REMOVE_ALIASES),
        ("mod_add", _MOD_ADD_ALIASES),
        ("mod_remove", _MOD_REMOVE_ALIASES),
        ("vip_add", _VIP_ADD_ALIASES),
        ("vip_remove", _VIP_REMOVE_ALIASES),
    ):
        parsed = _parse_target_command(raw, text, family, aliases)
        if parsed is not None:
            return parsed

    return None


def manager_text_command_filter():
    """Pyrogram filter for the slash-free manager/group command surface."""

    async def func(_flt, _client, message) -> bool:
        return parse_manager_text_command(message) is not None

    return filters.create(func, name="ManagerTextCommandFilter")

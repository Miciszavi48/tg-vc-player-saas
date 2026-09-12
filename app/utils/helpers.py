from __future__ import annotations

import re

import jdatetime

MAX_CREDIT_DAYS = 36_500
MAX_RATE_VALUE = 10_000_000_000
MAX_LIMIT_VALUE = 10_000_000
MAX_WALLET_AMOUNT = 1_000_000_000_000


def format_duration(seconds: int) -> str:
    if seconds < 0:
        seconds = 0
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours > 0:
        return f"{hours:d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:d}:{secs:02d}"


def parse_user_id(text: str) -> int | None:
    match = re.search(r"-?\d+", text)
    if match:
        return int(match.group())
    return None


def parse_bounded_int(
    text: str | None,
    *,
    min_value: int,
    max_value: int,
) -> int | None:
    from app.utils.text_commands import normalize_digits

    try:
        value = int(normalize_digits((text or "").strip()))
    except (TypeError, ValueError):
        return None
    if value < min_value or value > max_value:
        return None
    return value


def ensure_bounded_int(
    value: int,
    *,
    field_name: str,
    min_value: int,
    max_value: int,
) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"invalid_{field_name}")
    if value < min_value or value > max_value:
        raise ValueError(f"invalid_{field_name}")
    return value


def mask_phone(phone: str) -> str:
    digits = re.sub(r"\D", "", phone)
    if len(digits) <= 4:
        return "****" + digits
    return "*" * (len(digits) - 4) + digits[-4:]


def persian_date() -> str:
    return jdatetime.datetime.now().strftime("%Y/%m/%d")


def mention_user(user_id: int, name: str) -> str:
    safe_name = name.replace("[", "\\[").replace("]", "\\]")
    return f"[{safe_name}](tg://user?id={user_id})"


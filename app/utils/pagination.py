"""Universal pagination utility for Telegram inline keyboard lists.

Chunks text output to stay under Telegram's 4096-char limit and
generates standard navigation keyboards with Prev/Next buttons.
"""
from __future__ import annotations

from typing import Callable

from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.utils.i18n import t
from app.utils.ui import CB

_MAX_CHARS = 3800
_DEFAULT_PAGE_SIZE = 15


def paginate_text(
    items: list,
    formatter: Callable,
    header: str = "",
    page: int = 0,
    page_size: int = _DEFAULT_PAGE_SIZE,
) -> tuple[str, int]:
    """Format items into a paginated text block.

    Returns (text, total_pages).
    Ensures output stays under Telegram's 4096-char limit.
    """
    if not items:
        return header or "", 1

    total_pages = max(1, (len(items) + page_size - 1) // page_size)
    page = max(0, min(page, total_pages - 1))
    start = page * page_size
    chunk = items[start : start + page_size]

    lines = [header] if header else []
    for item in chunk:
        line = formatter(item)
        if sum(len(ln) for ln in lines) + len(line) + len(lines) > _MAX_CHARS:
            break
        lines.append(line)

    return "\n".join(lines), total_pages


def paginate_keyboard(
    lang: str,
    page: int,
    total_pages: int,
    cb_prefix: str,
    extra_buttons: list[list[InlineKeyboardButton]] | None = None,
) -> InlineKeyboardMarkup:
    """Build an inline keyboard with Prev/Next pagination + optional extra rows."""
    rows = []

    if total_pages > 1:
        nav_row = []
        if page > 0:
            nav_row.append(InlineKeyboardButton(
                t(lang, "common.buttons.prev"), callback_data=f"{cb_prefix}{page - 1}",
            ))
        nav_row.append(InlineKeyboardButton(
            f"{page + 1}/{total_pages}", callback_data=CB["NOOP"],
        ))
        if page < total_pages - 1:
            nav_row.append(InlineKeyboardButton(
                t(lang, "common.buttons.next"), callback_data=f"{cb_prefix}{page + 1}",
            ))
        rows.append(nav_row)

    if extra_buttons:
        rows.extend(extra_buttons)

    rows.append([InlineKeyboardButton(t(lang, "common.buttons.back"), callback_data=CB["NAV_BACK"])])

    return InlineKeyboardMarkup(rows)

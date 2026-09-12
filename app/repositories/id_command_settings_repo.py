"""Per-chat BotSetting storage for the Id command output mode and call-stats toggle."""

from __future__ import annotations

from typing import Literal

from sqlalchemy import select

from app.database.engine import async_session
from app.database.models import BotSetting

KEY_PREFIX = "id_command"
SETTING_OUTPUT_MODE = "output_mode"
SETTING_SHOW_CALL_STATS = "show_call_stats"

_TRUE_VALUES = {"1", "true", "yes", "on", "enabled", "active"}
_VALID_OUTPUT_MODES = frozenset({"simple", "photo", "inactive"})
IdOutputMode = Literal["simple", "photo", "inactive"]


def setting_key(chat_id: int, name: str) -> str:
    """Build a namespaced BotSetting key for one chat."""
    return f"{KEY_PREFIX}:{chat_id}:{name}"


async def _set_value(
    chat_id: int,
    name: str,
    value: str | None,
    *,
    updated_by: int | None = None,
) -> None:
    key = setting_key(chat_id, name)
    async with async_session() as session:
        result = await session.execute(select(BotSetting).where(BotSetting.key == key))
        row = result.scalar_one_or_none()
        if row is None:
            row = BotSetting(key=key, value=value, updated_by=updated_by)
            session.add(row)
        else:
            row.value = value
            if updated_by is not None:
                row.updated_by = updated_by
        await session.commit()


async def _get_value(chat_id: int, name: str) -> str | None:
    key = setting_key(chat_id, name)
    async with async_session() as session:
        result = await session.execute(select(BotSetting.value).where(BotSetting.key == key))
        return result.scalar_one_or_none()


async def get_output_mode(chat_id: int, *, default: IdOutputMode = "simple") -> IdOutputMode:
    """Return the Id command output mode for a chat."""
    value = await _get_value(chat_id, SETTING_OUTPUT_MODE)
    if value is None:
        return default
    normalized = str(value).strip().lower()
    if normalized in _VALID_OUTPUT_MODES:
        return normalized  # type: ignore[return-value]
    return default


async def set_output_mode(
    chat_id: int,
    mode: IdOutputMode,
    *,
    updated_by: int | None = None,
) -> None:
    """Set the Id command output mode for a chat."""
    if mode not in _VALID_OUTPUT_MODES:
        raise ValueError(f"invalid id output mode: {mode}")
    await _set_value(chat_id, SETTING_OUTPUT_MODE, mode, updated_by=updated_by)


async def get_show_call_stats(chat_id: int, *, default: bool = True) -> bool:
    """Return whether voice-call stats appear in Id command output."""
    value = await _get_value(chat_id, SETTING_SHOW_CALL_STATS)
    if value is None:
        return default
    return str(value).strip().lower() in _TRUE_VALUES


async def set_show_call_stats(
    chat_id: int,
    enabled: bool,
    *,
    updated_by: int | None = None,
) -> None:
    """Enable or disable voice-call stats in Id command output."""
    await _set_value(
        chat_id,
        SETTING_SHOW_CALL_STATS,
        "1" if enabled else "0",
        updated_by=updated_by,
    )

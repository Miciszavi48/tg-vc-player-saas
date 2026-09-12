"""Runtime state service for managed chats.

For groups, ``groups.status == "active"`` is the authoritative installation
state. Credit rows are only usable when that installed state is active.
"""

from __future__ import annotations

from app.database.models import GroupCredit
from app.repositories import group_runtime_repo

RuntimeChatState = group_runtime_repo.RuntimeChatState


async def get_group_runtime_state(
    chat_id: int,
    chat_type: str = "group",
) -> RuntimeChatState:
    return await group_runtime_repo.get_runtime_chat_state(chat_id, chat_type)


async def require_active_group(chat_id: int, chat_type: str = "group") -> bool:
    state = await get_group_runtime_state(chat_id, chat_type)
    return state.is_active


async def get_runtime_credit_state(
    chat_id: int,
    chat_type: str = "group",
) -> RuntimeChatState:
    return await get_group_runtime_state(chat_id, chat_type)


async def list_expiring_active_credits(
    hours: int = 24,
    chat_type: str | None = None,
) -> list[GroupCredit]:
    return await group_runtime_repo.list_expiring_active_credits(hours, chat_type)

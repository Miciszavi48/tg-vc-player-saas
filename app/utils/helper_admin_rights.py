"""Shared helpers for helper Telegram admin / call-management rights."""

from __future__ import annotations

from typing import Any

HELPER_CALL_ADMIN_FLAGS = (
    "can_manage_video_chats",
    "can_manage_voice_chats",
    "can_manage_chat",
)

_NON_MEMBER_STATUSES = {"left", "kicked", "banned"}


def member_status_value(member: Any) -> str:
    """Return a normalized Telegram chat-member status string."""
    status = getattr(getattr(member, "status", None), "value", None) or getattr(
        member,
        "status",
        None,
    )
    return str(status or "").lower()


def helper_has_call_admin_rights(member: Any) -> bool:
    """Return True when *member* can manage group voice/video calls."""
    status = member_status_value(member)
    if status in {"creator", "owner"}:
        return True
    if status != "administrator":
        return False
    privileges = getattr(member, "privileges", None) or member
    values = [getattr(privileges, name, None) for name in HELPER_CALL_ADMIN_FLAGS]
    if all(value is None for value in values):
        return True
    return any(bool(value) for value in values)


def member_is_in_group(member: Any) -> bool:
    """Return True when *member* is currently present in the chat."""
    return member_status_value(member) not in _NON_MEMBER_STATUSES

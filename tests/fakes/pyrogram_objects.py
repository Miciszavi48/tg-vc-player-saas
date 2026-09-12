"""Fake Pyrogram objects for handler testing without a live Telegram connection."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from unittest.mock import AsyncMock, MagicMock


@dataclass
class FakeUser:
    id: int = 12345
    username: str | None = "testuser"
    first_name: str = "Test"
    is_bot: bool = False


@dataclass
class FakeChat:
    id: int = -100999
    title: str = "Test Group"
    type: MagicMock = field(default_factory=lambda: MagicMock(value="supergroup"))


@dataclass
class FakeMessage:
    chat: FakeChat = field(default_factory=FakeChat)
    from_user: FakeUser | None = field(default_factory=FakeUser)
    text: str | None = None
    message_id: int = 1
    reply_to_message: Any = None
    audio: Any = None
    video: Any = None
    voice: Any = None
    document: Any = None
    new_chat_members: list = field(default_factory=list)
    left_chat_member: Any = None

    reply: AsyncMock = field(default_factory=AsyncMock)
    reply_text: AsyncMock = field(default_factory=AsyncMock)
    delete: AsyncMock = field(default_factory=AsyncMock)
    stop_propagation: MagicMock = field(default_factory=MagicMock)
    continue_propagation: MagicMock = field(default_factory=MagicMock)


@dataclass
class FakeCallbackQuery:
    id: str = "cb_1"
    from_user: FakeUser = field(default_factory=FakeUser)
    message: FakeMessage = field(default_factory=FakeMessage)
    data: str = ""
    chat_instance: str = "0"

    answer: AsyncMock = field(default_factory=AsyncMock)


@dataclass
class FakeClient:
    """Minimal fake Pyrogram client for handler tests."""
    send_message: AsyncMock = field(default_factory=AsyncMock)
    get_me: AsyncMock = field(default_factory=lambda: AsyncMock(return_value=FakeUser(id=999, is_bot=True)))
    get_chat_member: AsyncMock = field(default_factory=AsyncMock)
    get_chat_members_count: AsyncMock = field(default_factory=lambda: AsyncMock(return_value=100))
    download_media: AsyncMock = field(default_factory=lambda: AsyncMock(return_value="/tmp/fake.mp3"))
    leave_chat: AsyncMock = field(default_factory=AsyncMock)
    ask: AsyncMock = field(default_factory=AsyncMock)

"""Runtime source-of-truth reads for installed chat state and credit."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.engine import async_session
from app.database.models import Channel, ChatSettings, Group, GroupCredit
from app.utils.helpers import ensure_bounded_int

ChatType = Literal["group", "channel"]


@dataclass(frozen=True)
class RuntimeChatState:
    chat_id: int
    chat_type: str
    installed: object | None
    credit: GroupCredit | None
    settings: ChatSettings | None

    @property
    def is_active(self) -> bool:
        return self.installed is not None and getattr(self.installed, "status", None) == "active"

    @property
    def has_credit(self) -> bool:
        return self.credit is not None

    @property
    def credit_status(self) -> str:
        return str(getattr(self.credit, "status", "") or "") if self.credit is not None else "missing"

    @property
    def credit_days(self) -> int:
        return int(getattr(self.credit, "credit_days", 0) or 0) if self.credit is not None else 0

    @property
    def is_unlimited(self) -> bool:
        return self.credit_status == "unlimited"

    @property
    def has_runtime_credit(self) -> bool:
        return self.is_active and self.credit is not None and (
            self.is_unlimited or self.credit_days > 0
        )

    @property
    def reason(self) -> str:
        if not self.is_active:
            return "not_managed"
        if self.credit is None:
            return "credit_missing"
        return "active"


def _install_model(chat_type: str):
    return Channel if chat_type == "channel" else Group


async def get_runtime_chat_state(
    chat_id: int,
    chat_type: str = "group",
    *,
    session: AsyncSession | None = None,
) -> RuntimeChatState:
    chat_type = "channel" if chat_type == "channel" else "group"
    model = _install_model(chat_type)

    async def _load(db: AsyncSession) -> RuntimeChatState:
        installed = (
            await db.execute(select(model).where(model.chat_id == chat_id))
        ).scalar_one_or_none()
        credit = (
            await db.execute(
                select(GroupCredit).where(
                    GroupCredit.chat_id == chat_id,
                    GroupCredit.chat_type == chat_type,
                )
            )
        ).scalar_one_or_none()
        settings = (
            await db.execute(
                select(ChatSettings).where(
                    ChatSettings.chat_id == chat_id,
                    ChatSettings.chat_type == chat_type,
                )
            )
        ).scalar_one_or_none()
        return RuntimeChatState(
            chat_id=chat_id,
            chat_type=chat_type,
            installed=installed,
            credit=credit,
            settings=settings,
        )

    if session is not None:
        return await _load(session)
    async with async_session() as db:
        return await _load(db)


async def get_active_installed_chat(
    chat_id: int,
    chat_type: str = "group",
    *,
    session: AsyncSession | None = None,
) -> object | None:
    state = await get_runtime_chat_state(chat_id, chat_type, session=session)
    return state.installed if state.is_active else None


async def list_expiring_active_credits(
    hours: int,
    chat_type: str | None = None,
) -> list[GroupCredit]:
    hours = ensure_bounded_int(
        hours,
        field_name="hours",
        min_value=1,
        max_value=876_000,
    )
    limit_days = (hours // 24) + 1
    async with async_session() as session:
        if chat_type == "channel":
            stmt = (
                select(GroupCredit)
                .join(
                    Channel,
                    and_(
                        Channel.chat_id == GroupCredit.chat_id,
                        Channel.status == "active",
                    ),
                )
                .where(GroupCredit.chat_type == "channel")
            )
        elif chat_type == "group":
            stmt = (
                select(GroupCredit)
                .join(
                    Group,
                    and_(
                        Group.chat_id == GroupCredit.chat_id,
                        Group.status == "active",
                    ),
                )
                .where(GroupCredit.chat_type == "group")
            )
        else:
            stmt = (
                select(GroupCredit)
                .outerjoin(
                    Group,
                    and_(
                        Group.chat_id == GroupCredit.chat_id,
                        Group.status == "active",
                        GroupCredit.chat_type == "group",
                    ),
                )
                .outerjoin(
                    Channel,
                    and_(
                        Channel.chat_id == GroupCredit.chat_id,
                        Channel.status == "active",
                        GroupCredit.chat_type == "channel",
                    ),
                )
                .where(
                    or_(
                        and_(GroupCredit.chat_type == "group", Group.id.is_not(None)),
                        and_(GroupCredit.chat_type == "channel", Channel.id.is_not(None)),
                    )
                )
            )
        stmt = stmt.where(
            GroupCredit.status == "active",
            GroupCredit.credit_days <= limit_days,
            GroupCredit.credit_days > 0,
        )
        result = await session.execute(stmt)
        return list(result.scalars().all())

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import and_, func, or_, select

from app.database.engine import async_session
from app.database.models import (
    CallSecuritySettings,
    Channel,
    ChatSettings,
    Group,
    GroupCredit,
    Invoice,
    Owner,
    PlaybackState,
    Sudo,
    User,
)


@dataclass(slots=True)
class ChatInstallRow:
    chat_id: int
    chat_type: str
    title: str | None
    invite_link: str | None
    credit_days: int
    expire_at: datetime | None
    status: str | None = None
    installed_by: int | None = None


@dataclass(slots=True)
class UserListRow:
    user_id: int
    username: str | None
    first_name: str | None
    is_banned: bool
    last_seen: datetime | None


def _total_pages(total: int, page_size: int) -> int:
    return max(1, (total + page_size - 1) // page_size)


def _clamp_page(page: int, total_pages: int) -> int:
    return max(0, min(page, total_pages - 1))


def _active_install_predicate():
    return or_(
        and_(GroupCredit.chat_type == "group", Group.status == "active"),
        and_(GroupCredit.chat_type == "channel", Channel.status == "active"),
    )


async def get_groups_page(page: int, page_size: int = 10) -> tuple[list[ChatInstallRow], int]:
    async with async_session() as session:
        total = (
            await session.execute(
                select(func.count()).select_from(Group).where(Group.status == "active")
            )
        ).scalar() or 0
        if total == 0:
            return [], 1

        total_pages = _total_pages(total, page_size)
        page = _clamp_page(page, total_pages)

        stmt = (
            select(
                Group.chat_id,
                Group.chat_title,
                Group.invite_link,
                Group.status,
                GroupCredit.credit_days,
                GroupCredit.expire_at,
            )
            .select_from(Group)
            .outerjoin(
                GroupCredit,
                and_(
                    GroupCredit.chat_id == Group.chat_id,
                    GroupCredit.chat_type == "group",
                ),
            )
            .where(Group.status == "active")
            .order_by(Group.id.desc())
            .offset(page * page_size)
            .limit(page_size)
        )
        result = await session.execute(stmt)
        rows = [
            ChatInstallRow(
                chat_id=r.chat_id,
                chat_type="group",
                title=r.chat_title,
                invite_link=r.invite_link,
                credit_days=int(r.credit_days or 0),
                expire_at=r.expire_at,
                status=r.status,
            )
            for r in result.all()
        ]
        return rows, total_pages


async def get_channels_page(page: int, page_size: int = 10) -> tuple[list[ChatInstallRow], int]:
    async with async_session() as session:
        total = (
            await session.execute(
                select(func.count()).select_from(Channel).where(Channel.status == "active")
            )
        ).scalar() or 0
        if total == 0:
            return [], 1

        total_pages = _total_pages(total, page_size)
        page = _clamp_page(page, total_pages)

        stmt = (
            select(
                Channel.chat_id,
                Channel.chat_title,
                Channel.invite_link,
                Channel.status,
                GroupCredit.credit_days,
                GroupCredit.expire_at,
            )
            .select_from(Channel)
            .outerjoin(
                GroupCredit,
                and_(
                    GroupCredit.chat_id == Channel.chat_id,
                    GroupCredit.chat_type == "channel",
                ),
            )
            .where(Channel.status == "active")
            .order_by(Channel.id.desc())
            .offset(page * page_size)
            .limit(page_size)
        )
        result = await session.execute(stmt)
        rows = [
            ChatInstallRow(
                chat_id=r.chat_id,
                chat_type="channel",
                title=r.chat_title,
                invite_link=r.invite_link,
                credit_days=int(r.credit_days or 0),
                expire_at=r.expire_at,
                status=r.status,
            )
            for r in result.all()
        ]
        return rows, total_pages


async def get_no_credit_page(page: int, page_size: int = 10) -> tuple[list[ChatInstallRow], int]:
    base_stmt = (
        select(
            GroupCredit.id.label("row_id"),
            GroupCredit.chat_id,
            GroupCredit.chat_type,
            GroupCredit.credit_days,
            GroupCredit.expire_at,
            func.coalesce(Group.chat_title, Channel.chat_title).label("chat_title"),
            func.coalesce(Group.invite_link, Channel.invite_link).label("invite_link"),
            Group.status.label("group_status"),
            Channel.status.label("channel_status"),
        )
        .select_from(GroupCredit)
        .outerjoin(
            Group,
            and_(
                GroupCredit.chat_type == "group",
                Group.chat_id == GroupCredit.chat_id,
            ),
        )
        .outerjoin(
            Channel,
            and_(
                GroupCredit.chat_type == "channel",
                Channel.chat_id == GroupCredit.chat_id,
            ),
        )
        .where(
            GroupCredit.chat_type.in_(("group", "channel")),
            GroupCredit.credit_days <= 0,
            _active_install_predicate(),
        )
    )

    async with async_session() as session:
        total = (
            await session.execute(select(func.count()).select_from(base_stmt.subquery()))
        ).scalar() or 0
        if total == 0:
            return [], 1

        total_pages = _total_pages(total, page_size)
        page = _clamp_page(page, total_pages)

        result = await session.execute(
            base_stmt.order_by(GroupCredit.id.desc()).offset(page * page_size).limit(page_size)
        )
        rows = [
            ChatInstallRow(
                chat_id=r.chat_id,
                chat_type=r.chat_type,
                title=r.chat_title,
                invite_link=r.invite_link,
                credit_days=int(r.credit_days or 0),
                expire_at=r.expire_at,
                status=r.group_status if r.chat_type == "group" else r.channel_status,
            )
            for r in result.all()
        ]
        return rows, total_pages


async def get_renewal_page(
    page: int,
    *,
    page_size: int = 10,
    hours: int = 24,
) -> tuple[list[ChatInstallRow], int]:
    threshold_days = max(1, (hours // 24) + 1)
    base_stmt = (
        select(
            GroupCredit.id.label("row_id"),
            GroupCredit.chat_id,
            GroupCredit.chat_type,
            GroupCredit.credit_days,
            GroupCredit.expire_at,
            func.coalesce(Group.chat_title, Channel.chat_title).label("chat_title"),
            func.coalesce(Group.invite_link, Channel.invite_link).label("invite_link"),
            Group.status.label("group_status"),
            Channel.status.label("channel_status"),
        )
        .select_from(GroupCredit)
        .outerjoin(
            Group,
            and_(
                GroupCredit.chat_type == "group",
                Group.chat_id == GroupCredit.chat_id,
            ),
        )
        .outerjoin(
            Channel,
            and_(
                GroupCredit.chat_type == "channel",
                Channel.chat_id == GroupCredit.chat_id,
            ),
        )
        .where(
            GroupCredit.chat_type.in_(("group", "channel")),
            GroupCredit.status == "active",
            GroupCredit.credit_days > 0,
            GroupCredit.credit_days <= threshold_days,
            _active_install_predicate(),
        )
    )

    async with async_session() as session:
        total = (
            await session.execute(select(func.count()).select_from(base_stmt.subquery()))
        ).scalar() or 0
        if total == 0:
            return [], 1

        total_pages = _total_pages(total, page_size)
        page = _clamp_page(page, total_pages)

        result = await session.execute(
            base_stmt.order_by(GroupCredit.credit_days.asc(), GroupCredit.id.desc())
            .offset(page * page_size)
            .limit(page_size)
        )
        rows = [
            ChatInstallRow(
                chat_id=r.chat_id,
                chat_type=r.chat_type,
                title=r.chat_title,
                invite_link=r.invite_link,
                credit_days=int(r.credit_days or 0),
                expire_at=r.expire_at,
                status=r.group_status if r.chat_type == "group" else r.channel_status,
            )
            for r in result.all()
        ]
        return rows, total_pages


def _group_list_stmt():
    """Base select for group-scoped resource lists with credit info attached."""
    return (
        select(
            Group.chat_id,
            Group.chat_title,
            Group.invite_link,
            Group.status,
            GroupCredit.credit_days,
            GroupCredit.expire_at,
        )
        .select_from(Group)
        .outerjoin(
            GroupCredit,
            and_(
                GroupCredit.chat_id == Group.chat_id,
                GroupCredit.chat_type == "group",
            ),
        )
    )


def _group_row(r) -> ChatInstallRow:
    return ChatInstallRow(
        chat_id=r.chat_id,
        chat_type="group",
        title=r.chat_title,
        invite_link=r.invite_link,
        credit_days=int(r.credit_days or 0),
        expire_at=r.expire_at,
        status=r.status,
    )


async def _paged_group_rows(stmt, page: int, page_size: int) -> tuple[list[ChatInstallRow], int]:
    async with async_session() as session:
        total = (
            await session.execute(select(func.count()).select_from(stmt.subquery()))
        ).scalar() or 0
        if total == 0:
            return [], 1

        total_pages = _total_pages(total, page_size)
        page = _clamp_page(page, total_pages)

        result = await session.execute(
            stmt.order_by(Group.id.desc()).offset(page * page_size).limit(page_size)
        )
        return [_group_row(r) for r in result.all()], total_pages


async def get_unlimited_groups_page(
    page: int, page_size: int = 10
) -> tuple[list[ChatInstallRow], int]:
    """Active groups whose credit is set to unlimited (GroupCredit.status == 'unlimited')."""
    stmt = _group_list_stmt().where(
        Group.status == "active",
        GroupCredit.status == "unlimited",
    )
    return await _paged_group_rows(stmt, page, page_size)


async def get_call_security_groups_page(
    page: int, page_size: int = 10
) -> tuple[list[ChatInstallRow], int]:
    """Active groups with Call Security enabled (CallSecuritySettings.enabled)."""
    stmt = (
        _group_list_stmt()
        .join(CallSecuritySettings, CallSecuritySettings.chat_id == Group.chat_id)
        .where(
            Group.status == "active",
            CallSecuritySettings.enabled.is_(True),
        )
    )
    return await _paged_group_rows(stmt, page, page_size)


async def get_playback_groups_page(
    page: int, page_size: int = 10
) -> tuple[list[ChatInstallRow], int]:
    """Active groups with a live playback state row (currently playing)."""
    stmt = (
        _group_list_stmt()
        .join(PlaybackState, PlaybackState.chat_id == Group.chat_id)
        .where(Group.status == "active")
    )
    return await _paged_group_rows(stmt, page, page_size)


async def get_trial_groups_page(
    page: int, page_size: int = 10
) -> tuple[list[ChatInstallRow], int]:
    """Active groups running on an active trial credit (GroupCredit.is_trial)."""
    stmt = _group_list_stmt().where(
        Group.status == "active",
        GroupCredit.is_trial.is_(True),
        GroupCredit.status == "active",
    )
    return await _paged_group_rows(stmt, page, page_size)


async def get_media_mode_groups_page(
    page: int, page_size: int = 10, *, media: str = "music"
) -> tuple[list[ChatInstallRow], int]:
    """Active groups with the music/video capability enabled.

    Both flags default to enabled, so groups without a ChatSettings row count
    as enabled.
    """
    flag = ChatSettings.music_enabled if media == "music" else ChatSettings.video_enabled
    stmt = (
        _group_list_stmt()
        .outerjoin(
            ChatSettings,
            and_(
                ChatSettings.chat_id == Group.chat_id,
                ChatSettings.chat_type == "group",
            ),
        )
        .where(
            Group.status == "active",
            or_(ChatSettings.id.is_(None), flag.is_(True)),
        )
    )
    return await _paged_group_rows(stmt, page, page_size)


async def get_inactive_groups_page(
    page: int, page_size: int = 10
) -> tuple[list[ChatInstallRow], int]:
    """Groups no longer active (left/deactivated)."""
    stmt = _group_list_stmt().where(Group.status != "active")
    return await _paged_group_rows(stmt, page, page_size)


async def get_inactive_channels_page(
    page: int, page_size: int = 10
) -> tuple[list[ChatInstallRow], int]:
    """Channels no longer active (left/deactivated)."""
    stmt = (
        select(
            Channel.chat_id,
            Channel.chat_title,
            Channel.invite_link,
            Channel.status,
            GroupCredit.credit_days,
            GroupCredit.expire_at,
        )
        .select_from(Channel)
        .outerjoin(
            GroupCredit,
            and_(
                GroupCredit.chat_id == Channel.chat_id,
                GroupCredit.chat_type == "channel",
            ),
        )
        .where(Channel.status != "active")
    )
    async with async_session() as session:
        total = (
            await session.execute(select(func.count()).select_from(stmt.subquery()))
        ).scalar() or 0
        if total == 0:
            return [], 1

        total_pages = _total_pages(total, page_size)
        page = _clamp_page(page, total_pages)

        result = await session.execute(
            stmt.order_by(Channel.id.desc()).offset(page * page_size).limit(page_size)
        )
        rows = [
            ChatInstallRow(
                chat_id=r.chat_id,
                chat_type="channel",
                title=r.chat_title,
                invite_link=r.invite_link,
                credit_days=int(r.credit_days or 0),
                expire_at=r.expire_at,
                status=r.status,
            )
            for r in result.all()
        ]
        return rows, total_pages


async def get_users_page(
    page: int, page_size: int = 10, *, banned: bool = False
) -> tuple[list[UserListRow], int, int]:
    """Global bot users filtered by banned state; returns (rows, total_pages, total)."""
    async with async_session() as session:
        total = (
            await session.execute(
                select(func.count()).select_from(User).where(User.is_banned.is_(banned))
            )
        ).scalar() or 0
        if total == 0:
            return [], 1, 0

        total_pages = _total_pages(total, page_size)
        page = _clamp_page(page, total_pages)

        result = await session.execute(
            select(
                User.user_id,
                User.username,
                User.first_name,
                User.is_banned,
                User.last_seen,
            )
            .where(User.is_banned.is_(banned))
            .order_by(User.id.desc())
            .offset(page * page_size)
            .limit(page_size)
        )
        rows = [
            UserListRow(
                user_id=r.user_id,
                username=r.username,
                first_name=r.first_name,
                is_banned=bool(r.is_banned),
                last_seen=r.last_seen,
            )
            for r in result.all()
        ]
        return rows, total_pages, total


async def get_install_row(chat_id: int) -> ChatInstallRow | None:
    async with async_session() as session:
        group_stmt = (
            select(
                Group.chat_id,
                Group.chat_title,
                Group.invite_link,
                Group.status,
                Group.installed_by,
                GroupCredit.credit_days,
                GroupCredit.expire_at,
            )
            .select_from(Group)
            .outerjoin(
                GroupCredit,
                and_(
                    GroupCredit.chat_id == Group.chat_id,
                    GroupCredit.chat_type == "group",
                ),
            )
            .where(Group.chat_id == chat_id)
            .limit(1)
        )
        group_row = (await session.execute(group_stmt)).first()
        if group_row is not None:
            return ChatInstallRow(
                chat_id=group_row.chat_id,
                chat_type="group",
                title=group_row.chat_title,
                invite_link=group_row.invite_link,
                credit_days=int(group_row.credit_days or 0),
                expire_at=group_row.expire_at,
                status=group_row.status,
                installed_by=group_row.installed_by,
            )

        chan_stmt = (
            select(
                Channel.chat_id,
                Channel.chat_title,
                Channel.invite_link,
                Channel.status,
                Channel.installed_by,
                GroupCredit.credit_days,
                GroupCredit.expire_at,
            )
            .select_from(Channel)
            .outerjoin(
                GroupCredit,
                and_(
                    GroupCredit.chat_id == Channel.chat_id,
                    GroupCredit.chat_type == "channel",
                ),
            )
            .where(Channel.chat_id == chat_id)
            .limit(1)
        )
        chan_row = (await session.execute(chan_stmt)).first()
        if chan_row is None:
            return None
        return ChatInstallRow(
            chat_id=chan_row.chat_id,
            chat_type="channel",
            title=chan_row.chat_title,
            invite_link=chan_row.invite_link,
            credit_days=int(chan_row.credit_days or 0),
            expire_at=chan_row.expire_at,
            status=chan_row.status,
            installed_by=chan_row.installed_by,
        )


async def count_active_groups() -> int:
    async with async_session() as session:
        return (
            await session.execute(
                select(func.count()).select_from(Group).where(Group.status == "active")
            )
        ).scalar() or 0


async def count_active_channels() -> int:
    async with async_session() as session:
        return (
            await session.execute(
                select(func.count()).select_from(Channel).where(Channel.status == "active")
            )
        ).scalar() or 0


async def count_no_credit_chats() -> int:
    async with async_session() as session:
        return (
            await session.execute(
                select(func.count())
                .select_from(GroupCredit)
                .outerjoin(
                    Group,
                    and_(
                        GroupCredit.chat_type == "group",
                        Group.chat_id == GroupCredit.chat_id,
                    ),
                )
                .outerjoin(
                    Channel,
                    and_(
                        GroupCredit.chat_type == "channel",
                        Channel.chat_id == GroupCredit.chat_id,
                    ),
                )
                .where(
                    GroupCredit.chat_type.in_(("group", "channel")),
                    GroupCredit.credit_days <= 0,
                    _active_install_predicate(),
                )
            )
        ).scalar() or 0


async def count_renewal_chats(hours: int = 24) -> int:
    threshold_days = max(1, (hours // 24) + 1)
    async with async_session() as session:
        return (
            await session.execute(
                select(func.count())
                .select_from(GroupCredit)
                .outerjoin(
                    Group,
                    and_(
                        GroupCredit.chat_type == "group",
                        Group.chat_id == GroupCredit.chat_id,
                    ),
                )
                .outerjoin(
                    Channel,
                    and_(
                        GroupCredit.chat_type == "channel",
                        Channel.chat_id == GroupCredit.chat_id,
                    ),
                )
                .where(
                    GroupCredit.chat_type.in_(("group", "channel")),
                    GroupCredit.status == "active",
                    GroupCredit.credit_days > 0,
                    GroupCredit.credit_days <= threshold_days,
                    _active_install_predicate(),
                )
            )
        ).scalar() or 0


async def count_invoices_since(since: datetime) -> int:
    async with async_session() as session:
        return (
            await session.execute(
                select(func.count()).select_from(Invoice).where(Invoice.issued_at >= since)
            )
        ).scalar() or 0


async def count_active_owners() -> int:
    async with async_session() as session:
        return (
            await session.execute(
                select(func.count()).select_from(Owner).where(Owner.is_active.is_(True))
            )
        ).scalar() or 0


async def count_active_sudos() -> int:
    async with async_session() as session:
        return (
            await session.execute(
                select(func.count()).select_from(Sudo).where(Sudo.is_active.is_(True))
            )
        ).scalar() or 0

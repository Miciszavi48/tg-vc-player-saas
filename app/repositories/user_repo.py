from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select, update

from app.database.engine import async_session
from app.database.models import Owner, Sudo, User

SUDO_PERMISSION_FIELD_NAMES: tuple[str, ...] = (
    "can_manage_groups",
    "can_manage_channels",
    "can_manage_credit",
    "can_remove_bot",
    "can_manage_chat_settings",
    "auto_admin_bypass",
)

SUDO_PERMISSION_CALLBACK_KEYS: dict[str, str] = {
    "g": "can_manage_groups",
    "c": "can_manage_channels",
    "r": "can_manage_credit",
    "b": "can_remove_bot",
    "s": "can_manage_chat_settings",
    "a": "auto_admin_bypass",
}

SUDO_PERMISSION_FIELD_TO_CALLBACK_KEY: dict[str, str] = {
    field: key for key, field in SUDO_PERMISSION_CALLBACK_KEYS.items()
}

SUDO_PERMISSION_FIELD_LABEL_KEYS: dict[str, str] = {
    "can_manage_groups": "sudo_permissions.perm_groups",
    "can_manage_channels": "sudo_permissions.perm_channels",
    "can_manage_credit": "sudo_permissions.perm_credit",
    "can_remove_bot": "sudo_permissions.perm_remove_bot",
    "can_manage_chat_settings": "sudo_permissions.perm_chat_settings",
    "auto_admin_bypass": "sudo_permissions.perm_auto_admin",
}


def resolve_sudo_permission_field(name_or_key: str) -> str | None:
    """Map callback compact key or model field name to a whitelisted permission field."""
    if name_or_key in SUDO_PERMISSION_FIELD_NAMES:
        return name_or_key
    return SUDO_PERMISSION_CALLBACK_KEYS.get(name_or_key)


def _permission_bool(value: object | None) -> bool:
    """Treat missing/null permission values as enabled for backward compatibility."""
    if value is None:
        return True
    return bool(value)


def sudo_permissions_from_row(sudo: Sudo) -> dict[str, bool]:
    """Build permission map from a Sudo ORM row."""
    return {
        name: _permission_bool(getattr(sudo, name, None))
        for name in SUDO_PERMISSION_FIELD_NAMES
    }


async def upsert_user(
    user_id: int,
    username: str | None = None,
    first_name: str | None = None,
) -> User:
    async with async_session() as session:
        stmt = select(User).where(User.user_id == user_id)
        result = await session.execute(stmt)
        user = result.scalar_one_or_none()
        if user is None:
            user = User(
                user_id=user_id,
                username=username,
                first_name=first_name,
            )
            session.add(user)
        else:
            if username is not None:
                user.username = username
            if first_name is not None:
                user.first_name = first_name
            user.last_seen = datetime.now(timezone.utc)
        await session.commit()
        await session.refresh(user)
        return user


async def get_user(user_id: int) -> User | None:
    async with async_session() as session:
        stmt = select(User).where(User.user_id == user_id)
        result = await session.execute(stmt)
        return result.scalar_one_or_none()


async def is_owner(user_id: int) -> bool:
    async with async_session() as session:
        stmt = select(Owner).where(Owner.user_id == user_id, Owner.is_active.is_(True))
        result = await session.execute(stmt)
        return result.scalar_one_or_none() is not None


async def is_sudo(user_id: int) -> bool:
    async with async_session() as session:
        stmt = select(Sudo).where(Sudo.user_id == user_id, Sudo.is_active.is_(True))
        result = await session.execute(stmt)
        return result.scalar_one_or_none() is not None


async def is_sudo_or_above(user_id: int) -> bool:
    from app.utils.cache import get_role_cached, set_role_cached
    cached = await get_role_cached(user_id)
    if cached is not None:
        return cached in ("owner", "sudo")
    if await is_owner(user_id):
        await set_role_cached(user_id, "owner")
        return True
    if await is_sudo(user_id):
        await set_role_cached(user_id, "sudo")
        return True
    await set_role_cached(user_id, "user")
    return False


async def add_owner(
    user_id: int,
    username: str | None = None,
    display_name: str | None = None,
    added_by: int | None = None,
) -> Owner:
    from app.utils.cache import invalidate_role
    async with async_session() as session:
        stmt = select(Owner).where(Owner.user_id == user_id)
        result = await session.execute(stmt)
        owner = result.scalar_one_or_none()
        if owner is None:
            owner = Owner(
                user_id=user_id,
                username=username,
                display_name=display_name,
                added_by=added_by,
                is_active=True,
            )
            session.add(owner)
        else:
            owner.is_active = True
            owner.deactivated_at = None
            if username is not None:
                owner.username = username
            if display_name is not None:
                owner.display_name = display_name
        await session.commit()
        await session.refresh(owner)
    await invalidate_role(user_id)
    return owner


async def remove_owner(user_id: int) -> None:
    from app.utils.cache import invalidate_role
    async with async_session() as session:
        stmt = (
            update(Owner)
            .where(Owner.user_id == user_id)
            .values(is_active=False, deactivated_at=datetime.now(timezone.utc))
        )
        await session.execute(stmt)
        await session.commit()
    await invalidate_role(user_id)


async def get_all_owners() -> list[Owner]:
    async with async_session() as session:
        stmt = select(Owner).where(Owner.is_active.is_(True))
        result = await session.execute(stmt)
        return list(result.scalars().all())


async def get_owner(user_id: int) -> Owner | None:
    async with async_session() as session:
        stmt = select(Owner).where(Owner.user_id == user_id, Owner.is_active.is_(True))
        result = await session.execute(stmt)
        return result.scalar_one_or_none()


async def set_owner_admin_title(user_id: int, title: str | None) -> bool:
    """Set or clear a stored owner admin title candidate."""
    async with async_session() as session:
        stmt = select(Owner).where(Owner.user_id == user_id, Owner.is_active.is_(True))
        result = await session.execute(stmt)
        owner = result.scalar_one_or_none()
        if owner is None:
            return False
        owner.admin_title = title
        await session.commit()
    return True


async def add_sudo(
    user_id: int,
    username: str | None = None,
    display_name: str | None = None,
    added_by: int | None = None,
) -> Sudo:
    from app.utils.cache import invalidate_role
    async with async_session() as session:
        stmt = select(Sudo).where(Sudo.user_id == user_id)
        result = await session.execute(stmt)
        sudo = result.scalar_one_or_none()
        if sudo is None:
            sudo = Sudo(
                user_id=user_id,
                username=username,
                display_name=display_name,
                added_by=added_by,
                is_active=True,
            )
            session.add(sudo)
        else:
            sudo.is_active = True
            sudo.deactivated_at = None
            if added_by is not None:
                sudo.added_by = added_by
            if username is not None:
                sudo.username = username
            if display_name is not None:
                sudo.display_name = display_name
        await session.commit()
        await session.refresh(sudo)
    await invalidate_role(user_id)
    return sudo


async def remove_sudo(user_id: int) -> None:
    from app.utils.cache import invalidate_role
    async with async_session() as session:
        stmt = (
            update(Sudo)
            .where(Sudo.user_id == user_id)
            .values(is_active=False, deactivated_at=datetime.now(timezone.utc))
        )
        await session.execute(stmt)
        await session.commit()
    await invalidate_role(user_id)


async def get_all_sudos() -> list[Sudo]:
    async with async_session() as session:
        stmt = select(Sudo).where(Sudo.is_active.is_(True))
        result = await session.execute(stmt)
        return list(result.scalars().all())


async def get_sudo(user_id: int) -> Sudo | None:
    async with async_session() as session:
        stmt = select(Sudo).where(Sudo.user_id == user_id, Sudo.is_active.is_(True))
        result = await session.execute(stmt)
        return result.scalar_one_or_none()


async def get_sudo_record(user_id: int) -> Sudo | None:
    """Return sudo row by user_id regardless of active status (developer detail)."""
    async with async_session() as session:
        stmt = select(Sudo).where(Sudo.user_id == user_id)
        result = await session.execute(stmt)
        return result.scalar_one_or_none()


async def set_sudo_admin_title(user_id: int, title: str | None) -> bool:
    """Set or clear a stored sudo admin title candidate."""
    async with async_session() as session:
        stmt = select(Sudo).where(Sudo.user_id == user_id, Sudo.is_active.is_(True))
        result = await session.execute(stmt)
        sudo = result.scalar_one_or_none()
        if sudo is None:
            return False
        sudo.admin_title = title
        await session.commit()
    return True


async def get_sudo_permissions(user_id: int) -> dict[str, bool] | None:
    """Return permission flags for a sudo user, or None when the user is not a sudo."""
    row = await get_sudo_record(user_id)
    if row is None:
        return None
    return sudo_permissions_from_row(row)


async def set_sudo_permission(user_id: int, field: str, value: bool) -> bool:
    """Update a single sudo permission flag. Returns False if field or sudo is invalid."""
    if field not in SUDO_PERMISSION_FIELD_NAMES:
        return False
    from app.utils.cache import invalidate_role

    async with async_session() as session:
        stmt = select(Sudo).where(Sudo.user_id == user_id)
        result = await session.execute(stmt)
        sudo = result.scalar_one_or_none()
        if sudo is None:
            return False
        await session.execute(
            update(Sudo).where(Sudo.user_id == user_id).values({field: value})
        )
        await session.commit()
    await invalidate_role(user_id)
    return True


async def get_all_users() -> list[User]:
    async with async_session() as session:
        stmt = select(User)
        result = await session.execute(stmt)
        return list(result.scalars().all())


async def ban_user(user_id: int, banned_by: int | None = None) -> None:
    async with async_session() as session:
        stmt = (
            update(User)
            .where(User.user_id == user_id)
            .values(
                is_banned=True,
                banned_at=datetime.now(timezone.utc),
                banned_by=banned_by,
            )
        )
        await session.execute(stmt)
        await session.commit()


async def unban_user(user_id: int) -> None:
    async with async_session() as session:
        stmt = (
            update(User)
            .where(User.user_id == user_id)
            .values(is_banned=False, banned_at=None, banned_by=None)
        )
        await session.execute(stmt)
        await session.commit()


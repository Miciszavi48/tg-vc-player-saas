"""Owner install lineage resolution and scope guards for multi-tenant owner panel."""

from __future__ import annotations

from sqlalchemy import or_, select

from app.database.engine import async_session
from app.database.models import Channel, Group, Owner, Sudo
from app.utils.bot_guards import is_developer

_CHAT_MODELS = {"group": Group, "channel": Channel}


async def get_sudo_user_ids_for_owner(owner_user_id: int) -> frozenset[int]:
    """Return active sudo user IDs delegated by the given owner.

    Args:
        owner_user_id: Telegram user id of the creator/owner.

    Returns:
        Frozenset of sudo user ids where ``Sudo.added_by`` matches the owner.
    """
    async with async_session() as session:
        stmt = select(Sudo.user_id).where(
            Sudo.added_by == owner_user_id,
            Sudo.is_active.is_(True),
        )
        result = await session.execute(stmt)
        return frozenset(int(uid) for uid in result.scalars().all())


def install_scope_clause(model, owner_user_id: int, sudo_user_ids: frozenset[int]):
    """Build SQLAlchemy filter for installs owned by owner or their sudos.

    Args:
        model: ``Group`` or ``Channel`` ORM model class.
        owner_user_id: Telegram user id of the creator/owner.
        sudo_user_ids: Active sudo ids delegated by the owner.

    Returns:
        SQLAlchemy boolean clause suitable for ``where()``.
    """
    clauses = [model.installed_by == owner_user_id]
    if sudo_user_ids:
        clauses.append(model.installed_by.in_(sudo_user_ids))
    return or_(*clauses)


async def resolve_owner_user_id_from_installer(installed_by: int | None) -> int | None:
    """Map installer user id to owning creator user id when lineage is known.

    Resolution order:
        1. Active owner installer -> installer id.
        2. Active sudo installer -> sudo's ``added_by`` when that user is active owner.
        3. Otherwise -> ``None`` (deny owner scope safely).

    Args:
        installed_by: Telegram user id stored on the install row.

    Returns:
        Resolved owner user id, or ``None`` when lineage cannot be determined.
    """
    if installed_by is None:
        return None
    installer_id = int(installed_by)
    async with async_session() as session:
        owner_row = await session.execute(
            select(Owner.user_id).where(
                Owner.user_id == installer_id,
                Owner.is_active.is_(True),
            )
        )
        if owner_row.scalar_one_or_none() is not None:
            return installer_id

        sudo_row = await session.execute(
            select(Sudo.added_by).where(
                Sudo.user_id == installer_id,
                Sudo.is_active.is_(True),
            )
        )
        added_by = sudo_row.scalar_one_or_none()
        if added_by is None:
            return None

        owner_parent = await session.execute(
            select(Owner.user_id).where(
                Owner.user_id == int(added_by),
                Owner.is_active.is_(True),
            )
        )
        parent_id = owner_parent.scalar_one_or_none()
        return int(parent_id) if parent_id is not None else None


async def resolve_owner_user_id_for_chat(chat_id: int, chat_type: str) -> int | None:
    """Resolve owning creator user id for an installed group or channel.

    Args:
        chat_id: Telegram chat id.
        chat_type: ``group`` or ``channel``.

    Returns:
        Resolved owner user id, or ``None`` when chat is missing or lineage unknown.
    """
    model = _CHAT_MODELS.get(chat_type)
    if model is None:
        return None
    async with async_session() as session:
        row = await session.execute(
            select(model.installed_by).where(
                model.chat_id == chat_id,
                model.status == "active",
            )
        )
        installed_by = row.scalar_one_or_none()
    return await resolve_owner_user_id_from_installer(installed_by)


async def install_belongs_to_owner(
    owner_user_id: int,
    chat_id: int,
    chat_type: str,
    *,
    actor_user_id: int | None = None,
) -> bool:
    """Return True when the chat install is in the owner's lineage scope.

    Developer actors bypass owner scope checks when ``actor_user_id`` is provided.

    Args:
        owner_user_id: Telegram user id of the creator/owner being checked.
        chat_id: Telegram chat id.
        chat_type: ``group`` or ``channel``.
        actor_user_id: Optional acting user id for developer override.

    Returns:
        True when access is allowed for the owner scope.
    """
    if actor_user_id is not None and is_developer(actor_user_id):
        return True
    resolved = await resolve_owner_user_id_for_chat(chat_id, chat_type)
    return resolved is not None and resolved == int(owner_user_id)


def sudo_belongs_to_actor(sudo: Sudo, actor_id: int) -> bool:
    """Return True when the sudo row is in the acting owner's scope.

    Developers may manage any sudo; owners may manage only sudos they added.

    Args:
        sudo: Sudo ORM row.
        actor_id: Telegram user id of the acting panel user.

    Returns:
        True when the actor may manage this sudo.
    """
    return is_developer(actor_id) or sudo.added_by == actor_id


async def assert_sudo_belongs_to_actor(sudo: Sudo | None, actor_id: int) -> bool:
    """Return True when the sudo is in scope for the actor; False to deny."""
    if sudo is None:
        return False
    return sudo_belongs_to_actor(sudo, actor_id)


async def get_owner_sudos(owner_user_id: int) -> list[Sudo]:
    """Return active sudos delegated by the given owner."""
    async with async_session() as session:
        stmt = (
            select(Sudo)
            .where(Sudo.is_active.is_(True), Sudo.added_by == owner_user_id)
            .order_by(Sudo.id.desc())
        )
        result = await session.execute(stmt)
        return list(result.scalars().all())


async def get_scoped_sudos_for_actor(actor_user_id: int) -> list[Sudo]:
    """Return sudos visible in owner panel for the acting user."""
    if is_developer(actor_user_id):
        from app.repositories import user_repo

        return await user_repo.get_all_sudos()
    return await get_owner_sudos(actor_user_id)


async def assert_owner_install_access(
    owner_user_id: int,
    chat_id: int,
    chat_type: str,
    *,
    actor_user_id: int | None = None,
) -> bool:
    """Return True when owner access to the install is allowed; False to deny.

    Args:
        owner_user_id: Telegram user id of the creator/owner being checked.
        chat_id: Telegram chat id.
        chat_type: ``group`` or ``channel``.
        actor_user_id: Optional acting user id for developer override.

    Returns:
        True when the caller should proceed; False when access must be denied.
    """
    return await install_belongs_to_owner(
        owner_user_id,
        chat_id,
        chat_type,
        actor_user_id=actor_user_id,
    )

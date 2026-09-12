"""Repository for per-owner text/link override rows."""

from __future__ import annotations

from sqlalchemy import delete, select

from app.database.engine import async_session
from app.database.models import OwnerTextLink

_VALID_KINDS = frozenset({"text", "link"})


def _validate_owner_user_id(owner_user_id: int) -> int:
    """Return validated owner user id or raise ValueError."""
    uid = int(owner_user_id)
    if uid <= 0:
        raise ValueError("owner_user_id must be a positive integer")
    return uid


def _validate_key(key: str) -> str:
    """Return validated setting key or raise ValueError."""
    normalized = (key or "").strip()
    if not normalized:
        raise ValueError("key must not be empty")
    return normalized


def _validate_kind(kind: str) -> str:
    """Return validated kind or raise ValueError."""
    normalized = (kind or "").strip().lower()
    if normalized not in _VALID_KINDS:
        raise ValueError("kind must be 'text' or 'link'")
    return normalized


async def get_owner_text_link(owner_user_id: int, key: str) -> OwnerTextLink | None:
    """Return a single owner override row, if present.

    Args:
        owner_user_id: Telegram user id of the owner/creator.
        key: Whitelisted text/link field key.

    Returns:
        Matching ``OwnerTextLink`` row or ``None``.
    """
    owner_id = _validate_owner_user_id(owner_user_id)
    field_key = _validate_key(key)
    async with async_session() as session:
        stmt = select(OwnerTextLink).where(
            OwnerTextLink.owner_user_id == owner_id,
            OwnerTextLink.key == field_key,
        )
        result = await session.execute(stmt)
        return result.scalar_one_or_none()


async def set_owner_text_link(
    owner_user_id: int,
    key: str,
    kind: str,
    value: str | None,
    *,
    updated_by: int | None = None,
) -> OwnerTextLink:
    """Upsert an owner override row for the given owner/key.

    Args:
        owner_user_id: Telegram user id of the owner/creator.
        key: Whitelisted text/link field key.
        kind: ``text`` or ``link``.
        value: Stored override payload (plain text, link URL, or media JSON).
        updated_by: Optional Telegram actor id performing the write.

    Returns:
        Persisted ``OwnerTextLink`` row.
    """
    owner_id = _validate_owner_user_id(owner_user_id)
    field_key = _validate_key(key)
    field_kind = _validate_kind(kind)
    async with async_session() as session:
        stmt = select(OwnerTextLink).where(
            OwnerTextLink.owner_user_id == owner_id,
            OwnerTextLink.key == field_key,
        )
        result = await session.execute(stmt)
        row = result.scalar_one_or_none()
        if row is None:
            row = OwnerTextLink(
                owner_user_id=owner_id,
                key=field_key,
                kind=field_kind,
                value=value,
                updated_by=updated_by,
            )
            session.add(row)
        else:
            row.kind = field_kind
            row.value = value
            if updated_by is not None:
                row.updated_by = updated_by
        await session.commit()
        await session.refresh(row)
        return row


async def clear_owner_text_link(owner_user_id: int, key: str) -> bool:
    """Delete an owner override row.

    Args:
        owner_user_id: Telegram user id of the owner/creator.
        key: Whitelisted text/link field key.

    Returns:
        ``True`` when a row existed and was deleted; ``False`` otherwise.
    """
    owner_id = _validate_owner_user_id(owner_user_id)
    field_key = _validate_key(key)
    async with async_session() as session:
        stmt = (
            delete(OwnerTextLink)
            .where(
                OwnerTextLink.owner_user_id == owner_id,
                OwnerTextLink.key == field_key,
            )
            .returning(OwnerTextLink.id)
        )
        result = await session.execute(stmt)
        deleted_id = result.scalar_one_or_none()
        await session.commit()
        return deleted_id is not None


async def list_owner_text_links(owner_user_id: int) -> list[OwnerTextLink]:
    """Return all override rows for an owner ordered by id descending.

    Args:
        owner_user_id: Telegram user id of the owner/creator.

    Returns:
        List of ``OwnerTextLink`` rows for that owner only.
    """
    owner_id = _validate_owner_user_id(owner_user_id)
    async with async_session() as session:
        stmt = (
            select(OwnerTextLink)
            .where(OwnerTextLink.owner_user_id == owner_id)
            .order_by(OwnerTextLink.id.desc())
        )
        result = await session.execute(stmt)
        return list(result.scalars().all())

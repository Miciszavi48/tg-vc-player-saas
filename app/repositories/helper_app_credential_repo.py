from __future__ import annotations

from sqlalchemy import select, update
from sqlalchemy.sql import func

from app.database.engine import async_session
from app.database.models import HelperAppCredential


async def create_app_credential(
    *,
    api_id: int,
    api_hash_enc: str,
    label: str | None = None,
    created_by: int | None = None,
    notes: str | None = None,
    is_active: bool = True,
) -> HelperAppCredential:
    async with async_session() as session:
        credential = HelperAppCredential(
            label=label,
            api_id=api_id,
            api_hash_enc=api_hash_enc,
            created_by=created_by,
            notes=notes,
            is_active=is_active,
        )
        session.add(credential)
        await session.commit()
        await session.refresh(credential)
        return credential


async def list_active_app_credentials() -> list[HelperAppCredential]:
    async with async_session() as session:
        result = await session.execute(
            select(HelperAppCredential)
            .where(HelperAppCredential.is_active.is_(True))
            .order_by(HelperAppCredential.use_count.asc(), HelperAppCredential.id.asc())
        )
        return list(result.scalars().all())


async def get_app_credential(credential_id: int) -> HelperAppCredential | None:
    async with async_session() as session:
        return await session.get(HelperAppCredential, credential_id)


async def mark_app_credential_used(credential_id: int) -> None:
    async with async_session() as session:
        await session.execute(
            update(HelperAppCredential)
            .where(HelperAppCredential.id == credential_id)
            .values(
                last_used_at=func.now(),
                use_count=HelperAppCredential.use_count + 1,
                updated_at=func.now(),
            )
        )
        await session.commit()


async def deactivate_app_credential(credential_id: int) -> None:
    async with async_session() as session:
        await session.execute(
            update(HelperAppCredential)
            .where(HelperAppCredential.id == credential_id)
            .values(is_active=False, updated_at=func.now())
        )
        await session.commit()

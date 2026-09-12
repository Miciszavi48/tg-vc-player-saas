"""Persistence helpers for the encrypted global Fast-Creat token pools."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import case, func, select

from app.database.engine import async_session
from app.database.models import FastCreatApiToken, FastCreatApiTokenEvent


PROVIDERS = frozenset({"instagram", "tiktok", "spotify"})
_STATUSES = frozenset({"active", "disabled", "invalid"})


@dataclass(frozen=True)
class FastCreatTokenSummary:
    total: int
    active: int
    cooldown: int
    disabled_or_invalid: int


def _require_provider(provider: str) -> str:
    normalized = str(provider).strip().lower()
    if normalized not in PROVIDERS:
        raise ValueError("unsupported_fast_creat_provider")
    return normalized


async def get_token(token_id: int) -> FastCreatApiToken | None:
    async with async_session() as session:
        return await session.get(FastCreatApiToken, token_id)


async def get_by_fingerprint(provider: str, fingerprint: str) -> FastCreatApiToken | None:
    provider = _require_provider(provider)
    async with async_session() as session:
        row = await session.execute(
            select(FastCreatApiToken).where(
                FastCreatApiToken.provider == provider,
                FastCreatApiToken.fingerprint == fingerprint,
            )
        )
        return row.scalar_one_or_none()


async def create_token(
    *,
    provider: str,
    token_enc: str,
    fingerprint: str,
    created_by: int,
) -> FastCreatApiToken:
    provider = _require_provider(provider)
    async with async_session() as session:
        async with session.begin():
            row = FastCreatApiToken(
                provider=provider,
                token_enc=token_enc,
                fingerprint=fingerprint,
                status="active",
                created_by=created_by,
            )
            session.add(row)
            await session.flush()
            session.add(
                FastCreatApiTokenEvent(
                    token_id=row.id,
                    provider=provider,
                    actor_id=created_by,
                    action="created",
                    result_code="active",
                )
            )
        await session.refresh(row)
        return row


async def list_tokens(
    provider: str,
    *,
    page: int,
    page_size: int = 8,
) -> tuple[list[FastCreatApiToken], int]:
    provider = _require_provider(provider)
    offset = max(0, page) * page_size
    async with async_session() as session:
        total = int(
            (await session.execute(
                select(func.count()).select_from(FastCreatApiToken).where(
                    FastCreatApiToken.provider == provider
                )
            )).scalar_one()
        )
        result = await session.execute(
            select(FastCreatApiToken)
            .where(FastCreatApiToken.provider == provider)
            .order_by(FastCreatApiToken.id.asc())
            .offset(offset)
            .limit(page_size)
        )
        return list(result.scalars().all()), total


async def get_summary(provider: str) -> FastCreatTokenSummary:
    provider = _require_provider(provider)
    now = datetime.now(timezone.utc)
    eligible = (
        (FastCreatApiToken.status == "active")
        & (
            FastCreatApiToken.cooldown_until.is_(None)
            | (FastCreatApiToken.cooldown_until <= now)
        )
    )
    cooling_down = (
        (FastCreatApiToken.status == "active")
        & (FastCreatApiToken.cooldown_until.is_not(None))
        & (FastCreatApiToken.cooldown_until > now)
    )
    async with async_session() as session:
        total, active, cooldown = (await session.execute(
            select(
                func.count(FastCreatApiToken.id),
                func.coalesce(func.sum(case((eligible, 1), else_=0)), 0),
                func.coalesce(func.sum(case((cooling_down, 1), else_=0)), 0),
            ).where(FastCreatApiToken.provider == provider)
        )).one()
    total = int(total)
    active = int(active)
    cooldown = int(cooldown)
    return FastCreatTokenSummary(
        total=total,
        active=active,
        cooldown=cooldown,
        disabled_or_invalid=total - active - cooldown,
    )


async def reserve_next_token(
    provider: str,
    *,
    exclude_ids: set[int] | None = None,
) -> FastCreatApiToken | None:
    """Reserve the least-recently-selected eligible provider token briefly."""
    provider = _require_provider(provider)
    now = datetime.now(timezone.utc)
    async with async_session() as session:
        async with session.begin():
            stmt = select(FastCreatApiToken).where(
                FastCreatApiToken.provider == provider,
                FastCreatApiToken.status == "active",
                (FastCreatApiToken.cooldown_until.is_(None))
                | (FastCreatApiToken.cooldown_until <= now),
            )
            if exclude_ids:
                stmt = stmt.where(FastCreatApiToken.id.not_in(exclude_ids))
            row = (await session.execute(
                stmt.order_by(
                    FastCreatApiToken.last_selected_at.asc().nullsfirst(),
                    FastCreatApiToken.id.asc(),
                )
                .limit(1)
                .with_for_update(skip_locked=True)
            )).scalar_one_or_none()
            if row is None:
                return None
            row.last_selected_at = now
            row.use_count += 1
            session.add(
                FastCreatApiTokenEvent(
                    token_id=row.id,
                    provider=provider,
                    actor_id=None,
                    action="reserved",
                    result_code=None,
                )
            )
        await session.refresh(row)
        return row


async def mark_success(token_id: int) -> None:
    now = datetime.now(timezone.utc)
    async with async_session() as session:
        async with session.begin():
            row = await session.get(FastCreatApiToken, token_id)
            if row is None:
                return
            row.last_success_at = now
            row.last_error_code = None
            row.last_error_at = None
            row.cooldown_until = None
            session.add(FastCreatApiTokenEvent(
                token_id=row.id, provider=row.provider, actor_id=None,
                action="success", result_code=None,
            ))


async def mark_rate_limited(token_id: int) -> None:
    now = datetime.now(timezone.utc)
    async with async_session() as session:
        async with session.begin():
            row = await session.get(FastCreatApiToken, token_id)
            if row is None:
                return
            row.failure_count += 1
            row.last_error_code = "rate_limited"
            row.last_error_at = now
            row.cooldown_until = now + timedelta(minutes=1)
            session.add(FastCreatApiTokenEvent(
                token_id=row.id, provider=row.provider, actor_id=None,
                action="rate_limited", result_code="cooldown_60s",
            ))


async def mark_invalid(token_id: int, *, code: str = "invalid_token") -> None:
    now = datetime.now(timezone.utc)
    async with async_session() as session:
        async with session.begin():
            row = await session.get(FastCreatApiToken, token_id)
            if row is None:
                return
            row.failure_count += 1
            row.status = "invalid"
            row.cooldown_until = None
            row.last_error_code = code[:64]
            row.last_error_at = now
            session.add(FastCreatApiTokenEvent(
                token_id=row.id, provider=row.provider, actor_id=None,
                action="invalidated", result_code=code[:64],
            ))


async def mark_failure(token_id: int, *, code: str) -> None:
    now = datetime.now(timezone.utc)
    async with async_session() as session:
        async with session.begin():
            row = await session.get(FastCreatApiToken, token_id)
            if row is None:
                return
            row.failure_count += 1
            row.last_error_code = code[:64]
            row.last_error_at = now
            session.add(FastCreatApiTokenEvent(
                token_id=row.id, provider=row.provider, actor_id=None,
                action="failure", result_code=code[:64],
            ))


async def set_status(token_id: int, *, status: str, actor_id: int) -> FastCreatApiToken | None:
    if status not in _STATUSES:
        raise ValueError("invalid_fast_creat_token_status")
    async with async_session() as session:
        async with session.begin():
            row = await session.get(FastCreatApiToken, token_id)
            if row is None:
                return None
            row.status = status
            if status != "active":
                row.cooldown_until = None
            session.add(FastCreatApiTokenEvent(
                token_id=row.id, provider=row.provider, actor_id=actor_id,
                action="status_changed", result_code=status,
            ))
        await session.refresh(row)
        return row


async def delete_token(token_id: int, *, actor_id: int) -> bool:
    async with async_session() as session:
        async with session.begin():
            row = await session.get(FastCreatApiToken, token_id)
            if row is None:
                return False
            session.add(FastCreatApiTokenEvent(
                token_id=row.id, provider=row.provider, actor_id=actor_id,
                action="deleted", result_code=None,
            ))
            await session.delete(row)
        return True

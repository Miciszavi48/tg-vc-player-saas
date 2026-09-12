"""Persistence helpers for the encrypted global YouTube cookie-session pool."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

from app.database.engine import async_session
from app.database.models import YoutubeCookieSession, YoutubeCookieSessionEvent


@dataclass(frozen=True)
class YoutubeSessionSummary:
    total: int
    active: int
    cooldown: int
    disabled_or_invalid: int
    last_success_id: int | None
    last_success_at: datetime | None


async def get_session(session_id: int) -> YoutubeCookieSession | None:
    async with async_session() as session:
        return await session.get(YoutubeCookieSession, session_id)


async def get_by_fingerprint(fingerprint: str) -> YoutubeCookieSession | None:
    async with async_session() as session:
        result = await session.execute(
            select(YoutubeCookieSession).where(YoutubeCookieSession.fingerprint == fingerprint)
        )
        return result.scalar_one_or_none()


async def create_session(
    *,
    cookie_blob_enc: str,
    fingerprint: str,
    created_by: int,
    cookie_expires_at: datetime | None = None,
) -> YoutubeCookieSession:
    async with async_session() as session:
        async with session.begin():
            row = YoutubeCookieSession(
                cookie_blob_enc=cookie_blob_enc,
                fingerprint=fingerprint,
                status="disabled",
                created_by=created_by,
                cookie_expires_at=cookie_expires_at,
            )
            session.add(row)
            await session.flush()
            session.add(
                YoutubeCookieSessionEvent(
                    session_id=row.id,
                    actor_id=created_by,
                    action="created",
                    result_code="disabled",
                )
            )
        await session.refresh(row)
        return row


async def list_sessions(*, page: int, page_size: int = 5) -> tuple[list[YoutubeCookieSession], int]:
    offset = max(0, page) * page_size
    async with async_session() as session:
        count = int(
            (await session.execute(select(func.count()).select_from(YoutubeCookieSession))).scalar_one()
        )
        result = await session.execute(
            select(YoutubeCookieSession)
            .order_by(YoutubeCookieSession.id.asc())
            .offset(offset)
            .limit(page_size)
        )
        return list(result.scalars().all()), count


async def list_session_batch(*, after_id: int, batch_size: int = 100) -> list[YoutubeCookieSession]:
    """Load a bounded, deterministic maintenance batch without retaining all rows."""
    async with async_session() as session:
        result = await session.execute(
            select(YoutubeCookieSession)
            .where(YoutubeCookieSession.id > max(0, after_id))
            .order_by(YoutubeCookieSession.id.asc())
            .limit(batch_size)
        )
        return list(result.scalars().all())


async def list_expiring_active_sessions(*, expires_before: datetime) -> list[YoutubeCookieSession]:
    async with async_session() as session:
        result = await session.execute(
            select(YoutubeCookieSession)
            .where(
                YoutubeCookieSession.status == "active",
                YoutubeCookieSession.cookie_expires_at.is_not(None),
                YoutubeCookieSession.cookie_expires_at <= expires_before,
            )
            .order_by(YoutubeCookieSession.cookie_expires_at.asc(), YoutubeCookieSession.id.asc())
        )
        return list(result.scalars().all())


async def get_summary() -> YoutubeSessionSummary:
    now = datetime.now(timezone.utc)
    async with async_session() as session:
        rows = list((await session.execute(select(YoutubeCookieSession))).scalars().all())

    active = sum(
        row.status == "active"
        and (row.cooldown_until is None or row.cooldown_until <= now)
        for row in rows
    )
    cooldown = sum(
        row.status == "active"
        and row.cooldown_until is not None
        and row.cooldown_until > now
        for row in rows
    )
    latest = max(
        (row for row in rows if row.last_success_at is not None),
        key=lambda row: row.last_success_at or datetime.min.replace(tzinfo=timezone.utc),
        default=None,
    )
    return YoutubeSessionSummary(
        total=len(rows),
        active=active,
        cooldown=cooldown,
        disabled_or_invalid=len(rows) - active - cooldown,
        last_success_id=latest.id if latest else None,
        last_success_at=latest.last_success_at if latest else None,
    )


async def reserve_next_session(*, exclude_id: int | None = None) -> YoutubeCookieSession | None:
    """Pick the least-recently-selected eligible row in a short transaction."""
    now = datetime.now(timezone.utc)
    async with async_session() as session:
        async with session.begin():
            stmt = select(YoutubeCookieSession).where(
                YoutubeCookieSession.status == "active",
                (YoutubeCookieSession.cooldown_until.is_(None))
                | (YoutubeCookieSession.cooldown_until <= now),
            )
            if exclude_id is not None:
                stmt = stmt.where(YoutubeCookieSession.id != exclude_id)
            stmt = (
                stmt.order_by(
                    YoutubeCookieSession.last_selected_at.asc().nullsfirst(),
                    YoutubeCookieSession.id.asc(),
                )
                .limit(1)
                .with_for_update(skip_locked=True)
            )
            row = (await session.execute(stmt)).scalar_one_or_none()
            if row is None:
                return None
            row.last_selected_at = now
            row.use_count += 1
        await session.refresh(row)
        return row


async def mark_success(session_id: int, *, checked: bool = False, actor_id: int | None = None) -> None:
    now = datetime.now(timezone.utc)
    async with async_session() as session:
        async with session.begin():
            row = await session.get(YoutubeCookieSession, session_id)
            if row is None:
                return
            row.last_success_at = now
            row.last_error_code = None
            row.last_error_at = None
            row.cooldown_until = None
            if checked:
                row.last_checked_at = now
                if row.status == "invalid":
                    row.status = "disabled"
                session.add(
                    YoutubeCookieSessionEvent(
                        session_id=session_id,
                        actor_id=actor_id,
                        action="test_passed",
                        result_code=row.status,
                    )
                )


async def update_cookie_expiry(session_id: int, *, cookie_expires_at: datetime | None) -> None:
    """Persist derived, non-secret expiry metadata after importing or testing a jar."""
    async with async_session() as session:
        async with session.begin():
            row = await session.get(YoutubeCookieSession, session_id)
            if row is not None:
                row.cookie_expires_at = cookie_expires_at


async def replace_encrypted_blob_after_rekey(
    session_id: int,
    *,
    expected_blob: str,
    replacement_blob: str,
    cookie_expires_at: datetime | None,
    actor_id: int,
) -> bool:
    """Commit one rekeyed record without holding a transaction during decrypt/encrypt."""
    async with async_session() as session:
        async with session.begin():
            row = (
                await session.execute(
                    select(YoutubeCookieSession)
                    .where(YoutubeCookieSession.id == session_id)
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if row is None or row.cookie_blob_enc != expected_blob:
                return False
            row.cookie_blob_enc = replacement_blob
            row.cookie_expires_at = cookie_expires_at
            session.add(
                YoutubeCookieSessionEvent(
                    session_id=session_id,
                    actor_id=actor_id,
                    action="rekeyed",
                    result_code="current",
                )
            )
        return True


async def record_rekey_failure(session_id: int, *, actor_id: int, result_code: str) -> None:
    """Audit a failed rekey without changing the encrypted record or its status."""
    async with async_session() as session:
        async with session.begin():
            row = await session.get(YoutubeCookieSession, session_id)
            if row is None:
                return
            session.add(
                YoutubeCookieSessionEvent(
                    session_id=session_id,
                    actor_id=actor_id,
                    action="rekey_failed",
                    result_code=result_code,
                )
            )


async def mark_test_failure(
    session_id: int,
    *,
    error_code: str,
    invalid: bool,
    actor_id: int | None,
) -> None:
    now = datetime.now(timezone.utc)
    async with async_session() as session:
        async with session.begin():
            row = await session.get(YoutubeCookieSession, session_id)
            if row is None:
                return
            row.last_checked_at = now
            row.last_error_code = error_code
            row.last_error_at = now
            row.failure_count += 1
            if invalid:
                row.status = "invalid"
            session.add(
                YoutubeCookieSessionEvent(
                    session_id=session_id,
                    actor_id=actor_id,
                    action="invalidated" if invalid else "test_failed",
                    result_code=error_code,
                )
            )


async def mark_cooldown(session_id: int, *, seconds: int = 1800) -> None:
    now = datetime.now(timezone.utc)
    async with async_session() as session:
        async with session.begin():
            row = await session.get(YoutubeCookieSession, session_id)
            if row is None:
                return
            row.cooldown_until = now + timedelta(seconds=seconds)
            row.last_error_code = "youtube_cooldown"
            row.last_error_at = now
            row.failure_count += 1
            session.add(
                YoutubeCookieSessionEvent(
                    session_id=session_id,
                    actor_id=None,
                    action="cooldown",
                    result_code="youtube_cooldown",
                )
            )


async def mark_invalid(session_id: int, *, error_code: str) -> None:
    now = datetime.now(timezone.utc)
    async with async_session() as session:
        async with session.begin():
            row = await session.get(YoutubeCookieSession, session_id)
            if row is None:
                return
            row.status = "invalid"
            row.last_error_code = error_code
            row.last_error_at = now
            row.failure_count += 1
            session.add(
                YoutubeCookieSessionEvent(
                    session_id=session_id,
                    actor_id=None,
                    action="invalidated",
                    result_code=error_code,
                )
            )


async def set_status(session_id: int, *, status: str, actor_id: int) -> YoutubeCookieSession | None:
    if status not in {"active", "disabled"}:
        raise ValueError("unsupported YouTube session status")
    async with async_session() as session:
        async with session.begin():
            row = await session.get(YoutubeCookieSession, session_id)
            if row is None:
                return None
            if row.status == "invalid" and status == "active":
                return row
            row.status = status
            session.add(
                YoutubeCookieSessionEvent(
                    session_id=session_id,
                    actor_id=actor_id,
                    action="enabled" if status == "active" else "disabled",
                    result_code=status,
                )
            )
        await session.refresh(row)
        return row


async def delete_session(session_id: int, *, actor_id: int) -> bool:
    async with async_session() as session:
        async with session.begin():
            row = await session.get(YoutubeCookieSession, session_id)
            if row is None:
                return False
            session.add(
                YoutubeCookieSessionEvent(
                    session_id=row.id,
                    actor_id=actor_id,
                    action="deleted",
                    result_code=None,
                )
            )
            await session.delete(row)
        return True

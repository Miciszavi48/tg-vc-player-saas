from __future__ import annotations

import json
from datetime import datetime

from sqlalchemy import select

from app.database.engine import async_session
from app.database.models import HelperEvent


async def log_event(
    event_type: str,
    actor: str = "cli",
    helper_account_id: int | None = None,
    chat_id: int | None = None,
    metadata: dict | None = None,
) -> HelperEvent:
    async with async_session() as session:
        event = HelperEvent(
            event_type=event_type,
            actor=actor,
            helper_account_id=helper_account_id,
            chat_id=chat_id,
            metadata_json=json.dumps(metadata) if metadata else None,
        )
        session.add(event)
        await session.commit()
        await session.refresh(event)
        return event


async def get_events(
    helper_id: int | None = None,
    event_type: str | None = None,
    since: datetime | None = None,
    limit: int = 100,
) -> list[HelperEvent]:
    async with async_session() as session:
        stmt = select(HelperEvent).order_by(HelperEvent.created_at.desc())
        if helper_id is not None:
            stmt = stmt.where(HelperEvent.helper_account_id == helper_id)
        if event_type is not None:
            stmt = stmt.where(HelperEvent.event_type == event_type)
        if since is not None:
            stmt = stmt.where(HelperEvent.created_at >= since)
        stmt = stmt.limit(limit)
        result = await session.execute(stmt)
        return list(result.scalars().all())

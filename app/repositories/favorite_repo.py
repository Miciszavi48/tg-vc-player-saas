from __future__ import annotations

from sqlalchemy import func, select

from app.database.engine import async_session
from app.database.models import Favorite

FAVORITES_PAGE_SIZE = 8


def _total_pages(total: int, page_size: int) -> int:
    return max(1, (total + page_size - 1) // page_size)


def clamp_favorites_page(page: int, total: int, page_size: int = FAVORITES_PAGE_SIZE) -> int:
    """Clamp page index to valid range for favorites list."""
    if total <= 0:
        return 0
    return max(0, min(page, _total_pages(total, page_size) - 1))


async def count_favorites(user_id: int) -> int:
    async with async_session() as session:
        stmt = (
            select(func.count())
            .select_from(Favorite)
            .where(Favorite.user_id == user_id)
        )
        result = await session.execute(stmt)
        return int(result.scalar() or 0)


async def get_favorites_page(
    user_id: int,
    page: int,
    page_size: int = FAVORITES_PAGE_SIZE,
) -> tuple[list[Favorite], int, int]:
    """Return (rows, total_pages, clamped_page_index)."""
    total = await count_favorites(user_id)
    if total == 0:
        return [], 1, 0

    total_pages = _total_pages(total, page_size)
    page = clamp_favorites_page(page, total, page_size)

    async with async_session() as session:
        stmt = (
            select(Favorite)
            .where(Favorite.user_id == user_id)
            .order_by(Favorite.added_at.desc())
            .offset(page * page_size)
            .limit(page_size)
        )
        result = await session.execute(stmt)
        return list(result.scalars().all()), total_pages, page

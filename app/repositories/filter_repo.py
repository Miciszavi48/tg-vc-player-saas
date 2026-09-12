from __future__ import annotations

from sqlalchemy import delete, select

from app.database.engine import async_session
from app.database.models import FilterWord


async def get_filter_words() -> list[str]:
    async with async_session() as session:
        stmt = select(FilterWord.word)
        result = await session.execute(stmt)
        return list(result.scalars().all())


async def add_filter_word(word: str, added_by: int | None = None) -> FilterWord:
    async with async_session() as session:
        stmt = select(FilterWord).where(FilterWord.word == word)
        result = await session.execute(stmt)
        existing = result.scalar_one_or_none()
        if existing is not None:
            return existing

        fw = FilterWord(word=word, added_by=added_by)
        session.add(fw)
        await session.commit()
        await session.refresh(fw)
        return fw


async def remove_filter_word(word: str) -> None:
    async with async_session() as session:
        stmt = delete(FilterWord).where(FilterWord.word == word)
        await session.execute(stmt)
        await session.commit()

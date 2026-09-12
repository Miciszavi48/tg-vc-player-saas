from __future__ import annotations

from sqlalchemy import select

from app.database.engine import async_session
from app.database.models import BotSetting, ChatSettings

_TRUE_VALUES = {"1", "true", "yes", "on", "enabled"}
DEVELOPER_ADMIN_TITLE_KEY = "developer_admin_title"


async def get_chat_settings(
    chat_id: int, chat_type: str = "group", *, use_cache: bool = True
) -> ChatSettings | None:
    if use_cache:
        from app.utils.cache import get_chat_settings_cached

        cached = await get_chat_settings_cached(chat_id, chat_type)
        if cached is not None:
            cs = ChatSettings()
            for k, v in cached.items():
                if hasattr(cs, k):
                    setattr(cs, k, v)
            return cs
    async with async_session() as session:
        stmt = select(ChatSettings).where(
            ChatSettings.chat_id == chat_id,
            ChatSettings.chat_type == chat_type,
        )
        result = await session.execute(stmt)
        cs = result.scalar_one_or_none()
        if cs is not None and use_cache:
            from app.utils.cache import set_chat_settings_cached
            data = {
                c.name: getattr(cs, c.name)
                for c in ChatSettings.__table__.columns
                if not c.name.startswith("_")
            }
            for k, v in data.items():
                from datetime import datetime

                if isinstance(v, datetime):
                    data[k] = v.isoformat()
            await set_chat_settings_cached(chat_id, data, chat_type)
        return cs


async def create_defaults(chat_id: int, chat_type: str = "group") -> ChatSettings:
    async with async_session() as session:
        stmt = select(ChatSettings).where(
            ChatSettings.chat_id == chat_id, ChatSettings.chat_type == chat_type
        )
        result = await session.execute(stmt)
        existing = result.scalar_one_or_none()
        if existing is not None:
            return existing

        cs = ChatSettings(chat_id=chat_id, chat_type=chat_type)
        session.add(cs)
        await session.commit()
        await session.refresh(cs)
        return cs


async def update_setting(
    chat_id: int, key: str, value: object, chat_type: str = "group"
) -> None:
    from app.utils.cache import invalidate_chat_settings

    async with async_session() as session:
        stmt = select(ChatSettings).where(
            ChatSettings.chat_id == chat_id,
            ChatSettings.chat_type == chat_type,
        )
        result = await session.execute(stmt)
        cs = result.scalar_one_or_none()
        if cs is None:
            return
        setattr(cs, key, value)
        await session.commit()
    await invalidate_chat_settings(chat_id, chat_type)


def _is_true(value: str | None) -> bool:
    if value is None:
        return False
    return str(value).strip().lower() in _TRUE_VALUES


async def get_bot_setting(key: str, *, use_cache: bool = True) -> str | None:
    if use_cache:
        from app.utils.cache import get_bot_setting_cached

        cached = await get_bot_setting_cached(key)
        if cached is not None:
            return cached

    async with async_session() as session:
        stmt = select(BotSetting).where(BotSetting.key == key)
        result = await session.execute(stmt)
        bs = result.scalar_one_or_none()
        value = bs.value if bs else None

    if use_cache and value is not None:
        from app.utils.cache import set_bot_setting_cached

        await set_bot_setting_cached(key, value)
    return value


async def set_bot_setting(
    key: str,
    value: str | None,
    updated_by: int | None = None,
) -> None:
    async with async_session() as session:
        stmt = select(BotSetting).where(BotSetting.key == key)
        result = await session.execute(stmt)
        bs = result.scalar_one_or_none()
        if bs is None:
            bs = BotSetting(key=key, value=value, updated_by=updated_by)
            session.add(bs)
        else:
            bs.value = value
            if updated_by is not None:
                bs.updated_by = updated_by
        await session.commit()

    from app.utils.cache import invalidate_bot_setting, set_bot_setting_cached

    if value is None:
        await invalidate_bot_setting(key)
    else:
        await set_bot_setting_cached(key, value)


async def get_bot_setting_bool(key: str, *, default: bool = False) -> bool:
    raw = await get_bot_setting(key)
    if raw is None:
        return default
    return _is_true(raw)


async def get_developer_admin_title() -> str | None:
    """Return the stored developer admin title candidate, if configured."""
    value = await get_bot_setting(DEVELOPER_ADMIN_TITLE_KEY)
    if value is None:
        return None
    title = value.strip()
    return title or None


async def toggle_bot_setting(
    key: str,
    *,
    updated_by: int | None = None,
    default: bool = False,
) -> bool:
    current = await get_bot_setting_bool(key, default=default)
    new_state = not current
    await set_bot_setting(key, "1" if new_state else "0", updated_by=updated_by)
    return new_state


async def get_all_bot_settings() -> dict[str, str | None]:
    async with async_session() as session:
        stmt = select(BotSetting)
        result = await session.execute(stmt)
        rows = result.scalars().all()
        return {row.key: row.value for row in rows}

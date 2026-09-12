from __future__ import annotations

import random

from sqlalchemy import select, update
from sqlalchemy.sql import func

from app.database.engine import async_session
from app.database.models import HelperDeviceProfile


def _clean(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def _valid_profile(
    *,
    device_model: object,
    system_version: object,
    app_version: object,
) -> bool:
    return bool(_clean(device_model) and _clean(system_version) and _clean(app_version))


async def create_device_profile(
    *,
    device_model: str,
    system_version: str,
    app_version: str,
    lang_code: str = "en",
    system_lang_code: str = "en-US",
    source: str = "manual",
    is_active: bool = True,
) -> HelperDeviceProfile | None:
    if not _valid_profile(
        device_model=device_model,
        system_version=system_version,
        app_version=app_version,
    ):
        return None

    async with async_session() as session:
        profile = HelperDeviceProfile(
            device_model=_clean(device_model),
            system_version=_clean(system_version),
            app_version=_clean(app_version),
            lang_code=_clean(lang_code) or "en",
            system_lang_code=_clean(system_lang_code) or "en-US",
            source=_clean(source) or "manual",
            is_active=is_active,
        )
        session.add(profile)
        await session.commit()
        await session.refresh(profile)
        return profile


async def upsert_device_profile(
    *,
    device_model: str,
    system_version: str,
    app_version: str,
    lang_code: str = "en",
    system_lang_code: str = "en-US",
    source: str = "manual",
    is_active: bool = True,
) -> HelperDeviceProfile | None:
    if not _valid_profile(
        device_model=device_model,
        system_version=system_version,
        app_version=app_version,
    ):
        return None

    device_model_clean = _clean(device_model)
    system_version_clean = _clean(system_version)
    app_version_clean = _clean(app_version)

    async with async_session() as session:
        result = await session.execute(
            select(HelperDeviceProfile).where(
                HelperDeviceProfile.device_model == device_model_clean,
                HelperDeviceProfile.system_version == system_version_clean,
                HelperDeviceProfile.app_version == app_version_clean,
            )
        )
        profile = result.scalar_one_or_none()
        if profile is None:
            profile = HelperDeviceProfile(
                device_model=device_model_clean,
                system_version=system_version_clean,
                app_version=app_version_clean,
                lang_code=_clean(lang_code) or "en",
                system_lang_code=_clean(system_lang_code) or "en-US",
                source=_clean(source) or "manual",
                is_active=is_active,
            )
            session.add(profile)
        else:
            profile.lang_code = _clean(lang_code) or profile.lang_code or "en"
            profile.system_lang_code = _clean(system_lang_code) or profile.system_lang_code or "en-US"
            profile.source = _clean(source) or profile.source or "manual"
            profile.is_active = is_active
            profile.updated_at = func.now()
        await session.commit()
        await session.refresh(profile)
        return profile


async def list_active_device_profiles() -> list[HelperDeviceProfile]:
    async with async_session() as session:
        result = await session.execute(
            select(HelperDeviceProfile)
            .where(HelperDeviceProfile.is_active.is_(True))
            .order_by(HelperDeviceProfile.use_count.asc(), HelperDeviceProfile.id.asc())
        )
        return list(result.scalars().all())


async def get_random_active_device_profile() -> HelperDeviceProfile | None:
    profiles = await list_active_device_profiles()
    if not profiles:
        return None
    return random.choice(profiles)


async def mark_device_profile_used(profile_id: int) -> None:
    async with async_session() as session:
        await session.execute(
            update(HelperDeviceProfile)
            .where(HelperDeviceProfile.id == profile_id)
            .values(
                last_used_at=func.now(),
                use_count=HelperDeviceProfile.use_count + 1,
                updated_at=func.now(),
            )
        )
        await session.commit()


async def deactivate_device_profile(profile_id: int) -> None:
    async with async_session() as session:
        await session.execute(
            update(HelperDeviceProfile)
            .where(HelperDeviceProfile.id == profile_id)
            .values(is_active=False, updated_at=func.now())
        )
        await session.commit()

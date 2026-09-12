from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.database.models import HelperDeviceProfile
from app.utils.device_spoof import load_device_fingerprints


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Import Helper OTP device profiles into an explicit database.")
    parser.add_argument("--database-url", required=True, help="Async SQLAlchemy database URL for a disposable/target DB.")
    parser.add_argument("--file", type=Path, default=None, help="Optional app_information.json path.")
    parser.add_argument("--source", default="app_information", help="Source label stored with imported profiles.")
    return parser


async def _import_profiles(database_url: str, file_path: Path | None, source: str) -> int:
    profiles = load_device_fingerprints(file_path)
    if not profiles:
        return 0

    engine = create_async_engine(database_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    imported = 0
    try:
        async with session_factory() as session:
            for profile in profiles:
                result = await session.execute(
                    select(HelperDeviceProfile).where(
                        HelperDeviceProfile.device_model == profile.device_model,
                        HelperDeviceProfile.system_version == profile.system_version,
                        HelperDeviceProfile.app_version == profile.app_version,
                    )
                )
                existing = result.scalar_one_or_none()
                if existing is None:
                    session.add(
                        HelperDeviceProfile(
                            device_model=profile.device_model,
                            system_version=profile.system_version,
                            app_version=profile.app_version,
                            lang_code=profile.lang_code,
                            system_lang_code=profile.system_lang_code,
                            source=source,
                            is_active=True,
                        )
                    )
                    imported += 1
                else:
                    existing.lang_code = profile.lang_code
                    existing.system_lang_code = profile.system_lang_code
                    existing.source = source
                    existing.is_active = True
            await session.commit()
    finally:
        await engine.dispose()
    return imported


def main() -> None:
    args = _parser().parse_args()
    count = asyncio.run(_import_profiles(args.database_url, args.file, args.source))
    print(f"Imported or refreshed {count} helper device profile(s).")


if __name__ == "__main__":
    main()

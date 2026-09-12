#!/usr/bin/env python3
"""Read-only Helper OTP configuration diagnostic (no Telegram, no secrets printed)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

EXPECTED_ALEMBIC_HEAD = "0039_hot_seat"


def discover_local_alembic_heads() -> list[str]:
    """Return local Alembic heads without opening a database connection."""
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    config_path = ROOT / "app" / "alembic.ini"
    migrations_path = ROOT / "app" / "database" / "migrations"
    if not config_path.is_file() or not migrations_path.is_dir():
        return []

    config = Config(str(config_path))
    config.set_main_option("script_location", str(migrations_path))
    script = ScriptDirectory.from_config(config)
    return sorted(script.get_heads())


def _check_api_config() -> dict:
    from app.config.settings import settings

    return {
        "api_id_configured": bool(settings.API_ID),
        "api_hash_configured": bool(settings.API_HASH),
        "helper_session_key_configured": bool(settings.HELPER_SESSION_KEY_CURRENT),
    }


def _check_client_construct() -> dict:
    from app.config.settings import settings
    from pyrogram import Client

    try:
        Client(
            name="otp_diag_probe",
            api_id=int(settings.API_ID or 1),
            api_hash=settings.API_HASH or "0" * 32,
            in_memory=True,
        )
        return {"client_construct_ok": True}
    except Exception as exc:
        return {"client_construct_ok": False, "client_construct_error": type(exc).__name__}


def _check_app_information_files() -> dict:
    from app.utils.device_spoof import load_device_fingerprints, pick_random_fingerprint

    candidates = [
        ROOT / "app_information.json",
        ROOT / "app" / "app_information.json",
    ]
    found: list[str] = []
    valid_count = 0
    for path in candidates:
        if not path.is_file():
            continue
        found.append(str(path.relative_to(ROOT)))
        profiles = load_device_fingerprints(path)
        if profiles:
            valid_count += len(profiles)
    fallback_ok = pick_random_fingerprint() is not None
    return {
        "app_information_files": found,
        "valid_device_profile_entries": valid_count,
        "fallback_fingerprint_pool_ok": fallback_ok,
    }


def _check_migration_head() -> dict:
    heads = discover_local_alembic_heads()
    return {
        "expected_head": EXPECTED_ALEMBIC_HEAD,
        "local_heads": heads,
        "multiple_heads": len(heads) > 1,
        "expected_head_stale": heads != [EXPECTED_ALEMBIC_HEAD],
        "migration_head_ok": heads == [EXPECTED_ALEMBIC_HEAD],
    }


async def _check_db_counts() -> dict:
    import os

    db_url = os.getenv("DATABASE_URL") or os.getenv("TEST_DATABASE_URL")
    if not db_url:
        return {
            "db_checked": False,
            "active_credentials_count": None,
            "active_device_profiles_count": None,
        }
    try:
        from sqlalchemy import func, select

        from app.database.engine import async_session
        from app.database.models import HelperAppCredential, HelperDeviceProfile

        async with async_session() as session:
            cred_count = await session.scalar(
                select(func.count())
                .select_from(HelperAppCredential)
                .where(HelperAppCredential.is_active.is_(True))
            )
            profile_count = await session.scalar(
                select(func.count())
                .select_from(HelperDeviceProfile)
                .where(HelperDeviceProfile.is_active.is_(True))
            )
        return {
            "db_checked": True,
            "active_credentials_count": int(cred_count or 0),
            "active_device_profiles_count": int(profile_count or 0),
        }
    except Exception as exc:
        return {
            "db_checked": False,
            "db_error": type(exc).__name__,
            "active_credentials_count": None,
            "active_device_profiles_count": None,
        }


async def run_checks() -> dict:
    result = {
        "ok": True,
        **_check_api_config(),
        **_check_client_construct(),
        **_check_app_information_files(),
        **_check_migration_head(),
    }
    result.update(await _check_db_counts())

    required = [
        result.get("api_id_configured"),
        result.get("api_hash_configured"),
        result.get("helper_session_key_configured"),
        result.get("client_construct_ok"),
        result.get("migration_head_ok"),
    ]
    if not all(required):
        result["ok"] = False
    return result


def main() -> int:
    import asyncio

    summary = asyncio.run(run_checks())
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0 if summary.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import argparse
import asyncio
import getpass
import os

from cryptography.fernet import Fernet
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config.settings import settings
from app.database.models import HelperAppCredential


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Create an encrypted Helper OTP API credential.")
    parser.add_argument("--database-url", required=True, help="Explicit async SQLAlchemy database URL.")
    parser.add_argument("--api-id", required=True, type=int, help="Telegram API ID.")
    parser.add_argument("--label", default=None, help="Optional display label.")
    parser.add_argument("--created-by", default=None, type=int, help="Optional developer Telegram user id.")
    parser.add_argument("--notes", default=None, help="Optional non-secret notes.")
    parser.add_argument(
        "--api-hash-env",
        default=None,
        help="Optional env var name containing API hash; otherwise a hidden prompt is used.",
    )
    return parser


def _read_api_hash(env_name: str | None) -> str:
    if env_name:
        value = os.getenv(env_name, "").strip()
    else:
        value = getpass.getpass("Telegram API hash: ").strip()
    if not 8 <= len(value) <= 128:
        raise ValueError("API hash must be 8-128 characters")
    return value


def _encrypt_api_hash(api_hash: str) -> str:
    key = settings.HELPER_SESSION_KEY_CURRENT
    if not key:
        raise ValueError("HELPER_SESSION_KEY_CURRENT is not configured")
    fernet = Fernet(key.encode() if isinstance(key, str) else key)
    return fernet.encrypt(api_hash.encode()).decode()


async def _create(args: argparse.Namespace) -> int:
    api_hash = _read_api_hash(args.api_hash_env)
    api_hash_enc = _encrypt_api_hash(api_hash)
    engine = create_async_engine(args.database_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with session_factory() as session:
            credential = HelperAppCredential(
                label=args.label,
                api_id=args.api_id,
                api_hash_enc=api_hash_enc,
                created_by=args.created_by,
                notes=args.notes,
                is_active=True,
            )
            session.add(credential)
            await session.commit()
            await session.refresh(credential)
            print(f"Created helper app credential id={credential.id} api_id={credential.api_id}.")
            return credential.id
    finally:
        await engine.dispose()


def main() -> None:
    args = _parser().parse_args()
    asyncio.run(_create(args))


if __name__ == "__main__":
    main()

# ruff: noqa: E402
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from types import ModuleType
from typing import AsyncGenerator

import pytest
import pytest_asyncio
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

_TRUE_VALUES = {"1", "true", "yes"}
_BLOCKED_TEST_DB_NAMES = {"musicbot_dev"}
_BLOCKED_TEST_DB_MARKERS = ("prod", "production")
# Reserved Redis database for tests. Deployed instances default to db 0.
_TEST_REDIS_DB_INDEX = 15
_BLOCKED_TEST_REDIS_DB_INDEXES = {0}


def _env_true(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in _TRUE_VALUES


def _database_name(url: str) -> str:
    try:
        return make_url(url).database or ""
    except Exception:
        return ""


def _assert_safe_test_redis_url(url: str, *, allow_external: bool) -> None:
    """Refuse a Redis URL that a deployed instance is expected to be using.

    Fixtures in this suite call ``flushdb()``, which would destroy the runtime
    state of every instance sharing that database (keys, distributed leases,
    wizard state). Deployed instances default to db 0, so a test run must never
    point there by accident.
    """
    if allow_external:
        return
    try:
        index = int(make_url(url).database or "0")
    except Exception:
        return
    if index in _BLOCKED_TEST_REDIS_DB_INDEXES:
        raise RuntimeError(
            f"Refusing to run tests against Redis db {index}: deployed instances "
            "use it and this suite flushes the database it connects to. Use db "
            f"{_TEST_REDIS_DB_INDEX} (the default), or set ALLOW_EXTERNAL_REDIS=1 "
            "to override deliberately."
        )


def _assert_safe_test_database_url(url: str, *, allow_external: bool) -> None:
    if not url.startswith("postgresql"):
        return

    db_name = _database_name(url).lower()
    if db_name in _BLOCKED_TEST_DB_NAMES or any(
        marker in db_name for marker in _BLOCKED_TEST_DB_MARKERS
    ):
        raise RuntimeError(
            "Refusing to run tests against a blocked PostgreSQL database name. "
            "Use TEST_MODE=1 for SQLite tests or point DATABASE_URL at a disposable test DB."
        )
    if not allow_external:
        raise RuntimeError(
            "PostgreSQL test databases require ALLOW_EXTERNAL_TEST_DB=1 and a disposable "
            "DATABASE_URL. Use TEST_MODE=1 for the default SQLite test path."
        )


os.environ.setdefault("MUSICBOT_DOTENV_OVERRIDE", "false")

# Isolate the suite from any deployed environment file. settings.py searches
# PROJECT_ROOT/.env first, so on an installed host (setup_server.sh writes
# /opt/<instance>/.env) importing settings would pull that instance's BOT_TOKEN,
# RUNTIME_PATH and BOT_RELOAD_SENTINEL_PATH into os.environ — leaking real
# secrets into tests and into every subprocess built from os.environ.copy().
os.environ.setdefault(
    "MUSICBOT_ENV_FILE",
    str(Path(__file__).resolve().parent / "pytest-isolated.env"),
)

# Claim REDIS_URL here, unconditionally, before any test module is imported.
# Dozens of test modules open with setdefault("REDIS_URL", ".../0") — db 0 is the
# deployed instances' database — and setdefault is first-wins, so conftest must
# win. Doing this per-branch left the external-DATABASE_URL path unset, letting a
# test module pick db 0 after the guard below had already run.
os.environ.setdefault("REDIS_URL", f"redis://localhost:6379/{_TEST_REDIS_DB_INDEX}")

_TEST_MODE = _env_true("TEST_MODE")
_ALLOW_EXTERNAL_TEST_DB = _env_true("ALLOW_EXTERNAL_TEST_DB")

if _TEST_MODE:
    os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./.pytest-test-mode.db")
else:
    requested_url = os.getenv("DATABASE_URL", "").strip()
    if not requested_url:
        os.environ["TEST_MODE"] = "1"
        _TEST_MODE = True
        os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///./.pytest-test-mode.db"
    else:
        _assert_safe_test_database_url(
            requested_url,
            allow_external=_ALLOW_EXTERNAL_TEST_DB,
        )

_USE_LOCAL_SQLITE = os.environ["DATABASE_URL"].startswith("sqlite+aiosqlite://")
_USE_FAKE_REDIS = _TEST_MODE or not _env_true("ALLOW_EXTERNAL_REDIS")

# Checked even when fakeredis is active: a module that reads REDIS_URL directly
# bypasses the fixtures entirely, which is exactly how a real server gets flushed.
_assert_safe_test_redis_url(
    os.environ.get("REDIS_URL", ""),
    allow_external=_env_true("ALLOW_EXTERNAL_REDIS"),
)

if _USE_LOCAL_SQLITE:
    _sqlite_test_db = make_url(os.environ["DATABASE_URL"]).database
    if _sqlite_test_db and _sqlite_test_db != ":memory:":
        _sqlite_test_db = os.path.abspath(_sqlite_test_db)
        if os.path.isfile(_sqlite_test_db):
            os.remove(_sqlite_test_db)

if "pyromod" not in sys.modules:
    pyromod_module = ModuleType("pyromod")
    pyromod_exceptions = ModuleType("pyromod.exceptions")

    class _ListenerStopped(Exception):
        pass

    pyromod_exceptions.ListenerStopped = _ListenerStopped
    pyromod_module.exceptions = pyromod_exceptions
    sys.modules["pyromod"] = pyromod_module
    sys.modules["pyromod.exceptions"] = pyromod_exceptions

os.environ.setdefault("BOT_TOKEN", "test")
os.environ.setdefault("API_ID", "12345")
os.environ.setdefault("API_HASH", "testhash")
os.environ.setdefault("DEVELOPER_ID", "123456789")
os.environ.setdefault("INSTANCE_ID", "test")

from app.database.models import Base
from app.utils.i18n import TextService

# Force the app-level engine to use NullPool during tests to avoid
# asyncpg event-loop binding issues with per-function loops.


def _create_engine(url: str):
    kwargs: dict = {
        "poolclass": NullPool,
        "echo": False,
    }
    if url.startswith("sqlite+aiosqlite://"):
        kwargs["connect_args"] = {"check_same_thread": False}
    return create_async_engine(url, **kwargs)


_test_engine = _create_engine(os.environ["DATABASE_URL"])
_test_session = async_sessionmaker(
    _test_engine, class_=AsyncSession, expire_on_commit=False,
)
sys.modules["app.database.engine"].__dict__["engine"] = _test_engine
sys.modules["app.database.engine"].__dict__["async_session"] = _test_session

if _USE_LOCAL_SQLITE:

    async def _bootstrap_sqlite_schema() -> None:
        async with _test_engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_bootstrap_sqlite_schema())


@pytest_asyncio.fixture
async def db_engine():
    engine = _create_engine(os.environ["DATABASE_URL"])
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def db_session(db_engine) -> AsyncGenerator[AsyncSession, None]:
    session_factory = async_sessionmaker(
        db_engine, class_=AsyncSession, expire_on_commit=False
    )
    async with session_factory() as session:
        async with session.begin():
            yield session
            await session.rollback()


@pytest_asyncio.fixture
async def redis_conn():
    if _USE_FAKE_REDIS:
        import fakeredis.aioredis as fakeredis

        r = fakeredis.FakeRedis(decode_responses=True)
        yield r
        await r.flushdb()
        await r.aclose()
        return

    import redis.asyncio as redis

    r = redis.from_url(os.environ["REDIS_URL"], decode_responses=True)
    yield r
    await r.flushdb()
    await r.aclose()


@pytest_asyncio.fixture(autouse=True)
async def _inject_cache_singleton(redis_conn):
    import app.utils.cache as cache_mod

    cache_mod._redis = redis_conn
    yield
    cache_mod._redis = None


@pytest.fixture
def text_service() -> TextService:
    return TextService()

from __future__ import annotations

from typing import AsyncGenerator

from sqlalchemy.engine import make_url
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.config.settings import settings
from app.database.schema_readiness import ensure_database_schema_ready, should_use_create_all

_db_url = make_url(settings.DATABASE_URL)
_engine_kwargs: dict = {"echo": False}

if _db_url.drivername.startswith("sqlite"):
    _engine_kwargs["poolclass"] = NullPool
    _engine_kwargs["connect_args"] = {"check_same_thread": False}
else:
    _engine_kwargs.update(
        {
            "pool_size": settings.DB_POOL_SIZE,
            "max_overflow": settings.DB_MAX_OVERFLOW,
            "pool_timeout": settings.DB_POOL_TIMEOUT,
            "pool_recycle": settings.DB_POOL_RECYCLE,
            "pool_pre_ping": True,
            "connect_args": {
                "statement_cache_size": 0,
                "prepared_statement_cache_size": 0,
            },
        }
    )

engine = create_async_engine(settings.DATABASE_URL, **_engine_kwargs)

async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    async with async_session() as session:
        yield session


async def create_schema_for_test_or_explicit_dev_only() -> None:
    """Create tables via ORM metadata (tests, SQLite, or DB_ALLOW_CREATE_ALL only)."""
    from app.database.models import Base

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def seed_initial_settings() -> None:
    """Idempotent seed of bot_settings and install_policy_settings defaults."""
    _BOT_SETTINGS_DEFAULTS: dict[str, str] = {
        "start_text": settings.START_TEXT or "",
        "helper_start_text": "",
        "about_text": "",
        "helper_text": "",
        "tariff_text": "",
        "developer_link": settings.DEVELOPER_LINK or "",
        "developer_pv_link": settings.DEVELOPER_LINK or "",
        "bot_channel_link": settings.BOT_CHANNEL_LINK or "",
        "support_group_link": settings.SUPPORT_GROUP_LINK or "",
        "guide_channel_link": settings.GUIDE_CHANNEL_LINK or "",
        "broadcast_channel_link": "",
        "custom_link": "",
        "sudo_link_1": "",
        "sudo_link_2": "",
        "sudo_links": "[]",
        "trial_enabled": "true",
        "trial_days": str(settings.TRIAL_DAYS),
        "auto_leave_enabled": "true",
        "force_join_enabled": "false",
        "bot_enabled": "true",
        "sudo_panel_enabled": "true",
        "install_limit_enabled": "false",
        "base_rate": str(settings.BASE_CREDIT_RATE),
        "music_rate": str(settings.MUSIC_RATE),
        "music_sell_rate": str(settings.MUSIC_RATE),
        "video_rate": str(settings.VIDEO_RATE),
        "video_sell_rate": str(settings.VIDEO_RATE),
        "security_call_rate": str(settings.SECURITY_CALL_RATE),
        "monthly_invoice_amount": "",
        "monthly_invoice_auto_send_enabled": "0",
        "max_group_members": str(settings.MAX_GROUP_MEMBERS),
        "max_channel_admins": str(settings.MAX_CHANNEL_ADMINS),
        "log_channel_id": str(settings.LOG_CHANNEL_ID),
        "force_join_channel_id": "0",
        "force_join_channel_link": "",
        "filter_words": "[]",
        "blocked_chats": "[]",
        "blocked_users": "[]",
    }

    async with async_session() as session:
        async with session.begin():
            for k, v in _BOT_SETTINGS_DEFAULTS.items():
                await session.execute(
                    text(
                        "INSERT INTO bot_settings (key, value) "
                        "VALUES (:key, :value) "
                        "ON CONFLICT (key) DO NOTHING"
                    ),
                    {"key": k, "value": v},
                )

            await session.execute(
                text(
                    "INSERT INTO install_policy_settings "
                    "(id, policy_mode, trial_days, charge_on_install, "
                    "group_install_fee_irr, chan_install_fee_irr) "
                    "VALUES (1, 'open', :trial_days, false, 0, 0) "
                    "ON CONFLICT (id) DO NOTHING"
                ),
                {"trial_days": settings.TRIAL_DAYS},
            )


async def claim_database_for_instance() -> None:
    """Atomically bind this database to exactly one INSTANCE_ID."""
    from app.database.models import BotInstanceMetadata

    async with async_session() as session:
        async with session.begin():
            if session.bind is not None and session.bind.dialect.name == "postgresql":
                await session.execute(
                    text(
                        "SELECT pg_advisory_xact_lock("
                        "hashtextextended('musicbot_database_instance_claim', 0))"
                    )
                )

            row = (
                await session.execute(
                    select(BotInstanceMetadata)
                    .where(BotInstanceMetadata.id == 1)
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if row is None:
                session.add(
                    BotInstanceMetadata(id=1, instance_id=settings.INSTANCE_ID)
                )
                return
            if row.instance_id != settings.INSTANCE_ID:
                raise RuntimeError(
                    "Database instance ownership mismatch: this database belongs to "
                    f"{row.instance_id!r}, not {settings.INSTANCE_ID!r}. "
                    "Use a separate PostgreSQL database for each instance."
                )


async def init_db() -> None:
    """Prepare database for bot startup: schema check, optional create_all, seed defaults."""
    await ensure_database_schema_ready(
        engine,
        settings.DATABASE_URL,
        db_allow_create_all=settings.DB_ALLOW_CREATE_ALL,
    )

    if should_use_create_all(
        settings.DATABASE_URL,
        db_allow_create_all=settings.DB_ALLOW_CREATE_ALL,
    ):
        await create_schema_for_test_or_explicit_dev_only()

    await claim_database_for_instance()
    await seed_initial_settings()

"""First-run bootstrapper: ensures configured developer IDs are seeded into the database.

Called automatically from main.py before bot.start(). Performs safe
UPSERTs so it is idempotent — safe to run on every startup.

Seeds:
- users table: each configured developer as a registered user
- owners table: each configured developer as bot owner (if not already present)
- bot_settings defaults (handled by init_db)
- install_policy_settings defaults (handled by init_db)
"""
from __future__ import annotations

import logging

from sqlalchemy import select

from app.config.settings import settings
from app.database.engine import async_session
from app.database.models import Owner, User

logger = logging.getLogger(__name__)


async def bootstrap() -> None:
    """Seed critical data on first run. Idempotent — safe on every restart."""
    developer_ids = settings.DEVELOPER_IDS
    if not developer_ids:
        logger.warning("DEVELOPER_ID not set — skipping bootstrap")
        return

    seeded_users = 0
    seeded_owners = 0

    async with async_session() as session:
        async with session.begin():
            for dev_id in sorted(developer_ids):
                existing_user = (await session.execute(
                    select(User).where(User.user_id == dev_id)
                )).scalar_one_or_none()

                if existing_user is None:
                    session.add(User(user_id=dev_id, first_name="Developer"))
                    seeded_users += 1
                    logger.info(
                        "Bootstrap: seeded developer user_id=%d into users table",
                        dev_id,
                    )

                existing_owner = (await session.execute(
                    select(Owner).where(Owner.user_id == dev_id)
                )).scalar_one_or_none()

                if existing_owner is None:
                    session.add(Owner(
                        user_id=dev_id,
                        display_name="Developer",
                        is_active=True,
                    ))
                    seeded_owners += 1
                    logger.info(
                        "Bootstrap: seeded developer user_id=%d into owners table",
                        dev_id,
                    )

    logger.info(
        "Bootstrap complete — %d developer ID(s) configured (%d new user, %d new owner)",
        len(developer_ids),
        seeded_users,
        seeded_owners,
    )

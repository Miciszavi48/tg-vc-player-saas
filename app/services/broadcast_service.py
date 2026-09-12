from __future__ import annotations

import asyncio
import logging

from app.repositories import group_repo, user_repo

logger = logging.getLogger(__name__)

_FLOOD_SLEEP = 0.05
_FLOOD_WAIT_EXTRA = 5


class BroadcastService:

    @staticmethod
    async def broadcast_to_groups(
        bot_client,
        message,
        mode: str = "copy",
    ) -> dict[str, int]:
        groups = await group_repo.get_all_active_groups()
        chat_ids = [g.chat_id for g in groups]
        return await BroadcastService._broadcast(bot_client, message, chat_ids, mode)

    @staticmethod
    async def broadcast_to_channels(
        bot_client,
        message,
        mode: str = "copy",
    ) -> dict[str, int]:
        from app.database.engine import async_session
        from app.database.models import Channel
        from sqlalchemy import select

        async with async_session() as session:
            stmt = select(Channel).where(Channel.status == "active")
            result = await session.execute(stmt)
            channels = list(result.scalars().all())

        chat_ids = [c.chat_id for c in channels]
        return await BroadcastService._broadcast(bot_client, message, chat_ids, mode)

    @staticmethod
    async def broadcast_to_users(
        bot_client,
        message,
        mode: str = "copy",
    ) -> dict[str, int]:
        users = await user_repo.get_all_users()
        chat_ids = [u.user_id for u in users]
        return await BroadcastService._broadcast(bot_client, message, chat_ids, mode)

    @staticmethod
    async def send_to_sudo(
        bot_client,
        message,
        sudo_user_id: int,
    ) -> bool:
        try:
            await bot_client.send_message(sudo_user_id, message)
            return True
        except Exception:
            logger.exception("Failed to send message to sudo %s", sudo_user_id)
            return False

    @staticmethod
    async def _broadcast(
        bot_client,
        message,
        chat_ids: list[int],
        mode: str,
    ) -> dict[str, int]:
        sent = 0
        failed = 0

        for chat_id in chat_ids:
            try:
                if mode == "forward":
                    await message.forward(chat_id)
                else:
                    await message.copy(chat_id)
                sent += 1
            except Exception as exc:
                exc_name = type(exc).__name__
                if "FloodWait" in exc_name:
                    wait_time = getattr(exc, "value", _FLOOD_WAIT_EXTRA)
                    if not isinstance(wait_time, (int, float)):
                        wait_time = _FLOOD_WAIT_EXTRA
                    logger.warning("FloodWait: sleeping %s seconds", wait_time)
                    await asyncio.sleep(wait_time)
                    try:
                        if mode == "forward":
                            await message.forward(chat_id)
                        else:
                            await message.copy(chat_id)
                        sent += 1
                        continue
                    except Exception:
                        pass
                failed += 1
                logger.debug(
                    "Broadcast failed for chat %s: %s",
                    chat_id,
                    type(exc).__name__,
                )

            await asyncio.sleep(_FLOOD_SLEEP)

        return {"sent": sent, "failed": failed}

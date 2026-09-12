from __future__ import annotations

import logging

from app.repositories import force_join_repo
from app.repositories import settings_repo
from app.utils.cache import (
    acquire_lock,
    get_fm_targets_cached,
    invalidate_fm_targets,
    is_fm_ok,
    release_lock,
    set_fm_targets_cached,
    set_fm_ok,
)

logger = logging.getLogger(__name__)


class ForcedMembershipService:

    @staticmethod
    async def is_enabled() -> bool:
        val = await settings_repo.get_bot_setting("force_join_enabled")
        return val in ("1", "true")

    @staticmethod
    async def get_targets_cached() -> list[dict]:
        cached = await get_fm_targets_cached()
        if cached:
            return cached
        targets = await force_join_repo.get_active_targets()
        data = [
            {
                "id": t.id,
                "channel_id": t.channel_id,
                "username": t.channel_username,
                "invite_link": t.invite_link,
                "display_name": t.display_name,
                "chat_type": t.chat_type,
                "verify_status": t.verify_status,
            }
            for t in targets
        ]
        await set_fm_targets_cached(data)
        return data

    @staticmethod
    async def invalidate_cache() -> None:
        await invalidate_fm_targets()

    @staticmethod
    async def check(client, user_id: int) -> list[dict] | None:
        """Return None if all targets joined, or list of missing targets."""
        if not await ForcedMembershipService.is_enabled():
            return None
        if await is_fm_ok(user_id):
            return None

        targets = await ForcedMembershipService.get_targets_cached()
        if not targets:
            return None

        missing = []
        for t in targets:
            try:
                member = await client.get_chat_member(t["channel_id"], user_id)
                if member.status.value not in ("member", "administrator", "creator"):
                    missing.append(t)
            except Exception:
                missing.append(t)

        if not missing:
            await set_fm_ok(user_id)
            return None
        return missing

    @staticmethod
    async def add_target(client, identifier: str, added_by: int) -> dict:
        """Validate via Telegram API, then persist. Returns result dict."""
        try:
            chat = await client.get_chat(identifier)
        except Exception:
            return {"error": "inaccessible"}

        chat_id = chat.id
        username = getattr(chat, "username", None)
        invite = getattr(chat, "invite_link", None)
        title = getattr(chat, "title", None)
        chat_type = chat.type.value if hasattr(chat.type, "value") else str(chat.type)

        verify_status = "ok"
        try:
            bot_me = await client.get_me()
            bot_member = await client.get_chat_member(chat_id, bot_me.id)
            if bot_member.status.value not in ("administrator", "creator"):
                verify_status = "bot_not_admin"
        except Exception:
            verify_status = "bot_not_admin"

        target = await force_join_repo.upsert_target(
            channel_id=chat_id,
            channel_username=username,
            invite_link=invite,
            display_name=title,
            chat_type=chat_type,
            added_by=added_by,
            verify_status=verify_status,
        )
        await ForcedMembershipService.invalidate_cache()
        return {"target": target, "verify_status": verify_status, "title": title}

    @staticmethod
    async def remove_target(channel_id: int) -> None:
        await force_join_repo.deactivate(channel_id)
        await ForcedMembershipService.invalidate_cache()

    @staticmethod
    async def verify_all(client) -> dict[str, int]:
        from app.utils.redis_keys import FM_VERIFY_LOCK
        token = await acquire_lock(FM_VERIFY_LOCK, ttl_ms=30_000)
        if not token:
            return {"error": "already_running"}
        try:
            targets = await force_join_repo.get_all_targets()
            ok = broken = 0
            for t in targets:
                if not t.is_active:
                    continue
                status = await ForcedMembershipService._verify_one(client, t)
                await force_join_repo.update_verify_status(t.id, status)
                if status == "ok":
                    ok += 1
                else:
                    broken += 1
            await ForcedMembershipService.invalidate_cache()
            return {"ok": ok, "broken": broken}
        finally:
            await release_lock(FM_VERIFY_LOCK, token)

    @staticmethod
    async def _verify_one(client, target) -> str:
        try:
            chat = await client.get_chat(target.channel_id)
        except Exception:
            return "inaccessible"
        try:
            bot_me = await client.get_me()
            m = await client.get_chat_member(target.channel_id, bot_me.id)
            if m.status.value not in ("administrator", "creator"):
                return "bot_not_admin"
        except Exception:
            return "bot_not_admin"
        new_un = getattr(chat, "username", None)
        if new_un != target.channel_username:
            await force_join_repo.update_username(target.id, new_un)
        return "ok"

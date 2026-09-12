from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from app.repositories import admin_report_repo, force_join_repo, settings_repo
from app.utils.cache import get_redis
from app.utils.redis_keys import TTL_PANEL_SUMMARY, panel_summary_key


class AdminDashboardService:

    @staticmethod
    async def invalidate_cache(scope: str | None = None) -> None:
        redis = await get_redis()
        if scope is not None:
            await redis.delete(panel_summary_key(scope))
            return

        await redis.delete(
            panel_summary_key("general"),
            panel_summary_key("credit"),
            panel_summary_key("lists"),
            panel_summary_key("users"),
        )

    @staticmethod
    async def _get_cached(scope: str) -> dict | None:
        redis = await get_redis()
        raw = await redis.get(panel_summary_key(scope))
        if not raw:
            return None
        try:
            return json.loads(raw)
        except Exception:
            return None

    @staticmethod
    async def _set_cached(scope: str, payload: dict) -> dict:
        redis = await get_redis()
        await redis.set(
            panel_summary_key(scope),
            json.dumps(payload),
            ex=TTL_PANEL_SUMMARY,
        )
        return payload

    @staticmethod
    async def get_general_summary() -> dict[str, int | bool]:
        cached = await AdminDashboardService._get_cached("general")
        if cached is not None:
            return cached

        payload: dict[str, int | bool] = {
            "force_join_enabled": await settings_repo.get_bot_setting_bool("force_join_enabled"),
            "auto_leave_enabled": await settings_repo.get_bot_setting_bool("auto_leave_enabled"),
            "trial_enabled": await settings_repo.get_bot_setting_bool("trial_enabled"),
            "bot_enabled": await settings_repo.get_bot_setting_bool("bot_enabled", default=True),
            "sudo_panel_enabled": await settings_repo.get_bot_setting_bool(
                "sudo_panel_enabled", default=True
            ),
            "required_channels": await force_join_repo.count_active_targets(),
        }
        return await AdminDashboardService._set_cached("general", payload)

    @staticmethod
    async def get_credit_summary() -> dict[str, int]:
        cached = await AdminDashboardService._get_cached("credit")
        if cached is not None:
            return cached

        since = datetime.now(timezone.utc) - timedelta(hours=24)
        groups = await admin_report_repo.count_active_groups()
        channels = await admin_report_repo.count_active_channels()
        payload = {
            "active_installs": groups + channels,
            "expiring_24h": await admin_report_repo.count_renewal_chats(hours=24),
            "no_credit": await admin_report_repo.count_no_credit_chats(),
            "invoices_24h": await admin_report_repo.count_invoices_since(since),
        }
        return await AdminDashboardService._set_cached("credit", payload)

    @staticmethod
    async def get_lists_summary() -> dict[str, int]:
        cached = await AdminDashboardService._get_cached("lists")
        if cached is not None:
            return cached

        payload = {
            "groups": await admin_report_repo.count_active_groups(),
            "channels": await admin_report_repo.count_active_channels(),
            "no_credit": await admin_report_repo.count_no_credit_chats(),
            "expiring_24h": await admin_report_repo.count_renewal_chats(hours=24),
        }
        return await AdminDashboardService._set_cached("lists", payload)

    @staticmethod
    async def get_users_summary() -> dict[str, int]:
        cached = await AdminDashboardService._get_cached("users")
        if cached is not None:
            return cached

        payload = {
            "owners": await admin_report_repo.count_active_owners(),
            "sudos": await admin_report_repo.count_active_sudos(),
        }
        return await AdminDashboardService._set_cached("users", payload)
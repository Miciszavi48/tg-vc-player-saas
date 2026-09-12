from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import delete, select

from app.database.engine import async_session
from app.database.models import FreeInstallWhitelist, InstallPolicySetting
from app.utils.redis_keys import instance_key

class InstallPolicyService:

    _POLICY_CACHE_KEY = instance_key("install_policy:settings")
    _POLICY_CACHE_TTL = 60

    @staticmethod
    async def compute_install_cost(
        chat_type: str,
        installer_role: str,
        chat_id: int,
    ) -> int:
        """Compute install cost based on policy mode, installer role, and whitelist."""
        if installer_role in ("developer", "owner", "sudo"):
            return 0

        if await InstallPolicyService.is_free_install(chat_id):
            return 0

        policy = await InstallPolicyService.get_policy()
        if policy is None or not getattr(policy, "charge_on_install", False):
            return 0

        mode = getattr(policy, "policy_mode", "free")
        if mode == "free":
            return 0
        elif mode == "paid":
            if chat_type == "channel":
                return getattr(policy, "chan_install_fee_irr", 0) or 0
            return getattr(policy, "group_install_fee_irr", 0) or 0
        elif mode == "hybrid":
            if chat_type == "channel":
                return getattr(policy, "chan_install_fee_irr", 0) or 0
            return 0

        return 0

    @staticmethod
    async def is_free_install(chat_id: int) -> bool:
        async with async_session() as session:
            stmt = select(FreeInstallWhitelist).where(
                FreeInstallWhitelist.chat_id == chat_id
            )
            result = await session.execute(stmt)
            entry = result.scalar_one_or_none()
            if entry is None:
                return False

            if entry.expires_at is not None and entry.expires_at < datetime.now(timezone.utc):
                return False

            return True

    @staticmethod
    async def get_policy() -> InstallPolicySetting | None:
        from app.utils.cache import get_redis
        import json as _json

        try:
            r = await get_redis()
            raw = await r.get(InstallPolicyService._POLICY_CACHE_KEY)
            if raw is not None:
                d = _json.loads(raw)
                policy = InstallPolicySetting(
                    id=d["id"],
                    policy_mode=d["policy_mode"],
                    trial_days=d["trial_days"],
                    charge_on_install=d["charge_on_install"],
                    group_install_fee_irr=d["group_install_fee_irr"],
                    chan_install_fee_irr=d["chan_install_fee_irr"],
                )
                return policy
        except Exception:
            pass

        async with async_session() as session:
            stmt = select(InstallPolicySetting).where(InstallPolicySetting.id == 1)
            result = await session.execute(stmt)
            policy = result.scalar_one_or_none()

        if policy is not None:
            try:
                r = await get_redis()
                await r.set(
                    InstallPolicyService._POLICY_CACHE_KEY,
                    _json.dumps({
                        "id": policy.id,
                        "policy_mode": policy.policy_mode,
                        "trial_days": policy.trial_days,
                        "charge_on_install": policy.charge_on_install,
                        "group_install_fee_irr": policy.group_install_fee_irr,
                        "chan_install_fee_irr": policy.chan_install_fee_irr,
                    }),
                    ex=InstallPolicyService._POLICY_CACHE_TTL,
                )
            except Exception:
                pass

        return policy

    @staticmethod
    async def deduct_install_fee(
        chat_id: int,
        installer_id: int,
        cost: int,
    ) -> bool:
        """Compatibility surface: monetary install fees are disabled by policy."""
        return False

    @staticmethod
    async def determine_installer_role(installer_id: int | None) -> str:
        """Determine the role of the installer: developer, owner, sudo, or other."""
        if installer_id is None:
            return "other"
        from app.utils.bot_guards import is_developer

        if is_developer(installer_id):
            return "developer"

        async with async_session() as session:
            from app.database.models import Owner, Sudo
            owner_stmt = select(Owner).where(
                Owner.user_id == installer_id, Owner.is_active.is_(True)
            )
            result = await session.execute(owner_stmt)
            if result.scalar_one_or_none() is not None:
                return "owner"
            sudo_stmt = select(Sudo).where(
                Sudo.user_id == installer_id, Sudo.is_active.is_(True)
            )
            result = await session.execute(sudo_stmt)
            if result.scalar_one_or_none() is not None:
                return "sudo"
        return "other"

    @staticmethod
    async def update_policy(
        mode: str,
        trial_days: int,
        group_fee: int,
        chan_fee: int,
    ) -> InstallPolicySetting:
        async with async_session() as session:
            async with session.begin():
                stmt = select(InstallPolicySetting).where(
                    InstallPolicySetting.id == 1
                )
                result = await session.execute(stmt)
                policy = result.scalar_one_or_none()

                if policy is None:
                    policy = InstallPolicySetting(
                        id=1,
                        policy_mode=mode,
                        trial_days=trial_days,
                        charge_on_install=(mode != "open"),
                        group_install_fee_irr=0,
                        chan_install_fee_irr=0,
                    )
                    session.add(policy)
                else:
                    policy.policy_mode = mode
                    policy.trial_days = trial_days
                    policy.charge_on_install = mode != "open"
                    policy.group_install_fee_irr = 0
                    policy.chan_install_fee_irr = 0

            await session.refresh(policy)
            return policy

    @staticmethod
    async def add_whitelist(
        chat_id: int,
        chat_type: str = "group",
        reason: str | None = None,
        created_by: int | None = None,
        expires_at: datetime | None = None,
    ) -> FreeInstallWhitelist:
        async with async_session() as session:
            async with session.begin():
                stmt = select(FreeInstallWhitelist).where(
                    FreeInstallWhitelist.chat_id == chat_id
                )
                result = await session.execute(stmt)
                entry = result.scalar_one_or_none()

                if entry is not None:
                    entry.chat_type = chat_type
                    entry.reason = reason
                    entry.created_by = created_by
                    entry.expires_at = expires_at
                else:
                    entry = FreeInstallWhitelist(
                        chat_id=chat_id,
                        chat_type=chat_type,
                        reason=reason,
                        created_by=created_by,
                        expires_at=expires_at,
                    )
                    session.add(entry)

            await session.refresh(entry)
            return entry

    @staticmethod
    async def remove_whitelist(chat_id: int) -> None:
        async with async_session() as session:
            async with session.begin():
                stmt = delete(FreeInstallWhitelist).where(
                    FreeInstallWhitelist.chat_id == chat_id
                )
                await session.execute(stmt)

    @staticmethod
    async def get_whitelist() -> list[FreeInstallWhitelist]:
        async with async_session() as session:
            stmt = select(FreeInstallWhitelist).order_by(
                FreeInstallWhitelist.created_at.desc()
            )
            result = await session.execute(stmt)
            return list(result.scalars().all())

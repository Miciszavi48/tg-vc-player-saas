"""Repository helpers for slash-free manager/group text commands."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, func, or_, select, update

from app.config.settings import settings
from app.database.engine import async_session
from app.database.models import (
    BotSetting,
    CallSecuritySettings,
    ChatSettings,
    CreditHistory,
    Group,
    GroupCredit,
    HelperAccount,
    HelperChatBinding,
    MusicAdmin,
    Owner,
    PlaybackState,
    PlayerDeputy,
    PlayerOwner,
    PlayerVip,
    Playlist,
    Sudo,
    VideoAdmin,
)
from app.utils.helpers import MAX_CREDIT_DAYS, ensure_bounded_int


@dataclass(frozen=True)
class CreditUpdate:
    before: int
    after: int
    status: str


@dataclass(frozen=True)
class InstallSetupSnapshot:
    group: Group | None
    settings: ChatSettings | None
    credit: GroupCredit | None
    helper_id: int | None
    helper_binding_state: str | None
    helper_last_error: str | None
    helper_account_status: str | None
    music_admin_count: int
    video_admin_count: int
    player_owner_count: int
    player_deputy_count: int
    player_vip_count: int


async def get_active_group(chat_id: int) -> Group | None:
    async with async_session() as session:
        result = await session.execute(
            select(Group).where(Group.chat_id == chat_id, Group.status == "active")
        )
        return result.scalar_one_or_none()


async def get_group_any(chat_id: int) -> Group | None:
    async with async_session() as session:
        result = await session.execute(select(Group).where(Group.chat_id == chat_id))
        return result.scalar_one_or_none()


async def install_group(
    chat_id: int,
    chat_title: str | None,
    installed_by: int | None,
) -> tuple[Group, bool]:
    async with async_session() as session:
        async with session.begin():
            result = await session.execute(select(Group).where(Group.chat_id == chat_id))
            group = result.scalar_one_or_none()
            created_or_reactivated = group is None or group.status != "active"
            if group is None:
                group = Group(
                    chat_id=chat_id,
                    chat_title=chat_title,
                    installed_by=installed_by,
                    status="active",
                )
                session.add(group)
            else:
                group.status = "active"
                group.last_activity = datetime.now(timezone.utc)
                if chat_title is not None:
                    group.chat_title = chat_title
                if installed_by is not None and group.installed_by is None:
                    group.installed_by = installed_by

            settings_result = await session.execute(
                select(ChatSettings).where(
                    ChatSettings.chat_id == chat_id,
                    ChatSettings.chat_type == "group",
                )
            )
            if settings_result.scalar_one_or_none() is None:
                session.add(ChatSettings(chat_id=chat_id, chat_type="group"))

        await session.refresh(group)
    from app.utils.cache import invalidate_chat_settings

    await invalidate_chat_settings(chat_id, "group")
    return group, created_or_reactivated


async def ensure_credit_row(
    chat_id: int,
    *,
    days: int,
    operated_by: int | None,
) -> GroupCredit:
    days = ensure_bounded_int(
        days,
        field_name="credit_days",
        min_value=0,
        max_value=MAX_CREDIT_DAYS,
    )
    async with async_session() as session:
        async with session.begin():
            result = await session.execute(
                select(GroupCredit).where(
                    GroupCredit.chat_id == chat_id,
                    GroupCredit.chat_type == "group",
                )
            )
            credit = result.scalar_one_or_none()
            if credit is None:
                now = datetime.now(timezone.utc)
                is_trial = days > 0
                credit = GroupCredit(
                    chat_id=chat_id,
                    chat_type="group",
                    credit_days=days,
                    charged_by=operated_by,
                    total_charged=0,
                    is_trial=is_trial,
                    trial_started_at=now if is_trial else None,
                    trial_expire_at=now + timedelta(days=days) if is_trial else None,
                    status="active" if is_trial else "expired",
                )
                session.add(credit)
                if days > 0:
                    session.add(
                        CreditHistory(
                            chat_id=chat_id,
                            chat_type="group",
                            operation="trial",
                            amount_days=days,
                            operated_by=operated_by,
                            note="manager_text_install",
                        )
                    )
            await session.flush()

        await session.refresh(credit)
    from app.utils.cache import invalidate_credit

    await invalidate_credit(chat_id, "group")
    return credit


async def cleanup_group_management(chat_id: int) -> int | None:
    """Mark a group inactive and clear per-group player state.

    Returns the helper id that was bound before cleanup, if any.
    """
    role_user_ids: list[int] = []
    async with async_session() as session:
        async with session.begin():
            binding_result = await session.execute(
                select(HelperChatBinding.helper_account_id).where(
                    HelperChatBinding.chat_id == chat_id
                )
            )
            helper_id = binding_result.scalar_one_or_none()
            for role_model in (MusicAdmin, PlayerDeputy, PlayerOwner, PlayerVip):
                role_result = await session.execute(
                    select(role_model.user_id).where(role_model.chat_id == chat_id)
                )
                role_user_ids.extend(int(value) for value in role_result.scalars().all())

            await session.execute(
                update(Group).where(Group.chat_id == chat_id).values(status="inactive")
            )
            for model in (
                MusicAdmin,
                VideoAdmin,
                PlayerDeputy,
                PlayerOwner,
                PlayerVip,
                HelperChatBinding,
                PlaybackState,
                Playlist,
            ):
                await session.execute(delete(model).where(model.chat_id == chat_id))
            await session.execute(
                delete(CallSecuritySettings).where(CallSecuritySettings.chat_id == chat_id)
            )
            await session.execute(
                delete(ChatSettings).where(
                    ChatSettings.chat_id == chat_id,
                    ChatSettings.chat_type == "group",
                )
            )
            for prefix in ("group_text_call", "call_stats", "id_command"):
                await session.execute(
                    delete(BotSetting).where(
                        BotSetting.key.like(f"{prefix}:{chat_id}:%")
                    )
                )
    from app.utils.cache import invalidate_chat_settings, invalidate_credit

    await invalidate_chat_settings(chat_id, "group")
    await invalidate_credit(chat_id, "group")
    await _invalidate_role_cache("all", chat_id, role_user_ids)
    return int(helper_id) if helper_id is not None else None


async def add_install_log(
    chat_id: int,
    chat_title: str | None,
    triggered_by: int | None,
    action: str,
) -> None:
    from app.repositories import log_repo

    await log_repo.log_install(
        chat_id=chat_id,
        chat_title=chat_title,
        chat_type="group",
        triggered_by=triggered_by,
        sudo_id=triggered_by,
        action=action,
    )


async def get_credit(chat_id: int) -> GroupCredit | None:
    async with async_session() as session:
        result = await session.execute(
            select(GroupCredit).where(
                GroupCredit.chat_id == chat_id,
                GroupCredit.chat_type == "group",
            )
        )
        return result.scalar_one_or_none()


async def get_install_setup_snapshot(chat_id: int) -> InstallSetupSnapshot:
    async with async_session() as session:
        group = (
            await session.execute(
                select(Group).where(Group.chat_id == chat_id, Group.status == "active")
            )
        ).scalar_one_or_none()
        settings_row = (
            await session.execute(
                select(ChatSettings).where(
                    ChatSettings.chat_id == chat_id,
                    ChatSettings.chat_type == "group",
                )
            )
        ).scalar_one_or_none()
        credit = (
            await session.execute(
                select(GroupCredit).where(
                    GroupCredit.chat_id == chat_id,
                    GroupCredit.chat_type == "group",
                )
            )
        ).scalar_one_or_none()
        helper_row = (
            await session.execute(
                select(
                    HelperChatBinding.helper_account_id,
                    HelperChatBinding.binding_state,
                    HelperChatBinding.last_error,
                    HelperAccount.status,
                )
                .join(
                    HelperAccount,
                    HelperAccount.id == HelperChatBinding.helper_account_id,
                    isouter=True,
                )
                .where(HelperChatBinding.chat_id == chat_id)
            )
        ).one_or_none()

        async def _count(model) -> int:
            conditions = [model.chat_id == chat_id]
            if model is PlayerVip:
                conditions.append(
                    or_(
                        PlayerVip.expires_at.is_(None),
                        PlayerVip.expires_at > datetime.now(timezone.utc),
                    )
                )
            value = (await session.execute(select(func.count()).select_from(model).where(*conditions))).scalar()
            return int(value or 0)

        return InstallSetupSnapshot(
            group=group,
            settings=settings_row,
            credit=credit,
            helper_id=int(helper_row[0]) if helper_row and helper_row[0] is not None else None,
            helper_binding_state=str(helper_row[1]) if helper_row and helper_row[1] is not None else None,
            helper_last_error=str(helper_row[2]) if helper_row and helper_row[2] is not None else None,
            helper_account_status=str(helper_row[3]) if helper_row and helper_row[3] is not None else None,
            music_admin_count=await _count(MusicAdmin),
            video_admin_count=await _count(VideoAdmin),
            player_owner_count=await _count(PlayerOwner),
            player_deputy_count=await _count(PlayerDeputy),
            player_vip_count=await _count(PlayerVip),
        )


async def apply_credit_update(
    chat_id: int,
    *,
    mode: str,
    amount: int | None,
    operated_by: int | None,
) -> CreditUpdate:
    from app.services.credit_service import CreditService

    update = await CreditService.adjust_managed_credit(
        chat_id,
        "group",
        mode=mode,
        amount=amount,
        operated_by=operated_by,
        note="manager_text_command",
    )
    return CreditUpdate(before=update.before, after=update.after, status=update.status)


async def get_bound_helper_id(chat_id: int) -> int | None:
    async with async_session() as session:
        result = await session.execute(
            select(HelperChatBinding.helper_account_id).where(
                HelperChatBinding.chat_id == chat_id
            )
        )
        value = result.scalar_one_or_none()
        return int(value) if value is not None else None


async def promote_role(
    role: str,
    chat_id: int,
    user_id: int,
    *,
    username: str | None,
    display_name: str | None,
    promoted_by: int | None,
    expires_at: datetime | None = None,
) -> bool:
    if role == "vip":
        from app.repositories import admin_repo

        was_active = await admin_repo.is_vip(user_id, chat_id)
        await admin_repo.promote_vip(
            chat_id,
            user_id,
            username=username,
            display_name=display_name,
            promoted_by=promoted_by,
            expires_at=expires_at,
        )
        return not was_active
    model = _role_model(role)
    added = False
    async with async_session() as session:
        async with session.begin():
            result = await session.execute(
                select(model).where(model.chat_id == chat_id, model.user_id == user_id)
            )
            existing = result.scalar_one_or_none()
            if existing is not None:
                return False
            session.add(
                model(
                    chat_id=chat_id,
                    user_id=user_id,
                    username=username,
                    display_name=display_name,
                    promoted_by=promoted_by,
                )
            )
            added = True
    if added:
        await _invalidate_role_cache(role, chat_id, [user_id])
    return added


async def demote_role(role: str, chat_id: int, user_id: int) -> bool:
    model = _role_model(role)
    removed = False
    async with async_session() as session:
        async with session.begin():
            existing = (
                await session.execute(
                    select(model.user_id).where(
                        model.chat_id == chat_id,
                        model.user_id == user_id,
                    )
                )
            ).scalar_one_or_none()
            if existing is None:
                return False
            await session.execute(
                delete(model).where(model.chat_id == chat_id, model.user_id == user_id)
            )
            removed = True
    if removed:
        await _invalidate_role_cache(role, chat_id, [user_id])
    return removed


async def role_exists(role: str, chat_id: int, user_id: int) -> bool:
    if role == "vip":
        from app.repositories import admin_repo

        return await admin_repo.is_vip(user_id, chat_id)
    model = _role_model(role)
    async with async_session() as session:
        result = await session.execute(
            select(model.user_id).where(model.chat_id == chat_id, model.user_id == user_id)
        )
        return result.scalar_one_or_none() is not None


async def list_roles(role: str, chat_id: int) -> list[object]:
    if role == "vip":
        from app.repositories import admin_repo

        return (await admin_repo.get_player_vips_page(chat_id, 0, 1000))[0]
    model = _role_model(role)
    async with async_session() as session:
        result = await session.execute(
            select(model).where(model.chat_id == chat_id).order_by(model.id.asc())
        )
        return list(result.scalars().all())


async def clear_roles(
    role: str,
    chat_id: int,
    *,
    protect_player_owners: bool = True,
    protect_player_deputies: bool = True,
    protect_player_vips: bool = True,
) -> int:
    model = _role_model(role)
    protected = await protected_user_ids(
        chat_id,
        include_player_owners=protect_player_owners,
        include_player_deputies=protect_player_deputies,
        include_player_vips=protect_player_vips,
    )
    async with async_session() as session:
        rows = (
            await session.execute(select(model).where(model.chat_id == chat_id))
        ).scalars().all()
        delete_ids = [row.user_id for row in rows if int(row.user_id) not in protected]
        if not delete_ids:
            return 0
        await session.execute(
            delete(model).where(model.chat_id == chat_id, model.user_id.in_(delete_ids))
        )
        await session.commit()
        await _invalidate_role_cache(role, chat_id, delete_ids)
        return len(delete_ids)


async def protected_user_ids(
    chat_id: int,
    *,
    include_player_owners: bool = True,
    include_player_deputies: bool = True,
    include_player_vips: bool = True,
) -> set[int]:
    protected = set(settings.DEVELOPER_IDS)
    async with async_session() as session:
        for model in (Owner, Sudo):
            stmt = select(model.user_id).where(model.is_active.is_(True))
            result = await session.execute(stmt)
            protected.update(int(value) for value in result.scalars().all())
        for model, include in (
            (PlayerOwner, include_player_owners),
            (PlayerDeputy, include_player_deputies),
            (PlayerVip, include_player_vips),
        ):
            if include:
                stmt = select(model.user_id).where(model.chat_id == chat_id)
                result = await session.execute(stmt)
                protected.update(int(value) for value in result.scalars().all())
    return protected


async def replace_music_admins(chat_id: int, rows: list[dict[str, object]]) -> int:
    affected_user_ids: list[int] = []
    count = 0
    async with async_session() as session:
        async with session.begin():
            existing_result = await session.execute(
                select(MusicAdmin.user_id).where(MusicAdmin.chat_id == chat_id)
            )
            affected_user_ids.extend(int(value) for value in existing_result.scalars().all())
            await session.execute(delete(MusicAdmin).where(MusicAdmin.chat_id == chat_id))
            seen: set[int] = set()
            for row in rows:
                user_id = int(row["user_id"])
                if user_id in seen:
                    continue
                seen.add(user_id)
                session.add(
                    MusicAdmin(
                        chat_id=chat_id,
                        user_id=user_id,
                        username=row.get("username") if isinstance(row.get("username"), str) else None,
                        display_name=row.get("display_name") if isinstance(row.get("display_name"), str) else None,
                        promoted_by=row.get("promoted_by") if isinstance(row.get("promoted_by"), int) else None,
                    )
                )
                count += 1
    affected_user_ids.extend(int(row["user_id"]) for row in rows if "user_id" in row)
    await _invalidate_role_cache("mod", chat_id, affected_user_ids)
    return count


def _role_model(role: str):
    return {
        "owner": PlayerOwner,
        "deputy": PlayerDeputy,
        "mod": MusicAdmin,
        "vip": PlayerVip,
    }[role]


async def _invalidate_role_cache(role: str, chat_id: int, user_ids: list[int]) -> None:
    if not user_ids:
        return
    from app.repositories import admin_repo

    await admin_repo.refresh_player_role_caches(chat_id, [int(user_id) for user_id in user_ids])

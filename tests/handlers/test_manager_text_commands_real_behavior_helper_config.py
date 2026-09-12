from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select

from app.database.engine import async_session
from app.database.models import (
    HelperAccount,
    HelperChatBinding,
    MusicAdmin,
    PlayerDeputy,
    PlayerOwner,
    PlayerVip,
)
from app.repositories import admin_repo, manager_command_repo
from app.services.helper_pool_service import HelperJoinResult, HelperPoolService


class _RecorderBot:
    def __init__(self) -> None:
        self.message_handlers: list = []
        self.callback_handlers: list = []

    def on_message(self, *args, **kwargs):  # noqa: ANN001, ANN002
        def _decorator(fn):
            self.message_handlers.append(fn)
            return fn

        return _decorator

    def on_callback_query(self, *args, **kwargs):  # noqa: ANN001, ANN002
        def _decorator(fn):
            self.callback_handlers.append(fn)
            return fn

        return _decorator


def _handler():
    from app.handlers import manager_text_commands

    bot = _RecorderBot()
    manager_text_commands.register(bot, None)
    return bot.message_handlers[0]


def _message(text: str, *, chat_id: int, user_id: int = 123456789):
    msg = SimpleNamespace()
    msg.chat = SimpleNamespace(id=chat_id, title=f"Group {chat_id}", type=SimpleNamespace(value="supergroup"))
    msg.from_user = SimpleNamespace(id=user_id, username=f"user{user_id}", first_name=f"User {user_id}")
    msg.text = text
    msg.caption = None
    msg.entities = []
    msg.reply_to_message = None
    msg.reply = AsyncMock()
    msg.reply_text = AsyncMock()
    msg.continue_propagation = lambda: None
    return msg


async def _install(chat_id: int) -> None:
    await _handler()(AsyncMock(), _message("AddMusic", chat_id=chat_id))


async def _create_helper(suffix: int) -> HelperAccount:
    async with async_session() as session:
        helper = HelperAccount(
            phone=f"+168{suffix}",
            tg_user_id=168000000 + suffix,
            display_name=f"Helper {suffix}",
            username=f"helper{suffix}",
            session_string_enc=f"enc-{suffix}",
            status="active",
            max_concurrent_calls=50,
            current_active_calls=0,
        )
        session.add(helper)
        await session.commit()
        await session.refresh(helper)
        return helper


async def _binding(chat_id: int) -> HelperChatBinding | None:
    async with async_session() as session:
        return (
            await session.execute(
                select(HelperChatBinding).where(HelperChatBinding.chat_id == chat_id)
            )
        ).scalar_one_or_none()


async def _admin_members(users: list[SimpleNamespace]):
    for user in users:
        yield SimpleNamespace(user=user)


@pytest.mark.asyncio
async def test_addhelper_real_service_joins_persists_and_later_pool_reuses_binding():
    chat_id = -10067301
    await _install(chat_id)
    await _create_helper(67301)
    msg = _message("افزودن کمکی موزیک", chat_id=chat_id)

    with (
        patch(
            "app.services.helper_pool_service.HelperPoolService.ensure_helper_joined_detailed",
            AsyncMock(return_value=HelperJoinResult(ok=True, reason="joined")),
        ) as ensure_joined,
        patch(
            "app.services.helper_admin_service.ensure_helper_call_admin",
            AsyncMock(return_value=SimpleNamespace(ok=True, reason="promoted")),
        ),
        patch("app.repositories.helper_event_repo.log_event", AsyncMock()),
    ):
        await _handler()(AsyncMock(), msg)

    binding = await _binding(chat_id)
    assert binding is not None
    ensure_joined.assert_awaited()
    reserved = await HelperPoolService.reserve_best_helper(chat_id)
    assert reserved is not None
    assert reserved.id == binding.helper_account_id
    await HelperPoolService.release_helper_reservation(reserved.id)


@pytest.mark.asyncio
async def test_addhelper_existing_binding_is_idempotent_and_not_cross_group():
    chat_a = -10067302
    chat_b = -10067303
    await _install(chat_a)
    await _install(chat_b)
    helper = await _create_helper(67302)
    await HelperPoolService.bind_chat_to_helper(chat_a, helper.id)

    msg = _message("AddhelperMusic", chat_id=chat_a)
    with (
        patch(
            "app.services.helper_pool_service.HelperPoolService.ensure_helper_joined",
            AsyncMock(return_value=True),
        ) as ensure_joined,
        patch(
            "app.services.helper_admin_service.ensure_helper_call_admin",
            AsyncMock(return_value=SimpleNamespace(ok=True, reason="already_admin")),
        ),
        patch("app.repositories.helper_event_repo.log_event", AsyncMock()),
    ):
        await _handler()(AsyncMock(), msg)

    assert (await _binding(chat_a)).helper_account_id == helper.id
    assert await _binding(chat_b) is None
    ensure_joined.assert_awaited_once_with(helper.id, chat_a)
    assert "از قبل" in msg.reply.await_args.args[0]


@pytest.mark.asyncio
async def test_addhelper_existing_binding_promote_failure_is_visible_without_unbinding():
    chat_id = -10067308
    await _install(chat_id)
    helper = await _create_helper(67308)
    await HelperPoolService.bind_chat_to_helper(chat_id, helper.id)

    msg = _message("AddhelperMusic", chat_id=chat_id)
    with (
        patch(
            "app.services.helper_pool_service.HelperPoolService.ensure_helper_joined",
            AsyncMock(return_value=True),
        ),
        patch(
            "app.services.helper_admin_service.ensure_helper_call_admin",
            AsyncMock(return_value=SimpleNamespace(ok=False, reason="promote_failed")),
        ),
        patch("app.repositories.helper_event_repo.log_event", AsyncMock()),
    ):
        await _handler()(AsyncMock(), msg)

    assert (await _binding(chat_id)).helper_account_id == helper.id
    assert "ادمین" in msg.reply.await_args.args[0]


@pytest.mark.asyncio
async def test_addhelper_no_helper_or_join_failure_returns_visible_error_without_binding():
    chat_id = -10067304
    await _install(chat_id)
    no_helper = _message("AddhelperM", chat_id=chat_id)
    with patch(
        "app.services.helper_pool_service.HelperPoolService.reserve_best_helper",
        AsyncMock(return_value=None),
    ):
        await _handler()(AsyncMock(), no_helper)
    assert await _binding(chat_id) is None
    assert "در دسترس نیست" in no_helper.reply.await_args.args[0]

    helper = await _create_helper(67304)
    failed = _message("افزودن کمکی پلیر", chat_id=chat_id)
    with (
        patch(
            "app.services.helper_pool_service.HelperPoolService.reserve_best_helper",
            AsyncMock(side_effect=[SimpleNamespace(id=helper.id), None]),
        ),
        patch(
            "app.services.helper_pool_service.HelperPoolService.ensure_helper_joined_detailed",
            AsyncMock(return_value=HelperJoinResult(ok=False, reason="join_failed")),
        ),
        patch("app.services.helper_pool_service.HelperPoolService.get_all_helpers", AsyncMock(return_value=[])),
        patch("app.services.helper_pool_service.HelperPoolService._get_helper_row", AsyncMock(return_value=None)),
        patch(
            "app.services.helper_pool_service.HelperPoolService.release_helper_reservation",
            AsyncMock(),
        ),
        patch(
            "app.services.helper_pool_service.HelperPoolService.quarantine_helper",
            AsyncMock(),
        ),
        patch("app.repositories.helper_event_repo.log_event", AsyncMock()),
    ):
        await _handler()(AsyncMock(), failed)
    assert await _binding(chat_id) is None
    assert "ناموفق" in failed.reply.await_args.args[0]


@pytest.mark.asyncio
async def test_config_replaces_old_admins_skips_bots_preserves_owners_and_updates_permission_helper():
    chat_id = -10067305
    await _install(chat_id)
    async with async_session() as session:
        session.add(PlayerOwner(chat_id=chat_id, user_id=673501, username="owner"))
        session.add(PlayerDeputy(chat_id=chat_id, user_id=673504, username="deputy"))
        session.add(PlayerVip(chat_id=chat_id, user_id=673505, username="vip"))
        session.add(MusicAdmin(chat_id=chat_id, user_id=673599, username="old"))
        await session.commit()

    users = [
        SimpleNamespace(id=673501, username="owner", first_name="Owner", is_bot=False),
        SimpleNamespace(id=673502, username="admin", first_name="Admin", is_bot=False),
        SimpleNamespace(id=673503, username="bot", first_name="Bot", is_bot=True),
    ]
    client = AsyncMock()
    client.get_chat_members = lambda chat_id_arg, filter=None: _admin_members(users)
    msg = _message("ConfigPlayer", chat_id=chat_id)

    await _handler()(client, msg)

    async with async_session() as session:
        admins = (
            await session.execute(select(MusicAdmin.user_id).where(MusicAdmin.chat_id == chat_id))
        ).scalars().all()
        owners = (
            await session.execute(select(PlayerOwner.user_id).where(PlayerOwner.chat_id == chat_id))
        ).scalars().all()
    assert set(admins) == {673501, 673502}
    assert set(owners) == {673501}
    assert await admin_repo.is_music_admin_or_above(673502, chat_id) is True
    assert await admin_repo.is_music_admin_or_above(673599, chat_id) is False
    text = msg.reply.await_args.args[0]
    assert "⊹ مدیران:" in text
    assert "⊹ مالک‌ها:" in text
    assert "⊹ معاون‌ها:" in text
    assert "⊹ ویژه‌ها:" in text
    assert "@owner" in text
    assert "@deputy" in text
    assert "@vip" in text
    assert "@admin" in text
    assert "⊹ واردشده: 2" in text


@pytest.mark.asyncio
async def test_config_admin_fetch_failure_and_empty_list_are_visible():
    chat_id = -10067306
    await _install(chat_id)

    def _bad_members(chat_id_arg: int, filter=None):  # noqa: ANN001, ARG001
        raise RuntimeError("telegram down")

    bad_client = AsyncMock()
    bad_client.get_chat_members = _bad_members
    failed = _message("پیکربندی موزیک", chat_id=chat_id)
    await _handler()(bad_client, failed)
    assert "دریافت لیست" in failed.reply.await_args.args[0]

    empty_client = AsyncMock()
    empty_client.get_chat_members = lambda chat_id_arg, filter=None: _admin_members([])
    empty = _message("ConfigMusic", chat_id=chat_id)
    await _handler()(empty_client, empty)
    assert "پیدا نشد" in empty.reply.await_args.args[0]


@pytest.mark.asyncio
async def test_addhelper_unmanaged_group_does_not_bind_any_helper():
    chat_id = -10067307
    msg = _message("AddhelperMusic", chat_id=chat_id)

    await _handler()(AsyncMock(), msg)

    assert await manager_command_repo.get_active_group(chat_id) is None
    assert await _binding(chat_id) is None
    assert "فعال نیست" in msg.reply.await_args.args[0]

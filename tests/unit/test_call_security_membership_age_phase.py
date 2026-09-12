"""Call Security membership-age tracking and auto-unmute decisions."""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

os.environ.setdefault("TEST_MODE", "1")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./.pytest-test-mode.db")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/15")
os.environ.setdefault("BOT_TOKEN", "test")
os.environ.setdefault("API_ID", "12345")
os.environ.setdefault("API_HASH", "testhash")
os.environ.setdefault("DEVELOPER_ID", "123456789")


class _RecorderBot:
    def __init__(self) -> None:
        self.chat_member_handlers: list = []
        self.message_handlers: list = []

    def on_chat_member_updated(self, *args, **kwargs):  # noqa: ANN001, ANN002
        def _decorator(fn):
            self.chat_member_handlers.append(fn)
            return fn

        return _decorator

    def on_message(self, *args, **kwargs):  # noqa: ANN001, ANN002
        def _decorator(fn):
            self.message_handlers.append(fn)
            return fn

        return _decorator


def _member_update(
    *,
    chat_id: int,
    user_id: int,
    new_status: str,
    old_status: str | None,
    event_at: datetime,
):
    old = (
        SimpleNamespace(status=SimpleNamespace(value=old_status))
        if old_status is not None
        else None
    )
    return SimpleNamespace(
        chat=SimpleNamespace(id=chat_id, type=SimpleNamespace(value="supergroup")),
        from_user=SimpleNamespace(id=500),
        date=event_at,
        old_chat_member=old,
        new_chat_member=SimpleNamespace(
            user=SimpleNamespace(id=user_id),
            status=SimpleNamespace(value=new_status),
        ),
    )


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


@pytest.mark.asyncio
async def test_membership_join_is_recorded_from_chat_member_update():
    from app.handlers import install

    bot = _RecorderBot()
    install.register(bot, None)
    handler = bot.chat_member_handlers[0]
    event_at = datetime(2026, 6, 1, tzinfo=timezone.utc)
    update = _member_update(
        chat_id=-990025001,
        user_id=111,
        new_status="member",
        old_status=None,
        event_at=event_at,
    )
    client = SimpleNamespace(get_me=AsyncMock(return_value=SimpleNamespace(id=999)))

    with patch(
        "app.handlers.install.group_membership_age_service.record_member_join",
        AsyncMock(),
    ) as record_join:
        await handler(client, update)

    record_join.assert_awaited_once_with(
        -990025001,
        111,
        event_at,
        source="chat_member_update",
    )


@pytest.mark.asyncio
async def test_leave_sets_left_at():
    from app.repositories import group_membership_repo

    chat_id = -990025002
    user_id = 222
    joined = datetime(2026, 6, 1, tzinfo=timezone.utc)
    left = datetime(2026, 6, 3, tzinfo=timezone.utc)
    await group_membership_repo.record_member_join(chat_id, user_id, joined)
    await group_membership_repo.record_member_left(chat_id, user_id, left)

    row = await group_membership_repo.get_membership(chat_id, user_id)
    assert row is not None
    assert row.left_at is not None
    assert _utc(row.left_at) == left


@pytest.mark.asyncio
async def test_rejoin_resets_joined_at_and_clears_left_at():
    from app.repositories import group_membership_repo

    chat_id = -990025003
    user_id = 333
    first_join = datetime(2026, 6, 1, tzinfo=timezone.utc)
    left = datetime(2026, 6, 2, tzinfo=timezone.utc)
    rejoin = datetime(2026, 6, 8, tzinfo=timezone.utc)
    await group_membership_repo.record_member_join(chat_id, user_id, first_join)
    await group_membership_repo.record_member_left(chat_id, user_id, left)
    await group_membership_repo.record_member_join(chat_id, user_id, rejoin)

    row = await group_membership_repo.get_membership(chat_id, user_id)
    assert row is not None
    assert _utc(row.joined_at) == rejoin
    assert row.left_at is None


@pytest.mark.asyncio
async def test_first_seen_unknown_member_is_not_treated_as_old():
    from app.services import group_membership_age_service

    chat_id = -990025004
    user_id = 444
    seen = datetime(2026, 6, 1, tzinfo=timezone.utc)
    await group_membership_age_service.record_member_seen(chat_id, user_id, seen)

    assert await group_membership_age_service.is_membership_old_enough(
        chat_id,
        user_id,
        7,
        now=seen + timedelta(days=6),
    ) is False


@pytest.mark.asyncio
async def test_user_joined_8_days_ago_passes_threshold_7():
    from app.services import group_membership_age_service

    chat_id = -990025005
    user_id = 555
    now = datetime(2026, 6, 9, tzinfo=timezone.utc)
    await group_membership_age_service.record_member_join(
        chat_id,
        user_id,
        now - timedelta(days=8),
    )

    assert await group_membership_age_service.is_membership_old_enough(
        chat_id,
        user_id,
        7,
        now=now,
    ) is True


@pytest.mark.asyncio
async def test_user_joined_6_days_ago_fails_threshold_7():
    from app.services import group_membership_age_service

    chat_id = -990025006
    user_id = 666
    now = datetime(2026, 6, 9, tzinfo=timezone.utc)
    await group_membership_age_service.record_member_join(
        chat_id,
        user_id,
        now - timedelta(days=6),
    )

    assert await group_membership_age_service.is_membership_old_enough(
        chat_id,
        user_id,
        7,
        now=now,
    ) is False


@pytest.mark.asyncio
async def test_unknown_membership_age_fails_closed():
    from app.services import group_membership_age_service

    assert await group_membership_age_service.get_membership_age_days(
        -990025007,
        777,
    ) is None
    assert await group_membership_age_service.is_membership_old_enough(
        -990025007,
        777,
        7,
    ) is False


@pytest.mark.asyncio
async def test_privileged_user_bypasses_membership_age():
    from app.services import call_security_service

    settings = SimpleNamespace(membership_age_days=7)
    with patch(
        "app.services.call_security_service.group_membership_age_service.is_membership_old_enough",
        AsyncMock(return_value=False),
    ) as old_enough:
        result = await call_security_service.should_auto_unmute(
            888,
            -990025008,
            settings,
            privileged=True,
        )

    assert result is True
    old_enough.assert_not_awaited()


def test_call_security_service_does_not_use_telegram_account_age_estimator():
    source = Path("app/services/call_security_service.py").read_text(encoding="utf-8")
    assert "telegram_account_age" not in source
    assert "is_account_age_at_least" not in source


def test_ui_label_says_membership_age_not_account_age():
    from app.utils.i18n import t

    assert "قدمت عضویت" in t("fa", "call_security.feature_membership_age")
    assert "قدمت اکانت" not in t("fa", "call_security.btn_membership_age", days=7)


def test_migration_and_model_fields_added():
    import importlib

    from app.database.models import CallSecuritySettings, GroupMemberMembership

    mig = importlib.import_module(
        "app.database.migrations.versions.0025_group_member_membership_tracking"
    )
    assert mig.revision == "0025_group_member_membership"
    assert CallSecuritySettings.__table__.c.membership_age_days.default.arg == 7
    assert GroupMemberMembership.__tablename__ == "group_member_memberships"


def test_no_production_database_required():
    assert os.environ["DATABASE_URL"].startswith("sqlite+aiosqlite:")

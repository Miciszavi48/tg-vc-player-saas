from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import ChatSettings, Group, GroupCredit, OwnerTextLink, User


@pytest.mark.asyncio
class TestModels:
    async def test_create_user(self, db_session: AsyncSession):
        user = User(user_id=100001, username="testuser", first_name="Test")
        db_session.add(user)
        await db_session.flush()

        stmt = select(User).where(User.user_id == 100001)
        result = await db_session.execute(stmt)
        fetched = result.scalar_one()
        assert fetched.username == "testuser"
        assert fetched.first_name == "Test"
        assert fetched.is_banned is False

    async def test_create_group(self, db_session: AsyncSession):
        group = Group(chat_id=-100200, chat_title="Test Group", status="active")
        db_session.add(group)
        await db_session.flush()

        stmt = select(Group).where(Group.chat_id == -100200)
        result = await db_session.execute(stmt)
        fetched = result.scalar_one()
        assert fetched.chat_title == "Test Group"
        assert fetched.status == "active"

    async def test_group_credit(self, db_session: AsyncSession):
        credit = GroupCredit(
            chat_id=-100300,
            chat_type="group",
            credit_days=30,
            total_charged=30,
            status="active",
        )
        db_session.add(credit)
        await db_session.flush()

        stmt = select(GroupCredit).where(GroupCredit.chat_id == -100300)
        result = await db_session.execute(stmt)
        fetched = result.scalar_one()
        assert fetched.credit_days == 30
        assert fetched.chat_type == "group"
        assert fetched.status == "active"

    async def test_chat_settings_defaults(self, db_session: AsyncSession):
        cs = ChatSettings(chat_id=-100400, chat_type="group")
        db_session.add(cs)
        await db_session.flush()

        stmt = select(ChatSettings).where(ChatSettings.chat_id == -100400)
        result = await db_session.execute(stmt)
        fetched = result.scalar_one()
        assert fetched.music_enabled is True
        assert fetched.video_enabled is True
        assert fetched.force_join_enabled is False
        assert fetched.language == "fa"
        assert fetched.default_media_type == "audio"
        assert fetched.show_track_id is False
        assert fetched.show_cover is True
        assert fetched.show_now_playing_text is True

    async def test_owner_text_link_model_columns(self, db_session: AsyncSession):
        row = OwnerTextLink(
            owner_user_id=800_001,
            key="start_text",
            kind="text",
            value="hello",
        )
        db_session.add(row)
        await db_session.flush()

        stmt = select(OwnerTextLink).where(OwnerTextLink.owner_user_id == 800_001)
        result = await db_session.execute(stmt)
        fetched = result.scalar_one()
        assert fetched.key == "start_text"
        assert fetched.kind == "text"
        assert fetched.value == "hello"

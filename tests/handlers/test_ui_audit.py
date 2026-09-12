"""Phase 3: UI/Callback audit tests — behavioral, not shallow.

Tests that every CB constant in a keyboard has a handler,
every screen has Back/Home, pagination works, and features
actually update state.
"""
from __future__ import annotations

import asyncio
import os
import re
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from sqlalchemy import NullPool, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.database.models import (
    Base,
    Favorite,
    Sudo,
)
from app.services.call_service import CallService
from app.utils.i18n import t
from app.utils.ui import CB, KeyboardFactory

DATABASE_URL = os.getenv("TEST_DATABASE_URL") or os.environ["DATABASE_URL"]


def _run_async(coro):
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    return loop.run_until_complete(coro)


@pytest.fixture
def _patch_session():
    _engine = create_async_engine(DATABASE_URL, poolclass=NullPool)
    _session = async_sessionmaker(_engine, class_=AsyncSession, expire_on_commit=False)
    async def _create_schema():
        async with _engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    _run_async(_create_schema())
    import app.utils.cache as cache_mod
    old_redis = cache_mod._redis
    cache_mod._redis = None
    with patch.dict(
        sys.modules["app.database.engine"].__dict__,
        {"async_session": _session, "engine": _engine},
    ):
        yield _session
    cache_mod._redis = old_redis
    _run_async(_engine.dispose())


# ═══════════════════════════════════════════════════════════════════════
# CB Coverage: every CB used in a keyboard has a handler
# ═══════════════════════════════════════════════════════════════════════

class TestCallbackCoverage:

    def _get_all_handler_cb_patterns(self) -> set[str]:
        """Scan all handler files for registered callback_data patterns."""
        patterns = set()
        handler_dir = Path("app/handlers")
        for py_file in handler_dir.glob("*.py"):
            content = py_file.read_text()
            for m in re.finditer(r"on_callback_query\(filters\.regex\(f\"\^{CB\['(\w+)'\]}\$\"\)", content):
                patterns.add(m.group(1))
            for m in re.finditer(r'on_callback_query\(filters\.regex\(f\"\^{CB\[\"(\w+)\"\]}\$\"\)', content):
                patterns.add(m.group(1))
            for m in re.finditer(r'on_callback_query\(filters\.regex\(r"\^grp:set:"\)', content):
                for k in CB:
                    if k.startswith("GRP_") and CB[k].startswith("grp:set:"):
                        patterns.add(k)
            for m in re.finditer(r'on_callback_query\(filters\.regex\(f"\^{CB\[\'(\w+)\'\]}\$"\s*\)', content):
                patterns.add(m.group(1))
        return patterns

    def _get_keyboard_cb_values(self) -> set[str]:
        """Get all callback_data values that appear in any keyboard builder."""
        values = set()
        ui_content = Path("app/utils/ui.py").read_text()
        for m in re.finditer(r'CB\["(\w+)"\]', ui_content):
            cb_name = m.group(1)
            if cb_name in CB:
                values.add(cb_name)
        return values

    def test_all_keyboard_cbs_have_handlers(self):
        """Every CB constant rendered in a keyboard must have a handler."""
        keyboard_cbs = self._get_keyboard_cb_values()
        handler_cbs = self._get_all_handler_cb_patterns()

        need_handler = keyboard_cbs
        missing = need_handler - handler_cbs

        gp_content = Path("app/handlers/group_panel.py").read_text()
        grp_set_handled = (
            "_GROUP_SETTING_TOGGLE_REGEX" in gp_content
            or ("grp:set:" in gp_content and "on_callback_query" in gp_content)
        )
        if grp_set_handled:
            missing -= {k for k in missing if k.startswith("GRP_") and CB[k].startswith("grp:set:")}

        grp_mgmt = {
            "GRP_OWNERS_LIST",
            "GRP_DEPUTIES_LIST",
            "GRP_ADMINS_LIST",
            "GRP_VIP_LIST",
            "GRP_CLEAR_ALL",
        }
        for g in grp_mgmt:
            if CB[g] in gp_content:
                missing.discard(g)

        grp_others = {"GRP_HELP", "GRP_MANAGEMENT", "GRP_SETTINGS", "GRP_SUPPORT",
                       "GRP_PROMOTE_DEMOTE", "GRP_PLAY_COMMANDS", "GRP_GENERAL_COMMANDS",
                       "GRP_MANAGER_COMMANDS", "GRP_CALL_COMMANDS",
                       "GRP_SUPPORT_REQUEST", "GRP_CREATOR", "GRP_SUDO",
                       "GRP_GUIDE_CHANNEL", "GRP_SUPPORT_GROUP", "GRP_DEFAULT_MEDIA_TYPE"}
        for g in grp_others:
            if g in CB and CB[g] in gp_content:
                missing.discard(g)

        bc_content = Path("app/handlers/broadcast_panel.py").read_text()
        if "_BC_CANCEL_CONFIRM_RE" in bc_content:
            missing.discard("BC_CANCEL_CONFIRM_PREFIX")
        if "_BC_CANCEL_ABORT_RE" in bc_content:
            missing.discard("BC_CANCEL_ABORT_PREFIX")

        dp_content = Path("app/handlers/dev_panel.py").read_text()
        for key in (
            "DEV_OWNER_REMOVE_EXEC_PREFIX",
            "DEV_OWNER_REMOVE_ABORT_PREFIX",
            "DEV_SUDO_REMOVE_EXEC_PREFIX",
            "DEV_SUDO_REMOVE_ABORT_PREFIX",
            "DEV_TEXT_CLEAR_EXEC_PREFIX",
            "DEV_TEXT_CLEAR_ABORT_PREFIX",
        ):
            if CB[key] in dp_content:
                missing.discard(key)

        help_content = Path("app/handlers/help_center.py").read_text(encoding="utf-8")
        if "handle_help_callback" in help_content and "_register_help_callback_routes" in help_content:
            missing -= {key for key in missing if key.startswith("HELP_")}

        playback_dispatcher = Path("app/services/playback_callback_dispatcher.py").read_text(encoding="utf-8")
        if "CALLBACK_TO_ACTION" in playback_dispatcher:
            missing -= {key for key in missing if key.startswith("PB_") and CB[key] in playback_dispatcher}

        if missing:
            pytest.fail(f"CB constants in keyboards but no handler: {sorted(missing)}")

    def test_no_localized_callback_data(self):
        """All callback_data values must be ASCII."""
        for name, value in CB.items():
            assert value.isascii(), f"CB[{name}] = {value!r} contains non-ASCII"

    def test_cb_values_are_unique(self):
        """No two CB constants share the same callback_data value."""
        seen = {}
        for name, value in CB.items():
            assert value not in seen, f"Duplicate CB value {value!r}: {name} and {seen[value]}"
            seen[value] = name


# ═══════════════════════════════════════════════════════════════════════
# UX: Back/Home on every keyboard
# ═══════════════════════════════════════════════════════════════════════

class TestBackHomePresence:

    def _kb_has_nav(self, kb, nav_data: str) -> bool:
        for row in kb.inline_keyboard:
            for btn in row:
                if btn.callback_data == nav_data:
                    return True
        return False

    def test_dev_panel_has_back_to_start(self):
        kb = KeyboardFactory.developer_panel("fa")
        assert self._kb_has_nav(kb, CB["NAV_START"])
        assert not self._kb_has_nav(kb, CB["NAV_CLOSE"])

    def test_owner_panel_has_back_to_start(self):
        kb = KeyboardFactory.owner_panel("fa")
        assert self._kb_has_nav(kb, CB["NAV_START"])
        assert not self._kb_has_nav(kb, CB["NAV_CLOSE"])

    def test_sudo_panel_has_back_to_start(self):
        kb = KeyboardFactory.sudo_panel("fa")
        assert self._kb_has_nav(kb, CB["NAV_START"])
        assert not self._kb_has_nav(kb, CB["NAV_CLOSE"])

    def test_group_panel_has_close(self):
        kb = KeyboardFactory.group_panel("fa")
        assert self._kb_has_nav(kb, CB["NAV_CLOSE"])

    def test_playback_type_has_back(self):
        kb = KeyboardFactory.playback_type_menu("fa")
        assert self._kb_has_nav(kb, CB["NAV_BACK"])

    def test_now_playing_has_stop(self):
        kb = KeyboardFactory.now_playing_controls("fa", repeat_on=False)
        assert self._kb_has_nav(kb, CB["PB_STOP"])

    def test_back_button_has_back(self):
        kb = KeyboardFactory.back_button("fa")
        assert self._kb_has_nav(kb, CB["NAV_BACK"])

    def test_install_policy_has_back(self):
        kb = KeyboardFactory.install_policy_panel("fa")
        assert self._kb_has_nav(kb, CB["NAV_BACK"])

    def test_satellite_has_back(self):
        channels = [{"id": "t1", "name": "T1", "url": "http://x"}]
        kb = KeyboardFactory.satellite_channels_menu("fa", channels)
        assert self._kb_has_nav(kb, CB["NAV_BACK"])

    def test_post_install_has_close(self):
        kb = KeyboardFactory.post_install_panel("fa", 3, None, "https://t.me/guide")
        assert self._kb_has_nav(kb, CB["NAV_CLOSE"])

    def test_sudo_leave_confirm_has_cancel(self):
        kb = KeyboardFactory.sudo_leave_confirm("fa", 123)
        assert self._kb_has_nav(kb, f"{CB['SUDO_LEAVE_CANCEL_PREFIX']}123")


# ═══════════════════════════════════════════════════════════════════════
# Pagination
# ═══════════════════════════════════════════════════════════════════════

class TestPagination:

    def test_satellite_pagination_next(self):
        channels = [{"id": f"c{i}", "name": f"C{i}", "url": f"http://x/{i}"} for i in range(20)]
        kb = KeyboardFactory.satellite_channels_menu("fa", channels, page=0, per_page=8)
        all_cb = [btn.callback_data for row in kb.inline_keyboard for btn in row if btn.callback_data]
        assert any("pb:sat:page:1" in cb for cb in all_cb)

    def test_satellite_pagination_prev(self):
        channels = [{"id": f"c{i}", "name": f"C{i}", "url": f"http://x/{i}"} for i in range(20)]
        kb = KeyboardFactory.satellite_channels_menu("fa", channels, page=2, per_page=8)
        all_cb = [btn.callback_data for row in kb.inline_keyboard for btn in row if btn.callback_data]
        assert any("pb:sat:page:1" in cb for cb in all_cb)

    def test_no_prev_on_first_page(self):
        channels = [{"id": f"c{i}", "name": f"C{i}", "url": f"http://x/{i}"} for i in range(20)]
        kb = KeyboardFactory.satellite_channels_menu("fa", channels, page=0, per_page=8)
        all_cb = [btn.callback_data for row in kb.inline_keyboard for btn in row if btn.callback_data]
        assert not any("pb:sat:page:-" in str(cb) for cb in all_cb)


# ═══════════════════════════════════════════════════════════════════════
# Feature: Repeat Toggle
# ═══════════════════════════════════════════════════════════════════════

class TestRepeatToggle:

    def test_set_and_get_repeat_state(self):
        CallService.set_repeat_state(99999, True)
        assert CallService.get_repeat_state(99999) is True
        CallService.set_repeat_state(99999, False)
        assert CallService.get_repeat_state(99999) is False

    def test_default_repeat_is_false(self):
        assert CallService.get_repeat_state(88888) is False


# ═══════════════════════════════════════════════════════════════════════
# Feature: Favorites Persistence
# ═══════════════════════════════════════════════════════════════════════

class TestFavoritesPersistence:

    async def test_add_favorite_to_db(self, _patch_session):
        user_id = 7777001
        chat_id = -100777001
        title = "Test Track"

        async with _patch_session() as session:
            async with session.begin():
                await session.execute(text(
                    "DELETE FROM favorites WHERE user_id = :uid"
                ), {"uid": user_id})

        async with _patch_session() as session:
            async with session.begin():
                fav = Favorite(
                    user_id=user_id, chat_id=chat_id,
                    title=title, media_type="audio",
                )
                session.add(fav)

        async with _patch_session() as session:
            stmt = select(Favorite).where(Favorite.user_id == user_id)
            result = await session.execute(stmt)
            favs = list(result.scalars().all())
            assert len(favs) >= 1
            assert favs[0].title == title

    async def test_favorites_list_returns_data(self, _patch_session):
        user_id = 7777002
        async with _patch_session() as session:
            async with session.begin():
                await session.execute(text(
                    "DELETE FROM favorites WHERE user_id = :uid"
                ), {"uid": user_id})
                session.add(Favorite(
                    user_id=user_id, chat_id=-100777002,
                    title="Track A", media_type="audio",
                ))
                session.add(Favorite(
                    user_id=user_id, chat_id=-100777002,
                    title="Track B", media_type="audio",
                ))

        async with _patch_session() as session:
            stmt = (
                select(Favorite)
                .where(Favorite.user_id == user_id)
                .order_by(Favorite.added_at.desc())
            )
            result = await session.execute(stmt)
            favs = list(result.scalars().all())
            assert len(favs) == 2


# ═══════════════════════════════════════════════════════════════════════
# Feature: Sudo Link Management
# ═══════════════════════════════════════════════════════════════════════

class TestSudoLinkDB:

    async def test_set_sudo_link(self, _patch_session):
        uid = 8888001
        async with _patch_session() as session:
            async with session.begin():
                await session.execute(text("DELETE FROM sudos WHERE user_id = :u"), {"u": uid})
                session.add(Sudo(user_id=uid, username="test_link", is_active=True))

        async with _patch_session() as session:
            async with session.begin():
                stmt = select(Sudo).where(Sudo.user_id == uid)
                result = await session.execute(stmt)
                sudo = result.scalar_one()
                sudo.sudo_link = "https://t.me/test_link"

        async with _patch_session() as session:
            result = await session.execute(select(Sudo).where(Sudo.user_id == uid))
            sudo = result.scalar_one()
            assert sudo.sudo_link == "https://t.me/test_link"

    async def test_remove_sudo_link(self, _patch_session):
        uid = 8888002
        async with _patch_session() as session:
            async with session.begin():
                await session.execute(text("DELETE FROM sudos WHERE user_id = :u"), {"u": uid})
                session.add(Sudo(user_id=uid, username="test_rm", is_active=True, sudo_link="https://t.me/x"))

        async with _patch_session() as session:
            async with session.begin():
                result = await session.execute(select(Sudo).where(Sudo.user_id == uid))
                sudo = result.scalar_one()
                sudo.sudo_link = None

        async with _patch_session() as session:
            result = await session.execute(select(Sudo).where(Sudo.user_id == uid))
            sudo = result.scalar_one()
            assert sudo.sudo_link is None

    async def test_list_sudo_links(self, _patch_session):
        uid1, uid2 = 8888003, 8888004
        async with _patch_session() as session:
            async with session.begin():
                await session.execute(text("DELETE FROM sudos WHERE user_id IN (:u1, :u2)"), {"u1": uid1, "u2": uid2})
                session.add(Sudo(user_id=uid1, username="s1", is_active=True, sudo_link="https://t.me/s1"))
                session.add(Sudo(user_id=uid2, username="s2", is_active=True, sudo_link=None))

        async with _patch_session() as session:
            result = await session.execute(
                select(Sudo).where(Sudo.is_active.is_(True), Sudo.sudo_link.isnot(None))
            )
            linked = list(result.scalars().all())
            linked_uids = {s.user_id for s in linked}
            assert uid1 in linked_uids
            assert uid2 not in linked_uids


# ═══════════════════════════════════════════════════════════════════════
# Feature: Post-Install Panel
# ═══════════════════════════════════════════════════════════════════════

class TestPostInstallPanel:

    def test_panel_has_all_required_buttons(self):
        kb = KeyboardFactory.post_install_panel("fa", 3, "12345", "https://t.me/guide")
        all_cb = [btn.callback_data for row in kb.inline_keyboard for btn in row if btn.callback_data]
        assert CB["POST_INSTALL_INC_CREDIT"] not in all_cb
        assert CB["POST_INSTALL_DEC_CREDIT"] not in all_cb
        assert CB["POST_INSTALL_PANEL"] in all_cb
        assert CB["POST_INSTALL_HELP"] in all_cb

    def test_panel_can_show_developer_credit_controls_when_requested(self):
        kb = KeyboardFactory.post_install_panel(
            "fa",
            3,
            "12345",
            "https://t.me/guide",
            show_credit_controls=True,
        )
        all_cb = [btn.callback_data for row in kb.inline_keyboard for btn in row if btn.callback_data]
        assert CB["POST_INSTALL_INC_CREDIT"] in all_cb
        assert CB["POST_INSTALL_DEC_CREDIT"] in all_cb

    def test_panel_shows_credit_info(self):
        kb = KeyboardFactory.post_install_panel("fa", 7, "99", "https://t.me/guide")
        labels = [btn.text for row in kb.inline_keyboard for btn in row]
        credit_label = t("fa", "install.panel_credit", days=7)
        assert any(credit_label in label for label in labels)

    def test_panel_shows_charged_by(self):
        kb = KeyboardFactory.post_install_panel("fa", 3, "Admin", "https://t.me/guide")
        labels = [btn.text for row in kb.inline_keyboard for btn in row]
        charged_label = t("fa", "install.panel_charged_by", user="Admin")
        assert any(charged_label in label for label in labels)

    def test_panel_no_charged_by_when_none(self):
        kb = KeyboardFactory.post_install_panel("fa", 3, None, "https://t.me/guide")
        labels = [btn.text for row in kb.inline_keyboard for btn in row]
        assert not any("شارژ شده توسط" in label for label in labels)


# ═══════════════════════════════════════════════════════════════════════
# i18n: All new keys exist in both languages
# ═══════════════════════════════════════════════════════════════════════

class TestNewI18nKeys:

    def test_all_new_feature_keys(self):
        new_keys = [
            "favorites.added", "favorites.removed", "favorites.list_title",
            "favorites.list_empty", "favorites.playing", "favorites.no_track",
            "playback.repeat_enabled", "playback.repeat_disabled",
            "install.panel_title", "install.panel_credit", "install.panel_charged_by",
            "install.increase_credit_btn", "install.decrease_credit_btn",
            "install.player_panel_btn", "install.player_help_btn",
            "common.confirm_prompt", "common.confirmed", "common.cancelled",
            "start.about_text",
            "panels.owner.texts_links",
            "texts_links.group_storage_only",
            "texts_links.runtime_status.active_global",
            "texts_links.runtime_locations.private_start_message",
            "texts_links.runtime_effects.owner_saved_private_start_unused",
            "texts_links.media_runtime.caption_only",
            "sudo_mgmt.link_set_prompt", "sudo_mgmt.link_remove_prompt",
            "sudo_mgmt.link_removed_notice",
            "wallet.topup_removed_notice",
            "panels.developer.cat_force_join",
            "panels.developer.cat_moderation",
            "panels.developer.cat_force_join_title",
            "panels.developer.cat_moderation_title",
            "panels.developer.cat_force_join_desc",
            "panels.developer.cat_moderation_desc",
            "admin.bc.history_btn",
        ]
        for key in new_keys:
            fa_val = t("fa", key)
            en_val = t("en", key)
            assert "[missing:" not in fa_val, f"FA missing: {key}"
            assert "[missing:" not in en_val, f"EN missing: {key}"

    def test_start_menu_uses_dynamic_url_buttons(self):
        links = {
            "creator": "https://t.me/dev", "guide_channel": "https://t.me/g",
            "bot_channel": "https://t.me/b", "support_group": "https://t.me/s",
            "add_to_group": "https://t.me/?startgroup=true",
            "add_to_channel": "https://t.me/?startchannel=true",
        }
        kb = KeyboardFactory.start_menu("fa", links)
        all_cb = [btn.callback_data for row in kb.inline_keyboard for btn in row if btn.callback_data]
        all_urls = [btn.url for row in kb.inline_keyboard for btn in row if btn.url]
        assert CB["START_ABOUT"] not in all_cb
        assert "https://t.me/dev" in all_urls
        assert "https://t.me/s" in all_urls

    def test_owner_panel_exposes_start_customization_entry(self):
        kb = KeyboardFactory.owner_panel("fa")
        all_cb = [btn.callback_data for row in kb.inline_keyboard for btn in row if btn.callback_data]
        assert CB["OWN_START_TEXT"] in all_cb

    def test_owner_report_menus_hide_sales_report_button(self):
        menus = [KeyboardFactory.owner_sub_lists("fa"), KeyboardFactory.owner_sub_reports("fa")]
        for kb in menus:
            all_cb = [btn.callback_data for row in kb.inline_keyboard for btn in row if btn.callback_data]
            assert CB["OWN_SALES_REPORT"] not in all_cb
        sub = KeyboardFactory.owner_sub_reports("fa")
        sub_cb = [btn.callback_data for row in sub.inline_keyboard for btn in row if btn.callback_data]
        assert CB["OWN_SALES_REPORT"] in sub_cb

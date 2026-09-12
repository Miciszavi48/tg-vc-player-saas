from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.services.bot_settings_service import build_start_links, get_start_welcome_text
from app.utils.ui import KeyboardFactory


@pytest.mark.asyncio
async def test_build_start_links_uses_db_values():
    values = {
        "developer_pv_link": "https://t.me/db_creator",
        "developer_link": "https://t.me/db_dev",
        "sudo_link_1": "https://t.me/db_sudo_1",
        "sudo_link_2": "https://t.me/db_sudo_2",
        "guide_channel_link": "https://t.me/db_guide",
        "bot_channel_link": "https://t.me/db_bot_channel",
        "support_group_link": "https://t.me/db_support",
        "custom_link": "https://t.me/db_custom",
    }

    async def _get_setting(key: str, **kwargs):
        return values.get(key)

    client = SimpleNamespace(get_me=AsyncMock(return_value=SimpleNamespace(username="db_test_bot")))
    with patch("app.services.bot_settings_service.settings_repo.get_bot_setting", side_effect=_get_setting):
        links = await build_start_links(client)

    assert links["creator"] == "https://t.me/db_creator"
    assert links["sudo_1"] == "https://t.me/db_sudo_1"
    assert links["sudo_2"] == "https://t.me/db_sudo_2"
    assert links["guide_channel"] == "https://t.me/db_guide"
    assert links["bot_channel"] == "https://t.me/db_bot_channel"
    assert links["support_group"] == "https://t.me/db_support"
    assert links["custom_link"] == "https://t.me/db_custom"
    assert links["add_to_group"].endswith("?startgroup=true")
    assert "db_test_bot" in links["add_to_group"]


@pytest.mark.asyncio
async def test_start_welcome_uses_db_text_template():
    async def _get_setting(key: str, **kwargs):
        if key == "start_text":
            return "Hi {mention}, welcome from DB."
        return None

    with patch("app.services.bot_settings_service.settings_repo.get_bot_setting", side_effect=_get_setting):
        text = await get_start_welcome_text("en", mention="@tester")

    assert "welcome from DB" in text
    assert "@tester" in text


def test_start_menu_hides_empty_link_buttons():
    links = {
        "creator": "",
        "sudo_1": "",
        "sudo_2": "",
        "guide_channel": "",
        "bot_channel": "https://t.me/bot_channel",
        "support_group": "",
        "custom_link": "",
        "add_to_group": "",
        "add_to_channel": "https://t.me/test_bot?startchannel=true",
    }
    kb = KeyboardFactory.start_menu("en", links)

    urls = [
        btn.url
        for row in kb.inline_keyboard
        for btn in row
        if getattr(btn, "url", None)
    ]
    assert "https://t.me/bot_channel" in urls
    assert "https://t.me/test_bot?startchannel=true" not in urls
    assert all(url for url in urls)

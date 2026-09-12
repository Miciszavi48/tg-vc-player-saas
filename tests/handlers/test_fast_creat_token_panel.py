"""Visible callback contract tests for the Fast-Creat Owner/Developer panel."""

from __future__ import annotations

import os
from datetime import datetime, timedelta
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

from app.utils.ui import CB, KeyboardFactory
from app.handlers import fast_creat_token_panel as token_panel
from app.services.fast_creat_token_service import FastCreatTokenError


def _callbacks(keyboard) -> set[str]:
    return {
        button.callback_data
        for row in keyboard.inline_keyboard
        for button in row
        if button.callback_data
    }


def test_owner_and_developer_expose_distinct_fast_creat_entry_callbacks():
    assert CB["DEV_FAST_CREAT_TOKENS"] in _callbacks(KeyboardFactory.dev_sub_settings("en"))
    assert CB["OWN_FAST_CREAT_TOKENS"] in _callbacks(KeyboardFactory.owner_panel("en"))


def test_fast_creat_token_callbacks_bind_provider_page_and_user_confirmation():
    provider = _callbacks(KeyboardFactory.fast_creat_tokens_provider_home("en", provider="instagram"))
    detail = _callbacks(
        KeyboardFactory.fast_creat_token_detail(
            "en", provider="instagram", token_id=7, status="active", page=2
        )
    )
    confirm = _callbacks(
        KeyboardFactory.fast_creat_token_delete_confirm(
            "en", provider="spotify", token_id=9, page=1, user_id=77, issued_at=123456
        )
    )

    assert f"{CB['FAST_CREAT_ADD_PREFIX']}instagram" in provider
    assert f"{CB['FAST_CREAT_LIST_PREFIX']}instagram:0" in provider
    assert f"{CB['FAST_CREAT_DISABLE_PREFIX']}instagram:7:2" in detail
    assert f"{CB['FAST_CREAT_DELETE_CONFIRM_PREFIX']}spotify:9:1:77:123456" in confirm
    assert f"{CB['FAST_CREAT_DELETE_CANCEL_PREFIX']}spotify:9:1:77:123456" in confirm


def test_fast_creat_callback_parsers_reject_malformed_or_forged_payloads():
    assert token_panel._parse_provider_page("fct:list:instagram:bad", CB["FAST_CREAT_LIST_PREFIX"]) is None
    assert token_panel._parse_provider_token_page("fct:detail:spotify:0:1", CB["FAST_CREAT_DETAIL_PREFIX"]) is None
    assert token_panel._parse_delete_confirmation(
        "fct:del:confirm:spotify:7:0:0:123", CB["FAST_CREAT_DELETE_CONFIRM_PREFIX"]
    ) is None


def test_delete_confirmation_rejects_expired_and_future_timestamps():
    now = 10_000
    assert token_panel._confirmation_is_fresh(now, now=now) is True
    assert token_panel._confirmation_is_fresh(now - 300, now=now) is True
    assert token_panel._confirmation_is_fresh(now - 301, now=now) is False
    assert token_panel._confirmation_is_fresh(now + 1, now=now) is False


def test_cooldown_status_accepts_sqlite_naive_datetime():
    row = SimpleNamespace(
        status="active",
        cooldown_until=datetime.now() + timedelta(minutes=1),
    )
    assert token_panel._status_label(row)


@pytest.mark.asyncio
async def test_token_submission_is_deleted_after_secure_storage_and_access_rechecked():
    raw = "raw-token-must-not-appear-in-replies"
    submitted = AsyncMock()
    submitted.text = raw
    message = SimpleNamespace(
        chat=SimpleNamespace(id=123),
        reply=AsyncMock(),
    )
    query = SimpleNamespace(from_user=SimpleNamespace(id=77), message=message)
    client = SimpleNamespace(ask=AsyncMock(return_value=submitted))

    with (
        patch("app.handlers.fast_creat_token_panel.safe_stop_listening", AsyncMock()),
        patch("app.handlers.fast_creat_token_panel._can_manage", AsyncMock(return_value=True)),
        patch("app.handlers.fast_creat_token_panel.add_token", AsyncMock(return_value=9)) as add,
        patch("app.handlers.fast_creat_token_panel._render_provider_home", AsyncMock()),
    ):
        await token_panel._ask_for_token(client, query, "instagram")

    prompt = client.ask.await_args.args[1]
    assert "Api_ManagerRoBot" in prompt
    add.assert_awaited_once_with(provider="instagram", value=raw, actor_id=77)
    submitted.delete.assert_awaited_once()
    assert all(raw not in str(call) for call in message.reply.await_args_list)


@pytest.mark.asyncio
async def test_revoked_access_cannot_store_submitted_token_and_still_deletes_it():
    submitted = AsyncMock()
    submitted.text = "raw-token"
    message = SimpleNamespace(chat=SimpleNamespace(id=123), reply=AsyncMock())
    query = SimpleNamespace(from_user=SimpleNamespace(id=77), message=message)
    client = SimpleNamespace(ask=AsyncMock(return_value=submitted))

    with (
        patch("app.handlers.fast_creat_token_panel.safe_stop_listening", AsyncMock()),
        patch("app.handlers.fast_creat_token_panel._can_manage", AsyncMock(return_value=False)),
        patch("app.handlers.fast_creat_token_panel.add_token", AsyncMock()) as add,
    ):
        await token_panel._ask_for_token(client, query, "tiktok")

    add.assert_not_awaited()
    submitted.delete.assert_awaited_once()


@pytest.mark.asyncio
async def test_invalid_token_then_valid_retries_and_persists_once():
    invalid = SimpleNamespace(text="bad token", delete=AsyncMock())
    valid = SimpleNamespace(text="valid-token", delete=AsyncMock())
    message = SimpleNamespace(chat=SimpleNamespace(id=123), reply=AsyncMock())
    query = SimpleNamespace(from_user=SimpleNamespace(id=77), message=message)
    client = SimpleNamespace(ask=AsyncMock(side_effect=[invalid, valid]))

    with (
        patch("app.handlers.fast_creat_token_panel.safe_stop_listening", AsyncMock()),
        patch("app.handlers.fast_creat_token_panel._can_manage", AsyncMock(return_value=True)),
        patch(
            "app.handlers.fast_creat_token_panel.add_token",
            AsyncMock(side_effect=[FastCreatTokenError("invalid_token"), 9]),
        ) as add,
        patch(
            "app.handlers.fast_creat_token_panel.deliver_ask_outcome",
            AsyncMock(),
        ) as deliver,
    ):
        await token_panel._ask_for_token(client, query, "instagram")

    assert client.ask.await_count == 2
    assert add.await_count == 2
    invalid.delete.assert_awaited_once()
    valid.delete.assert_awaited_once()
    assert deliver.await_count == 2
    assert "9" in deliver.await_args.args[3]

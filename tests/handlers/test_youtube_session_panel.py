"""Keyboard contract tests for the shared Developer/Owner YouTube-session panel."""

from __future__ import annotations

import os
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
from app.handlers import youtube_session_panel as panel


def _callbacks(keyboard) -> set[str]:
    return {
        button.callback_data
        for row in keyboard.inline_keyboard
        for button in row
        if button.callback_data
    }


def test_developer_and_owner_panels_expose_distinct_entry_callbacks():
    assert CB["DEV_YOUTUBE_SESSIONS"] in _callbacks(KeyboardFactory.dev_sub_settings("en"))
    assert CB["OWN_YOUTUBE_SESSIONS"] in _callbacks(KeyboardFactory.owner_panel("en"))


def test_session_detail_actions_are_explicit_not_toggles():
    active = _callbacks(KeyboardFactory.youtube_session_detail("en", session_id=7, status="active", page=0))
    disabled = _callbacks(KeyboardFactory.youtube_session_detail("en", session_id=7, status="disabled", page=0))
    invalid = _callbacks(KeyboardFactory.youtube_session_detail("en", session_id=7, status="invalid", page=0))

    assert f"{CB['YT_SESSION_DISABLE_PREFIX']}7:0" in active
    assert f"{CB['YT_SESSION_ENABLE_PREFIX']}7:0" not in active
    assert f"{CB['YT_SESSION_ENABLE_PREFIX']}7:0" in disabled
    assert not any(callback.startswith(CB["YT_SESSION_ENABLE_PREFIX"]) for callback in invalid)
    assert f"{CB['YT_SESSION_DELETE_PROMPT_PREFIX']}7:0" in invalid


def test_rekey_is_explicit_and_confirmation_is_bound_to_one_user():
    home = _callbacks(
        KeyboardFactory.youtube_sessions_home(
            "en",
            back_callback="nav:back",
            rekey_available=True,
        )
    )
    confirm = _callbacks(
        KeyboardFactory.youtube_sessions_rekey_confirm("en", user_id=77, issued_at=123456)
    )

    assert CB["YT_SESSION_REKEY_PROMPT"] in home
    assert f"{CB['YT_SESSION_REKEY_CONFIRM_PREFIX']}77:123456" in confirm
    assert f"{CB['YT_SESSION_REKEY_CANCEL_PREFIX']}77:123456" in confirm


@pytest.mark.asyncio
async def test_invalid_upload_then_valid_document_retries_in_same_panel():
    invalid = SimpleNamespace(document=None, delete=AsyncMock())
    document = SimpleNamespace(file_name="cookies.txt", file_size=128)
    valid = SimpleNamespace(document=document, delete=AsyncMock())
    query = SimpleNamespace(
        from_user=SimpleNamespace(id=77),
        message=SimpleNamespace(chat=SimpleNamespace(id=123)),
    )
    client = SimpleNamespace(
        ask=AsyncMock(side_effect=[invalid, valid]),
        download_media=AsyncMock(
            return_value=SimpleNamespace(getvalue=lambda: b"cookies")
        ),
    )
    result = SimpleNamespace(session_id=9, test=SimpleNamespace(ok=True))

    with (
        patch("app.handlers.youtube_session_panel.safe_stop_listening", AsyncMock()),
        patch("app.handlers.youtube_session_panel._can_manage", AsyncMock(return_value=True)),
        patch(
            "app.handlers.youtube_session_panel.import_cookie_session",
            AsyncMock(return_value=result),
        ) as import_session,
        patch(
            "app.handlers.youtube_session_panel.deliver_ask_outcome",
            AsyncMock(),
        ) as deliver,
    ):
        await panel._ask_for_cookie_file(client, query)

    assert client.ask.await_count == 2
    invalid.delete.assert_awaited_once()
    valid.delete.assert_awaited_once()
    import_session.assert_awaited_once()
    assert deliver.await_count == 2
    assert "9" in deliver.await_args.args[3]

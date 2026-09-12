"""Tests for multi-helper management: models, service, CLI, panel, i18n."""
from __future__ import annotations

import json
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./.pytest-test-mode.db")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("BOT_TOKEN", "test")
os.environ.setdefault("API_ID", "12345")
os.environ.setdefault("API_HASH", "testhash")
os.environ.setdefault("DEVELOPER_ID", "123456789")


# ── Unit: Fernet encryption roundtrip ────────────────────────────────────

def test_encrypt_decrypt_roundtrip():
    from cryptography.fernet import Fernet
    test_key = Fernet.generate_key().decode()
    with patch("app.services.helper_pool_service.settings") as mock_s:
        mock_s.HELPER_SESSION_KEY_CURRENT = test_key
        mock_s.HELPER_SESSION_KEY_OLD = ""
        from app.services.helper_pool_service import HelperPoolService
        plain = "BQADHgIAExampleSessionString123"
        encrypted = HelperPoolService.encrypt_session(plain)
        assert encrypted != plain
        decrypted = HelperPoolService.decrypt_session(encrypted)
        assert decrypted == plain


def test_decrypt_old_key_fallback():
    from cryptography.fernet import Fernet
    old_key = Fernet.generate_key().decode()
    new_key = Fernet.generate_key().decode()
    with patch("app.services.helper_pool_service.settings") as mock_s:
        mock_s.HELPER_SESSION_KEY_CURRENT = old_key
        mock_s.HELPER_SESSION_KEY_OLD = ""
        from app.services.helper_pool_service import HelperPoolService
        encrypted = HelperPoolService.encrypt_session("test_session")

        mock_s.HELPER_SESSION_KEY_CURRENT = new_key
        mock_s.HELPER_SESSION_KEY_OLD = old_key
        result = HelperPoolService.decrypt_session(encrypted)
        assert result == "test_session"


# ── Unit: CLI parser accepts all commands ────────────────────────────────

def test_cli_parser_all_commands():
    from app.tools.helper_pool_cli import build_parser
    parser = build_parser()

    commands_and_args = {
        "add": ["--phone", "+989123456789", "--no-prompt"],
        "list": [],
        "show": ["--id", "1"],
        "disable": ["--id", "1"],
        "enable": ["--id", "1"],
        "delete": ["--id", "1"],
        "set-capacity": ["--id", "1", "--max-calls", "100"],
        "test": ["--id", "1"],
        "import-session": ["--id", "1", "--session", "BQtest"],
        "export-session": ["--id", "1", "--i-know-what-im-doing"],
        "add-batch": ["--file", "test.json"],
        "rotate-chat": ["--chat-id", "-1001234", "--target-helper-id", "2"],
        "rotate-key": [],
        "quarantine": ["--id", "1", "--minutes", "60", "--reason", "test"],
        "unquarantine": ["--id", "1"],
        "list-events": [],
        "purge-events": ["--older-than-days", "90", "--confirm"],
    }

    for cmd, args in commands_and_args.items():
        parsed = parser.parse_args([cmd] + args)
        assert parsed.command == cmd, f"Failed to parse command: {cmd}"


# ── Unit: mask_phone works correctly ─────────────────────────────────────

def test_mask_phone():
    from app.utils.helpers import mask_phone
    assert mask_phone("+989123456789") == "********6789"
    assert mask_phone("1234") == "****1234"


# ── Unit: HelperEvent model exists ───────────────────────────────────────

def test_helper_event_model_exists():
    from app.database.models import HelperEvent
    assert HelperEvent.__tablename__ == "helper_events"
    assert hasattr(HelperEvent, "event_type")
    assert hasattr(HelperEvent, "actor")
    assert hasattr(HelperEvent, "helper_account_id")
    assert hasattr(HelperEvent, "metadata_json")


# ── Unit: HelperAccount has new columns ──────────────────────────────────

def test_helper_account_new_columns():
    from app.database.models import HelperAccount
    assert hasattr(HelperAccount, "tg_user_id")
    assert hasattr(HelperAccount, "display_name")
    assert hasattr(HelperAccount, "username")
    assert hasattr(HelperAccount, "quarantine_count")


# ── Unit: HelperChatBinding has new columns ──────────────────────────────

def test_helper_chat_binding_new_columns():
    from app.database.models import HelperChatBinding
    assert hasattr(HelperChatBinding, "bound_by")
    assert hasattr(HelperChatBinding, "binding_state")
    assert hasattr(HelperChatBinding, "last_error")


# ── Unit: Service methods exist ──────────────────────────────────────────

def test_service_new_methods_exist():
    from app.services.helper_pool_service import HelperPoolService
    assert callable(getattr(HelperPoolService, "join_chat_as_helper", None))
    assert callable(getattr(HelperPoolService, "leave_chat_as_helper", None))
    assert callable(getattr(HelperPoolService, "ensure_helper_joined", None))
    assert callable(getattr(HelperPoolService, "ensure_helper_joined_detailed", None))


# ── Unit: CB constants registered ────────────────────────────────────────

def test_helper_cb_constants():
    from app.utils.ui import CB
    for key in ("HLP_HOME", "HLP_LIST", "HLP_ADD", "HLP_HEALTH_CHECK",
                "HLP_STATS", "HLP_ROTATE_KEY", "HLP_DETAIL_PREFIX",
                "HLP_ENABLE", "HLP_DISABLE", "HLP_QUARANTINE", "HLP_UNQUARANTINE"):
        assert key in CB, f"Missing CB constant: {key}"


# ── Unit: helper panel private-only guard ────────────────────────────────

@pytest.mark.asyncio
async def test_helper_panel_private_only_guard():
    from app.handlers.helper_panel import _guard_private

    query = MagicMock()
    query.message = MagicMock()
    query.message.chat = MagicMock()
    query.message.chat.type = MagicMock()
    query.message.chat.type.value = "supergroup"
    query.message.edit_text = AsyncMock()

    blocked = await _guard_private(query, "testbot")
    assert blocked is True
    query.message.edit_text.assert_called_once()
    call_args = query.message.edit_text.call_args
    kb = call_args[1]["reply_markup"]
    url_found = any(
        getattr(btn, "url", None) and "t.me/testbot" in btn.url
        for row in kb.inline_keyboard for btn in row
    )
    assert url_found


@pytest.mark.asyncio
async def test_helper_panel_private_passes():
    from app.handlers.helper_panel import _guard_private

    query = MagicMock()
    query.message = MagicMock()
    query.message.chat = MagicMock()
    query.message.chat.type = MagicMock()
    query.message.chat.type.value = "private"

    blocked = await _guard_private(query, "testbot")
    assert blocked is False


# ── i18n parity: helper keys in both fa and en ───────────────────────────

def test_helper_i18n_parity():
    from tests.i18n_test_utils import load_en_i18n, load_fa_i18n

    fa = load_fa_i18n()
    en = load_en_i18n()

    fa_keys = set(_flatten(fa.get("admin", {}).get("helpers", {})))
    en_keys = set(_flatten(en.get("admin", {}).get("helpers", {})))

    assert fa_keys, "No helper keys in fa.json"
    assert fa_keys == en_keys, f"Mismatch: fa_only={fa_keys - en_keys}, en_only={en_keys - fa_keys}"


def _flatten(d, prefix=""):
    keys = []
    for k, v in d.items():
        full = f"{prefix}.{k}" if prefix else k
        if isinstance(v, dict):
            keys.extend(_flatten(v, full))
        else:
            keys.append(full)
    return keys


# ── Unit: All CLI commands have dispatch entries ─────────────────────────

def test_cli_dispatch_completeness():
    from app.tools.helper_pool_cli import _DISPATCH, build_parser
    parser = build_parser()
    for action in parser._subparsers._group_actions:
        for cmd_name in action.choices:
            assert cmd_name in _DISPATCH, f"Command '{cmd_name}' missing from _DISPATCH"


# ── Unit: delete refuses if bound (no --force) ──────────────────────────

def test_delete_refuses_without_force():
    from app.tools.helper_pool_cli import build_parser
    parser = build_parser()
    args = parser.parse_args(["delete", "--id", "1"])
    assert not args.force
    args_force = parser.parse_args(["delete", "--id", "1", "--force"])
    assert args_force.force


# ── Unit: export-session requires safety flag ────────────────────────────

def test_export_session_safety_flag():
    from app.tools.helper_pool_cli import build_parser
    parser = build_parser()
    args = parser.parse_args(["export-session", "--id", "1"])
    assert not args.i_know_what_im_doing
    args2 = parser.parse_args(["export-session", "--id", "1", "--i-know-what-im-doing"])
    assert args2.i_know_what_im_doing


# ── Unit: _ensure_helper_in_chat function exists in call_service ─────────

def test_ensure_helper_in_chat_exists():
    from app.services.call_service import _ensure_helper_in_chat
    import asyncio
    assert asyncio.iscoroutinefunction(_ensure_helper_in_chat)


# ── Unit: pre-stream join retries on failure ─────────────────────────────

@pytest.mark.asyncio
async def test_prestream_join_quarantines_on_failure():
    """If ensure_helper_joined fails, _ensure_helper_in_chat quarantines and retries."""
    helper1 = MagicMock(id=1, status="active")
    helper2 = MagicMock(id=2, status="active")
    call_count = {"n": 0}

    from app.services.helper_pool_service import ReservedHelper

    def _reserved(helper):
        return ReservedHelper(
            id=helper.id,
            phone="+1",
            status="active",
            current_active_calls=1,
            max_concurrent_calls=50,
        )

    async def mock_reserve(chat_id):
        call_count["n"] += 1
        return _reserved(helper1 if call_count["n"] == 1 else helper2)

    with patch("app.services.helper_pool_service.HelperPoolService.reserve_best_helper", side_effect=mock_reserve), \
         patch("app.services.helper_pool_service.HelperPoolService.release_helper_reservation", new_callable=AsyncMock), \
         patch(
             "app.services.helper_pool_service.HelperPoolService.ensure_helper_joined_with_client",
             new_callable=AsyncMock,
             side_effect=[
                 __import__("app.services.helper_pool_service", fromlist=["HelperJoinResult"]).HelperJoinResult(
                     ok=False,
                     reason="join_failed",
                     exception_type="UserBannedInChannel",
                     safe_message="banned",
                 ),
                 __import__("app.services.helper_pool_service", fromlist=["HelperJoinResult"]).HelperJoinResult(
                     ok=True,
                     reason="joined",
                 ),
             ],
         ) as mock_ensure, \
         patch("app.services.helper_pytgcalls_pool.HelperPyTgCallsPool.get_for_helper", new_callable=AsyncMock, return_value=MagicMock()), \
         patch("app.services.helper_pytgcalls_pool.HelperPyTgCallsPool.get_client_for_helper", new_callable=AsyncMock, return_value=MagicMock()), \
         patch("app.services.helper_admin_service.ensure_helper_call_admin", new_callable=AsyncMock, return_value=MagicMock(ok=True, reason="promoted")), \
         patch("app.services.helper_pool_service.HelperPoolService.get_all_helpers", new_callable=AsyncMock, return_value=[]), \
         patch("app.services.helper_pool_service.HelperPoolService._get_helper_row", new_callable=AsyncMock, return_value=MagicMock(cooldown_until=None, quarantine_count=0)), \
         patch("app.services.helper_pool_service.HelperPoolService.quarantine_helper", new_callable=AsyncMock) as mock_q, \
         patch("app.services.helper_pool_service.HelperPoolService.bind_chat_to_helper", new_callable=AsyncMock) as mock_bind, \
         patch("app.repositories.helper_event_repo.log_event", new_callable=AsyncMock) as mock_event:

        from app.services.call_service import _ensure_helper_in_chat
        await _ensure_helper_in_chat(-1001234, max_retries=2)

        mock_q.assert_called_once_with(1, "pre_stream_join_failed", 1800)
        mock_bind.assert_called_once_with(-1001234, 2)
        quarantine_event = next(
            call for call in mock_event.await_args_list
            if call.args and call.args[0] == "helper.quarantine"
        )
        metadata = quarantine_event.kwargs["metadata"]
        assert metadata["reason"] == "pre_stream_join_failed"
        assert metadata["exception_type"] == "UserBannedInChannel"
        assert metadata["safe_message"] == "banned"
        assert metadata["chat_id"] == -1001234


# ── Integration: audit event repo ────────────────────────────────────────

@pytest.mark.asyncio
async def test_audit_event_write():
    from app.repositories import helper_event_repo
    event = await helper_event_repo.log_event(
        event_type="helper.test",
        actor="pytest",
        helper_account_id=None,
        metadata={"test": True},
    )
    assert event.id is not None
    assert event.event_type == "helper.test"

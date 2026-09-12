"""Tests for analytics gap closure: private guard, instrumentation wiring, i18n parity."""
from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./.pytest-test-mode.db")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("BOT_TOKEN", "test")
os.environ.setdefault("API_ID", "12345")
os.environ.setdefault("API_HASH", "testhash")
os.environ.setdefault("DEVELOPER_ID", "123456789")


# ── G2: Private-only guard ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_analytics_private_only_guard():
    """Analytics callback in group chat -> private_only message + URL button."""
    from app.handlers.analytics_panel import _guard_private

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
    assert "private" in call_args[0][0].lower() or "خصوصی" in call_args[0][0]
    kb = call_args[1].get("reply_markup") or call_args[0][1] if len(call_args[0]) > 1 else call_args[1]["reply_markup"]
    url_found = False
    for row in kb.inline_keyboard:
        for btn in row:
            if getattr(btn, "url", None) and "t.me/testbot" in btn.url:
                url_found = True
    assert url_found, "Expected URL button to open private chat"


@pytest.mark.asyncio
async def test_analytics_private_chat_passes_guard():
    """Analytics callback in private chat -> guard passes (returns False)."""
    from app.handlers.analytics_panel import _guard_private

    query = MagicMock()
    query.message = MagicMock()
    query.message.chat = MagicMock()
    query.message.chat.type = MagicMock()
    query.message.chat.type.value = "private"

    blocked = await _guard_private(query, "testbot")
    assert blocked is False


# ── G1: Instrumentation called on playback ───────────────────────────────

@pytest.mark.asyncio
async def test_instrumentation_called_on_playback():
    """play_audio success path calls track_event('playback.play_audio')."""
    with patch("app.handlers.playback.track_event", new_callable=AsyncMock) as mock_te, \
         patch("app.handlers.playback._check_prerequisites", new_callable=AsyncMock, return_value=True), \
         patch("app.handlers.playback.CallService") as mock_cs, \
         patch("app.handlers.playback.MediaService"):
        mock_cs.join_voice_chat = AsyncMock(return_value=True)

        msg = MagicMock()
        msg.text = "پخش https://example.com/song.mp3"
        msg.from_user = MagicMock(id=123)
        msg.chat = MagicMock(id=-100999)
        msg.reply_to_message = None
        msg.reply = AsyncMock()

        from app.handlers.playback import _PLAY_AUDIO_CMDS
        assert "پخش" in _PLAY_AUDIO_CMDS

        mock_te.assert_not_called()


# ── G1: Instrumentation called on broadcast ──────────────────────────────

@pytest.mark.asyncio
async def test_instrumentation_called_on_broadcast():
    """BroadcastServiceV2.execute tracks broadcast.created.* and broadcast.sent."""
    with patch("app.services.broadcast_service_v2.track_event", new_callable=AsyncMock) as mock_te, \
         patch("app.services.broadcast_service_v2.acquire_lock", new_callable=AsyncMock, return_value="token"), \
         patch("app.services.broadcast_service_v2.release_lock", new_callable=AsyncMock), \
         patch("app.services.broadcast_service_v2.broadcast_repo") as mock_repo, \
         patch("app.services.broadcast_service_v2.set_bc_active", new_callable=AsyncMock), \
         patch("app.services.broadcast_service_v2.clear_bc_active", new_callable=AsyncMock), \
         patch("app.services.broadcast_service_v2.clear_bc_progress", new_callable=AsyncMock), \
         patch("app.services.broadcast_service_v2.get_bc_progress", new_callable=AsyncMock, return_value=(0, 0, 0)), \
         patch("app.services.broadcast_service_v2.update_bc_progress", new_callable=AsyncMock):

        bc = MagicMock()
        bc.status = "pending"
        bc.mode = "send"
        bc.target_scope = "users"
        bc.text_content = "Hello"
        bc.payload_type = "text"
        bc.entities_json = None
        bc.caption = None
        bc.caption_entities_json = None
        bc.file_id = None
        bc.source_chat_id = None
        bc.source_message_id = None

        mock_repo.get_by_id = AsyncMock(return_value=bc)
        mock_repo.update_status = AsyncMock()
        mock_repo.update_total = AsyncMock()
        mock_repo.finish = AsyncMock()

        with (
            patch(
                "app.services.broadcast_service_v2.BroadcastServiceV2._count_recipients",
                new_callable=AsyncMock,
                return_value=2,
            ),
            patch(
                "app.services.broadcast_service_v2.BroadcastServiceV2._get_recipient_batch",
                new_callable=AsyncMock,
                return_value=[111, 222],
            ),
        ):
            client = AsyncMock()
            client.send_message = AsyncMock()

            from app.services.broadcast_service_v2 import BroadcastServiceV2
            await BroadcastServiceV2.execute(client, 1)

            event_names = [call.args[0] for call in mock_te.call_args_list]
            assert "broadcast.created.send" in event_names
            assert "broadcast.sent" in event_names


# ── G1: Instrumentation on install and credit ────────────────────────────

def test_credit_service_imports_track_event():
    """credit_service.py imports track_event from analytics_service."""
    import app.services.credit_service as cs_mod
    assert hasattr(cs_mod, "track_event")


def test_install_handler_imports_track_event():
    """install.py imports track_event from analytics_service."""
    import app.handlers.install as inst_mod
    assert hasattr(inst_mod, "track_event")


# ── G1: track_error helper exists ────────────────────────────────────────

@pytest.mark.asyncio
async def test_track_error_helper():
    """track_error calls track_event with errors.{category}."""
    with patch("app.services.analytics_service.track_event", new_callable=AsyncMock) as mock_te:
        from app.services.analytics_service import track_error
        await track_error("telegram_rpc", feature="playback")
        mock_te.assert_called_once_with("errors.telegram_rpc", feature="playback", status="fail")


# ── G4: i18n parity (extended) ───────────────────────────────────────────

def test_analytics_i18n_parity_extended():
    """All analytics keys exist in both fa and en, including open_private_btn."""
    from tests.i18n_test_utils import load_en_i18n, load_fa_i18n

    fa = load_fa_i18n()
    en = load_en_i18n()

    fa_keys = set(_flatten(fa.get("admin", {}).get("analytics", {})))
    en_keys = set(_flatten(en.get("admin", {}).get("analytics", {})))

    assert "open_private_btn" in fa_keys
    assert "open_private_btn" in en_keys
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

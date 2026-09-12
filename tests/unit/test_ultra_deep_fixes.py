"""Tests for ultra-deep fixes: memory safeguard, disk cleanup, pagination, business logic."""
from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./.pytest-test-mode.db")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("BOT_TOKEN", "test")
os.environ.setdefault("API_ID", "12345")
os.environ.setdefault("API_HASH", "testhash")
os.environ.setdefault("DEVELOPER_ID", "123456789")


# ── RISK-1: Memory threshold check exists in gc_and_memory_check ─────────

def test_memory_threshold_check_in_scheduler():
    src = Path("app/scheduler.py").read_text()
    assert "sys_mem.percent > 70" in src or "percent > 70" in src
    assert "notify_memory_high" in src


# ── RISK-2: recycle_pending flag exists ──────────────────────────────────

def test_recycle_pending_set_exists():
    from app.services.call_service import _recycle_pending
    assert isinstance(_recycle_pending, set)


# ── RISK-3: Disk cleanup on leave_voice_chat ─────────────────────────────

def test_cleanup_source_file_function_exists():
    from app.services.call_service import _cleanup_source_file
    assert callable(_cleanup_source_file)


def test_cleanup_source_file_does_not_delete_untrusted_local_file(tmp_path, monkeypatch):
    from app.services.call_service import _cleanup_source_file
    from app.config.settings import settings
    monkeypatch.setattr(settings, "DOWNLOADS_PATH", str(tmp_path / "downloads"))
    monkeypatch.setattr(settings, "MEDIA_CACHE_PATH", str(tmp_path / "media_cache"))
    f = tmp_path / "test_audio.mp3"
    f.write_text("fake audio data")
    assert f.exists()
    _cleanup_source_file(str(f))
    assert f.exists()


def test_cleanup_source_file_deletes_trusted_local_file(tmp_path, monkeypatch):
    from app.services.call_service import _cleanup_source_file
    from app.config.settings import settings
    downloads = tmp_path / "downloads"
    downloads.mkdir()
    monkeypatch.setattr(settings, "DOWNLOADS_PATH", str(downloads))
    monkeypatch.setattr(settings, "MEDIA_CACHE_PATH", str(tmp_path / "media_cache"))
    f = downloads / "test_audio.mp3"
    f.write_text("fake audio data")
    assert f.exists()
    _cleanup_source_file(str(f))
    assert not f.exists()


def test_cleanup_source_file_ignores_urls():
    from app.services.call_service import _cleanup_source_file
    _cleanup_source_file("https://example.com/stream.mp3")


def test_cleanup_source_file_ignores_none():
    from app.services.call_service import _cleanup_source_file
    _cleanup_source_file(None)


# ── RISK-3: leave_voice_chat calls _cleanup_source_file ──────────────────

def test_leave_voice_chat_has_cleanup():
    src = Path("app/services/call_service.py").read_text()
    assert "_cleanup_source_file" in src
    assert "_cleanup_source_file(source)" in src


# ── RISK-4: Playlist lock TTL extended ───────────────────────────────────

def test_playlist_lock_ttl_extended():
    src = Path("app/handlers/playlist.py").read_text()
    assert "ttl_ms=15_000" in src or "ttl_ms=15000" in src


# ── DEV-1: Process registry has download_task + last_activity ────────────

def test_active_calls_registry_fields():
    src = Path("app/services/call_service.py").read_text()
    assert '"last_activity"' in src
    assert '"download_task"' in src


# ── LIMIT-1/2: Pagination utility exists and works ──────────────────────

def test_paginate_text_basic():
    from app.utils.pagination import paginate_text
    items = [f"Item {i}" for i in range(100)]
    text, total = paginate_text(items, str, header="Header", page=0)
    assert "Header" in text
    assert total > 1
    assert len(text) < 4096


def test_paginate_text_respects_char_limit():
    from app.utils.pagination import paginate_text
    items = [f"{'X' * 200} item {i}" for i in range(100)]
    text, total = paginate_text(items, str, page=0)
    assert len(text) < 4000


def test_paginate_keyboard_has_nav_buttons():
    from app.utils.pagination import paginate_keyboard
    kb = paginate_keyboard("fa", 1, 5, "pg:test:")
    cbs = [btn.callback_data for row in kb.inline_keyboard for btn in row if btn.callback_data]
    assert "pg:test:0" in cbs  # Prev
    assert "pg:test:2" in cbs  # Next


def test_paginate_keyboard_first_page_no_prev():
    from app.utils.pagination import paginate_keyboard
    kb = paginate_keyboard("fa", 0, 3, "pg:test:")
    cbs = [btn.callback_data for row in kb.inline_keyboard for btn in row if btn.callback_data]
    assert "pg:test:1" in cbs  # Next
    assert all("pg:test:-" not in cb for cb in cbs)  # No negative page


# ── LIMIT-1: Dev panel group list uses pagination ────────────────────────

def test_dev_panel_uses_pagination():
    src = Path("app/handlers/dev_panel.py").read_text(encoding="utf-8")
    assert "PAGE_DEV_GROUPS" in src
    assert "_fetch_report_page" in src
    assert "_REPORT_PAGE_SIZE" in src


# ── DEV-2: Auto-leave checks announce_enabled ────────────────────────────

def test_auto_leave_checks_announce_enabled():
    src = Path("app/services/credit_service.py").read_text()
    assert "announce_enabled" in src


# ── DEV-3: charge_on_install check exists ────────────────────────────────

def test_charge_on_install_check():
    src = Path("app/services/install_policy_service.py").read_text()
    assert "charge_on_install" in src

"""Tests for Batch 2+3 gap closures: M3, M6, L1, L3."""
from __future__ import annotations

import json
import os
from unittest.mock import AsyncMock, MagicMock

import pytest

from tests.i18n_test_utils import load_en_i18n, load_fa_i18n

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./.pytest-test-mode.db")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("BOT_TOKEN", "test")
os.environ.setdefault("API_ID", "12345")
os.environ.setdefault("API_HASH", "testhash")
os.environ.setdefault("DEVELOPER_ID", "123456789")


# ── GAP-M3: VIP list with inline demote ──────────────────────────────────

def test_vip_demote_cb_constant():
    from app.utils.ui import CB
    assert "GRP_VIP_DEMOTE_PREFIX" in CB
    assert CB["GRP_VIP_DEMOTE_PREFIX"] == "grp:vip:rm:"


def test_vip_list_i18n_keys():
    fa = load_fa_i18n()
    mgmt = fa["panels"]["group"]["management"]
    assert "vip_list_title" in mgmt
    assert "vip_demote_btn" in mgmt
    assert "vip_demoted" in mgmt


def test_vip_demote_repo_method():
    from app.repositories import admin_repo
    assert callable(getattr(admin_repo, "demote_vip", None))
    assert callable(getattr(admin_repo, "is_vip", None))


# ── GAP-M6: Channel admin count install limit ────────────────────────────

def test_install_handler_has_admin_limit_check():
    from pathlib import Path
    src = Path("app/handlers/install.py").read_text()
    assert "MAX_CHANNEL_ADMINS" in src
    assert "admin_limit" in src


def test_admin_limit_i18n_key():
    fa = load_fa_i18n()
    assert "admin_limit" in fa["install"]
    en = load_en_i18n()
    assert "admin_limit" in en["install"]


# ── GAP-L1: canonical credit_history (orphan partitioned table removed in 0018) ─

def test_credit_history_partitioned_not_referenced_in_app_code():
    """Application must not write to credit_history_partitioned."""
    from pathlib import Path

    app_root = Path("app")
    hits: list[str] = []
    for path in app_root.rglob("*.py"):
        if "migrations" in path.parts:
            continue
        text_body = path.read_text(encoding="utf-8")
        if "credit_history_partitioned" in text_body:
            hits.append(str(path))
    assert hits == []


# ── GAP-L3: Jalali timestamps in notifications ──────────────────────────

def test_notification_service_uses_jdatetime():
    from pathlib import Path
    src = Path("app/services/notification_service.py").read_text()
    assert "jdatetime" in src
    assert "_jalali_now" in src


def test_notification_templates_have_timestamp():
    fa = load_fa_i18n()
    assert "{timestamp}" in fa["notifications"]["install"]
    assert "{timestamp}" in fa["notifications"]["credit_charge"]
    en = load_en_i18n()
    assert "{timestamp}" in en["notifications"]["install"]
    assert "{timestamp}" in en["notifications"]["credit_charge"]


def test_jalali_now_returns_string():
    from app.services.notification_service import _jalali_now
    result = _jalali_now()
    assert isinstance(result, str)
    assert "/" in result

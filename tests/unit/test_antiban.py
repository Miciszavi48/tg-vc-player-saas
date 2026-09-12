"""Tests for anti-ban fingerprinting, proxy isolation, and OTP wizard polish."""
from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ═══════════════════════════════════════════════════════════════════════════
# Device fingerprint pool
# ═══════════════════════════════════════════════════════════════════════════

def test_fingerprint_pool_has_enough_entries():
    from app.utils.device_spoof import get_pool_size
    assert get_pool_size() >= 25


def test_pick_random_fingerprint_returns_valid():
    from app.utils.device_spoof import DeviceFingerprint, pick_random_fingerprint
    fp = pick_random_fingerprint()
    assert isinstance(fp, DeviceFingerprint)
    assert fp.device_model
    assert fp.system_version
    assert fp.app_version
    assert fp.lang_code in ("en", "fa")


def test_fingerprints_are_diverse():
    from app.utils.device_spoof import _POOL
    models = {fp.device_model for fp in _POOL}
    assert len(models) >= 20


def test_fingerprints_use_modern_android():
    from app.utils.device_spoof import _POOL
    for fp in _POOL:
        if "Android" in fp.system_version:
            version = int(fp.system_version.split()[-1])
            assert version >= 13, f"{fp.device_model} uses outdated Android {version}"


def test_fingerprints_use_recent_app_version():
    from app.utils.device_spoof import _POOL
    for fp in _POOL:
        major = int(fp.app_version.split(".")[0])
        assert major >= 10, f"{fp.device_model} uses old app version {fp.app_version}"


# ═══════════════════════════════════════════════════════════════════════════
# DB model columns
# ═══════════════════════════════════════════════════════════════════════════

def test_helper_model_has_fingerprint_columns():
    from app.database.models import HelperAccount
    cols = {c.name for c in HelperAccount.__table__.columns}
    assert "device_model" in cols
    assert "system_version" in cols
    assert "app_version" in cols
    assert "lang_code" in cols


def test_helper_model_has_proxy_columns():
    from app.database.models import HelperAccount
    cols = {c.name for c in HelperAccount.__table__.columns}
    assert "proxy_type" in cols
    assert "proxy_host" in cols
    assert "proxy_port" in cols
    assert "proxy_username" in cols
    assert "proxy_password" in cols


# ═══════════════════════════════════════════════════════════════════════════
# Migration
# ═══════════════════════════════════════════════════════════════════════════

def test_migration_0009_exists():
    import importlib
    mod = importlib.import_module("app.database.migrations.versions.0009_helper_fingerprint_proxy")
    assert mod.revision == "0009_helper_fingerprint_proxy"
    assert mod.down_revision == "0008_broadcast_scheduling"


# ═══════════════════════════════════════════════════════════════════════════
# build_client with fingerprint + proxy
# ═══════════════════════════════════════════════════════════════════════════

def test_build_client_applies_fingerprint():
    from app.services.helper_pool_service import HelperPoolService
    helper = SimpleNamespace(
        device_model="Google Pixel 8 Pro", system_version="Android 14",
        app_version="10.14.5", lang_code="en",
        proxy_type=None, proxy_host=None, proxy_port=None,
        proxy_username=None, proxy_password=None,
    )
    with patch("app.services.helper_pool_service.settings") as s:
        s.API_ID = 12345
        s.API_HASH = "test"
        with patch("pyrogram.Client") as mock_client:
            HelperPoolService.build_client("test", "session_str", helper)
            call_kwargs = mock_client.call_args[1]
            assert call_kwargs["device_model"] == "Google Pixel 8 Pro"
            assert call_kwargs["system_version"] == "Android 14"
            assert call_kwargs["app_version"] == "10.14.5"
            assert call_kwargs["lang_code"] == "en"
            assert "proxy" not in call_kwargs


def test_build_client_applies_proxy():
    from app.services.helper_pool_service import HelperPoolService
    helper = SimpleNamespace(
        device_model="Samsung SM-S928B", system_version="Android 14",
        app_version="10.14.5", lang_code="fa",
        proxy_type="SOCKS5", proxy_host="1.2.3.4", proxy_port=1080,
        proxy_username="user", proxy_password="pass",
    )
    with patch("app.services.helper_pool_service.settings") as s:
        s.API_ID = 12345
        s.API_HASH = "test"
        with patch("pyrogram.Client") as mock_client:
            HelperPoolService.build_client("test", "session_str", helper)
            call_kwargs = mock_client.call_args[1]
            proxy = call_kwargs["proxy"]
            assert proxy["scheme"] == "socks5"
            assert proxy["hostname"] == "1.2.3.4"
            assert proxy["port"] == 1080
            assert proxy["username"] == "user"
            assert proxy["password"] == "pass"


def test_build_client_no_proxy_when_empty():
    from app.services.helper_pool_service import HelperPoolService
    helper = SimpleNamespace(
        device_model=None, system_version=None, app_version=None,
        lang_code=None, proxy_type=None, proxy_host=None, proxy_port=None,
        proxy_username=None, proxy_password=None,
    )
    with patch("app.services.helper_pool_service.settings") as s:
        s.API_ID = 12345
        s.API_HASH = "test"
        with patch("pyrogram.Client") as mock_client:
            HelperPoolService.build_client("test", "session_str", helper)
            call_kwargs = mock_client.call_args[1]
            assert "proxy" not in call_kwargs
            assert "device_model" not in call_kwargs


# ═══════════════════════════════════════════════════════════════════════════
# OTP wizard back buttons
# ═══════════════════════════════════════════════════════════════════════════

def test_otp_wizard_nav_kb_has_back_and_cancel():
    from app.handlers.helper_otp_wizard import _nav_kb
    kb = _nav_kb(back_cb="hlp:otp:back:phone")
    buttons = [btn for row in kb.inline_keyboard for btn in row]
    cbs = [btn.callback_data for btn in buttons]
    assert "hlp:otp:back:phone" in cbs
    assert "hlp:otp:cancel" in cbs


def test_otp_wizard_nav_kb_cancel_only():
    from app.handlers.helper_otp_wizard import _nav_kb
    from app.utils.ui import CB
    kb = _nav_kb()
    buttons = [btn for row in kb.inline_keyboard for btn in row]
    cbs = [btn.callback_data for btn in buttons]
    assert "hlp:otp:cancel" in cbs
    assert CB["WZ_HOME"] in cbs
    assert len(cbs) == 2


# ═══════════════════════════════════════════════════════════════════════════
# Helper detail shows device + proxy info
# ═══════════════════════════════════════════════════════════════════════════

def test_helper_detail_keyboard_has_proxy_button():
    from app.utils.ui import CB, KeyboardFactory
    kb = KeyboardFactory.helper_detail("en", 5, "active")
    cbs = [btn.callback_data for row in kb.inline_keyboard for btn in row]
    assert f"{CB['HLP_SET_PROXY_PREFIX']}5" in cbs


# ═══════════════════════════════════════════════════════════════════════════
# i18n parity
# ═══════════════════════════════════════════════════════════════════════════

def test_otp_i18n_parity():
    from tests.i18n_test_utils import load_en_i18n, load_fa_i18n

    fa = load_fa_i18n()
    en = load_en_i18n()
    fa_keys = set(fa.get("admin", {}).get("helpers", {}).keys())
    en_keys = set(en.get("admin", {}).get("helpers", {}).keys())
    assert fa_keys == en_keys, f"Mismatch: {fa_keys ^ en_keys}"

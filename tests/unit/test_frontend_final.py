"""Tests for final frontend gaps: favorites list/remove, i18n parity."""
from __future__ import annotations

import json
import os

import pytest

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./.pytest-test-mode.db")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("BOT_TOKEN", "test")
os.environ.setdefault("API_ID", "12345")
os.environ.setdefault("API_HASH", "testhash")
os.environ.setdefault("DEVELOPER_ID", "123456789")


def test_fav_list_cb_constant():
    from app.utils.ui import CB
    assert "PB_FAV_LIST" in CB
    assert "PAGE_FAV" in CB
    assert "FAV_RM_PREFIX" in CB
    assert "PB_FAV_RM" not in CB


def test_fav_list_handler_exists():
    from pathlib import Path
    src = Path("app/handlers/callbacks.py").read_text()
    assert "pb_fav_list" in src
    assert "pb_fav_rm" in src
    assert "_render_fav_page" in src
    assert "PB_FAV_LIST" in src


def test_fav_remove_handler_deletes_with_user_check():
    from pathlib import Path
    src = Path("app/handlers/callbacks.py").read_text()
    assert "Favorite.user_id == query.from_user.id" in src


def test_fav_i18n_keys():
    from tests.i18n_test_utils import load_en_i18n, load_fa_i18n

    fa = load_fa_i18n()
    fav = fa["favorites"]
    assert "list_header" in fav
    assert "remove_btn" in fav
    assert "removed_toast" in fav

    en = load_en_i18n()
    efav = en["favorites"]
    assert "list_header" in efav
    assert "remove_btn" in efav
    assert "removed_toast" in efav


def test_post_install_guide_is_url_button():
    """The post-install guide is a URL button, not a callback constant."""
    from pathlib import Path
    from app.utils.ui import CB

    src = Path("app/utils/ui.py").read_text()
    assert "_url_btn" in src
    assert "guide_channel_btn" in src
    assert "POST_INSTALL_GUIDE" not in CB


def test_post_install_add_helper_not_in_keyboard():
    """No unused post-install helper callback constant is exposed."""
    from pathlib import Path
    from app.utils.ui import CB

    src = Path("app/utils/ui.py").read_text()
    keyboard_section = src[src.index("def post_install_panel"):src.index("def post_install_panel") + 1000]
    assert "POST_INSTALL_ADD_HELPER" not in keyboard_section
    assert "POST_INSTALL_ADD_HELPER" not in CB

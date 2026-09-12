from __future__ import annotations

import json
import os
import re
from pathlib import Path
from types import SimpleNamespace

from app.utils.pagination import paginate_keyboard
from app.utils.ui import CB, KeyboardFactory

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./.pytest-test-mode.db")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("BOT_TOKEN", "test")
os.environ.setdefault("API_ID", "12345")
os.environ.setdefault("API_HASH", "testhash")
os.environ.setdefault("DEVELOPER_ID", "123456789")


def _callback_data_set(kb) -> set[str]:
    return {
        btn.callback_data
        for row in getattr(kb, "inline_keyboard", [])
        for btn in row
        if getattr(btn, "callback_data", None)
    }


def _assert_callback_data_lengths(callbacks: set[str]) -> None:
    over_limit = sorted(
        (cb, len(cb.encode("utf-8")))
        for cb in callbacks
        if len(cb.encode("utf-8")) > 64
    )
    assert not over_limit, f"callback_data over Telegram 64-byte limit: {over_limit}"


def test_risk_scope_callbacks_have_registered_patterns():
    callbacks: set[str] = set()

    callbacks.update(_callback_data_set(KeyboardFactory.helper_home("en", 1, 0, 0)))
    callbacks.update(_callback_data_set(KeyboardFactory.helper_add_menu("en")))
    callbacks.update(_callback_data_set(KeyboardFactory.helper_detail("en", 5, "active")))
    callbacks.update(_callback_data_set(KeyboardFactory.helper_detail("en", 5, "disabled")))
    callbacks.update(_callback_data_set(KeyboardFactory.helper_detail("en", 5, "quarantined")))

    bc = SimpleNamespace(
        id=9,
        target_scope="users",
        status="queued",
        sent_count=0,
        total_recipients=0,
    )
    callbacks.update(_callback_data_set(KeyboardFactory.broadcast_history("en", [bc])))
    callbacks.update(_callback_data_set(paginate_keyboard("en", page=1, total_pages=3, cb_prefix="pg:test:")))

    sampled = {
        cb for cb in callbacks
        if cb and (cb.startswith("hlp:") or cb.startswith("bc:") or cb == CB["NOOP"])
    }
    sampled.add(f"{CB['BC_CANCEL']}:9")

    patterns = [
        re.compile(rf"^{re.escape(CB['NOOP'])}$"),
        re.compile(rf"^{re.escape(CB['HLP_HOME'])}$"),
        re.compile(rf"^{re.escape(CB['HLP_ADD'])}$"),
        re.compile(rf"^{re.escape(CB['HLP_LIST'])}$"),
        re.compile(rf"^{re.escape(CB['HLP_ROTATE_KEY'])}$"),
        re.compile(rf"^{re.escape(CB['HLP_HEALTH_CHECK'])}$"),
        re.compile(rf"^{re.escape(CB['HLP_STATS'])}$"),
        re.compile(rf"^{re.escape(CB['HLP_ADD_OTP'])}$"),
        re.compile(rf"^{re.escape(CB['HLP_IMPORT_SESSION'])}$"),
        re.compile(rf"^{re.escape(CB['HLP_DETAIL_PREFIX'])}\d+$"),
        re.compile(rf"^{re.escape(CB['HLP_SET_PROXY_PREFIX'])}\d+$"),
        re.compile(rf"^{re.escape(CB['HLP_ENABLE'])}\d+$"),
        re.compile(rf"^{re.escape(CB['HLP_DISABLE'])}\d+$"),
        re.compile(rf"^{re.escape(CB['HLP_QUARANTINE'])}\d+$"),
        re.compile(rf"^{re.escape(CB['HLP_UNQUARANTINE'])}\d+$"),
        re.compile(r"^hlp:proxy:cancel$"),
        re.compile(r"^hlp:otp:cancel$"),
        re.compile(r"^hlp:otp:back:phone$"),
        re.compile(r"^hlp:otp:back:code$"),
        re.compile(r"^hlp:imp:back:session$"),
        re.compile(r"^hlp:imp:back:phone$"),
        re.compile(r"^hlp:imp:back:max_calls$"),
        re.compile(rf"^{re.escape(CB['BC_DETAIL'])}:\d+$"),
        re.compile(rf"^{re.escape(CB['BC_CANCEL'])}:\d+$"),
    ]

    for cb in sorted(sampled):
        matched = [rx.pattern for rx in patterns if rx.match(cb)]
        assert matched, f"missing route pattern for callback: {cb}"
        assert len(matched) == 1, f"ambiguous route pattern for callback {cb}: {matched}"


def test_no_literal_callback_drift_for_hardened_families():
    broadcast_panel_text = Path("app/handlers/broadcast_panel.py").read_text(encoding="utf-8")
    callbacks_text = Path("app/handlers/callbacks.py").read_text(encoding="utf-8")
    pagination_text = Path("app/utils/pagination.py").read_text(encoding="utf-8")
    ui_text = Path("app/utils/ui.py").read_text(encoding="utf-8")

    assert r"^bc:detail:" not in broadcast_panel_text
    assert r"^bc:cancel:" not in broadcast_panel_text
    assert 'callback_data="noop"' not in callbacks_text
    assert 'callback_data="noop"' not in pagination_text
    assert "f\"{CB['BC_DETAIL']}:{bc.id}\"" in ui_text


def test_representative_generated_callback_data_stays_within_telegram_limit():
    callbacks: set[str] = set(CB.values())

    callbacks.update(_callback_data_set(KeyboardFactory.helper_home("en", 1, 0, 0)))
    callbacks.update(_callback_data_set(KeyboardFactory.helper_add_menu("en")))
    callbacks.update(_callback_data_set(KeyboardFactory.helper_detail("en", 5, "active")))
    callbacks.update(
        _callback_data_set(
            KeyboardFactory.helper_rotate_key_confirm("en", 123456789, 2_000_000_000)
        )
    )
    callbacks.update(
        _callback_data_set(
            KeyboardFactory.helper_state_action_confirm(
                "en",
                CB["HLP_ENABLE_CONFIRM_PREFIX"],
                CB["HLP_ENABLE_ABORT_PREFIX"],
                123456,
                123456789,
                2_000_000_000,
            )
        )
    )
    callbacks.update(
        _callback_data_set(
            KeyboardFactory.group_clear_admins_confirm(
                "en",
                -1001234567890,
                123456789,
                2_000_000_000,
            )
        )
    )
    callbacks.update(
        _callback_data_set(
            KeyboardFactory.broadcast_cancel_confirm(
                "en",
                987654,
                123456789,
                2_000_000_000,
            )
        )
    )

    assets_dir = Path("app/assets")
    tv_channels = json.loads((assets_dir / "tv_channels.json").read_text(encoding="utf-8"))
    sat_channels = json.loads((assets_dir / "satellite_channels.json").read_text(encoding="utf-8"))
    radio_stations = json.loads((assets_dir / "radio_stations.json").read_text(encoding="utf-8"))

    callbacks.update(_callback_data_set(KeyboardFactory.tv_channels_menu("en", tv_channels)))
    for page in range(max(1, (len(sat_channels) + 7) // 8)):
        callbacks.update(
            _callback_data_set(
                KeyboardFactory.satellite_channels_menu("en", sat_channels, page=page, per_page=8)
            )
        )
    callbacks.update(f"pb:radio:{station['id']}" for station in radio_stations)
    callbacks.add("search:play:dQw4w9WgXcQ")

    _assert_callback_data_lengths(callbacks)

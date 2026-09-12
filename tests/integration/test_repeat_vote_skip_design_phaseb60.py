"""Phase B6-0: design-only assertions for repeat vs vote-skip (no implementation)."""

from __future__ import annotations

from pathlib import Path


def test_design_document_exists():
    doc = Path("docs/reports/archive/phase_b6_repeat_vote_skip_decision.md")
    assert doc.is_file()
    text = doc.read_text(encoding="utf-8")
    assert "vote_skip_enabled" in text
    assert "repeat_enabled" in text
    assert "Option A" in text


def test_repeat_toggle_hidden_from_group_keyboard():
    from app.utils.ui import _GRP_VISIBLE_SETTING_TOGGLES

    assert "repeat" not in _GRP_VISIBLE_SETTING_TOGGLES


def test_grp_repeat_still_maps_to_vote_skip_enabled():
    from app.handlers.group_panel import _SETTING_TOGGLE_MAP
    from app.utils.ui import CB

    assert _SETTING_TOGGLE_MAP[CB["GRP_REPEAT"]] == "vote_skip_enabled"


def test_vote_skip_not_read_in_playback_runtime():
    for rel in (
        "app/handlers/callbacks.py",
        "app/services/call_service.py",
        "app/handlers/playback.py",
        "app/main.py",
    ):
        source = Path(rel).read_text(encoding="utf-8")
        assert "vote_skip_enabled" not in source


def test_pb_repeat_uses_in_memory_repeat_state():
    source = Path("app/handlers/callbacks.py").read_text(encoding="utf-8")
    assert "pb:repeat" in source or "PB_REPEAT_TOGGLE" in source
    assert "set_repeat_state" in source
    assert "get_repeat_state" in source


def test_no_repeat_enabled_column_yet():
    source = Path("app/database/models.py").read_text(encoding="utf-8")
    assert "vote_skip_enabled" in source
    assert "repeat_enabled" not in source


def test_design_recommends_defer_vote_skip_runtime():
    doc = Path("docs/reports/archive/phase_b6_repeat_vote_skip_decision.md").read_text(encoding="utf-8")
    assert "does not exist" in doc.lower() or "no runtime" in doc.lower()

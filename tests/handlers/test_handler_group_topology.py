from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from app.handlers.priority import (
    CALL_SECURITY_RAW_GROUP,
    HANDLER_GROUP_TOPOLOGY,
    HELPER_OTP_INPUT_GROUP,
    HELPER_PROXY_INPUT_GROUP,
    SAFE_ASK_LISTENER_GROUP,
)


ROOT = Path(__file__).resolve().parents[2]


def test_handler_group_inventory_gate(tmp_path: Path):
    output = tmp_path / "handler_group_inventory.json"
    env = os.environ.copy()
    env.setdefault("TEST_MODE", "1")
    env.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./.handler-group-test.db")
    env.setdefault("REDIS_URL", "redis://localhost:6379/15")
    env.setdefault("BOT_TOKEN", "test")
    env.setdefault("API_ID", "12345")
    env.setdefault("API_HASH", "testhash")
    env.setdefault("DEVELOPER_ID", "123456789")

    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/audit_handler_group_topology.py"),
            "--check",
            "--out",
            str(output),
        ],
        cwd=ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr

    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["defects"] == []
    assert report["totals"]["custom_multi_update_handler_classes"] == 0

    raw_rows = [
        row for row in report["handlers"]
        if row["handler_type"] == "RawUpdateHandler"
    ]
    assert len(raw_rows) == 1
    assert raw_rows[0]["group"] == CALL_SECURITY_RAW_GROUP
    assert raw_rows[0]["filter"]["filterless"] is True
    assert raw_rows[0]["accepted_update_types"] == ["RawUpdate(any)"]
    assert raw_rows[0]["processed_update_types"] == [
        "UpdateGroupCall",
        "UpdateGroupCallParticipants",
    ]

    groups_by_symbol = {row["symbol"]: row["group"] for row in report["handlers"]}
    assert groups_by_symbol["register.<locals>.otp_text_handler"] == HELPER_OTP_INPUT_GROUP
    assert groups_by_symbol["register.<locals>.hlp_proxy_input"] == HELPER_PROXY_INPUT_GROUP
    assert HELPER_OTP_INPUT_GROUP != HELPER_PROXY_INPUT_GROUP

    safe_ask = next(
        row for row in report["dynamic_handlers"]
        if row["module"] == "app.utils.safe_ask"
    )
    assert safe_ask["group"] == SAFE_ASK_LISTENER_GROUP


def test_every_canonical_group_documents_allowed_handler_types_and_propagation():
    for group, spec in HANDLER_GROUP_TOPOLOGY.items():
        assert isinstance(group, int)
        assert spec["names"]
        assert spec["handler_types"]
        assert spec["purpose"]
        assert spec["propagation"]

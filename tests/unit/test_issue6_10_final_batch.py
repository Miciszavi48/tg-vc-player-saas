from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import Text


def _load_install_module():
    spec = importlib.util.spec_from_file_location(
        "issue6_10_install_under_test",
        Path("app/handlers/install.py"),
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_lock_key_to_bigint_is_stable_sha256() -> None:
    from app.utils.cache import _lock_key_to_bigint

    key = "credit:-1001234567890"
    expected = int.from_bytes(hashlib.sha256(key.encode("utf-8")).digest()[:8], "big")
    expected &= 0x7FFF_FFFF_FFFF_FFFF

    assert _lock_key_to_bigint(key) == expected
    assert _lock_key_to_bigint(key) == _lock_key_to_bigint(key)
    assert 0 <= _lock_key_to_bigint(key) <= 0x7FFF_FFFF_FFFF_FFFF


def test_channel_admin_limit_rejects_only_over_limit() -> None:
    src = Path("app/handlers/install.py").read_text(encoding="utf-8")

    assert "if admin_count > max_admins:" in src
    assert "if admin_count < max_admins:" not in src
    assert "from pyrogram.enums import ChatMembersFilter" in src
    assert "filter=ChatMembersFilter.ADMINISTRATORS" in src


def test_sudo_leave_installs_deactivates_only_recorded_chat_type() -> None:
    src = Path("app/handlers/sudo_panel.py").read_text(encoding="utf-8")

    assert "for cid, chat_type in installs.items():" in src
    assert 'if chat_type == "channel":' in src
    assert "await channel_repo.deactivate_channel(cid)" in src
    assert "await group_repo.deactivate_group(cid)" in src
    assert "# Keep install state in sync for both groups and channels." not in src


@pytest.mark.asyncio
async def test_channel_install_over_admin_limit_leaves(monkeypatch) -> None:
    install = _load_install_module()

    async def admins(_chat_id, filter=None):  # noqa: ARG001
        for _ in range(3):
            yield SimpleNamespace()

    client = SimpleNamespace(
        get_chat_members=lambda chat_id, filter=None: admins(chat_id, filter=filter),
        send_message=AsyncMock(),
        leave_chat=AsyncMock(),
    )

    monkeypatch.setattr(install.blacklist_repo, "is_blacklisted", AsyncMock(return_value=False))
    monkeypatch.setattr(install.settings, "MAX_CHANNEL_ADMINS", 2)

    await install._do_install(client, -10055, "Channel", "channel", 123)

    client.send_message.assert_awaited_once()
    client.leave_chat.assert_awaited_once_with(-10055)


@pytest.mark.asyncio
async def test_channel_uninstall_deactivates_channel_not_group(monkeypatch) -> None:
    install = _load_install_module()

    deactivate_channel = AsyncMock()
    deactivate_group = AsyncMock()
    monkeypatch.setattr(install, "acquire_lock", AsyncMock(return_value="tok"))
    monkeypatch.setattr(install, "release_lock", AsyncMock(return_value=True))
    monkeypatch.setattr(install.channel_repo, "deactivate_channel", deactivate_channel)
    monkeypatch.setattr(install.group_repo, "deactivate_group", deactivate_group)
    monkeypatch.setattr(install.log_repo, "log_install", AsyncMock())
    monkeypatch.setattr(install.NotificationService, "notify_uninstall", AsyncMock())

    await install._on_left(SimpleNamespace(), -10077, "Channel", "channel", 42)

    deactivate_channel.assert_awaited_once_with(-10077)
    deactivate_group.assert_not_awaited()


@pytest.mark.asyncio
async def test_group_uninstall_deactivates_group_not_channel(monkeypatch) -> None:
    install = _load_install_module()

    deactivate_channel = AsyncMock()
    deactivate_group = AsyncMock()
    monkeypatch.setattr(install, "acquire_lock", AsyncMock(return_value="tok"))
    monkeypatch.setattr(install, "release_lock", AsyncMock(return_value=True))
    monkeypatch.setattr(install.channel_repo, "deactivate_channel", deactivate_channel)
    monkeypatch.setattr(install.group_repo, "deactivate_group", deactivate_group)
    monkeypatch.setattr(install.log_repo, "log_install", AsyncMock())
    monkeypatch.setattr(install.NotificationService, "notify_uninstall", AsyncMock())

    await install._on_left(SimpleNamespace(), -10088, "Group", "group", 42)

    deactivate_group.assert_awaited_once_with(-10088)
    deactivate_channel.assert_not_awaited()


def test_playback_state_source_uses_text_column() -> None:
    from app.database.models import PlaybackState

    assert isinstance(PlaybackState.__table__.c.source.type, Text)


def test_playback_state_migration_and_safe_warning_logging() -> None:
    migration = Path(
        "app/database/migrations/versions/0011_playback_state_source_text.py"
    ).read_text(encoding="utf-8")
    call_service = Path("app/services/call_service.py").read_text(encoding="utf-8")

    assert 'op.alter_column(\n        "playback_states",' in migration
    assert "type_=sa.Text()" in migration
    assert "Failed to save playback state for chat %s (%s)" in call_service
    assert "Failed to save playback state for %s" not in call_service


def test_next_recurring_run_skips_expired_occurrences() -> None:
    from app.scheduler import _next_recurring_run

    start = datetime(2026, 5, 29, 0, 0, tzinfo=timezone.utc)
    now = datetime(2026, 5, 29, 7, 30, tzinfo=timezone.utc)

    assert _next_recurring_run(start, 3, now) == datetime(
        2026, 5, 29, 9, 0, tzinfo=timezone.utc
    )


def test_schedule_broadcast_job_uses_stable_ids_and_replace_existing(monkeypatch) -> None:
    import app.scheduler as scheduler_mod

    fake_scheduler = SimpleNamespace(add_job=MagicMock())
    monkeypatch.setattr(scheduler_mod, "scheduler", fake_scheduler)

    now = datetime(2026, 5, 29, 12, 0, tzinfo=timezone.utc)
    bc = SimpleNamespace(id=12, run_at=now - timedelta(hours=1), interval_hours=None)

    job_id = scheduler_mod.schedule_broadcast_job(SimpleNamespace(), bc, now=now)

    assert job_id == "test:bc_sched_12"
    kwargs = fake_scheduler.add_job.call_args.kwargs
    assert kwargs["id"] == "test:bc_sched_12"
    assert kwargs["replace_existing"] is True
    assert kwargs["run_date"] == now


@pytest.mark.asyncio
async def test_restore_scheduled_broadcasts_loads_pending_jobs(monkeypatch) -> None:
    import app.scheduler as scheduler_mod

    pending = [
        SimpleNamespace(id=1, run_at=datetime.now(timezone.utc), interval_hours=None),
        SimpleNamespace(id=2, run_at=datetime.now(timezone.utc), interval_hours=6),
    ]
    scheduled: list[int] = []

    monkeypatch.setattr(
        scheduler_mod.broadcast_repo,
        "get_pending_scheduled",
        AsyncMock(return_value=pending),
    )
    monkeypatch.setattr(
        scheduler_mod,
        "schedule_broadcast_job",
        lambda client, bc: scheduled.append(bc.id) or f"bc_sched_{bc.id}",
    )

    await scheduler_mod.restore_scheduled_broadcasts(SimpleNamespace())

    assert scheduled == [1, 2]


@pytest.mark.asyncio
async def test_recurring_broadcast_stays_pending_for_next_run(monkeypatch) -> None:
    import app.services.broadcast_service_v2 as bc_mod
    from app.services.broadcast_service_v2 import BroadcastServiceV2

    bc = SimpleNamespace(
        id=77,
        status="pending",
        mode="send",
        target_scope="users",
        filter_type="all",
        interval_hours=6,
        payload_type="text",
        text_content="hello",
        entities_json=None,
        caption=None,
        caption_entities_json=None,
        file_id=None,
    )
    finish = AsyncMock()

    monkeypatch.setattr(bc_mod, "acquire_lock", AsyncMock(return_value="tok"))
    monkeypatch.setattr(bc_mod, "release_lock", AsyncMock(return_value=True))
    monkeypatch.setattr(bc_mod, "set_bc_active", AsyncMock())
    monkeypatch.setattr(bc_mod, "clear_bc_active", AsyncMock())
    monkeypatch.setattr(bc_mod, "clear_bc_progress", AsyncMock())
    monkeypatch.setattr(bc_mod, "get_bc_progress", AsyncMock(return_value=(0, 0, 0)))
    monkeypatch.setattr(bc_mod, "track_event", AsyncMock())
    monkeypatch.setattr(bc_mod.broadcast_repo, "get_by_id", AsyncMock(return_value=bc))
    monkeypatch.setattr(bc_mod.broadcast_repo, "update_status", AsyncMock())
    monkeypatch.setattr(bc_mod.broadcast_repo, "update_total", AsyncMock())
    monkeypatch.setattr(bc_mod.broadcast_repo, "finish", finish)
    monkeypatch.setattr(BroadcastServiceV2, "_count_recipients", AsyncMock(return_value=0))
    monkeypatch.setattr(BroadcastServiceV2, "_get_recipient_batch", AsyncMock(return_value=[]))

    await BroadcastServiceV2.execute(SimpleNamespace(), 77)

    finish.assert_awaited_once_with(77, "pending", 0, 0)


def test_unsupported_playback_controls_return_specific_messages() -> None:
    from tests.i18n_test_utils import load_en_i18n, load_fa_i18n

    callbacks = Path("app/handlers/callbacks.py").read_text(encoding="utf-8")
    en = json.dumps(load_en_i18n(), ensure_ascii=False)
    fa = json.dumps(load_fa_i18n(), ensure_ascii=False)

    assert "playback.controls.previous_unavailable" in callbacks
    assert "change_playback_speed" in callbacks
    assert "playback.type_hints.audio" in callbacks
    assert "playback.type_hints.video" in callbacks
    assert "playback.type_hints.download" in callbacks
    assert "Previous track is unavailable because history is not tracked." in en
    assert "Playback speed: {speed}x" in en
    assert "Speed control is not supported for this media type." in en
    assert "Speed change failed." in en
    assert "پخش آهنگ قبلی به دلیل عدم ذخیره تاریخچه امکان‌پذیر نیست." in fa
    assert "سرعت پخش: {speed}x" in fa
    assert "تغییر سرعت برای این نوع رسانه پشتیبانی نمی‌شود." in fa
    assert "تغییر سرعت ناموفق بود." in fa


def test_log_runtime_files_are_ignored_and_tracked_logs_removed() -> None:
    gitignore = Path(".gitignore").read_text(encoding="utf-8")

    assert "app/logs/*" in gitignore
    assert "!app/logs/.gitkeep" in gitignore
    assert ".pytest-test-mode.db" in gitignore
    assert not Path("app/logs/bot.log").exists()
    assert not Path("app/logs/bot_error.log").exists()
    assert Path("app/logs/.gitkeep").exists()

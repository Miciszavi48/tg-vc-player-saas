"""Tests for Developer Panel media JSON export (Phase 2)."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./.pytest-test-mode.db")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("BOT_TOKEN", "test")
os.environ.setdefault("API_ID", "12345")
os.environ.setdefault("API_HASH", "testhash")
os.environ.setdefault("DEVELOPER_ID", "123456789")

if "pyromod" not in sys.modules:
    pyromod_module = ModuleType("pyromod")
    pyromod_exceptions = ModuleType("pyromod.exceptions")
    pyromod_exceptions.ListenerStopped = Exception
    pyromod_module.exceptions = pyromod_exceptions
    sys.modules["pyromod"] = pyromod_module
    sys.modules["pyromod.exceptions"] = pyromod_exceptions

from app.config.settings import settings
from app.handlers import dev_panel
from app.services.media_health_service import (
    TOP_FILES_LIMIT,
    build_media_report,
    redact_media_source,
    write_media_report_tempfile,
)
from app.utils.i18n import t
from app.utils.ui import CB, KeyboardFactory


class _RecorderBot:
    def __init__(self) -> None:
        self.callback_handlers: list = []

    def on_callback_query(self, *args, **kwargs):  # noqa: ANN001, ANN002
        def _decorator(fn):
            self.callback_handlers.append(fn)
            return fn

        return _decorator

    def __getattr__(self, name: str):
        if name.startswith("on_"):

            def _register_other(*args, **kwargs):  # noqa: ANN001, ANN002
                def _decorator(fn):
                    return fn

                return _decorator

            return _register_other
        raise AttributeError(name)


def _handler_by_name(handlers: list, name: str):
    for fn in handlers:
        if fn.__name__ == name:
            return fn
    raise AssertionError(f"handler not found: {name}")


def _kb_callbacks(kb) -> set[str]:
    return {
        btn.callback_data
        for row in kb.inline_keyboard
        for btn in row
        if btn.callback_data
    }


def _pm_query(user_id: int, data: str):
    return SimpleNamespace(
        from_user=SimpleNamespace(id=user_id, first_name="Tester"),
        data=data,
        answer=AsyncMock(),
        message=SimpleNamespace(
            chat=SimpleNamespace(id=100, type=SimpleNamespace(value="private")),
            edit_text=AsyncMock(),
            reply=AsyncMock(),
        ),
    )


def _sample_report() -> dict:
    return {
        "generated_at": "2026-06-02T00:00:00+00:00",
        "health": {"yt_dlp": {"available": True}, "ffmpeg": {"available": True}},
        "storage": {
            "media_cache": {"top_largest_files": [{"relative_path": "ab/hash.opus", "size_bytes": 1}]},
            "downloads": {"top_largest_files": []},
        },
        "runtime": {"active_calls_count": 0},
        "playback": {"active_states": [], "queue_summary": []},
        "limits": {"truncated": False},
    }


@pytest.mark.asyncio
async def test_media_health_keyboard_contains_export_button():
    kb = KeyboardFactory.dev_media_health("en")
    assert CB["DEV_MEDIA_EXPORT"] in _kb_callbacks(kb)


@pytest.mark.asyncio
async def test_non_developer_cannot_export_media_report():
    bot = _RecorderBot()
    dev_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "dev_media_export")
    query = _pm_query(9999, CB["DEV_MEDIA_EXPORT"])

    with patch("app.handlers.dev_panel.build_media_report", AsyncMock()) as build_mock:
        result = await handler(SimpleNamespace(), query)

    assert result is None
    build_mock.assert_not_awaited()
    query.message.reply.assert_not_awaited()


@pytest.mark.asyncio
async def test_developer_export_sends_document_and_cleans_temp_file(tmp_path, monkeypatch):
    bot = _RecorderBot()
    dev_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "dev_media_export")
    query = _pm_query(settings.DEVELOPER_ID, CB["DEV_MEDIA_EXPORT"])
    client = SimpleNamespace(send_document=AsyncMock())

    temp_file = tmp_path / "report.json"
    temp_file.write_text("{}", encoding="utf-8")

    with (
        patch("app.handlers.dev_panel.build_media_report", AsyncMock(return_value=_sample_report())),
        patch(
            "app.handlers.dev_panel.write_media_report_tempfile",
            return_value=(temp_file, "media_report_20260602_120000.json"),
        ),
    ):
        await handler(client, query)

    client.send_document.assert_awaited_once()
    assert not temp_file.exists()
    query.message.edit_text.assert_awaited_once()


@pytest.mark.asyncio
async def test_export_failure_shows_safe_message():
    bot = _RecorderBot()
    dev_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "dev_media_export")
    query = _pm_query(settings.DEVELOPER_ID, CB["DEV_MEDIA_EXPORT"])
    client = SimpleNamespace(send_document=AsyncMock())

    with patch("app.handlers.dev_panel.build_media_report", AsyncMock(side_effect=RuntimeError("boom"))):
        await handler(client, query)

    client.send_document.assert_not_awaited()
    body = query.message.edit_text.await_args.args[0]
    assert t("fa", "media_health.export_failed") in body


def test_redact_url_strips_query_and_adds_fingerprint():
    redacted = redact_media_source(
        "https://www.youtube.com/watch?v=abc12345678&si=secret&token=abc"
    )
    assert redacted["kind"] == "url"
    assert "secret" not in json.dumps(redacted)
    assert "token" not in json.dumps(redacted)
    assert redacted["host"] == "www.youtube.com"
    assert "url_fingerprint" in redacted


def test_redact_unsafe_local_path():
    redacted = redact_media_source("C:\\Windows\\temp\\evil.mp3")
    assert redacted["kind"] == "redacted"


def test_redact_trusted_local_path_is_relative(monkeypatch, tmp_path):
    cache_root = tmp_path / "media_cache"
    cache_root.mkdir()
    media_file = cache_root / "ab" / "file.opus"
    media_file.parent.mkdir(parents=True)
    media_file.write_bytes(b"x")

    monkeypatch.setattr(settings, "MEDIA_CACHE_PATH", str(cache_root))
    monkeypatch.setattr(settings, "DOWNLOADS_PATH", str(tmp_path / "downloads"))

    redacted = redact_media_source(str(media_file))
    assert redacted["kind"] == "local"
    assert "media_cache" not in redacted["relative_path"] or redacted["relative_path"].startswith("ab/")
    if cache_root.drive:
        assert str(cache_root.drive) not in redacted.get("relative_path", "")


@pytest.mark.asyncio
async def test_build_media_report_sections_present(monkeypatch, tmp_path):
    cache_root = tmp_path / "media_cache"
    cache_root.mkdir()
    (cache_root / "aa").mkdir()
    (cache_root / "aa" / "big.bin").write_bytes(b"x" * 200)
    downloads_root = tmp_path / "downloads"
    downloads_root.mkdir()

    monkeypatch.setattr(settings, "MEDIA_CACHE_PATH", str(cache_root))
    monkeypatch.setattr(settings, "DOWNLOADS_PATH", str(downloads_root))

    with (
        patch("app.services.media_health_service.build_health_snapshot", AsyncMock()),
        patch("app.services.media_health_service._fetch_active_playback_states", AsyncMock(return_value=([], False))),
        patch("app.services.media_health_service._fetch_queue_summary", AsyncMock(return_value=([], False))),
    ):
        from app.services.media_health_service import DirStats, MediaHealthSnapshot, ToolVersion, YoutubeProbeResult

        snap = MediaHealthSnapshot(
            ytdlp=ToolVersion(True, "v", ""),
            ffmpeg=ToolVersion(True, "ff", ""),
            cache_dir=DirStats(True, 1, 200, False),
            cache_size_bytes=200,
            downloads_dir=DirStats(True, 0, 0, False),
            active_calls=0,
            download_limit=10,
            download_available=10,
            youtube_probe=YoutubeProbeResult(True, False, 0, ""),
            last_eviction=None,
        )
        build_health_snapshot_mock = AsyncMock(return_value=snap)
        with patch("app.services.media_health_service.build_health_snapshot", build_health_snapshot_mock):
            report = await build_media_report()

    assert "health" in report
    assert "storage" in report
    assert "runtime" in report
    assert "playback" in report
    assert "ranking" in report
    assert report["ranking"]["generated_from"] == "media_events"
    top = report["storage"]["media_cache"]["top_largest_files"]
    assert top
    assert "relative_path" in top[0]
    assert "/" not in top[0]["relative_path"][:1] or top[0]["relative_path"].startswith("aa/")


@pytest.mark.asyncio
async def test_top_files_limited(monkeypatch, tmp_path):
    cache_root = tmp_path / "media_cache"
    cache_root.mkdir()
    for idx in range(TOP_FILES_LIMIT + 5):
        path = cache_root / f"f{idx}.bin"
        path.write_bytes(b"x" * (idx + 1))

    monkeypatch.setattr(settings, "MEDIA_CACHE_PATH", str(cache_root))
    monkeypatch.setattr(settings, "DOWNLOADS_PATH", str(tmp_path / "downloads"))

    from app.services.media_health_service import _collect_top_largest_files

    top, _count, _total, _trunc = _collect_top_largest_files(cache_root)
    assert len(top) <= TOP_FILES_LIMIT


def test_write_media_report_tempfile_roundtrip(tmp_path, monkeypatch):
    out = tmp_path / "out"
    monkeypatch.chdir(tmp_path)
    path, filename = write_media_report_tempfile(_sample_report())
    try:
        assert path.exists()
        assert filename.startswith("media_report_")
        loaded = json.loads(path.read_text(encoding="utf-8"))
        assert loaded["generated_at"] == _sample_report()["generated_at"]
    finally:
        path.unlink(missing_ok=True)

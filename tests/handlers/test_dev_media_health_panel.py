"""Tests for Developer Panel media / webservice health (Phase 1)."""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, patch

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
    DirStats,
    EvictionSnapshot,
    MediaHealthSnapshot,
    ToolVersion,
    YoutubeProbeResult,
    YTDLP_PROBE_URL,
    _run_ytdlp_simulate,
    build_health_snapshot,
    format_health_report,
    probe_youtube_cached,
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
        ),
    )


def _sample_snapshot() -> MediaHealthSnapshot:
    return MediaHealthSnapshot(
        ytdlp=ToolVersion(ok=True, version="2025.01.01", detail=""),
        ffmpeg=ToolVersion(ok=True, version="ffmpeg version 6.0", detail=""),
        cache_dir=DirStats(exists=True, file_count=10, total_bytes=1024**3, truncated=False),
        cache_size_bytes=1024**3,
        downloads_dir=DirStats(exists=True, file_count=2, total_bytes=512, truncated=False),
        active_calls=1,
        download_limit=10,
        download_available=9,
        youtube_probe=YoutubeProbeResult(ok=True, cached=False, age_seconds=0, detail=""),
        last_eviction=EvictionSnapshot(at="2026-06-02T00:00:00+00:00", stale_removed=1, lru_removed=2),
        ytdlp_ejs=ToolVersion(ok=True, version="1.0", detail=""),
        javascript_runtime=ToolVersion(ok=True, version="deno 2.0", detail=""),
    )


@pytest.mark.asyncio
async def test_dev_settings_submenu_shows_webservice_check():
    kb = KeyboardFactory.dev_sub_settings("en")
    assert CB["DEV_MEDIA_HEALTH"] in _kb_callbacks(kb)


@pytest.mark.asyncio
async def test_media_health_panel_has_export_button():
    kb = KeyboardFactory.dev_media_health("en")
    assert CB["DEV_MEDIA_EXPORT"] in _kb_callbacks(kb)


@pytest.mark.asyncio
async def test_developer_handler_renders_health_report():
    bot = _RecorderBot()
    dev_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "dev_media_health")
    query = _pm_query(settings.DEVELOPER_ID, CB["DEV_MEDIA_HEALTH"])

    with patch(
        "app.handlers.dev_panel.build_health_snapshot",
        AsyncMock(return_value=_sample_snapshot()),
    ):
        await handler(SimpleNamespace(), query)

    query.message.edit_text.assert_awaited_once()
    body = query.message.edit_text.await_args.args[0]
    assert t("fa", "media_health.title") in body
    assert CB["WZ_BACK_PREFIX"] + "dev_settings" in _kb_callbacks(
        query.message.edit_text.await_args.kwargs["reply_markup"]
    )


@pytest.mark.asyncio
async def test_non_developer_cannot_open_media_health():
    bot = _RecorderBot()
    dev_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "dev_media_health")
    query = _pm_query(9999, CB["DEV_MEDIA_HEALTH"])

    with patch(
        "app.handlers.dev_panel.build_health_snapshot",
        AsyncMock(),
    ) as build_mock:
        result = await handler(SimpleNamespace(), query)

    assert result is None
    build_mock.assert_not_awaited()
    query.message.edit_text.assert_not_awaited()


@pytest.mark.asyncio
async def test_ytdlp_simulate_uses_fixed_url():
    with patch("app.services.media_health_service.subprocess.run") as run_mock:
        run_mock.return_value = SimpleNamespace(returncode=0, stdout="", stderr="")
        result = _run_ytdlp_simulate(YTDLP_PROBE_URL)
    assert result.ok is True
    cmd = run_mock.call_args.args[0]
    assert YTDLP_PROBE_URL in cmd
    assert "--simulate" in cmd


@pytest.mark.asyncio
async def test_probe_youtube_cache_hit_skips_subprocess():
    cached = {
        "ok": True,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "detail": "",
    }
    redis = AsyncMock()
    redis.get = AsyncMock(return_value=json.dumps(cached))

    with (
        patch("app.services.media_health_service.get_redis", AsyncMock(return_value=redis)),
        patch("app.services.media_health_service.subprocess.run") as run_mock,
    ):
        result = await probe_youtube_cached()

    assert result.cached is True
    assert result.ok is True
    run_mock.assert_not_called()


@pytest.mark.asyncio
async def test_media_health_i18n_keys_exist():
    snapshot = _sample_snapshot()
    for lang in ("fa", "en"):
        text = format_health_report(lang, snapshot)
        assert "[missing:" not in text
        assert t(lang, "panels.developer.webservice_check")
        assert "[missing:" not in t(lang, "panels.developer.webservice_check")


@pytest.mark.asyncio
async def test_build_health_snapshot_integration_mocked():
    with (
        patch(
            "app.services.media_health_service.get_ytdlp_version",
            AsyncMock(return_value=ToolVersion(ok=True, version="v1", detail="")),
        ),
        patch(
            "app.services.media_health_service.get_ffmpeg_version",
            AsyncMock(return_value=ToolVersion(ok=True, version="ff", detail="")),
        ),
        patch(
            "app.services.media_health_service.get_ytdlp_ejs_version",
            AsyncMock(return_value=ToolVersion(ok=True, version="ejs", detail="")),
        ),
        patch(
            "app.services.media_health_service.get_javascript_runtime_version",
            AsyncMock(return_value=ToolVersion(ok=True, version="deno 2", detail="")),
        ),
        patch(
            "app.services.media_health_service.get_dir_stats",
            AsyncMock(return_value=DirStats(True, 0, 0, False)),
        ),
        patch("app.services.media_health_service.get_cache_size_bytes", return_value=0),
        patch("app.services.media_health_service.CallService.get_active_calls", return_value={}),
        patch(
            "app.services.media_health_service.probe_youtube_cached",
            AsyncMock(
                return_value=YoutubeProbeResult(True, False, 0, ""),
            ),
        ),
        patch(
            "app.services.media_health_service.get_last_eviction_snapshot",
            AsyncMock(return_value=None),
        ),
        patch(
            "app.services.media_health_service._download_slot_info",
            return_value=(10, 10),
        ),
    ):
        snap = await build_health_snapshot()
    assert snap.ytdlp.ok is True
    assert snap.download_limit == 10
    assert snap.ytdlp_ejs is not None and snap.ytdlp_ejs.ok is True
    assert snap.javascript_runtime is not None and snap.javascript_runtime.ok is True

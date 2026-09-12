"""Tests for Developer Panel media URL ranking (Phase 4)."""
from __future__ import annotations

import json
import os
import sys
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
from app.services.media_event_service import (
    HostRankingEntry,
    MediaRankingSummary,
    RankingEntry,
    ranking_summary_to_json,
)
from app.services.media_health_service import (
    DirStats,
    MediaHealthSnapshot,
    ToolVersion,
    YoutubeProbeResult,
    build_media_report,
    format_media_ranking_report,
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


def _sample_summary(*, has_data: bool = True) -> MediaRankingSummary:
    if not has_data:
        return MediaRankingSummary((), (), (), (), 0, False)
    return MediaRankingSummary(
        top_played=(
            RankingEntry(
                redacted_url="https://youtube.com/watch",
                host="youtube.com",
                count=5,
                url_fingerprint="abc123",
            ),
        ),
        top_downloaded=(
            RankingEntry(
                redacted_url="https://example.com/track",
                host="example.com",
                count=2,
                url_fingerprint="def456",
            ),
        ),
        top_hosts=(HostRankingEntry(host="youtube.com", count=5),),
        recent_top_played=(
            RankingEntry(
                redacted_url="https://youtube.com/watch",
                host="youtube.com",
                count=3,
                url_fingerprint="abc123",
            ),
        ),
        total_events=7,
        has_data=True,
    )


@pytest.mark.asyncio
async def test_media_health_panel_has_ranking_button():
    kb = KeyboardFactory.dev_media_health("en")
    assert CB["DEV_MEDIA_RANKING"] in _kb_callbacks(kb)


@pytest.mark.asyncio
async def test_developer_can_open_ranking_panel():
    bot = _RecorderBot()
    dev_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "dev_media_ranking")
    query = _pm_query(settings.DEVELOPER_ID, CB["DEV_MEDIA_RANKING"])

    with patch(
        "app.handlers.dev_panel.get_media_ranking_summary",
        AsyncMock(return_value=_sample_summary()),
    ):
        await handler(SimpleNamespace(), query)

    body = query.message.edit_text.await_args.args[0]
    assert "https://youtube.com/watch" in body
    assert "token=" not in body


@pytest.mark.asyncio
async def test_non_developer_cannot_open_ranking():
    bot = _RecorderBot()
    dev_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "dev_media_ranking")
    query = _pm_query(9999, CB["DEV_MEDIA_RANKING"])

    with patch(
        "app.handlers.dev_panel.get_media_ranking_summary",
        AsyncMock(),
    ) as ranking_mock:
        result = await handler(SimpleNamespace(), query)

    assert result is None
    ranking_mock.assert_not_awaited()
    query.message.edit_text.assert_not_awaited()


def test_empty_ranking_shows_no_data_message():
    text = format_media_ranking_report("en", _sample_summary(has_data=False))
    assert t("en", "media_health.ranking_no_data") in text


def test_ranking_groups_by_fingerprint_in_json():
    payload = ranking_summary_to_json(_sample_summary())
    assert payload["top_played"][0]["url_fingerprint"] == "abc123"
    assert payload["top_played"][0]["count"] == 5


def test_ranking_shows_played_and_downloaded_counts():
    text = format_media_ranking_report("fa", _sample_summary())
    assert "5" in text
    assert "2" in text
    assert t("fa", "media_health.ranking_top_played") in text
    assert t("fa", "media_health.ranking_top_downloaded") in text


def test_ranking_never_displays_query_strings():
    summary = MediaRankingSummary(
        top_played=(
            RankingEntry(
                redacted_url="https://youtube.com/watch",
                host="youtube.com",
                count=1,
                url_fingerprint="x",
            ),
        ),
        top_downloaded=(),
        top_hosts=(),
        recent_top_played=(),
        total_events=1,
        has_data=True,
    )
    text = format_media_ranking_report("en", summary)
    assert "?" not in text
    assert "token" not in text.lower()


@pytest.mark.asyncio
async def test_json_report_contains_ranking_section():
    empty_summary = _sample_summary(has_data=False)
    snapshot = MediaHealthSnapshot(
        ytdlp=ToolVersion(ok=True, version="v1", detail=""),
        ffmpeg=ToolVersion(ok=True, version="ff", detail=""),
        cache_dir=DirStats(True, 0, 0, False),
        cache_size_bytes=0,
        downloads_dir=DirStats(True, 0, 0, False),
        active_calls=0,
        download_limit=10,
        download_available=10,
        youtube_probe=YoutubeProbeResult(True, False, 0, ""),
        last_eviction=None,
    )
    with (
        patch("app.services.media_health_service.build_health_snapshot", AsyncMock(return_value=snapshot)),
        patch("app.services.media_health_service.get_dir_stats", AsyncMock(return_value=DirStats(True, 0, 0, False))),
        patch("app.services.media_health_service.get_cache_size_bytes", return_value=0),
        patch("app.services.media_health_service._fetch_active_playback_states", AsyncMock(return_value=([], False))),
        patch("app.services.media_health_service._fetch_queue_summary", AsyncMock(return_value=([], False))),
        patch("app.services.media_health_service.get_media_ranking_summary", AsyncMock(return_value=empty_summary)),
    ):
        report = await build_media_report()
    assert "ranking" in report
    assert report["ranking"]["generated_from"] == "media_events"


def test_json_ranking_contains_only_redacted_urls():
    payload = ranking_summary_to_json(_sample_summary())
    encoded = json.dumps(payload)
    assert "token=" not in encoded
    assert "https://youtube.com/watch" in encoded


def test_ranking_i18n_keys_exist():
    for lang in ("fa", "en"):
        assert "[missing:" not in t(lang, "media_health.ranking_button")
        assert "[missing:" not in format_media_ranking_report(lang, _sample_summary(has_data=False))

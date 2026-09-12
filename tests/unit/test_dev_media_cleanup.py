"""Tests for Developer Panel media safe cleanup (Phase 3)."""
from __future__ import annotations

import os
import sys
import time
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
    CleanupPreview,
    CleanupResult,
    DOWNLOAD_RETENTION_SECONDS,
    _delete_cleanup_paths,
    _protected_path_set,
    _register_protected_path,
    _resolved_trusted_file,
    _scan_cleanup_file_paths,
    build_cleanup_preview,
    cleanup_stale_downloads_sync,
    collect_protected_local_paths,
    execute_safe_cleanup,
    format_cleanup_preview,
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


@pytest.fixture
def media_roots(tmp_path, monkeypatch):
    cache_root = tmp_path / "cache"
    downloads_root = tmp_path / "downloads"
    cache_root.mkdir()
    downloads_root.mkdir()
    monkeypatch.setattr(settings, "MEDIA_CACHE_PATH", str(cache_root))
    monkeypatch.setattr(settings, "DOWNLOADS_PATH", str(downloads_root))
    monkeypatch.setattr(settings, "MEDIA_CACHE_MAX_AGE_HOURS", 24)
    return cache_root, downloads_root


def _touch_stale(path: Path, *, age_seconds: int = 90000) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * 64)
    old = time.time() - age_seconds
    os.utime(path, (old, old))


@pytest.mark.asyncio
async def test_media_health_panel_has_cleanup_preview_button():
    kb = KeyboardFactory.dev_media_health("en")
    assert CB["DEV_MEDIA_CLEANUP_PREVIEW"] in _kb_callbacks(kb)


@pytest.mark.asyncio
async def test_cleanup_preview_handler_is_developer_only():
    bot = _RecorderBot()
    dev_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "dev_media_cleanup_preview")
    query = _pm_query(9999, CB["DEV_MEDIA_CLEANUP_PREVIEW"])

    with patch(
        "app.handlers.dev_panel.build_cleanup_preview",
        AsyncMock(),
    ) as preview_mock:
        result = await handler(SimpleNamespace(), query)

    assert result is None
    preview_mock.assert_not_awaited()
    query.message.edit_text.assert_not_awaited()


@pytest.mark.asyncio
async def test_cleanup_preview_does_not_delete_files(media_roots):
    cache_root, downloads_root = media_roots
    stale = cache_root / "ab" / "stale.bin"
    _touch_stale(stale)

    with patch(
        "app.services.media_health_service.collect_protected_local_paths",
        AsyncMock(return_value=frozenset()),
    ):
        preview = await build_cleanup_preview()

    assert stale.exists()
    assert preview.cache_count >= 1


@pytest.mark.asyncio
async def test_cleanup_preview_shows_counts_and_bytes(media_roots):
    cache_root, _ = media_roots
    _touch_stale(cache_root / "aa" / "one.bin")
    _touch_stale(cache_root / "bb" / "two.bin")

    with patch(
        "app.services.media_health_service.collect_protected_local_paths",
        AsyncMock(return_value=frozenset()),
    ):
        preview = await build_cleanup_preview()

    text = format_cleanup_preview("en", preview)
    assert "2" in text or preview.cache_count == 2
    assert preview.total_bytes > 0


@pytest.mark.asyncio
async def test_cleanup_preview_uses_relative_paths_not_absolute(media_roots):
    cache_root, _ = media_roots
    _touch_stale(cache_root / "cc" / "sample.bin")

    with patch(
        "app.services.media_health_service.collect_protected_local_paths",
        AsyncMock(return_value=frozenset()),
    ):
        preview = await build_cleanup_preview()

    text = format_cleanup_preview("en", preview)
    assert str(cache_root) not in text
    if preview.samples:
        assert not preview.samples[0].relative_path.startswith(str(cache_root))


def test_resolved_trusted_file_rejects_symlink_escape(media_roots, tmp_path, monkeypatch):
    _, downloads_root = media_roots
    outside = tmp_path / "outside.txt"
    outside.write_text("secret")
    fake_link = downloads_root / "escape.link"
    fake_link.write_text("link")

    def _fake_is_symlink(self) -> bool:
        return self.name == "escape.link"

    original_resolve = Path.resolve

    def _fake_resolve(self, strict=False):  # noqa: ANN001
        if self.name == "escape.link":
            return outside.resolve()
        return original_resolve(self, strict=strict)

    monkeypatch.setattr(Path, "is_symlink", _fake_is_symlink)
    monkeypatch.setattr(Path, "resolve", _fake_resolve)
    assert _resolved_trusted_file(fake_link) is None


@pytest.mark.asyncio
async def test_confirm_same_developer_deletes_candidates(media_roots):
    cache_root, _ = media_roots
    stale = cache_root / "dd" / "remove-me.bin"
    _touch_stale(stale)

    with patch(
        "app.services.media_health_service.collect_protected_local_paths",
        AsyncMock(return_value=frozenset()),
    ):
        result = await execute_safe_cleanup()

    assert not stale.exists()
    assert result.deleted_count >= 1
    assert result.freed_bytes > 0


@pytest.mark.asyncio
async def test_confirm_wrong_user_rejected():
    bot = _RecorderBot()
    dev_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "dev_media_cleanup_confirm")
    token = f"{settings.DEVELOPER_ID}:{int(time.time())}"
    query = _pm_query(8888, f"{CB['DEV_MEDIA_CLEANUP_EXEC_PREFIX']}{token}")

    with patch(
        "app.handlers.dev_panel.execute_safe_cleanup",
        AsyncMock(),
    ) as exec_mock:
        await handler(SimpleNamespace(), query)

    exec_mock.assert_not_awaited()
    query.answer.assert_awaited()


@pytest.mark.asyncio
async def test_stale_confirm_rejected():
    bot = _RecorderBot()
    dev_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "dev_media_cleanup_confirm")
    old_ts = int(time.time()) - 9999
    token = f"{settings.DEVELOPER_ID}:{old_ts}"
    query = _pm_query(settings.DEVELOPER_ID, f"{CB['DEV_MEDIA_CLEANUP_EXEC_PREFIX']}{token}")

    with patch(
        "app.handlers.dev_panel.execute_safe_cleanup",
        AsyncMock(),
    ) as exec_mock:
        await handler(SimpleNamespace(), query)

    exec_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_malformed_confirm_rejected():
    bot = _RecorderBot()
    dev_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "dev_media_cleanup_malformed")
    query = _pm_query(settings.DEVELOPER_ID, "dev:media:cleanup:do:bad")

    await handler(SimpleNamespace(), query)
    query.answer.assert_awaited()


@pytest.mark.asyncio
async def test_cancel_deletes_nothing(media_roots):
    cache_root, _ = media_roots
    stale = cache_root / "ee" / "keep-me.bin"
    _touch_stale(stale)

    bot = _RecorderBot()
    dev_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "dev_media_cleanup_cancel")
    token = f"{settings.DEVELOPER_ID}:{int(time.time())}"
    query = _pm_query(settings.DEVELOPER_ID, f"{CB['DEV_MEDIA_CLEANUP_ABORT_PREFIX']}{token}")

    with patch(
        "app.handlers.dev_panel.build_health_snapshot",
        AsyncMock(return_value=SimpleNamespace()),
    ), patch(
        "app.handlers.dev_panel.format_health_report",
        return_value="health",
    ):
        await handler(SimpleNamespace(), query)

    assert stale.exists()


def test_only_trusted_roots_deleted(media_roots, tmp_path):
    cache_root, downloads_root = media_roots
    stale = cache_root / "ff" / "trusted.bin"
    _touch_stale(stale)
    outside = tmp_path / "outside.bin"
    _touch_stale(outside)

    paths, _ = _scan_cleanup_file_paths(set())
    resolved_names = {p.name for p in paths}
    assert "trusted.bin" in resolved_names
    assert "outside.bin" not in resolved_names


def test_unsafe_path_skipped_on_delete(media_roots, tmp_path):
    outside = tmp_path / "unsafe.bin"
    outside.write_bytes(b"no")

    result = _delete_cleanup_paths([outside], set())
    assert result.deleted_count == 0
    assert result.skipped_count == 1
    assert outside.exists()


def test_active_playback_file_skipped(media_roots):
    cache_root, _ = media_roots
    active = cache_root / "gg" / "active.bin"
    _touch_stale(active)
    protected = frozenset({str(active.resolve())})

    paths, _ = _scan_cleanup_file_paths(_protected_path_set(protected))
    assert active not in paths


def test_queue_referenced_file_skipped(media_roots):
    _, downloads_root = media_roots
    nested = downloads_root / "12345" / "queued.bin"
    _touch_stale(nested)
    protected = frozenset({str(nested.resolve())})

    paths, _ = _scan_cleanup_file_paths(_protected_path_set(protected))
    assert nested not in paths


def test_one_failed_delete_does_not_stop_cleanup(media_roots, monkeypatch):
    cache_root, _ = media_roots
    ok_file = cache_root / "hh" / "ok.bin"
    bad_file = cache_root / "ii" / "bad.bin"
    _touch_stale(ok_file)
    _touch_stale(bad_file)

    original_unlink = Path.unlink

    def _unlink(self, missing_ok=False):  # noqa: ANN001
        if self.name == "bad.bin":
            raise OSError("permission denied")
        return original_unlink(self, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", _unlink)
    result = _delete_cleanup_paths([bad_file, ok_file], set())
    assert result.deleted_count == 1
    assert result.failed_count == 1
    assert not ok_file.exists()


def test_empty_directories_removed_after_file_delete(media_roots):
    cache_root, _ = media_roots
    nested = cache_root / "jj" / "nested.bin"
    _touch_stale(nested)
    parent = nested.parent

    _delete_cleanup_paths([nested.resolve()], set())
    assert not nested.exists()
    assert not parent.exists()


def test_scheduler_cleans_nested_download_files(media_roots):
    _, downloads_root = media_roots
    nested = downloads_root / "999" / "old.mp3"
    _touch_stale(nested, age_seconds=DOWNLOAD_RETENTION_SECONDS + 3600)

    removed = cleanup_stale_downloads_sync(frozenset(), DOWNLOAD_RETENTION_SECONDS)
    assert removed == 1
    assert not nested.exists()


def test_scheduler_skips_active_download_files(media_roots):
    _, downloads_root = media_roots
    active = downloads_root / "888" / "live.mp3"
    _touch_stale(active, age_seconds=DOWNLOAD_RETENTION_SECONDS + 3600)
    protected = frozenset({str(active.resolve())})

    removed = cleanup_stale_downloads_sync(protected, DOWNLOAD_RETENTION_SECONDS)
    assert removed == 0
    assert active.exists()


def test_scheduler_cleanup_missing_directory(monkeypatch):
    missing = Path("/nonexistent/downloads/path/for/test")
    monkeypatch.setattr(settings, "DOWNLOADS_PATH", str(missing))
    removed = cleanup_stale_downloads_sync(frozenset(), DOWNLOAD_RETENTION_SECONDS)
    assert removed == 0


@pytest.mark.asyncio
async def test_developer_preview_handler_renders_confirm_keyboard():
    bot = _RecorderBot()
    dev_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "dev_media_cleanup_preview")
    query = _pm_query(settings.DEVELOPER_ID, CB["DEV_MEDIA_CLEANUP_PREVIEW"])
    preview = CleanupPreview(
        cache_count=1,
        downloads_count=1,
        total_bytes=2048,
        samples=(),
        scan_truncated=False,
    )

    with patch(
        "app.handlers.dev_panel.build_cleanup_preview",
        AsyncMock(return_value=preview),
    ):
        await handler(SimpleNamespace(), query)

    kb = query.message.edit_text.await_args.kwargs["reply_markup"]
    callbacks = _kb_callbacks(kb)
    assert any(cb.startswith(CB["DEV_MEDIA_CLEANUP_EXEC_PREFIX"]) for cb in callbacks)
    assert any(cb.startswith(CB["DEV_MEDIA_CLEANUP_ABORT_PREFIX"]) for cb in callbacks)


def test_protected_path_includes_transcode_siblings(media_roots):
    _, downloads_root = media_roots
    source = downloads_root / "777" / "track.mp3"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(b"audio")
    transcode = source.with_suffix(".tc.ogg")
    transcode.write_bytes(b"transcoded")

    protected: set[Path] = set()
    _register_protected_path(protected, str(source))
    assert source.resolve() in protected
    assert transcode.resolve() in protected


@pytest.mark.asyncio
async def test_cleanup_i18n_keys_exist():
    preview = CleanupPreview(1, 2, 1024, (), False)
    result = CleanupResult(1, 0, 0, 512, 1, 0)
    for lang in ("fa", "en"):
        assert "[missing:" not in format_cleanup_preview(lang, preview)
        assert "[missing:" not in t(lang, "media_health.cleanup_completed", deleted=1, skipped=0, failed=0, size_gb="0")

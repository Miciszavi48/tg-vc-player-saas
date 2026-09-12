"""Tests for P0 (media cache), P1 (prefetch), P2 (transcode pool)."""
from __future__ import annotations

import asyncio
import os
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ═══════════════════════════════════════════════════════════════════════════
# P0: Media Cache
# ═══════════════════════════════════════════════════════════════════════════

class TestMediaCache:

    def test_canonical_url_strips_tracking(self):
        from app.services.media_cache import _canonical_url
        raw = "https://youtube.com/watch?v=abc&si=xyz&list=PLfoo&t=10"
        clean = _canonical_url(raw)
        assert "si=" not in clean
        assert "list=" not in clean
        assert "t=" not in clean
        assert "v=abc" in clean

    def test_url_hash_deterministic(self):
        from app.services.media_cache import _url_hash
        h1 = _url_hash("https://youtube.com/watch?v=abc")
        h2 = _url_hash("https://youtube.com/watch?v=abc")
        assert h1 == h2
        assert len(h1) == 64

    def test_url_hash_ignores_tracking(self):
        from app.services.media_cache import _url_hash
        h1 = _url_hash("https://youtube.com/watch?v=abc")
        h2 = _url_hash("https://youtube.com/watch?v=abc&si=foo&t=5")
        assert h1 == h2

    def test_cache_lookup_miss(self):
        from app.services.media_cache import cache_lookup
        with patch("app.services.media_cache.settings") as s:
            s.MEDIA_CACHE_PATH = tempfile.mkdtemp()
            result = cache_lookup("https://example.com/nonexistent", "audio")
            assert result is None

    def test_cache_store_and_lookup(self):
        from app.services.media_cache import cache_lookup, cache_store
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("app.services.media_cache.settings") as s:
                s.MEDIA_CACHE_PATH = tmpdir
                src = Path(tmpdir) / "song.opus"
                src.write_bytes(b"fake audio data" * 100)

                url = "https://youtube.com/watch?v=test123"
                cached = cache_store(url, "audio", str(src))
                assert cached.endswith(".opus")
                assert Path(cached).exists()

                found = cache_lookup(url, "audio")
                assert found == cached

    def test_cache_store_deduplicates(self):
        from app.services.media_cache import cache_store
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("app.services.media_cache.settings") as s:
                s.MEDIA_CACHE_PATH = tmpdir
                src = Path(tmpdir) / "song.opus"
                src.write_bytes(b"data")

                url = "https://youtube.com/watch?v=dedup"
                p1 = cache_store(url, "audio", str(src))
                p2 = cache_store(url, "audio", str(src))
                assert p1 == p2

    def test_evict_lru_removes_oldest(self):
        from app.services.media_cache import evict_lru, get_cache_size_bytes
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("app.services.media_cache.settings") as s:
                s.MEDIA_CACHE_PATH = tmpdir
                s.MEDIA_CACHE_MAX_GB = 10
                s.MEDIA_CACHE_TARGET_GB = 8
                shard = Path(tmpdir) / "ab"
                shard.mkdir()
                old = shard / "old.opus"
                old.write_bytes(b"x" * 1000)
                os.utime(str(old), (time.time() - 86400, time.time() - 86400))

                new = shard / "new.opus"
                new.write_bytes(b"y" * 1000)

                removed = evict_lru(max_bytes=1500, target_bytes=1000)
                assert removed == 1
                assert not old.exists()
                assert new.exists()

    def test_evict_stale(self):
        from app.services.media_cache import evict_stale
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("app.services.media_cache.settings") as s:
                s.MEDIA_CACHE_PATH = tmpdir
                s.MEDIA_CACHE_MAX_AGE_HOURS = 1
                shard = Path(tmpdir) / "cd"
                shard.mkdir()
                old = shard / "stale.opus"
                old.write_bytes(b"z" * 100)
                os.utime(str(old), (time.time() - 7200, time.time() - 7200))

                fresh = shard / "fresh.opus"
                fresh.write_bytes(b"w" * 100)

                removed = evict_stale(max_age_hours=1)
                assert removed == 1
                assert not old.exists()
                assert fresh.exists()

    def test_get_cache_size_bytes(self):
        from app.services.media_cache import get_cache_size_bytes
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("app.services.media_cache.settings") as s:
                s.MEDIA_CACHE_PATH = tmpdir
                f = Path(tmpdir) / "test.bin"
                f.write_bytes(b"a" * 4096)
                size = get_cache_size_bytes()
                assert size >= 4096


# ═══════════════════════════════════════════════════════════════════════════
# P1: Prefetch Pipeline
# ═══════════════════════════════════════════════════════════════════════════

class TestPrefetch:

    def test_prefetch_cache_and_tasks_exist(self):
        from app.services.call_service import _prefetch_cache, _prefetch_tasks
        assert isinstance(_prefetch_cache, dict)
        assert isinstance(_prefetch_tasks, dict)

    def test_cancel_prefetch_clears_state(self):
        from app.services.call_service import (
            _cancel_prefetch,
            _prefetch_cache,
            _prefetch_tasks,
        )
        _prefetch_cache[999] = {"source": "x", "path": "/tmp/x"}
        mock_task = MagicMock()
        mock_task.done.return_value = False
        _prefetch_tasks[999] = mock_task

        _cancel_prefetch(999)

        assert 999 not in _prefetch_cache
        assert 999 not in _prefetch_tasks
        mock_task.cancel.assert_called_once()

    def test_cancel_prefetch_noop_on_missing(self):
        from app.services.call_service import _cancel_prefetch
        _cancel_prefetch(12345)

    @pytest.mark.asyncio
    async def test_prefetch_next_short_queue(self):
        from app.services.call_service import _prefetch_cache, _prefetch_next
        with patch("app.services.call_service.playlist_repo") as mock_pr:
            mock_pr.get_queue = AsyncMock(return_value=[SimpleNamespace()])
            await _prefetch_next(888)
            assert 888 not in _prefetch_cache

    @pytest.mark.asyncio
    async def test_prefetch_next_populates_cache(self, tmp_path, monkeypatch):
        from app.config.settings import settings
        from app.services.call_service import _prefetch_cache, _prefetch_next
        downloads = tmp_path / "downloads"
        downloads.mkdir()
        monkeypatch.setattr(settings, "DOWNLOADS_PATH", str(downloads))
        monkeypatch.setattr(settings, "MEDIA_CACHE_PATH", str(tmp_path / "media_cache"))
        current = downloads / "a.opus"
        next_file = downloads / "b.opus"
        current.write_bytes(b"a")
        next_file.write_bytes(b"b")
        item0 = SimpleNamespace(stream_url=None, file_path=str(current),
                                media_type="audio", title="Track A")
        item1 = SimpleNamespace(stream_url=None, file_path=str(next_file),
                                media_type="audio", title="Track B")
        transcode_result = downloads / "b.tc.ogg"
        transcode_result.write_bytes(b"tc")
        with (
            patch("app.services.call_service.playlist_repo") as mock_pr,
            patch("app.services.transcode_pool.pre_transcode",
                  return_value=str(transcode_result)) as mock_tc,
        ):
            mock_pr.get_queue = AsyncMock(return_value=[item0, item1])
            await _prefetch_next(777)
            assert 777 in _prefetch_cache
            assert _prefetch_cache[777]["path"] == str(transcode_result.resolve())
            mock_tc.assert_awaited_once_with(str(next_file.resolve()), "audio")

    @pytest.mark.asyncio
    async def test_prefetch_next_rejects_untrusted_file_path(self, tmp_path, monkeypatch):
        from app.config.settings import settings
        from app.services.call_service import _prefetch_cache, _prefetch_next
        downloads = tmp_path / "downloads"
        downloads.mkdir()
        monkeypatch.setattr(settings, "DOWNLOADS_PATH", str(downloads))
        monkeypatch.setattr(settings, "MEDIA_CACHE_PATH", str(tmp_path / "media_cache"))
        outside = tmp_path / "outside.opus"
        outside.write_bytes(b"outside")
        item0 = SimpleNamespace(stream_url=None, file_path=str(downloads / "a.opus"),
                                media_type="audio", title="Track A")
        item1 = SimpleNamespace(stream_url=None, file_path=str(outside),
                                media_type="audio", title="Track B")
        with (
            patch("app.services.call_service.playlist_repo") as mock_pr,
            patch("app.services.transcode_pool.pre_transcode",
                  new_callable=AsyncMock) as mock_tc,
        ):
            mock_pr.get_queue = AsyncMock(return_value=[item0, item1])
            await _prefetch_next(778)
            assert 778 not in _prefetch_cache
            mock_tc.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_trigger_prefetch_creates_task(self):
        from app.services.call_service import _prefetch_tasks, _trigger_prefetch

        async def _dummy_prefetch(cid):
            pass

        with patch("app.services.call_service._prefetch_next", side_effect=_dummy_prefetch):
            try:
                _trigger_prefetch(555)
                assert 555 in _prefetch_tasks
            finally:
                for t in list(_prefetch_tasks.values()):
                    t.cancel()
                _prefetch_tasks.clear()


# ═══════════════════════════════════════════════════════════════════════════
# P2: Transcode Pool
# ═══════════════════════════════════════════════════════════════════════════

class TestTranscodePool:

    @pytest.mark.asyncio
    async def test_pre_transcode_url_passthrough(self):
        from app.services.transcode_pool import pre_transcode
        result = await pre_transcode("https://93.184.216.34/stream.m3u8", "audio")
        assert result == "https://93.184.216.34/stream.m3u8"

    @pytest.mark.asyncio
    async def test_pre_transcode_missing_file_passthrough(self):
        from app.services.transcode_pool import pre_transcode
        result = await pre_transcode("/nonexistent/file.opus", "audio")
        assert result == "/nonexistent/file.opus"

    @pytest.mark.asyncio
    async def test_pre_transcode_audio_success(self, tmp_path, monkeypatch):
        from app.config.settings import settings
        from app.services.transcode_pool import pre_transcode
        downloads = tmp_path / "downloads"
        downloads.mkdir()
        monkeypatch.setattr(settings, "DOWNLOADS_PATH", str(downloads))
        monkeypatch.setattr(settings, "MEDIA_CACHE_PATH", str(tmp_path / "media_cache"))
        src_path = downloads / "source.opus"
        src_path.write_bytes(b"fake" * 100)
        src = str(src_path)
        try:
            mock_proc = AsyncMock()
            mock_proc.wait = AsyncMock(return_value=0)
            with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
                tc_path = Path(src).with_suffix(".tc.ogg")
                tc_path.write_bytes(b"transcoded")
                result = await pre_transcode(src, "audio")
                assert result.endswith(".tc.ogg")
        finally:
            for p in [Path(src), Path(src).with_suffix(".tc.ogg")]:
                p.unlink(missing_ok=True)

    @pytest.mark.asyncio
    async def test_pre_transcode_video_success(self, tmp_path, monkeypatch):
        from app.config.settings import settings
        from app.services.transcode_pool import pre_transcode
        downloads = tmp_path / "downloads"
        downloads.mkdir()
        monkeypatch.setattr(settings, "DOWNLOADS_PATH", str(downloads))
        monkeypatch.setattr(settings, "MEDIA_CACHE_PATH", str(tmp_path / "media_cache"))
        src_path = downloads / "source.mp4"
        src_path.write_bytes(b"fake" * 100)
        src = str(src_path)
        try:
            mock_proc = AsyncMock()
            mock_proc.wait = AsyncMock(return_value=0)
            with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
                tc_path = Path(src).with_suffix(".tc.mp4")
                tc_path.write_bytes(b"transcoded")
                result = await pre_transcode(src, "video")
                assert result.endswith(".tc.mp4")
        finally:
            for p in [Path(src), Path(src).with_suffix(".tc.mp4")]:
                p.unlink(missing_ok=True)

    @pytest.mark.asyncio
    async def test_pre_transcode_failure_fallback(self, tmp_path, monkeypatch):
        from app.config.settings import settings
        from app.services.transcode_pool import pre_transcode
        downloads = tmp_path / "downloads"
        downloads.mkdir()
        monkeypatch.setattr(settings, "DOWNLOADS_PATH", str(downloads))
        monkeypatch.setattr(settings, "MEDIA_CACHE_PATH", str(tmp_path / "media_cache"))
        src_path = downloads / "source.opus"
        src_path.write_bytes(b"fake" * 100)
        src = str(src_path)
        try:
            mock_proc = AsyncMock()
            mock_proc.wait = AsyncMock(return_value=1)
            with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
                result = await pre_transcode(src, "audio")
                assert result == src
        finally:
            Path(src).unlink(missing_ok=True)

    @pytest.mark.asyncio
    async def test_pre_transcode_timeout_fallback(self, tmp_path, monkeypatch):
        from app.config.settings import settings
        from app.services.transcode_pool import pre_transcode
        downloads = tmp_path / "downloads"
        downloads.mkdir()
        monkeypatch.setattr(settings, "DOWNLOADS_PATH", str(downloads))
        monkeypatch.setattr(settings, "MEDIA_CACHE_PATH", str(tmp_path / "media_cache"))
        src_path = downloads / "source.opus"
        src_path.write_bytes(b"fake" * 100)
        src = str(src_path)
        try:
            mock_proc = AsyncMock()
            mock_proc.wait = AsyncMock(side_effect=asyncio.TimeoutError)
            mock_proc.kill = MagicMock()
            with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
                result = await pre_transcode(src, "audio")
                assert result == src
        finally:
            Path(src).unlink(missing_ok=True)

    @pytest.mark.asyncio
    async def test_pre_transcode_rejects_untrusted_local_file(self, tmp_path, monkeypatch):
        from app.config.settings import settings
        from app.services.transcode_pool import pre_transcode
        downloads = tmp_path / "downloads"
        downloads.mkdir()
        monkeypatch.setattr(settings, "DOWNLOADS_PATH", str(downloads))
        monkeypatch.setattr(settings, "MEDIA_CACHE_PATH", str(tmp_path / "media_cache"))
        outside = tmp_path / "outside.opus"
        outside.write_bytes(b"outside")

        with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
            result = await pre_transcode(str(outside), "audio")
            assert result == str(outside)
            mock_exec.assert_not_called()

    @pytest.mark.asyncio
    async def test_pre_transcode_rejects_symlink_output_escape(self, tmp_path, monkeypatch):
        from app.config.settings import settings
        from app.services.transcode_pool import pre_transcode
        downloads = tmp_path / "downloads"
        downloads.mkdir()
        monkeypatch.setattr(settings, "DOWNLOADS_PATH", str(downloads))
        monkeypatch.setattr(settings, "MEDIA_CACHE_PATH", str(tmp_path / "media_cache"))
        src = downloads / "source.opus"
        src.write_bytes(b"source")
        outside = tmp_path / "outside.tc.ogg"
        outside.write_bytes(b"outside")
        out_link = downloads / "source.tc.ogg"
        try:
            out_link.symlink_to(outside)
        except OSError as exc:
            pytest.skip(f"symlink creation is not available: {exc}")

        with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
            result = await pre_transcode(str(src), "audio")
            assert result == str(src)
            mock_exec.assert_not_called()

    def test_cleanup_transcoded(self, tmp_path, monkeypatch):
        from app.config.settings import settings
        from app.services.transcode_pool import cleanup_transcoded
        downloads = tmp_path / "downloads"
        downloads.mkdir()
        monkeypatch.setattr(settings, "DOWNLOADS_PATH", str(downloads))
        monkeypatch.setattr(settings, "MEDIA_CACHE_PATH", str(tmp_path / "media_cache"))
        src_path = downloads / "source.opus"
        src_path.write_bytes(b"source")
        src = str(src_path)
        tc = src_path.with_suffix(".tc.ogg")
        tc.write_bytes(b"tc")
        assert tc.exists()
        cleanup_transcoded(src)
        assert not tc.exists()

    def test_pool_semaphore_bounded(self):
        from app.services.transcode_pool import _get_pool
        with patch("app.services.transcode_pool.settings") as s:
            s.TRANSCODE_POOL_SIZE = 4
            from app.services import transcode_pool
            transcode_pool._pool_sem = None
            sem = _get_pool()
            assert sem._value == 4
            transcode_pool._pool_sem = None


# ═══════════════════════════════════════════════════════════════════════════
# Integration: media_service uses cache
# ═══════════════════════════════════════════════════════════════════════════

class TestMediaServiceCache:

    @pytest.mark.asyncio
    async def test_download_returns_cached(self):
        from app.services.media_service import MediaService
        with (
            patch("app.services.media_service.validate_safe_url_with_redirects", new_callable=AsyncMock, return_value="https://example.com/song"),
            patch("app.services.media_cache.cache_lookup", return_value="/cache/hit.opus"),
        ):
            result = await MediaService._download("https://example.com/song", 1, "audio")
            assert result == "/cache/hit.opus"

    @pytest.mark.asyncio
    async def test_download_stores_to_cache_on_miss(self):
        from app.services.media_service import MediaService
        with (
            patch("app.services.media_service.validate_safe_url_with_redirects", new_callable=AsyncMock, return_value="https://example.com/song"),
            patch("app.services.media_cache.cache_lookup", return_value=None),
            patch("app.services.media_cache.cache_store", return_value="/cache/stored.opus") as mock_store,
            patch("app.services.media_cache.evict_lru"),
            patch("app.services.media_service._run_subprocess", return_value=0),
            patch("app.services.media_service._find_latest_file", return_value=Path("/tmp/song.opus")),
            patch("app.services.media_service._get_semaphore", return_value=asyncio.Semaphore(1)),
        ):
            result = await MediaService._download("https://example.com/song", 1, "audio")
            assert result == "/cache/stored.opus"
            mock_store.assert_called_once()


# ═══════════════════════════════════════════════════════════════════════════
# Redis keys registry
# ═══════════════════════════════════════════════════════════════════════════

def test_new_redis_keys_exist():
    from app.utils.redis_keys import (
        MEDIA_CACHE_EVICTION_LOCK,
        instance_key,
        media_cache_hit_key,
        prefetch_key,
        credit_lock_key,
        install_lock_key,
        install_fee_lock_key,
        wallet_lock_key,
        chat_lock_key,
    )
    assert MEDIA_CACHE_EVICTION_LOCK == instance_key("media_cache:evict")
    assert media_cache_hit_key("abc") == instance_key("media_cache:hit:abc")
    assert prefetch_key(123) == instance_key("prefetch:123")
    assert credit_lock_key(100) == instance_key("credit:group:100")
    assert credit_lock_key(100, "channel") == instance_key("credit:channel:100")
    assert install_lock_key(200) == instance_key("install:200")
    assert install_fee_lock_key(300, 400) == instance_key("install_fee:300:400")
    assert wallet_lock_key(500) == instance_key("wallet:500")
    assert chat_lock_key(-100) == instance_key("chat:-100")


# ═══════════════════════════════════════════════════════════════════════════
# Scheduler job
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_media_cache_eviction_job():
    from app.scheduler import media_cache_eviction
    with (
        patch("app.services.media_cache.evict_stale", return_value=3),
        patch("app.services.media_cache.evict_lru", return_value=1),
    ):
        await media_cache_eviction()


# ═══════════════════════════════════════════════════════════════════════════
# Settings
# ═══════════════════════════════════════════════════════════════════════════

def test_settings_have_cache_fields():
    from app.config.settings import settings
    assert hasattr(settings, "MEDIA_CACHE_PATH")
    assert hasattr(settings, "MEDIA_CACHE_MAX_GB")
    assert hasattr(settings, "MEDIA_CACHE_TARGET_GB")
    assert hasattr(settings, "MEDIA_CACHE_MAX_AGE_HOURS")
    assert hasattr(settings, "TRANSCODE_POOL_SIZE")

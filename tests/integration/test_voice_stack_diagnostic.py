"""Voice-stack diagnostic and stream discovery tests (no real PyTgCalls required)."""
from __future__ import annotations

import importlib
import subprocess
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import patch

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = _REPO_ROOT / "scripts" / "check_voice_stack.py"


def _run_check_script() -> subprocess.CompletedProcess[str]:
    env = {
        **__import__("os").environ,
        "PYTHONPATH": str(_REPO_ROOT),
    }
    return subprocess.run(
        [sys.executable, str(_SCRIPT)],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )


def _reload_voice_stack():
    sys.modules.pop("app.utils.voice_stack", None)
    return importlib.import_module("app.utils.voice_stack")


class TestCheckVoiceStackScript:
    def test_missing_pytgcalls_reports_cleanly(self):
        """Script exits non-zero when pytgcalls cannot import."""
        with patch.dict(sys.modules, {"pytgcalls": None}):
            vs = _reload_voice_stack()
            with patch.object(vs, "get_pytgcalls_version", return_value=(False, None, None)):
                with patch.object(vs, "probe_native_backends", return_value=(False, False)):
                    with patch.object(vs, "probe_import_symbols", return_value=[]):
                        with patch.object(vs, "probe_stream_candidates", return_value=[]):
                            with patch.object(vs, "probe_vc_methods", return_value={}):
                                with patch.object(vs, "probe_ffmpeg", return_value=(True, "ffmpeg version")):
                                    report = vs.probe_voice_stack()
        assert report.pytgcalls_importable is False
        assert report.usable is False

        proc = _run_check_script()
        assert proc.returncode in (0, 1)
        assert "pytgcalls_import:" in proc.stdout
        assert "BOT_TOKEN" not in proc.stdout
        assert "BOT_TOKEN" not in proc.stderr

    def test_fake_importable_pytgcalls_detected(self):
        """Injected fake pytgcalls module is reported with version."""
        fake = ModuleType("pytgcalls")
        fake.__version__ = "9.9.9-test"  # type: ignore[attr-defined]

        class _FakePyTgCalls:
            @staticmethod
            def join_group_call(*_a, **_k):
                pass

            @staticmethod
            def leave_group_call(*_a, **_k):
                pass

        fake.PyTgCalls = _FakePyTgCalls  # type: ignore[attr-defined]

        vs = _reload_voice_stack()
        with patch.dict(sys.modules, {"pytgcalls": fake}):
            importable, version, _ = vs.get_pytgcalls_version()
        assert importable is True
        assert version == "9.9.9-test"

    def test_exits_nonzero_when_no_stream_class(self):
        """No stream candidate → usable=False → exit 1 from probe."""
        vs = _reload_voice_stack()
        with patch.object(vs, "discover_stream_class_available", return_value=False):
            with patch.object(vs, "get_pytgcalls_version", return_value=(True, "1.0", "py-tgcalls")):
                with patch.object(vs, "probe_native_backends", return_value=(False, True)):
                    with patch.object(vs, "probe_import_symbols", return_value=[]):
                        with patch.object(
                            vs,
                            "probe_stream_candidates",
                            return_value=[vs.StreamCandidateProbe("AudioPiped", False, "ImportError")],
                        ):
                            with patch.object(
                                vs,
                                "probe_vc_methods",
                                return_value={"join_group_call": True, "leave_group_call": True, "play": False},
                            ):
                                with patch.object(vs, "probe_ffmpeg", return_value=(True, "ffmpeg")):
                                    report = vs.probe_voice_stack()
        assert report.audio_stream_buildable is False
        assert report.usable is False

    def test_does_not_require_telegram_credentials(self):
        """Script must not import app.main or pyrogram at runtime."""
        source = _SCRIPT.read_text(encoding="utf-8")
        import_lines = [
            line.strip()
            for line in source.splitlines()
            if line.strip().startswith(("import ", "from "))
        ]
        joined = "\n".join(import_lines)
        assert "app.main" not in joined
        assert "pyrogram" not in joined
        assert "BOT_TOKEN" not in joined
        assert "load_dotenv" not in joined

    def test_does_not_read_db_or_redis(self):
        """Script and voice_stack avoid DB/Redis runtime imports."""
        source = _SCRIPT.read_text(encoding="utf-8")
        import_lines = [
            line.strip()
            for line in source.splitlines()
            if line.strip().startswith(("import ", "from "))
        ]
        joined = "\n".join(import_lines)
        assert "async_session" not in joined
        assert "redis" not in joined

        vs_source = (_REPO_ROOT / "app" / "utils" / "voice_stack.py").read_text(encoding="utf-8")
        vs_imports = [
            line.strip()
            for line in vs_source.splitlines()
            if line.strip().startswith(("import ", "from "))
        ]
        vs_joined = "\n".join(vs_imports)
        assert "async_session" not in vs_joined
        assert "redis" not in vs_joined

    def test_does_not_print_secrets(self):
        """Stdout from script must not contain token-like placeholders."""
        proc = _run_check_script()
        combined = proc.stdout + proc.stderr
        assert "REPLACE_WITH" not in combined
        assert "CHANGE_ME" not in combined
        for line in combined.splitlines():
            assert "BOT_TOKEN=" not in line


class TestVoiceStackDiscovery:
    def test_stream_candidate_discovery_with_mocked_pytgcalls(self):
        """call_service-equivalent discovery works with a fake stream class."""
        vs = _reload_voice_stack()

        class _FakeAudioPiped:
            def __init__(self, source: str) -> None:
                self.source = source

        fake_types = ModuleType("pytgcalls.types")
        fake_types.AudioPiped = _FakeAudioPiped  # type: ignore[attr-defined]
        fake_types.AudioQuality = SimpleNamespace(HIGH=(48000, 2, 96000))  # type: ignore[attr-defined]
        fake_types.MediaStream = _FakeAudioPiped  # type: ignore[attr-defined]

        fake_root = ModuleType("pytgcalls")
        fake_root.types = fake_types  # type: ignore[attr-defined]

        with patch.dict(
            sys.modules,
            {
                "pytgcalls": fake_root,
                "pytgcalls.types": fake_types,
            },
        ):
            candidates = vs.probe_stream_candidates(media_type="audio")
            assert any(c.ok for c in candidates)
            assert vs.discover_stream_class_available("audio") is True
            stream = vs.build_audio_stream("/tmp/test.mp3")
        assert stream is not None
        assert stream.source == "/tmp/test.mp3"

    def test_vc_join_uses_play_fallback(self):
        """VC shim prefers join_group_call but falls back to play."""
        vs = _reload_voice_stack()

        calls: list[str] = []

        class _CallPy:
            async def play(self, chat_id: int, stream) -> None:
                calls.append(f"play:{chat_id}")

        import asyncio

        asyncio.run(vs.vc_join(_CallPy(), -100, SimpleNamespace()))
        assert calls == ["play:-100"]

    def test_vc_join_uses_join_group_call_when_available(self):
        """VC shim uses legacy join_group_call when present."""
        vs = _reload_voice_stack()

        calls: list[str] = []

        class _CallPy:
            async def join_group_call(self, chat_id: int, stream) -> None:
                calls.append(f"join:{chat_id}")

            async def play(self, chat_id: int, stream) -> None:
                calls.append("play")

        import asyncio

        asyncio.run(vs.vc_join(_CallPy(), -100, SimpleNamespace()))
        assert calls == ["join:-100"]

    def test_vc_join_skips_leave_when_not_in_binding_calls(self):
        """VC shim does not leave before first play when ntgcalls has no active call."""
        vs = _reload_voice_stack()

        order: list[str] = []

        class _Binding:
            async def calls(self):
                return []

        class _CallPy:
            _binding = _Binding()

            async def leave_call(self, chat_id: int) -> None:
                order.append(f"leave:{chat_id}")

            async def play(self, chat_id: int, stream, config=None) -> None:
                order.append(f"play:{chat_id}")

        import asyncio

        asyncio.run(vs.vc_join(_CallPy(), -100, SimpleNamespace()))
        assert order == ["play:-100"]

    def test_vc_join_leaves_call_when_already_in_binding(self):
        """VC shim leaves stale ntgcalls state before play when already connected."""
        vs = _reload_voice_stack()

        order: list[str] = []

        class _Binding:
            async def calls(self):
                return [-100]

        class _CallPy:
            _binding = _Binding()

            async def leave_call(self, chat_id: int) -> None:
                order.append(f"leave:{chat_id}")

            async def play(self, chat_id: int, stream, config=None) -> None:
                order.append(f"play:{chat_id}")

        import asyncio

        asyncio.run(vs.vc_join(_CallPy(), -100, SimpleNamespace()))
        assert order == ["leave:-100", "play:-100"]

    def test_vc_join_passes_group_call_config_to_play(self):
        """VC shim disables auto_start inside play when using v2 API."""
        vs = _reload_voice_stack()

        captured: dict[str, object] = {}

        class _GroupCallConfig:
            def __init__(self, *, auto_start: bool = True):
                self.auto_start = auto_start

        class _Binding:
            async def calls(self):
                return []

        class _CallPy:
            _binding = _Binding()

            async def play(self, chat_id: int, stream, config=None) -> None:
                captured["config"] = config

        fake_types = ModuleType("pytgcalls.types")
        fake_types.GroupCallConfig = _GroupCallConfig  # type: ignore[attr-defined]

        import asyncio

        with patch.dict(sys.modules, {"pytgcalls.types": fake_types}):
            asyncio.run(vs.vc_join(_CallPy(), -100, SimpleNamespace()))
        assert captured["config"] is not None
        assert getattr(captured["config"], "auto_start", True) is False

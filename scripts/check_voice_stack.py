#!/usr/bin/env python3
"""Probe PyTgCalls / voice-stack compatibility without starting the bot.

Safe to run on production servers. Does not connect to Telegram, read
config.env, or touch DB/Redis.

Usage:
    cd /opt/musicbot-a
    sudo -u mb-musicbot-a /opt/musicbot-a/venv/bin/python scripts/check_voice_stack.py

Exit codes:
    0 — voice stack looks usable
    1 — imports missing or no compatible stream class / VC API
    2 — unexpected script error
"""
from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


def _status(ok: bool) -> str:
    return "yes" if ok else "no"


def _print_report() -> int:
    from app.utils.voice_stack import probe_stream_candidates, probe_voice_stack

    report = probe_voice_stack()

    print(f"python: {report.python_version}")
    print(f"platform: {report.platform_info}")
    print(f"pytgcalls_import: {_status(report.pytgcalls_importable)}")
    if report.pytgcalls_version:
        print(f"pytgcalls_version: {report.pytgcalls_version}")
    if report.pytgcalls_dist_name:
        print(f"pytgcalls_dist: {report.pytgcalls_dist_name}")
    print(f"tgcalls_import: {_status(report.tgcalls_importable)}")
    print(f"ntgcalls_import: {_status(report.ntgcalls_importable)}")

    for probe in report.import_probes:
        print(f"import.{probe.name}: {_status(probe.ok)}")
        if not probe.ok and probe.error:
            print(f"  error: {probe.error}")

    video_candidates = (
        probe_stream_candidates(media_type="video")
        if report.pytgcalls_importable
        else []
    )
    print("stream_candidates.audio:")
    for cand in report.stream_candidates:
        line = f"  {cand.name}: {_status(cand.ok)}"
        print(line)
        if not cand.ok and cand.error:
            print(f"    error: {cand.error}")
    print("stream_candidates.video:")
    for cand in video_candidates:
        line = f"  {cand.name}: {_status(cand.ok)}"
        print(line)
        if not cand.ok and cand.error:
            print(f"    error: {cand.error}")

    print(f"audio_stream_buildable: {_status(report.audio_stream_buildable)}")
    print(f"video_stream_buildable: {_status(report.video_stream_buildable)}")

    print("vc_methods:")
    for name, available in sorted(report.vc_methods.items()):
        if available:
            print(f"  {name}: yes")

    print(f"ffmpeg_in_path: {_status(report.ffmpeg_in_path)}")
    if report.ffmpeg_version:
        print(f"ffmpeg_version: {report.ffmpeg_version}")

    for note in report.notes:
        print(f"note: {note}")

    print(f"voice_stack_usable: {_status(report.usable)}")
    return 0 if report.usable else 1


def main() -> int:
    try:
        return _print_report()
    except Exception as exc:
        print(f"error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

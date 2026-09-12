"""Unit tests for the offline yt-dlp runtime preflight."""

from __future__ import annotations

from unittest.mock import patch

from scripts import check_youtube_runtime as runtime_check


def test_node_is_accepted_when_deno_is_missing():
    def run_version(command):
        values = {
            "yt-dlp": (True, "2026.07.01"),
            "ffmpeg": (True, "ffmpeg version 7.0"),
            "deno": (False, "not found"),
            "node": (True, "v22.4.0"),
        }
        return values[command[0]]

    with (
        patch.object(runtime_check, "_run_version", side_effect=run_version),
        patch.object(runtime_check, "package_version", return_value="0.8.0"),
    ):
        results = runtime_check.check_runtime()

    assert all(item.ok for item in results)
    assert results[-1].detail.startswith("node")


def test_outdated_runtime_fails_strict_mode():
    def run_version(command):
        values = {
            "yt-dlp": (True, "2026.07.01"),
            "ffmpeg": (True, "ffmpeg version 7.0"),
            "deno": (True, "deno 2.2.0"),
            "node": (False, "not found"),
        }
        return values[command[0]]

    with (
        patch.object(runtime_check, "_run_version", side_effect=run_version),
        patch.object(runtime_check, "package_version", return_value="0.7.0"),
    ):
        assert runtime_check.main(["--strict"]) == 1

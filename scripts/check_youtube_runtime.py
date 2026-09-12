"""Verify local yt-dlp YouTube runtime prerequisites without network access."""

from __future__ import annotations

import argparse
import re
import subprocess
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, version as package_version


_VERSION_RE = re.compile(r"(?:v|deno\s+)?(\d+)(?:\.(\d+))?(?:\.(\d+))?", re.IGNORECASE)
_MIN_EJS = (0, 8, 0)
_RUNTIME_MINIMUMS = (("deno", (2, 3, 0)), ("node", (22, 0, 0)))


@dataclass(frozen=True)
class CheckResult:
    name: str
    ok: bool
    detail: str


def _parse_version(value: str) -> tuple[int, int, int] | None:
    match = _VERSION_RE.search(value or "")
    if match is None:
        return None
    return tuple(int(part or 0) for part in match.groups())


def _run_version(command: list[str]) -> tuple[bool, str]:
    try:
        completed = subprocess.run(command, capture_output=True, text=True, timeout=10, check=False)
    except FileNotFoundError:
        return False, "not found"
    except subprocess.TimeoutExpired:
        return False, "timeout"
    except Exception as exc:
        return False, type(exc).__name__
    if completed.returncode != 0:
        return False, f"exit {completed.returncode}"
    lines = (completed.stdout or "").strip().splitlines()
    return bool(lines), lines[0] if lines else "no version output"


def _check_ejs() -> CheckResult:
    try:
        installed = package_version("yt-dlp-ejs")
    except PackageNotFoundError:
        return CheckResult("yt-dlp-ejs", False, "not installed")
    parsed = _parse_version(installed)
    if parsed is None or parsed < _MIN_EJS:
        return CheckResult("yt-dlp-ejs", False, f"{installed} < 0.8.0")
    return CheckResult("yt-dlp-ejs", True, installed)


def check_runtime() -> list[CheckResult]:
    ytdlp_ok, ytdlp_detail = _run_version(["yt-dlp", "--version"])
    ffmpeg_ok, ffmpeg_detail = _run_version(["ffmpeg", "-version"])
    results = [
        CheckResult("yt-dlp", ytdlp_ok, ytdlp_detail),
        CheckResult("ffmpeg", ffmpeg_ok, ffmpeg_detail),
        _check_ejs(),
    ]

    runtime_results: list[CheckResult] = []
    for executable, minimum in _RUNTIME_MINIMUMS:
        ok, detail = _run_version([executable, "--version"])
        parsed = _parse_version(detail) if ok else None
        compatible = bool(parsed is not None and parsed >= minimum)
        runtime_results.append(
            CheckResult(
                executable,
                compatible,
                detail if compatible else f"{detail} (requires >= {'.'.join(map(str, minimum))})",
            )
        )
    compatible_runtime = next((item for item in runtime_results if item.ok), None)
    if compatible_runtime is not None:
        results.append(CheckResult("JavaScript runtime", True, f"{compatible_runtime.name} {compatible_runtime.detail}"))
    else:
        details = "; ".join(f"{item.name}: {item.detail}" for item in runtime_results)
        results.append(CheckResult("JavaScript runtime", False, details))
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--strict", action="store_true", help="return non-zero when a required component is missing")
    args = parser.parse_args(argv)

    results = check_runtime()
    for item in results:
        status = "OK" if item.ok else "FAIL"
        print(f"{status:<4} {item.name}: {item.detail}")
    return 1 if args.strict and not all(item.ok for item in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())

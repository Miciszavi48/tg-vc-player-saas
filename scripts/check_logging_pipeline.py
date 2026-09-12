#!/usr/bin/env python3
"""Read-only logging pipeline checks (no bot start, DB, or Telegram calls)."""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config.settings import settings  # noqa: E402
from app.utils.diagnostic_logging import (  # noqa: E402
    LOGURU_DIRECT_MODULE_SUFFIXES,
    find_broken_loguru_percent_formatting,
)
from app.utils.logger import setup_logger  # noqa: E402
from app.utils.logging_pipeline_scan import collect_runtime_logging_issues  # noqa: E402

_SERVICE_CANDIDATES = (
    ROOT / "scripts" / "musicbot.service",
    ROOT / "deployment" / "musicbot.service",
)


def _check_service_file(path: Path) -> list[str]:
    issues: list[str] = []
    if not path.is_file():
        return issues
    text = path.read_text(encoding="utf-8")
    for token in (
        "PYTHONUNBUFFERED=1",
        "StandardOutput=journal",
        "StandardError=journal",
        "SyslogIdentifier=musicbot",
    ):
        if token not in text:
            issues.append(f"{path.name}: missing {token}")
    return issues


def _scan_loguru_formatting() -> list[str]:
    issues: list[str] = []
    app_dir = ROOT / "app"
    for path in sorted(app_dir.rglob("*.py")):
        if path.name not in LOGURU_DIRECT_MODULE_SUFFIXES:
            continue
        rel = path.relative_to(ROOT).as_posix()
        broken = find_broken_loguru_percent_formatting(path.read_text(encoding="utf-8-sig"))
        for line in broken:
            issues.append(f"{rel}: {line}")
    return issues


def main() -> int:
    print("Logging pipeline check (read-only)")
    print(f"  LOG_LEVEL={settings.LOG_LEVEL}")
    print(f"  LOG_FILE={settings.LOG_FILE}")
    print(f"  PYTHONUNBUFFERED={os.environ.get('PYTHONUNBUFFERED', '<unset>')}")

    setup_logger()

    issues: list[str] = []
    for candidate in _SERVICE_CANDIDATES:
        issues.extend(_check_service_file(candidate))
    issues.extend(_scan_loguru_formatting())
    issues.extend(collect_runtime_logging_issues())

    if issues:
        print("FAIL:")
        for item in issues:
            print(f"  - {item}")
        return 1

    print("OK: logging pipeline configuration looks healthy")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

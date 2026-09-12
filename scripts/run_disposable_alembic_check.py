#!/usr/bin/env python3
"""Run Alembic head/upgrade/current against a safe disposable/test database.

Never uses production ``app/config.env`` by default. Does not start the bot.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
APP_DIR = REPO_ROOT / "app"
SCRIPTS_DIR = REPO_ROOT / "scripts"

if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import safe_db_url  # noqa: E402


def _run_step(label: str, cmd: list[str], env: dict[str, str]) -> None:
    """Run a subprocess step and fail fast on non-zero exit."""
    print(f"\n--- {label} ---")
    print(" ".join(cmd))
    completed = subprocess.run(
        cmd,
        cwd=APP_DIR,
        env=env,
        check=False,
        text=True,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"{label} failed with exit code {completed.returncode}")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(
        description="Alembic disposable validation (heads, upgrade head, current, drift check).",
    )
    parser.add_argument("--database-url", default=None, help="Override safe DB URL resolution")
    parser.add_argument(
        "--allow-non-test-db",
        action="store_true",
        help="Allow non-test DB names (not recommended for local disposable validation)",
    )
    parser.add_argument(
        "--skip-drift",
        action="store_true",
        help="Skip db_schema_drift_check.py after Alembic steps",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint."""
    args = parse_args(argv)

    try:
        resolved = safe_db_url.load_safe_database_url(
            cli_url=args.database_url,
            allow_non_test_db=args.allow_non_test_db,
        )
    except safe_db_url.SafeDbUrlError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 2

    print("=== Disposable Alembic validation ===")
    print(f"Source: {resolved.source}")
    print(f"URL: {resolved.redacted_url}")
    print(f"Database: {resolved.db_name}")

    env = os.environ.copy()
    env["DATABASE_URL"] = resolved.url
    env["MUSICBOT_DOTENV_OVERRIDE"] = "false"
    env["PYTHONPATH"] = str(REPO_ROOT)

    try:
        _run_step("alembic heads", ["alembic", "heads"], env)
        _run_step("alembic upgrade head", ["alembic", "upgrade", "head"], env)
        _run_step("alembic current", ["alembic", "current"], env)

        if not args.skip_drift:
            drift_script = REPO_ROOT / "scripts" / "db_schema_drift_check.py"
            drift_cmd = [
                sys.executable,
                str(drift_script),
                "--database-url",
                resolved.url,
            ]
            print("\n--- db_schema_drift_check ---")
            print(
                " ".join(drift_cmd[:-1] + [resolved.redacted_url]),
            )
            drift = subprocess.run(drift_cmd, cwd=REPO_ROOT, check=False, text=True)
            if drift.returncode != 0:
                raise RuntimeError(
                    f"db_schema_drift_check failed with exit code {drift.returncode}"
                )
    except RuntimeError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1

    print("\n=== Disposable Alembic validation: PASS ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

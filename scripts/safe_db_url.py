"""Resolve a safe disposable/test database URL for validation scripts.

Never connects to a database. Does not read production ``app/config.env`` by default.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote_plus

from sqlalchemy.engine import make_url

REPO_ROOT = Path(__file__).resolve().parents[1]

DISPOSABLE_ENV_FILE = REPO_ROOT / "app" / "config.env.disposable"
DISPOSABLE_ENV_EXAMPLE = REPO_ROOT / "app" / "config.env.disposable.example"
TEST_ENV_FILE = REPO_ROOT / "app" / "config.env.test"

SAFE_DB_NAME_MARKERS = ("disposable", "test", "dev", "local", "staging")

SETUP_INSTRUCTIONS = (
    "Copy app/config.env.disposable.example to app/config.env.disposable, "
    "set DATABASE_URL / TEST_DATABASE_URL to a safe database "
    "(e.g. musicbot_disposable), or pass --database-url. "
    "For intentional production/staging read-only checks, pass --allow-non-test-db."
)


@dataclass(frozen=True, slots=True)
class SafeDbUrlResult:
    """Resolved database URL metadata for validation scripts."""

    url: str
    redacted_url: str
    source: str
    db_name: str | None


class SafeDbUrlError(Exception):
    """Raised when no safe database URL can be resolved."""


def database_name_from_url(url: str) -> str | None:
    """Extract database name from a SQLAlchemy URL."""
    try:
        return make_url(url).database
    except Exception:
        return None


def redact_database_url(url: str) -> str:
    """Return a display-safe database URL with credentials redacted."""
    try:
        parsed = make_url(url)
    except Exception:
        return "<invalid-url>"

    username = parsed.username or ""
    password = "****" if parsed.password else ""
    host = parsed.host or ""
    port = f":{parsed.port}" if parsed.port else ""
    database = parsed.database or ""
    driver = parsed.drivername or "unknown"

    auth = ""
    if username:
        auth = quote_plus(username)
        if password:
            auth += f":{password}"
        auth += "@"

    return f"{driver}://{auth}{host}{port}/{database}"


def is_safe_database_name(
    db_name: str | None,
    *,
    allow_non_test_db: bool,
) -> tuple[bool, str]:
    """Return whether the database name is allowed for unattended validation."""
    if allow_non_test_db:
        return True, "override flag --allow-non-test-db"

    if not db_name:
        return False, "could not determine database name from URL"

    lowered = db_name.lower()
    for marker in SAFE_DB_NAME_MARKERS:
        if marker in lowered:
            return True, f"database name contains '{marker}'"

    return (
        False,
        "database name does not contain disposable/test/dev/local/staging "
        "(pass --allow-non-test-db to proceed)",
    )


def _read_env_file_urls(path: Path) -> dict[str, str]:
    """Parse DATABASE_URL / TEST_DATABASE_URL from a dotenv-style file."""
    values: dict[str, str] = {}
    if not path.is_file():
        return values

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if key not in ("DATABASE_URL", "TEST_DATABASE_URL"):
            continue
        values[key] = value.strip().strip('"').strip("'")

    return values


def _url_from_env_file(path: Path) -> tuple[str | None, str | None]:
    """Return (url, key_name) preferring TEST_DATABASE_URL over DATABASE_URL."""
    values = _read_env_file_urls(path)
    if values.get("TEST_DATABASE_URL"):
        return values["TEST_DATABASE_URL"], "TEST_DATABASE_URL"
    if values.get("DATABASE_URL"):
        return values["DATABASE_URL"], "DATABASE_URL"
    return None, None


def _validate_candidate(
    url: str,
    source: str,
    *,
    allow_non_test_db: bool,
) -> SafeDbUrlResult:
    """Validate safety and build a result for an accepted URL."""
    db_name = database_name_from_url(url)
    safe, reason = is_safe_database_name(db_name, allow_non_test_db=allow_non_test_db)
    if not safe:
        raise SafeDbUrlError(
            f"Unsafe database URL from {source}: {reason}. {SETUP_INSTRUCTIONS}"
        )
    return SafeDbUrlResult(
        url=url,
        redacted_url=redact_database_url(url),
        source=source,
        db_name=db_name,
    )


def load_safe_database_url(
    *,
    cli_url: str | None = None,
    allow_non_test_db: bool = False,
    repo_root: Path | None = None,
) -> SafeDbUrlResult:
    """Resolve a safe database URL for disposable/test validation.

    Search order:
    1. ``cli_url`` when provided
    2. ``TEST_DATABASE_URL`` environment variable
    3. ``DATABASE_URL`` environment variable (only when DB name is safe)
    4. ``app/config.env.disposable``
    5. ``app/config.env.test``

    Does not read production ``app/config.env``. Does not connect to any database.

    Args:
        cli_url: Explicit ``--database-url`` override.
        allow_non_test_db: Allow production-like DB names (read-only tooling only).
        repo_root: Repository root override for tests.

    Returns:
        SafeDbUrlResult with actual URL, redacted URL, source label, and DB name.

    Raises:
        SafeDbUrlError: When no URL is found or the resolved URL is not safe.
    """
    root = repo_root or REPO_ROOT
    disposable_file = root / "app" / "config.env.disposable"
    test_file = root / "app" / "config.env.test"

    if cli_url:
        return _validate_candidate(
            cli_url.strip(),
            "cli:--database-url",
            allow_non_test_db=allow_non_test_db,
        )

    test_env_url = os.environ.get("TEST_DATABASE_URL", "").strip()
    if test_env_url:
        return _validate_candidate(
            test_env_url,
            "env:TEST_DATABASE_URL",
            allow_non_test_db=allow_non_test_db,
        )

    database_env_url = os.environ.get("DATABASE_URL", "").strip()
    if database_env_url:
        return _validate_candidate(
            database_env_url,
            "env:DATABASE_URL",
            allow_non_test_db=allow_non_test_db,
        )

    for env_path in (disposable_file, test_file):
        file_url, key_name = _url_from_env_file(env_path)
        if file_url:
            rel = env_path.relative_to(root).as_posix()
            return _validate_candidate(
                file_url,
                f"file:{rel}:{key_name}",
                allow_non_test_db=allow_non_test_db,
            )

    raise SafeDbUrlError(
        "No safe database URL found. "
        f"{SETUP_INSTRUCTIONS} "
        f"Expected file: {disposable_file.relative_to(root).as_posix()}"
    )

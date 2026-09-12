"""Tests for scripts/safe_db_url.py and script integration (no live DB)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = REPO_ROOT / "scripts"


def _load_safe_db_url():
    path = SCRIPTS_DIR / "safe_db_url.py"
    spec = importlib.util.spec_from_file_location("safe_db_url", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["safe_db_url"] = module
    spec.loader.exec_module(module)
    return module


def _load_script(name: str):
    path = SCRIPTS_DIR / name
    mod_name = name.replace(".py", "")
    spec = importlib.util.spec_from_file_location(mod_name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def safe_db_url_module():
    return _load_safe_db_url()


@pytest.fixture
def temp_repo(tmp_path):
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    return tmp_path


def test_cli_url_wins(safe_db_url_module, monkeypatch, temp_repo):
    mod = safe_db_url_module
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("TEST_DATABASE_URL", raising=False)

    result = mod.load_safe_database_url(
        cli_url="postgresql+asyncpg://u:secret@localhost:5432/musicbot_disposable",
        repo_root=temp_repo,
    )
    assert result.source == "cli:--database-url"
    assert result.db_name == "musicbot_disposable"
    assert result.url.endswith("/musicbot_disposable")


def test_test_database_url_wins_over_database_url(safe_db_url_module, monkeypatch, temp_repo):
    mod = safe_db_url_module
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+asyncpg://u:secret@localhost:5432/musicbot_dev",
    )
    monkeypatch.setenv(
        "TEST_DATABASE_URL",
        "postgresql+asyncpg://u:secret@localhost:5432/musicbot_disposable",
    )

    result = mod.load_safe_database_url(repo_root=temp_repo)
    assert result.source == "env:TEST_DATABASE_URL"
    assert result.db_name == "musicbot_disposable"


def test_safe_database_url_env_allowed(safe_db_url_module, monkeypatch, temp_repo):
    mod = safe_db_url_module
    monkeypatch.delenv("TEST_DATABASE_URL", raising=False)
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+asyncpg://u:secret@localhost:5432/musicbot_disposable",
    )

    result = mod.load_safe_database_url(repo_root=temp_repo)
    assert result.source == "env:DATABASE_URL"
    assert result.db_name == "musicbot_disposable"


def test_production_like_url_rejected_without_override(safe_db_url_module, monkeypatch, temp_repo):
    mod = safe_db_url_module
    monkeypatch.delenv("TEST_DATABASE_URL", raising=False)
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+asyncpg://u:secret@localhost:5432/musicbot_db",
    )

    with pytest.raises(mod.SafeDbUrlError) as exc:
        mod.load_safe_database_url(repo_root=temp_repo)
    assert "Unsafe database URL" in str(exc.value)
    assert "config.env.disposable.example" in str(exc.value)


def test_production_like_url_allowed_with_override(safe_db_url_module, monkeypatch, temp_repo):
    mod = safe_db_url_module
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+asyncpg://u:secret@localhost:5432/musicbot_db",
    )

    result = mod.load_safe_database_url(
        repo_root=temp_repo,
        allow_non_test_db=True,
    )
    assert result.db_name == "musicbot_db"


def test_reads_disposable_config_file(safe_db_url_module, monkeypatch, temp_repo):
    mod = safe_db_url_module
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("TEST_DATABASE_URL", raising=False)

    disposable = temp_repo / "app" / "config.env.disposable"
    disposable.parent.mkdir(parents=True, exist_ok=True)
    disposable.write_text(
        "DATABASE_URL=postgresql+asyncpg://musicbot:pw@127.0.0.1:5432/musicbot_disposable\n",
        encoding="utf-8",
    )

    result = mod.load_safe_database_url(repo_root=temp_repo)
    assert result.source == "file:app/config.env.disposable:DATABASE_URL"
    assert result.db_name == "musicbot_disposable"


def test_password_is_redacted(safe_db_url_module):
    mod = safe_db_url_module
    url = "postgresql+asyncpg://musicbot:topsecret@localhost:5432/musicbot_test"
    redacted = mod.redact_database_url(url)
    assert "topsecret" not in redacted
    assert "****" in redacted
    assert "musicbot_test" in redacted


def test_loader_does_not_import_db_connection_stack(safe_db_url_module):
    mod = safe_db_url_module
    source = (SCRIPTS_DIR / "safe_db_url.py").read_text(encoding="utf-8")
    assert "create_async_engine" not in source
    assert "asyncpg.connect" not in source
    assert "psycopg" not in source
    # make_url is URL parsing only
    mod.load_safe_database_url(
        cli_url="postgresql+asyncpg://u:secret@localhost:5432/musicbot_test",
        repo_root=REPO_ROOT,
    )


def test_missing_config_gives_clear_error(safe_db_url_module, monkeypatch, temp_repo):
    mod = safe_db_url_module
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("TEST_DATABASE_URL", raising=False)

    with pytest.raises(mod.SafeDbUrlError) as exc:
        mod.load_safe_database_url(repo_root=temp_repo)
    message = str(exc.value)
    assert "No safe database URL found" in message
    assert "config.env.disposable.example" in message


@pytest.mark.asyncio
async def test_db_schema_drift_check_uses_loader_when_no_url(monkeypatch, temp_repo):
    drift_mod = _load_script("db_schema_drift_check.py")
    monkeypatch.setattr(
        drift_mod.safe_db_url,
        "load_safe_database_url",
        lambda **kwargs: drift_mod.safe_db_url.SafeDbUrlResult(
            url="sqlite+aiosqlite:///./loader-test.db",
            redacted_url="sqlite+aiosqlite:///./loader-test.db",
            source="test:mock",
            db_name="loader-test.db",
        ),
    )
    monkeypatch.setattr(drift_mod, "print_human_report", lambda _result: None)

    code = await drift_mod.async_main([])
    assert code == 0


def test_validate_daily_deduct_batching_uses_loader_when_no_url(monkeypatch, temp_repo):
    validate_mod = _load_script("validate_daily_deduct_batching.py")

    def _fake_loader(**kwargs):
        return validate_mod.safe_db_url.SafeDbUrlResult(
            url="postgresql+asyncpg://u:secret@localhost:5432/musicbot_disposable",
            redacted_url="postgresql+asyncpg://u:****@localhost:5432/musicbot_disposable",
            source="test:mock",
            db_name="musicbot_disposable",
        )

    monkeypatch.setattr(validate_mod.safe_db_url, "load_safe_database_url", _fake_loader)

    async def _fake_run_validation(*_args, **_kwargs):
        return validate_mod.ValidationReport(status="PASS", exit_code=0)

    monkeypatch.setattr(validate_mod, "run_validation", _fake_run_validation)
    monkeypatch.setattr(validate_mod, "print_report", lambda _report: None)

    code = validate_mod.main([])
    assert code == 0

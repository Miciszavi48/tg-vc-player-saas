"""Central logging pipeline configuration and static guard tests."""
from __future__ import annotations

import ast
import logging
import os
import tempfile
from io import StringIO
from pathlib import Path

import pytest
from loguru import logger

os.environ.setdefault("TEST_MODE", "1")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./.pytest-logging-pipeline.db")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/15")
os.environ.setdefault("BOT_TOKEN", "test")
os.environ.setdefault("API_ID", "12345")
os.environ.setdefault("API_HASH", "testhash")
os.environ.setdefault("DEVELOPER_ID", "123456789")

ROOT = Path(__file__).resolve().parents[2]
CENTRAL_LOGGER = ROOT / "app" / "utils" / "logger.py"
DIAGNOSTIC_LOGGER = ROOT / "app" / "utils" / "diagnostic_logging.py"
MAIN_ENTRY = ROOT / "app" / "main.py"

FORBIDDEN_CALLS = frozenset({
    "logger.add",
    "logger.remove",
    "basicConfig",
    "dictConfig",
    "FileHandler",
    "RotatingFileHandler",
    "TimedRotatingFileHandler",
})

RUNTIME_ALLOWLIST = frozenset({
    CENTRAL_LOGGER.resolve(),
})

TEST_ALLOWLIST_PREFIX = (ROOT / "tests").resolve()


def _iter_call_names(node: ast.AST) -> list[str]:
    names: list[str] = []
    for child in ast.walk(node):
        if isinstance(child, ast.Call):
            func = child.func
            if isinstance(func, ast.Attribute):
                if isinstance(func.value, ast.Name):
                    names.append(f"{func.value.id}.{func.attr}")
                elif isinstance(func.value, ast.Attribute) and isinstance(func.value.value, ast.Name):
                    names.append(f"{func.value.value.id}.{func.value.attr}.{func.attr}")
            elif isinstance(func, ast.Name):
                names.append(func.id)
    return names


def _scan_forbidden_calls(path: Path) -> list[str]:
    resolved = path.resolve()
    if resolved in RUNTIME_ALLOWLIST:
        return []
    if str(resolved).startswith(str(TEST_ALLOWLIST_PREFIX)):
        return []

    source = path.read_text(encoding="utf-8-sig")
    tree = ast.parse(source, filename=str(path))
    rel = path.relative_to(ROOT).as_posix()
    hits: list[str] = []
    for name in _iter_call_names(tree):
        if name in FORBIDDEN_CALLS or name.endswith(".basicConfig"):
            hits.append(f"{rel}: forbidden call {name}")
    return hits


def _scan_manual_log_opens(path: Path) -> list[str]:
    resolved = path.resolve()
    if str(resolved).startswith(str(TEST_ALLOWLIST_PREFIX)):
        return []
    if resolved in RUNTIME_ALLOWLIST:
        return []

    rel = path.relative_to(ROOT).as_posix()
    hits: list[str] = []
    source = path.read_text(encoding="utf-8-sig")
    tree = ast.parse(source, filename=str(path))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Name) and func.id == "open"):
            continue
        for arg in node.args:
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str) and arg.value.endswith(".log"):
                hits.append(f"{rel}: manual open() on log file {arg.value!r}")
    return hits


def test_setup_logger_is_exposed():
    from app.utils.logger import InterceptHandler, setup_logger

    assert callable(setup_logger)
    assert issubclass(InterceptHandler, logging.Handler)


def _teardown_loguru_sinks() -> None:
    """Close all Loguru sinks so temp log files can be deleted on Windows."""
    logger.remove()


def test_setup_logger_installs_stdlib_interception():
    from app.config.settings import settings
    from app.utils.logger import InterceptHandler, setup_logger

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        original = settings.LOG_FILE
        settings.LOG_FILE = str(Path(tmp) / "bot.log")
        try:
            setup_logger()
            root = logging.getLogger()
            assert any(isinstance(h, InterceptHandler) for h in root.handlers)
        finally:
            _teardown_loguru_sinks()
            settings.LOG_FILE = original


def test_stdlib_logger_routes_through_loguru_pipeline():
    from app.config.settings import settings
    from app.utils.logger import setup_logger

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        original = settings.LOG_FILE
        settings.LOG_FILE = str(Path(tmp) / "bot.log")
        try:
            setup_logger()
            buffer = StringIO()
            handler_id = logger.add(buffer, format="{message}", level="DEBUG")
            try:
                stdlib = logging.getLogger("tests.logging.pipeline.routing")
                stdlib.info("stdlib routed through loguru")
                output = buffer.getvalue()
            finally:
                logger.remove(handler_id)
        finally:
            _teardown_loguru_sinks()
            settings.LOG_FILE = original
    assert "stdlib routed through loguru" in output


def test_error_log_path_derivation():
    from app.utils.logger import _error_log_path

    assert Path(_error_log_path("./logs/bot.log")) == Path("./logs/bot_error.log")
    assert Path(_error_log_path("./logs/custom")) == Path("./logs/custom_error.log")


def test_setup_logger_creates_log_directory():
    from app.config.settings import settings
    from app.utils.logger import setup_logger

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        log_dir = Path(tmp) / "nested" / "logs"
        log_file = log_dir / "bot.log"
        original = settings.LOG_FILE
        settings.LOG_FILE = str(log_file)
        try:
            setup_logger()
            assert log_dir.is_dir()
        finally:
            _teardown_loguru_sinks()
            settings.LOG_FILE = original


def test_main_entrypoint_calls_setup_logger_once():
    source = MAIN_ENTRY.read_text(encoding="utf-8-sig")
    assert "setup_logger()" in source
    assert source.count("setup_logger()") == 1


def test_diagnostic_logging_does_not_configure_sinks():
    source = DIAGNOSTIC_LOGGER.read_text(encoding="utf-8-sig")
    tree = ast.parse(source, filename=str(DIAGNOSTIC_LOGGER))
    for name in _iter_call_names(tree):
        assert name not in FORBIDDEN_CALLS, f"diagnostic_logging must not call {name}"


@pytest.mark.parametrize("subdir", ["app", "scripts"])
def test_runtime_forbidden_logging_configuration(subdir: str):
    base = ROOT / subdir
    violations: list[str] = []
    for path in sorted(base.rglob("*.py")):
        violations.extend(_scan_forbidden_calls(path))
        violations.extend(_scan_manual_log_opens(path))
    assert not violations, "Forbidden logging configuration:\n" + "\n".join(violations)


def test_no_runtime_setup_logger_outside_entry_and_central():
    from app.utils.logging_pipeline_scan import scan_setup_logger_outside_entry

    hits = scan_setup_logger_outside_entry()
    assert not hits, f"setup_logger() outside central/entry: {hits}"


def test_no_traceback_print_exc_in_app():
    from app.utils.logging_pipeline_scan import scan_traceback_print_exc

    hits: list[str] = []
    for path in sorted((ROOT / "app").rglob("*.py")):
        hits.extend(scan_traceback_print_exc(path))
    assert not hits


def test_no_runtime_print_outside_cli_allowlist():
    from app.utils.logging_pipeline_scan import scan_runtime_print

    hits: list[str] = []
    for path in sorted((ROOT / "app").rglob("*.py")):
        hits.extend(scan_runtime_print(path))
    assert not hits


def test_checker_catches_artificial_violation(tmp_path: Path):
    from app.utils.logging_pipeline_scan import scan_forbidden_calls

    bad_file = tmp_path / "bad_module.py"
    bad_file.write_text("from loguru import logger\nlogger.add('x')\n", encoding="utf-8")
    hits = scan_forbidden_calls(bad_file)
    assert hits
    assert "logger.add" in hits[0]


def test_collect_runtime_logging_issues_passes_current_repo():
    from app.utils.logging_pipeline_scan import collect_runtime_logging_issues

    issues = collect_runtime_logging_issues()
    assert not issues, "Logging pipeline scan issues:\n" + "\n".join(issues)

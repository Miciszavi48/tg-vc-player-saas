"""Static AST/regex scans for centralized logging pipeline compliance."""
from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CENTRAL_LOGGER = (ROOT / "app" / "utils" / "logger.py").resolve()
MAIN_ENTRY = (ROOT / "app" / "main.py").resolve()
CHECKER_SCRIPT = (ROOT / "scripts" / "check_logging_pipeline.py").resolve()
SCAN_MODULE = (ROOT / "app" / "utils" / "logging_pipeline_scan.py").resolve()

SCAN_EXEMPT = frozenset({
    CENTRAL_LOGGER,
    CHECKER_SCRIPT,
    SCAN_MODULE,
})

FORBIDDEN_CALLS = frozenset({
    "logger.add",
    "logger.remove",
    "basicConfig",
    "dictConfig",
    "FileHandler",
    "RotatingFileHandler",
    "TimedRotatingFileHandler",
})

APP_PRINT_ALLOWLIST = frozenset({
    (ROOT / "app" / "tools" / "helper_pool_cli.py").resolve(),
    (ROOT / "app" / "database" / "migrate_sqlite_to_pg.py").resolve(),
})

SECRET_LOG_PATTERNS = (
    re.compile(r"logger\.\w+\([^)]*session_string"),
    re.compile(r"logger\.\w+\([^)]*api_hash[^_enc]"),
    re.compile(r"logger\.\w+\([^)]*\bpassword\b", re.I),
    re.compile(r"logger\.\w+\([^)]*\bcode\b.*digits", re.I),
    re.compile(r"logger\.\w+\([^)]*settings\.BOT_TOKEN"),
    re.compile(r"logger\.\w+\([^)]*settings\.API_HASH"),
    re.compile(r"logger\.\w+\([^)]*settings\.DATABASE_URL"),
    re.compile(r"logger\.\w+\([^)]*stderr\.decode\(\)"),
)

MAIN_RAW_EXC_PATTERN = re.compile(
    r'logger\.(?:error|warning|info|debug)\([^)]*\{\}[^)]*\bexc\b[^)]*\)',
)


def _repo_relative(path: Path) -> str:
    """Return a repo-relative path when possible, otherwise the file name."""
    try:
        return path.relative_to(ROOT).as_posix()
    except ValueError:
        return path.name


def iter_call_names(node: ast.AST) -> list[str]:
    """Return dotted call names from an AST tree."""
    names: list[str] = []
    for child in ast.walk(node):
        if not isinstance(child, ast.Call):
            continue
        func = child.func
        if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
            names.append(f"{func.value.id}.{func.attr}")
        elif isinstance(func, ast.Name):
            names.append(func.id)
    return names


def _call_matches(node: ast.Call, *, module: str | None, func: str) -> bool:
    """Return True when ``node`` is a call to ``module.func`` or bare ``func``."""
    call_func = node.func
    if module is None:
        return isinstance(call_func, ast.Name) and call_func.id == func
    return (
        isinstance(call_func, ast.Attribute)
        and isinstance(call_func.value, ast.Name)
        and call_func.value.id == module
        and call_func.attr == func
    )


def scan_forbidden_calls(path: Path) -> list[str]:
    """Flag sink configuration outside the central logger module."""
    resolved = path.resolve()
    if resolved == CENTRAL_LOGGER:
        return []

    rel = _repo_relative(path)
    tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
    hits: list[str] = []
    for name in iter_call_names(tree):
        if name in FORBIDDEN_CALLS:
            hits.append(f"{rel}: forbidden call {name}")
    return hits


def scan_manual_log_opens(path: Path) -> list[str]:
    """Flag manual ``open("*.log")`` in runtime code."""
    resolved = path.resolve()
    if resolved == CENTRAL_LOGGER:
        return []

    rel = _repo_relative(path)
    hits: list[str] = []
    tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
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


def scan_runtime_print(path: Path) -> list[str]:
    """Flag ``print()`` in app runtime code outside CLI allowlist."""
    resolved = path.resolve()
    if resolved in APP_PRINT_ALLOWLIST:
        return []
    if not str(resolved).startswith(str((ROOT / "app").resolve())):
        return []

    rel = _repo_relative(path)
    tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
    hits: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name) and func.id == "print":
            hits.append(f"{rel}: runtime print() outside CLI allowlist")
    return hits


def scan_traceback_print_exc(path: Path) -> list[str]:
    """Flag ``traceback.print_exc()`` in runtime code."""
    resolved = path.resolve()
    if resolved in SCAN_EXEMPT:
        return []
    if not str(resolved).startswith(str((ROOT / "app").resolve())):
        return []
    rel = _repo_relative(path)
    tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and _call_matches(node, module="traceback", func="print_exc"):
            return [f"{rel}: traceback.print_exc()"]
    return []


def scan_setup_logger_outside_entry() -> list[str]:
    """Ensure setup_logger() is only called from main entrypoint."""
    hits: list[str] = []
    for path in sorted((ROOT / "app").rglob("*.py")):
        resolved = path.resolve()
        if resolved in {CENTRAL_LOGGER, MAIN_ENTRY, CHECKER_SCRIPT, SCAN_MODULE}:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and _call_matches(node, module=None, func="setup_logger"):
                hits.append(f"{path.relative_to(ROOT).as_posix()}: setup_logger() outside entrypoint")
                break
    return hits


def scan_secret_logging_patterns(paths: list[Path]) -> list[str]:
    """Flag obvious secret logging patterns in selected files."""
    issues: list[str] = []
    for path in paths:
        if not path.is_file():
            continue
        rel = _repo_relative(path)
        for lineno, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
            for pattern in SECRET_LOG_PATTERNS:
                if pattern.search(line):
                    issues.append(f"{rel}:{lineno}: suspicious secret log pattern")
    return issues


def scan_main_raw_exception_logging() -> list[str]:
    """Ensure main.py does not log raw exception objects in Loguru placeholders."""
    if not MAIN_ENTRY.is_file():
        return []
    text = MAIN_ENTRY.read_text(encoding="utf-8-sig")
    issues: list[str] = []
    for lineno, line in enumerate(text.splitlines(), 1):
        if MAIN_RAW_EXC_PATTERN.search(line) and "safe_exc_name" not in line:
            issues.append(f"app/main.py:{lineno}: raw exception object in logger call")
    return issues


def scan_script_redacted_db_url_prints() -> list[str]:
    """Ensure disposable alembic script does not print raw DB URLs."""
    path = ROOT / "scripts" / "run_disposable_alembic_check.py"
    if not path.is_file():
        return []
    text = path.read_text(encoding="utf-8-sig")
    if 'print(" ".join(drift_cmd))' in text:
        return ["scripts/run_disposable_alembic_check.py: prints raw drift_cmd with DATABASE_URL"]
    return []


def collect_runtime_logging_issues() -> list[str]:
    """Run all static logging pipeline scans and return issue strings."""
    issues: list[str] = []
    secret_targets = [
        ROOT / "app" / "handlers" / "helper_otp_wizard.py",
        ROOT / "app" / "services" / "helper_otp_pre_auth_registry.py",
        ROOT / "app" / "main.py",
        ROOT / "app" / "scheduler.py",
    ]

    for subdir in ("app", "scripts"):
        for path in sorted((ROOT / subdir).rglob("*.py")):
            issues.extend(scan_forbidden_calls(path))
            issues.extend(scan_manual_log_opens(path))
            if subdir == "app":
                issues.extend(scan_runtime_print(path))
                issues.extend(scan_traceback_print_exc(path))

    issues.extend(scan_secret_logging_patterns(secret_targets))
    issues.extend(scan_main_raw_exception_logging())
    issues.extend(scan_setup_logger_outside_entry())
    issues.extend(scan_script_redacted_db_url_prints())
    return issues

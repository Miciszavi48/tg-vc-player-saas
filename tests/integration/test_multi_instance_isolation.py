from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import socket
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import fakeredis.aioredis
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config.settings import (
    INSTANCE_MARKER_FILENAME,
    instance_session_name,
    parse_instance_id,
    settings,
)
from app.database.models import Base
from app.runtime.http_endpoints import OperationalEndpoints

if sys.platform == "win32":
    pytest.skip("fcntl module is unix-only (requires Linux/macOS host)", allow_module_level=True)

from app.runtime.instance import (
    InstanceAlreadyRunningError,
    InstanceResourceConflictError,
    InstanceRuntimeGuard,
)
from app.utils.redis_keys import settings_key


ROOT = Path(__file__).resolve().parents[2]
SETUP = ROOT / "setup_server.sh"


def _runtime_config(root: Path, instance_id: str) -> SimpleNamespace:
    data = root / "var"
    runtime = root / "run"
    return SimpleNamespace(
        INSTANCE_ID=instance_id,
        INSTANCE_ROOT=str(root),
        INSTANCE_DATA_DIR=str(data),
        DOWNLOADS_PATH=str(data / "downloads"),
        SESSION_PATH=str(data / "sessions"),
        MEDIA_CACHE_PATH=str(data / "cache"),
        TEMP_PATH=str(data / "tmp"),
        BACKUP_DIR=str(data / "backups"),
        RUNTIME_PATH=str(runtime),
        LOG_FILE=str(data / "logs" / "bot.log"),
        PID_FILE=str(runtime / "musicbot.pid"),
        LOCK_FILE=str(runtime / "musicbot.lock"),
    )


def _run_setup(*args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(SETUP), *args],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def test_instance_id_is_required_and_strict(tmp_path: Path) -> None:
    assert parse_instance_id("musicbot-a", "") == "musicbot-a"
    assert parse_instance_id("", "musicbot-b") == "musicbot-b"

    with pytest.raises(ValueError, match="must match"):
        parse_instance_id("MusicBot_A", "")
    with pytest.raises(ValueError, match="must match"):
        parse_instance_id("../musicbot", "")
    with pytest.raises(ValueError, match="must match"):
        parse_instance_id("a" * 33, "")
    with pytest.raises(ValueError, match="must match"):
        parse_instance_id("musicbot-a", "musicbot-b")

    env_file = tmp_path / "missing-instance.env"
    env_file.write_text("BOT_TOKEN=test\n", encoding="utf-8")
    env = os.environ.copy()
    for key in ("INSTANCE_ID", "BOT_INSTANCE_ID", "TEST_MODE"):
        env.pop(key, None)
    env["MUSICBOT_ENV_FILE"] = str(env_file)
    env["MUSICBOT_DOTENV_OVERRIDE"] = "false"
    result = subprocess.run(
        [sys.executable, "-c", "from app.config.settings import settings"],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode != 0
    assert "INSTANCE_ID is required" in result.stderr


def test_two_instance_settings_derive_distinct_runtime_resources(tmp_path: Path) -> None:
    outputs: dict[str, dict] = {}
    for instance_id in ("musicbot-a", "musicbot-b"):
        root = tmp_path / instance_id
        env_file = tmp_path / f"{instance_id}.env"
        env_file.write_text(
            "\n".join(
                (
                    f"INSTANCE_ID={instance_id}",
                    f"INSTANCE_ROOT={root}",
                    f"INSTANCE_DATA_DIR={root / 'var'}",
                    f"DOWNLOADS_PATH={root / 'var' / 'downloads'}",
                    f"SESSION_PATH={root / 'var' / 'sessions'}",
                    f"LOG_FILE={root / 'var' / 'logs' / 'bot.log'}",
                    f"MEDIA_CACHE_PATH={root / 'var' / 'cache'}",
                    f"TEMP_PATH={root / 'var' / 'tmp'}",
                    f"BACKUP_DIR={root / 'var' / 'backups'}",
                    f"RUNTIME_PATH={root / 'var' / 'run'}",
                    f"PID_FILE={root / 'var' / 'run' / 'musicbot.pid'}",
                    f"LOCK_FILE={root / 'var' / 'run' / 'musicbot.lock'}",
                    "DATABASE_URL=sqlite+aiosqlite:///:memory:",
                    "REDIS_URL=redis://127.0.0.1:6379/15",
                )
            ),
            encoding="utf-8",
        )
        env = os.environ.copy()
        for key in (
            "INSTANCE_ID",
            "DOWNLOADS_PATH",
            "SESSION_PATH",
            "LOG_FILE",
            "MEDIA_CACHE_PATH",
            "TEMP_PATH",
            "BACKUP_DIR",
            "RUNTIME_PATH",
            "PID_FILE",
            "LOCK_FILE",
        ):
            env.pop(key, None)
        env["MUSICBOT_ENV_FILE"] = str(env_file)
        env["MUSICBOT_DOTENV_OVERRIDE"] = "true"
        code = """
import json
from app.config.settings import settings
print(json.dumps({
    "instance": settings.INSTANCE_ID,
    "redis": settings.REDIS_NAMESPACE,
    "session_name": settings.BOT_SESSION_NAME,
    "downloads": settings.DOWNLOADS_PATH,
    "sessions": settings.SESSION_PATH,
    "logs": settings.LOG_FILE,
    "cache": settings.MEDIA_CACHE_PATH,
    "temp": settings.TEMP_PATH,
    "backup": settings.BACKUP_DIR,
    "pid": settings.PID_FILE,
    "lock": settings.LOCK_FILE,
    "scheduler": settings.SCHEDULER_ID,
}))
"""
        result = subprocess.run(
            [sys.executable, "-c", code],
            cwd=ROOT,
            env=env,
            text=True,
            capture_output=True,
            check=True,
        )
        outputs[instance_id] = json.loads(result.stdout)

    values_a = set(outputs["musicbot-a"].values())
    values_b = set(outputs["musicbot-b"].values())
    assert outputs["musicbot-a"]["redis"] == "musicbot:musicbot-a:"
    assert outputs["musicbot-b"]["redis"] == "musicbot:musicbot-b:"
    assert values_a.isdisjoint(values_b)


def test_single_instance_legacy_layout_still_loads(tmp_path: Path) -> None:
    """One instance on the pre-multi-instance folder layout must still start.

    Mirrors an install made before this architecture: data directories under
    app/, an out-of-tree BACKUP_DIR, and no RUNTIME_PATH/TEMP_PATH (both fall
    back to defaults). Only INSTANCE_ID was added.
    """
    root = tmp_path / "musicbot"
    env_file = tmp_path / "legacy.env"
    env_file.write_text(
        "\n".join(
            (
                "INSTANCE_ID=musicbot",
                f"INSTANCE_ROOT={root}",
                f"INSTANCE_DATA_DIR={root / 'var'}",
                # Legacy trailing slashes and app/-relative locations.
                f"DOWNLOADS_PATH={root / 'app' / 'downloads'}/",
                f"SESSION_PATH={root / 'app' / 'sessions'}/",
                f"LOG_FILE={root / 'app' / 'logs' / 'bot.log'}",
                f"MEDIA_CACHE_PATH={root / 'app' / 'cache'}/",
                f"BACKUP_DIR={tmp_path / 'backups' / 'musicbot'}",
                "DATABASE_URL=sqlite+aiosqlite:///:memory:",
                "REDIS_URL=redis://127.0.0.1:6379/15",
            )
        ),
        encoding="utf-8",
    )
    env = os.environ.copy()
    for key in (
        "INSTANCE_ID",
        "DOWNLOADS_PATH",
        "SESSION_PATH",
        "LOG_FILE",
        "MEDIA_CACHE_PATH",
        "TEMP_PATH",
        "BACKUP_DIR",
        "RUNTIME_PATH",
        "PID_FILE",
        "LOCK_FILE",
    ):
        env.pop(key, None)
    env["MUSICBOT_ENV_FILE"] = str(env_file)
    env["MUSICBOT_DOTENV_OVERRIDE"] = "true"

    code = """
import json
from app.config.settings import settings
print(json.dumps({
    "instance": settings.INSTANCE_ID,
    "redis": settings.REDIS_NAMESPACE,
    "session_name": settings.BOT_SESSION_NAME,
    "unit": settings.SYSTEMD_IDENTIFIER,
    "runtime": settings.RUNTIME_PATH,
    "pid": settings.PID_FILE,
    "temp": settings.TEMP_PATH,
}))
"""
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=True,
    )
    values = json.loads(result.stdout)
    assert values["instance"] == "musicbot"
    assert values["redis"] == "musicbot:musicbot:"
    assert values["session_name"] == "musicbot_musicbot"
    assert values["unit"] == "musicbot-musicbot"
    # Unset runtime paths fall back under INSTANCE_DATA_DIR, not the repo root.
    assert values["runtime"] == str(root / "var" / "run")
    assert values["pid"] == str(root / "var" / "run" / "musicbot.pid")
    assert values["temp"] == str(root / "var" / "tmp")


@pytest.mark.asyncio
async def test_redis_keys_do_not_collide_between_instances(monkeypatch) -> None:
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    original_namespace = settings.REDIS_NAMESPACE
    try:
        monkeypatch.setattr(settings, "REDIS_NAMESPACE", "musicbot:musicbot-a:")
        key_a = settings_key(-100)
        await redis.set(key_a, "a")

        monkeypatch.setattr(settings, "REDIS_NAMESPACE", "musicbot:musicbot-b:")
        key_b = settings_key(-100)
        await redis.set(key_b, "b")

        assert key_a != key_b
        assert await redis.get(key_a) == "a"
        assert await redis.get(key_b) == "b"
    finally:
        settings.REDIS_NAMESPACE = original_namespace
        await redis.aclose()


def test_telegram_session_names_are_instance_scoped(monkeypatch) -> None:
    monkeypatch.setenv("INSTANCE_ID", "musicbot-a")
    name_a = instance_session_name("helper_vc_7")
    monkeypatch.setenv("INSTANCE_ID", "musicbot-b")
    name_b = instance_session_name("helper_vc_7")
    assert name_a == "musicbot_a_helper_vc_7"
    assert name_b == "musicbot_b_helper_vc_7"
    assert name_a != name_b


def test_file_locks_are_instance_scoped_and_stale_files_are_safe(tmp_path: Path) -> None:
    config_a = _runtime_config(tmp_path / "a", "musicbot-a")
    config_b = _runtime_config(tmp_path / "b", "musicbot-b")
    guard_a = InstanceRuntimeGuard(config_a)
    guard_b = InstanceRuntimeGuard(config_b)
    guard_a.prepare_directories()
    guard_b.prepare_directories()
    guard_a.acquire_file_lock()
    guard_b.acquire_file_lock()
    assert Path(config_a.PID_FILE).read_text().strip() != ""
    assert Path(config_b.PID_FILE).read_text().strip() != ""

    duplicate = InstanceRuntimeGuard(config_a)
    with pytest.raises(InstanceAlreadyRunningError):
        duplicate.acquire_file_lock()

    guard_a._release_file_lock()
    Path(config_a.LOCK_FILE).write_text('{"stale":true}', encoding="utf-8")
    replacement = InstanceRuntimeGuard(config_a)
    replacement.acquire_file_lock()
    replacement._release_file_lock()
    guard_b._release_file_lock()


def test_shared_runtime_directory_is_rejected_for_another_instance(
    tmp_path: Path,
) -> None:
    config_a = _runtime_config(tmp_path / "shared", "musicbot-a")
    config_b = _runtime_config(tmp_path / "shared", "musicbot-b")
    InstanceRuntimeGuard(config_a).prepare_directories()

    with pytest.raises(InstanceResourceConflictError, match="musicbot-a"):
        InstanceRuntimeGuard(config_b).prepare_directories()


@pytest.mark.asyncio
async def test_database_claim_rejects_another_instance(tmp_path: Path, monkeypatch) -> None:
    engine_mod = sys.modules["app.database.engine"]

    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'instance.db'}"
    )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    monkeypatch.setattr(engine_mod, "engine", engine)
    monkeypatch.setattr(engine_mod, "async_session", factory)
    monkeypatch.setattr(settings, "INSTANCE_ID", "musicbot-a")
    await engine_mod.claim_database_for_instance()
    await engine_mod.claim_database_for_instance()

    monkeypatch.setattr(settings, "INSTANCE_ID", "musicbot-b")
    with pytest.raises(RuntimeError, match="ownership mismatch"):
        await engine_mod.claim_database_for_instance()
    await engine.dispose()


@pytest.mark.asyncio
async def test_operational_ports_can_run_independently_and_conflicts_fail() -> None:
    port_a = _free_port()
    port_b = _free_port()
    while port_b == port_a:
        port_b = _free_port()

    config_a = SimpleNamespace(
        INSTANCE_ID="musicbot-a",
        HEALTH_HOST="127.0.0.1",
        HEALTH_PORT=port_a,
        METRICS_HOST="127.0.0.1",
        METRICS_PORT=0,
    )
    config_b = SimpleNamespace(
        INSTANCE_ID="musicbot-b",
        HEALTH_HOST="127.0.0.1",
        HEALTH_PORT=port_b,
        METRICS_HOST="127.0.0.1",
        METRICS_PORT=0,
    )
    endpoints_a = OperationalEndpoints(config_a)
    endpoints_b = OperationalEndpoints(config_b)
    await endpoints_a.start()
    await endpoints_b.start()
    try:
        conflict = OperationalEndpoints(
            SimpleNamespace(
                INSTANCE_ID="musicbot-c",
                HEALTH_HOST="127.0.0.1",
                HEALTH_PORT=port_a,
                METRICS_HOST="127.0.0.1",
                METRICS_PORT=0,
            )
        )
        with pytest.raises(OSError):
            await conflict.start()
    finally:
        await endpoints_b.stop()
        await endpoints_a.stop()


def test_cleanup_only_touches_the_selected_instance(tmp_path: Path, monkeypatch) -> None:
    from app.services.media_health_service import cleanup_stale_downloads_sync

    downloads_a = tmp_path / "a" / "downloads"
    downloads_b = tmp_path / "b" / "downloads"
    downloads_a.mkdir(parents=True)
    downloads_b.mkdir(parents=True)
    file_a = downloads_a / "old-a.mp3"
    file_b = downloads_b / "old-b.mp3"
    file_a.write_bytes(b"a")
    file_b.write_bytes(b"b")
    marker_a = downloads_a / INSTANCE_MARKER_FILENAME
    marker_a.write_text("musicbot-a\n", encoding="ascii")
    os.utime(file_a, (1, 1))
    os.utime(file_b, (1, 1))
    os.utime(marker_a, (1, 1))

    monkeypatch.setattr(settings, "DOWNLOADS_PATH", str(downloads_a))
    assert cleanup_stale_downloads_sync(frozenset(), retention_seconds=0) == 1
    assert not file_a.exists()
    assert marker_a.exists()
    assert file_b.exists()


def test_scheduler_jobs_include_instance_identity() -> None:
    from app.scheduler import scheduler_job_id

    assert scheduler_job_id("nightly_db_backup") == "test:nightly_db_backup"


def test_installer_render_is_idempotent_and_instance_specific(tmp_path: Path) -> None:
    render_a = tmp_path / "render-a"
    render_b = tmp_path / "render-b"
    args_a = (
        "--instance",
        "musicbot-a",
        "--path",
        "/opt/musicbot-a",
        "--health-port",
        "18101",
        "--metrics-port",
        "19101",
        "--render-only",
        str(render_a),
    )
    first = _run_setup(*args_a)
    assert first.returncode == 0, first.stderr
    unit_a = render_a / "musicbot-musicbot-a.service"
    digest = _sha256(unit_a)

    second = _run_setup(*args_a)
    assert second.returncode == 0, second.stderr
    assert _sha256(unit_a) == digest

    result_b = _run_setup(
        "--instance",
        "musicbot-b",
        "--path",
        "/opt/musicbot-b",
        "--health-port",
        "18102",
        "--metrics-port",
        "19102",
        "--render-only",
        str(render_b),
    )
    assert result_b.returncode == 0, result_b.stderr
    unit_b = render_b / "musicbot-musicbot-b.service"
    assert unit_a.read_text() != unit_b.read_text()
    assert "/opt/musicbot-a/app/config.env" in unit_a.read_text()
    assert "/opt/musicbot-b/app/config.env" in unit_b.read_text()
    # The old root .env layout must not reappear.
    assert "/opt/musicbot-a/.env" not in unit_a.read_text()
    assert "musicbot-musicbot-a" in unit_a.read_text()
    assert "musicbot-musicbot-b" in unit_b.read_text()


def test_installer_rejects_invalid_ids_and_registered_conflicts(tmp_path: Path) -> None:
    invalid = _run_setup(
        "--instance",
        "../bad",
        "--path",
        "/opt/bad",
        "--render-only",
        str(tmp_path / "invalid"),
    )
    assert invalid.returncode != 0
    assert "Invalid instance id" in invalid.stderr

    registry = tmp_path / "registry"
    registry.mkdir()
    (registry / "musicbot-b.conf").write_text(
        "\n".join(
            (
                "INSTANCE_ID=musicbot-b",
                "INSTALL_DIR=/opt/musicbot-b",
                "DB_NAME=musicbot_musicbot_b",
                "HEALTH_PORT=18101",
                "METRICS_PORT=19101",
            )
        ),
        encoding="utf-8",
    )
    env = os.environ.copy()
    env["MUSICBOT_INSTANCE_REGISTRY"] = str(registry)

    same_port = _run_setup(
        "--instance",
        "musicbot-a",
        "--path",
        "/opt/musicbot-a",
        "--health-port",
        "18101",
        "--render-only",
        str(tmp_path / "same-port"),
        env=env,
    )
    assert same_port.returncode != 0
    assert "Port 18101" in same_port.stderr

    same_path = _run_setup(
        "--instance",
        "musicbot-a",
        "--path",
        "/opt/musicbot-b",
        "--health-port",
        "18102",
        "--render-only",
        str(tmp_path / "same-path"),
        env=env,
    )
    assert same_path.returncode != 0
    assert "already used" in same_path.stderr


def _parse_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.startswith("#"):
            key, _, value = line.partition("=")
            values[key.strip()] = value.strip()
    return values


def _unit_directive(unit: Path, name: str) -> str:
    for line in unit.read_text(encoding="utf-8").splitlines():
        if line.startswith(f"{name}="):
            return line.split("=", 1)[1].strip()
    return ""


def test_generated_unit_grants_write_access_to_every_runtime_path(tmp_path: Path) -> None:
    """The unit sandbox must cover every path the instance writes to.

    The unit sets ProtectSystem=strict, which makes the filesystem read-only
    apart from ReadWritePaths. A writable path missing from that list fails at
    startup with "Read-only file system" and, with Restart=on-failure, turns
    into a crash loop rather than an obvious config error.
    """
    render = tmp_path / "render"
    result = _run_setup(
        "--instance",
        "musicbot-a",
        "--path",
        "/opt/musicbot-a",
        "--render-only",
        str(render),
        env=_setup_env(tmp_path / "systemd", tmp_path / "registry"),
    )
    assert result.returncode == 0, result.stderr

    env = _parse_env_file(render / "musicbot-a.config.env")
    unit = render / "musicbot-musicbot-a.service"
    writable = [Path(p) for p in _unit_directive(unit, "ReadWritePaths").split() if p]
    assert writable, "unit declares no ReadWritePaths"

    written_paths = {
        key: Path(env[key])
        for key in (
            "INSTANCE_DATA_DIR",
            "RUNTIME_PATH",
            "DOWNLOADS_PATH",
            "SESSION_PATH",
            "MEDIA_CACHE_PATH",
            "TEMP_PATH",
            "BACKUP_DIR",
            "PID_FILE",
            "LOCK_FILE",
            "LOG_FILE",
            "BOT_RELOAD_SENTINEL_PATH",
        )
    }
    for key, path in written_paths.items():
        assert any(
            path == root or root in path.parents for root in writable
        ), f"{key}={path} is not under ReadWritePaths {writable}"

    # RuntimeDirectory must match RUNTIME_PATH so systemd creates and owns it.
    assert _unit_directive(unit, "RuntimeDirectory") == env["RUNTIME_PATH"].removeprefix(
        "/run/"
    )


def test_restart_budget_outlasts_redis_lease(tmp_path: Path) -> None:
    """systemd must keep retrying until a crashed instance's lease expires.

    SIGKILL/OOM leaves the Redis runtime lease unreleased for up to its TTL, so
    every restart inside that window fails with "Redis lease is already held".
    If RestartSec x StartLimitBurst is shorter than the TTL, systemd exhausts the
    budget first and parks the unit in failed state until an operator intervenes.
    """
    from app.runtime.instance import _REDIS_LEASE_TTL_SECONDS

    render = tmp_path / "render"
    result = _run_setup(
        "--instance",
        "musicbot-a",
        "--path",
        "/opt/musicbot-a",
        "--render-only",
        str(render),
        env=_setup_env(tmp_path / "systemd", tmp_path / "registry"),
    )
    assert result.returncode == 0, result.stderr

    unit = render / "musicbot-musicbot-a.service"
    restart_sec = int(_unit_directive(unit, "RestartSec"))
    burst = int(_unit_directive(unit, "StartLimitBurst"))
    interval = int(_unit_directive(unit, "StartLimitIntervalSec"))

    budget = restart_sec * burst
    assert budget > _REDIS_LEASE_TTL_SECONDS, (
        f"restart budget {budget}s (RestartSec={restart_sec} x "
        f"StartLimitBurst={burst}) must exceed the {_REDIS_LEASE_TTL_SECONDS}s "
        "Redis lease TTL, or a SIGKILL/OOM leaves the unit permanently failed"
    )
    # The burst window must be long enough to actually spend that budget.
    assert interval >= budget, (
        f"StartLimitIntervalSec={interval} is shorter than the {budget}s budget"
    )


def test_generated_unit_keeps_its_sandbox_hardening(tmp_path: Path) -> None:
    """Hardening is what keeps one instance out of another's files.

    ProtectSystem=strict in particular is what makes ReadWritePaths meaningful;
    dropping it would silently give an instance write access to the whole disk.
    """
    render = tmp_path / "render"
    result = _run_setup(
        "--instance",
        "musicbot-a",
        "--path",
        "/opt/musicbot-a",
        "--render-only",
        str(render),
        env=_setup_env(tmp_path / "systemd", tmp_path / "registry"),
    )
    assert result.returncode == 0, result.stderr

    unit = render / "musicbot-musicbot-a.service"
    for directive, expected in (
        ("NoNewPrivileges", "true"),
        ("PrivateTmp", "true"),
        ("ProtectHome", "true"),
        ("ProtectSystem", "strict"),
        ("UMask", "0077"),
        ("RuntimeDirectoryMode", "0750"),
        ("Restart", "on-failure"),
        ("KillSignal", "SIGTERM"),
    ):
        assert _unit_directive(unit, directive) == expected, (
            f"{directive} must stay {expected}"
        )

    # The service account must be instance-specific, never a shared/root identity.
    assert _unit_directive(unit, "User") == "mb-musicbot-a"
    assert _unit_directive(unit, "Group") == "mb-musicbot-a"
    assert _unit_directive(unit, "SyslogIdentifier") == "musicbot-musicbot-a"


def test_generated_control_script_is_valid_shell_and_instance_scoped(
    tmp_path: Path,
) -> None:
    """musicbotctl is emitted through a heredoc; a quoting slip breaks every op."""
    render = tmp_path / "render"
    result = _run_setup(
        "--instance",
        "musicbot-a",
        "--path",
        "/opt/musicbot-a",
        "--render-only",
        str(render),
        env=_setup_env(tmp_path / "systemd", tmp_path / "registry"),
    )
    assert result.returncode == 0, result.stderr

    control = render / "musicbotctl-musicbot-a"
    syntax = subprocess.run(
        ["bash", "-n", str(control)], text=True, capture_output=True, check=False
    )
    assert syntax.returncode == 0, syntax.stderr

    body = control.read_text(encoding="utf-8")
    assert "UNIT='musicbot-musicbot-a.service'" in body
    assert "INSTANCE='musicbot-a'" in body
    # Every documented per-instance operation must be routed.
    for action in (
        "status",
        "start",
        "restart",
        "stop",
        "logs",
        "migrate",
        "backup",
        "restore",
        "upgrade",
        "uninstall",
    ):
        assert f"{action})" in body, f"musicbotctl does not handle {action}"


def _load_conftest_module():
    """Load tests/conftest.py standalone so its guards can be asserted directly."""
    path = ROOT / "tests" / "conftest.py"
    spec = importlib.util.spec_from_file_location("_isolation_conftest", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_test_suite_refuses_the_deployed_redis_database() -> None:
    """Fixtures here call flushdb(); db 0 belongs to deployed instances.

    A flush of a shared Redis destroys every instance's keys and leases at once,
    so pointing the suite at db 0 must fail loudly rather than run.
    """
    conftest = _load_conftest_module()

    with pytest.raises(RuntimeError, match="Refusing to run tests against Redis db 0"):
        conftest._assert_safe_test_redis_url(
            "redis://localhost:6379/0", allow_external=False
        )

    # The reserved test db and a deliberate opt-in must both be accepted.
    conftest._assert_safe_test_redis_url("redis://localhost:6379/15", allow_external=False)
    conftest._assert_safe_test_redis_url("redis://localhost:6379/0", allow_external=True)

    # A URL with no db component resolves to db 0 and must not slip through.
    for aliased in (
        "redis://127.0.0.1:6379/0",
        "redis://localhost:6379",
        "redis://localhost:6379/",
        "rediss://user:pw@example:6380/0",
    ):
        with pytest.raises(RuntimeError):
            conftest._assert_safe_test_redis_url(aliased, allow_external=False)


def test_suite_never_loads_a_deployed_env_file() -> None:
    """Tests must not read an installed instance's .env.

    settings.py searches PROJECT_ROOT/.env first. Once setup_server.sh installs an
    instance there, importing settings would pull that instance's BOT_TOKEN,
    RUNTIME_PATH and BOT_RELOAD_SENTINEL_PATH into os.environ — real secrets in
    the test process, and in every subprocess built from os.environ.copy().
    """
    from app.config.settings import LOADED_ENV_FILE

    isolated = (ROOT / "tests" / "pytest-isolated.env").resolve()
    assert isolated.is_file(), "the isolated test env file is missing"
    assert LOADED_ENV_FILE == isolated, (
        f"tests loaded {LOADED_ENV_FILE} instead of {isolated}; a deployed .env "
        "would leak its secrets into the suite"
    )

    # Nothing instance-owned from a deployed host may reach the environment.
    for leaked in ("BOT_RELOAD_SENTINEL_PATH", "RUNTIME_PATH", "LOCK_FILE", "PID_FILE"):
        assert not os.environ.get(leaked), f"{leaked} leaked from a deployed env file"


def test_conftest_claims_redis_url_before_test_modules_can() -> None:
    """conftest must win the REDIS_URL setdefault race.

    Dozens of test modules open with setdefault("REDIS_URL", ".../0"). setdefault
    is first-wins, so if conftest leaves it unset on any path, one of those
    modules silently points the run at the deployed database.
    """
    source = (ROOT / "tests" / "conftest.py").read_text(encoding="utf-8")
    assert 'os.environ.setdefault("REDIS_URL"' in source

    # The claim must not sit inside a conditional branch.
    for line in source.splitlines():
        if 'setdefault("REDIS_URL"' in line:
            assert not line.startswith((" ", "\t")), (
                "REDIS_URL default is indented, so some path leaves it unset: "
                f"{line!r}"
            )


def _write_unit(path: Path, workdir: str) -> None:
    """Write a pre-multi-instance style unit that runs app.main from workdir."""
    path.write_text(
        "\n".join(
            (
                "[Service]",
                f"WorkingDirectory={workdir}",
                f"ExecStart={workdir}/venv/bin/python -m app.main",
            )
        ),
        encoding="utf-8",
    )


def _setup_env(systemd_dir: Path, registry: Path) -> dict[str, str]:
    env = os.environ.copy()
    env["MUSICBOT_SYSTEMD_DIR"] = str(systemd_dir)
    env["MUSICBOT_INSTANCE_REGISTRY"] = str(registry)
    return env


def test_legacy_adoption_runs_after_every_fallible_step() -> None:
    """Stopping the old service must be the last thing before the new unit lands.

    Adoption disables and deletes the running unit. If it ran before the venv,
    database, or migration steps, any failure there (no network, bad pip, DB
    down) would leave the host with the old unit deleted, no new unit written,
    and the bot down with no rollback target.
    """
    source = SETUP.read_text(encoding="utf-8")
    lines = source.splitlines()

    def call_line(name: str) -> int:
        for i, line in enumerate(lines):
            if line.strip() == name:
                return i
        raise AssertionError(f"{name} is never called")

    adopt = call_line("adopt_conflicting_units")
    write_unit = call_line("write_deployment_files")
    for fallible in (
        "install_packages",
        "copy_release",
        "install_venv",
        "prepare_env_base",
        "prepare_database",
        "run_migrations",
    ):
        assert call_line(fallible) < adopt, (
            f"{fallible} must run before adopt_conflicting_units stops the old unit"
        )
    assert adopt < write_unit, "the new unit must be written straight after adoption"

    # The early check must stay detection-only.
    detect_body = source.split("assert_no_conflicting_units() {", 1)[1].split("\n}", 1)[0]
    assert "rm -f" not in detect_body and "disable --now" not in detect_body, (
        "assert_no_conflicting_units must not mutate; adoption belongs in "
        "adopt_conflicting_units"
    )


def test_installer_refuses_legacy_unit_owning_the_same_path(tmp_path: Path) -> None:
    """A pre-multi-instance unit must never be left fighting the new unit."""
    systemd_dir = tmp_path / "systemd"
    systemd_dir.mkdir()
    legacy = systemd_dir / "musicbot.service"
    _write_unit(legacy, "/opt/musicbot-a")
    env = _setup_env(systemd_dir, tmp_path / "registry")

    refused = _run_setup(
        "--instance",
        "musicbot-a",
        "--path",
        "/opt/musicbot-a",
        "--render-only",
        str(tmp_path / "refused"),
        env=env,
    )
    assert refused.returncode != 0
    # warn() reports the offending unit on stdout; fail() explains on stderr.
    assert "musicbot.service" in refused.stdout
    assert "Refusing to install" in refused.stderr
    # Detection alone must never touch the server.
    assert legacy.exists()


def test_installer_ignores_units_owning_a_different_path(tmp_path: Path) -> None:
    """Another instance's unit must not block this instance's install."""
    systemd_dir = tmp_path / "systemd"
    systemd_dir.mkdir()
    _write_unit(systemd_dir / "musicbot-musicbot-b.service", "/opt/musicbot-b")
    env = _setup_env(systemd_dir, tmp_path / "registry")

    result = _run_setup(
        "--instance",
        "musicbot-a",
        "--path",
        "/opt/musicbot-a",
        "--render-only",
        str(tmp_path / "render"),
        env=env,
    )
    assert result.returncode == 0, result.stderr


def test_render_only_never_removes_a_legacy_unit_even_when_adopting(
    tmp_path: Path,
) -> None:
    """--render-only must stay side-effect free while reporting the adoption."""
    systemd_dir = tmp_path / "systemd"
    systemd_dir.mkdir()
    legacy = systemd_dir / "musicbot.service"
    _write_unit(legacy, "/opt/musicbot-a")
    env = _setup_env(systemd_dir, tmp_path / "registry")

    result = _run_setup(
        "--instance",
        "musicbot-a",
        "--path",
        "/opt/musicbot-a",
        "--adopt-legacy-unit",
        "--render-only",
        str(tmp_path / "render"),
        env=env,
    )
    assert result.returncode == 0, result.stderr
    assert "would be stopped and removed" in result.stdout
    assert legacy.exists()


# ── Zero-argument inference, canonical env, and release-source safety ────────


def _fake_install_tree(base: Path, folder: str) -> Path:
    """Create a minimal tree the installer accepts as a release/install folder."""
    root = base / folder
    (root / "app").mkdir(parents=True)
    shutil.copy2(SETUP, root / "setup_server.sh")
    (root / "requirements.txt").write_text("# minimal\n", encoding="utf-8")
    shutil.copy2(ROOT / "app" / "config.env.example", root / "app" / "config.env.example")
    return root


def _run_installer(script: Path, *args: str, env: dict[str, str] | None = None, cwd: Path | None = None):
    return subprocess.run(
        [str(script), *args],
        cwd=str(cwd) if cwd else str(script.parent),
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def _infer_env(tmp_path: Path) -> dict[str, str]:
    env = os.environ.copy()
    env["MUSICBOT_INSTANCE_REGISTRY"] = str(tmp_path / "registry")
    env["MUSICBOT_SYSTEMD_DIR"] = str(tmp_path / "systemd")
    return env


def test_zero_argument_install_infers_path_and_instance(tmp_path: Path) -> None:
    """`sudo ./setup_server.sh` with no flags must configure this folder."""
    root = _fake_install_tree(tmp_path, "musicbot-z")
    render = tmp_path / "render"

    result = _run_installer(
        root / "setup_server.sh", "--render-only", str(render), env=_infer_env(tmp_path)
    )
    assert result.returncode == 0, result.stderr
    assert "musicbot-z" in result.stdout

    unit = render / "musicbot-musicbot-z.service"
    assert unit.is_file(), sorted(p.name for p in render.iterdir())
    assert _unit_directive(unit, "WorkingDirectory") == str(root)


def test_install_path_follows_the_script_not_the_caller_cwd(tmp_path: Path) -> None:
    """The target is where the script lives; cwd must never decide it."""
    root = _fake_install_tree(tmp_path, "musicbot-z")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    render = tmp_path / "render"

    result = _run_installer(
        root / "setup_server.sh",
        "--render-only",
        str(render),
        env=_infer_env(tmp_path),
        cwd=elsewhere,
    )
    assert result.returncode == 0, result.stderr
    unit = render / "musicbot-musicbot-z.service"
    assert _unit_directive(unit, "WorkingDirectory") == str(root)


def test_symlinked_installer_resolves_to_the_real_folder(tmp_path: Path) -> None:
    root = _fake_install_tree(tmp_path, "musicbot-z")
    link = tmp_path / "run-setup.sh"
    link.symlink_to(root / "setup_server.sh")
    render = tmp_path / "render"

    result = _run_installer(link, "--render-only", str(render), env=_infer_env(tmp_path))
    assert result.returncode == 0, result.stderr
    unit = render / "musicbot-musicbot-z.service"
    assert _unit_directive(unit, "WorkingDirectory") == str(root)


def test_folder_name_that_is_not_a_valid_instance_id_is_rejected(tmp_path: Path) -> None:
    root = _fake_install_tree(tmp_path, "Bad_Name")
    result = _run_installer(
        root / "setup_server.sh",
        "--render-only",
        str(tmp_path / "render"),
        env=_infer_env(tmp_path),
    )
    assert result.returncode != 0
    assert "Cannot derive an instance id" in result.stderr
    assert "--instance" in result.stderr


def test_explicit_flags_override_inference(tmp_path: Path) -> None:
    root = _fake_install_tree(tmp_path, "musicbot-z")
    other = tmp_path / "opt" / "musicbot-other"
    other.mkdir(parents=True)
    render = tmp_path / "render"

    result = _run_installer(
        root / "setup_server.sh",
        "--instance",
        "musicbot-other",
        "--path",
        str(other),
        "--render-only",
        str(render),
        env=_infer_env(tmp_path),
    )
    assert result.returncode == 0, result.stderr
    unit = render / "musicbot-musicbot-other.service"
    assert _unit_directive(unit, "WorkingDirectory") == str(other)


def test_generated_unit_uses_the_canonical_app_config_env(tmp_path: Path) -> None:
    """The only runtime env file is <root>/app/config.env — never <root>/.env."""
    root = _fake_install_tree(tmp_path, "musicbot-z")
    render = tmp_path / "render"

    result = _run_installer(
        root / "setup_server.sh", "--render-only", str(render), env=_infer_env(tmp_path)
    )
    assert result.returncode == 0, result.stderr

    unit = render / "musicbot-musicbot-z.service"
    canonical = f"{root}/app/config.env"
    assert _unit_directive(unit, "EnvironmentFile") == canonical
    body = unit.read_text(encoding="utf-8")
    assert f'MUSICBOT_ENV_FILE={canonical}' in body
    assert f"{root}/.env" not in body

    control = (render / "musicbotctl-musicbot-z").read_text(encoding="utf-8")
    assert f"ENV_FILE='{canonical}'" in control
    assert f"{root}/.env'" not in control


def test_shared_redis_values_are_fixed_for_every_instance(tmp_path: Path) -> None:
    """REDIS_URL/REDIS_NAMESPACE_ROOT are shared constants, never per-instance."""
    root = _fake_install_tree(tmp_path, "musicbot-z")
    render = tmp_path / "render"
    result = _run_installer(
        root / "setup_server.sh", "--render-only", str(render), env=_infer_env(tmp_path)
    )
    assert result.returncode == 0, result.stderr

    env = _parse_env_file(render / "musicbot-z.config.env")
    assert env["REDIS_URL"] == "redis://127.0.0.1:6379/0"
    assert env["REDIS_NAMESPACE_ROOT"] == "musicbot"
    # Isolation still comes from the namespace, not a separate server.
    assert env["INSTANCE_ID"] == "musicbot-z"


def test_upgrade_from_external_release_never_targets_the_release_folder(
    tmp_path: Path,
) -> None:
    """A release folder is a source, never the installation being upgraded."""
    release = _fake_install_tree(tmp_path, "release-new")
    registry = tmp_path / "registry"
    registry.mkdir()
    env = _infer_env(tmp_path)

    # No registry entry: must fail rather than install into the release folder.
    blind = _run_installer(
        release / "setup_server.sh",
        "--upgrade",
        "--source",
        str(release),
        "--render-only",
        str(tmp_path / "render-blind"),
        env=env,
    )
    assert blind.returncode != 0
    assert "Cannot infer which installation to upgrade" in blind.stderr

    # With a registry entry it resolves to the installed directory.
    installed = _fake_install_tree(tmp_path, "musicbot-z")
    (registry / "musicbot-z.conf").write_text(
        "\n".join(
            (
                "INSTANCE_ID=musicbot-z",
                f"INSTALL_DIR={installed}",
                "DB_NAME=musicbot_musicbot_z",
                "HEALTH_PORT=0",
                "METRICS_PORT=0",
            )
        ),
        encoding="utf-8",
    )
    render = tmp_path / "render-resolved"
    resolved = _run_installer(
        release / "setup_server.sh",
        "--upgrade",
        "--source",
        str(release),
        "--render-only",
        str(render),
        env=env,
    )
    assert resolved.returncode == 0, resolved.stderr
    unit = render / "musicbot-musicbot-z.service"
    assert _unit_directive(unit, "WorkingDirectory") == str(installed)
    assert _unit_directive(unit, "WorkingDirectory") != str(release)


def test_non_interactive_run_fails_instead_of_hanging(tmp_path: Path) -> None:
    """Without a TTY the installer must not block on a prompt."""
    root = _fake_install_tree(tmp_path, "musicbot-z")
    result = subprocess.run(
        [str(root / "setup_server.sh"), "--render-only", str(tmp_path / "render")],
        cwd=str(root),
        env=_infer_env(tmp_path),
        text=True,
        capture_output=True,
        stdin=subprocess.DEVNULL,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "Using inferred instance id" in result.stdout


# ── Root .env migration and deployment ZIP ──────────────────────────────────


def _extract_shell_function(name: str) -> str:
    """Pull one function verbatim out of the installer for isolated testing."""
    source = SETUP.read_text(encoding="utf-8")
    start = source.index(f"{name}() {{")
    end = source.index("\n}\n", start) + len("\n}\n")
    return source[start:end]


def _run_migrate_root_env(root: Path, instance_id: str) -> subprocess.CompletedProcess[str]:
    harness = root.parent / f"harness-{instance_id}.sh"
    harness.write_text(
        "\n".join(
            (
                "set -uo pipefail",
                'warn() { echo "[WARN] $*"; }',
                'fail() { echo "[FAIL] $*" >&2; exit 1; }',
                f'INSTALL_DIR={str(root)!r}',
                f'INSTANCE_ID={instance_id!r}',
                'ENV_FILE="$INSTALL_DIR/app/config.env"',
                'LEGACY_ROOT_ENV="$INSTALL_DIR/.env"',
                _extract_shell_function("migrate_root_env"),
                "migrate_root_env",
            )
        ),
        encoding="utf-8",
    )
    return subprocess.run(
        ["bash", str(harness)], text=True, capture_output=True, check=False
    )


def test_root_env_is_migrated_into_app_config_env_and_cannot_shadow(tmp_path: Path) -> None:
    """An old root .env must move to app/config.env with its secrets intact."""
    root = tmp_path / "musicbot"
    (root / "app").mkdir(parents=True)
    (root / ".env").write_text(
        "\n".join(
            (
                "INSTANCE_ID=musicbot",
                "BOT_TOKEN=123456:LIVE-TOKEN-VALUE",
                "API_ID=1234567",
                "API_HASH=abcdef1234567890abcdef1234567890",
                "DEVELOPER_ID=42",
                "DATABASE_URL=postgresql+asyncpg://u:p@localhost:5432/musicbot_db",
                "RUNTIME_PATH=/run/musicbot-musicbot",
            )
        ),
        encoding="utf-8",
    )
    # A stale canonical file from before the root .env era.
    (root / "app" / "config.env").write_text(
        "INSTANCE_ID=musicbot\nBOT_TOKEN=STALE\nRUNTIME_PATH=/opt/musicbot/var/run\n",
        encoding="utf-8",
    )

    result = _run_migrate_root_env(root, "musicbot")
    assert result.returncode == 0, result.stderr

    canonical = _parse_env_file(root / "app" / "config.env")
    # The live root .env wins, secrets and all.
    assert canonical["BOT_TOKEN"] == "123456:LIVE-TOKEN-VALUE"
    assert canonical["RUNTIME_PATH"] == "/run/musicbot-musicbot"
    assert oct(( root / "app" / "config.env").stat().st_mode)[-3:] == "600"

    # The root file must be gone so it can never shadow the canonical one.
    assert not (root / ".env").exists()
    archived = list(root.glob(".env.migrated.*"))
    assert len(archived) == 1, "the migrated root .env must be archived"
    assert "LIVE-TOKEN-VALUE" in archived[0].read_text(encoding="utf-8")
    superseded = list((root / "app").glob("config.env.superseded.*"))
    assert len(superseded) == 1, "the replaced config.env must be kept"


def test_root_env_of_another_instance_is_never_migrated(tmp_path: Path) -> None:
    root = tmp_path / "musicbot"
    (root / "app").mkdir(parents=True)
    (root / ".env").write_text("INSTANCE_ID=musicbot-other\nBOT_TOKEN=x\n", encoding="utf-8")

    result = _run_migrate_root_env(root, "musicbot")
    assert result.returncode != 0
    assert "belongs to instance musicbot-other" in result.stderr
    assert (root / ".env").exists(), "a foreign .env must be left untouched"


def test_settings_never_discovers_a_root_env_file(tmp_path: Path) -> None:
    """<INSTANCE_ROOT>/.env must not be a settings discovery candidate."""
    source = (ROOT / "app" / "config" / "settings.py").read_text(encoding="utf-8")
    # The whole assignment, up to the line that closes it.
    candidates = source.split("_config_candidates = (", 1)[1].split("\n)", 1)[0]
    assert 'PROJECT_ROOT / ".env"' not in candidates
    assert 'Path.cwd() / ".env"' not in candidates
    # app/config.env (settings.py lives in app/config/) is the canonical first choice.
    assert 'parent.parent / "config.env"' in candidates
    assert candidates.index('parent.parent / "config.env"') < candidates.index(
        'PROJECT_ROOT / "config.env"'
    )


def test_deploy_zip_ships_the_installer_and_no_secrets(tmp_path: Path) -> None:
    """The ZIP must carry the new installer/env template and no live state."""
    import zipfile

    out = tmp_path / "deploy.zip"
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "build_deploy_zip.py"), "--output", str(out)],
        cwd=ROOT,
        env={**os.environ, "TEST_MODE": "1"},
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr + result.stdout
    with zipfile.ZipFile(out) as archive:
        names = set(archive.namelist())
        installer = archive.read("setup_server.sh").decode("utf-8")

    for required in (
        "setup_server.sh",
        "requirements.txt",
        "app/config.env.example",
        "app/main.py",
        "app/alembic.ini",
        "docs/MULTI_INSTANCE_DEPLOYMENT.md",
        "docs/SIMPLE_MULTI_INSTANCE_GUIDE_FA.md",
        "app/database/migrations/versions/0033_instance_database_ownership.py",
    ):
        assert required in names, f"{required} must ship in the deployment ZIP"

    # The shipped installer is the current one.
    assert 'ENV_FILE="${INSTALL_DIR}/app/config.env"' in installer
    assert "migrate_root_env" in installer

    # Nothing live, generated, or secret may ship.
    for name in names:
        lowered = name.lower()
        assert lowered != ".env" and not lowered.startswith(".env.")
        assert lowered != "app/config.env"
        assert ".session" not in lowered
        assert "config.env.superseded" not in lowered
        assert not lowered.startswith(("venv/", "var/", "tests/", "logs/", "dist/"))
        assert lowered != "musicbotctl"
        assert "__pycache__" not in lowered


def test_reinstall_preserves_secrets_and_writes_fixed_redis(tmp_path: Path) -> None:
    """Re-running the installer must never clobber configured credentials."""
    root = tmp_path / "musicbot-k"
    (root / "app").mkdir(parents=True)
    env_file = root / "app" / "config.env"
    env_file.write_text(
        "\n".join(
            (
                "INSTANCE_ID=musicbot-k",
                "BOT_TOKEN=123456:EXISTING-SECRET",
                "API_ID=7654321",
                "API_HASH=" + "a" * 32,
                "DEVELOPER_ID=999",
                "DATABASE_URL=postgresql+asyncpg://u:secretpw@localhost:5432/musicbot_musicbot_k",
                "HELPER_SESSION_KEY_CURRENT=zzzz",
            )
        ),
        encoding="utf-8",
    )

    harness = tmp_path / "reinstall.sh"
    harness.write_text(
        "\n".join(
            (
                "set -uo pipefail",
                'warn() { echo "[WARN] $*"; }',
                'fail() { echo "[FAIL] $*" >&2; exit 1; }',
                f'INSTALL_DIR={str(root)!r}',
                'INSTANCE_ID="musicbot-k"',
                'ENV_FILE="$INSTALL_DIR/app/config.env"',
                'LEGACY_ROOT_ENV="$INSTALL_DIR/.env"',
                'DATA_DIR="$INSTALL_DIR/var"',
                'DOWNLOADS_DIR="$DATA_DIR/downloads"',
                'SESSIONS_DIR="$DATA_DIR/sessions"',
                'LOGS_DIR="$DATA_DIR/logs"',
                'CACHE_DIR="$DATA_DIR/cache"',
                'TEMP_DIR="$DATA_DIR/tmp"',
                'BACKUP_DIR="$DATA_DIR/backups"',
                'RUNTIME_DIR="/run/musicbot-musicbot-k"',
                'PID_FILE="$RUNTIME_DIR/musicbot.pid"',
                'LOCK_FILE="$RUNTIME_DIR/musicbot.lock"',
                "HEALTH_PORT=0",
                "METRICS_PORT=0",
                'REDIS_URL_DEFAULT="redis://127.0.0.1:6379/0"',
                _extract_shell_function("get_env"),
                _extract_shell_function("set_env"),
                _extract_shell_function("migrate_root_env"),
                _extract_shell_function("prepare_env_base"),
                "prepare_env_base",
            )
        ),
        encoding="utf-8",
    )
    result = subprocess.run(["bash", str(harness)], text=True, capture_output=True, check=False)
    assert result.returncode == 0, result.stderr

    env = _parse_env_file(env_file)
    assert env["BOT_TOKEN"] == "123456:EXISTING-SECRET"
    assert env["API_ID"] == "7654321"
    assert env["DEVELOPER_ID"] == "999"
    assert env["HELPER_SESSION_KEY_CURRENT"] == "zzzz"
    assert "secretpw" in env["DATABASE_URL"]

    # Shared infrastructure is rewritten to the fixed values every time.
    assert env["REDIS_URL"] == "redis://127.0.0.1:6379/0"
    assert env["REDIS_NAMESPACE_ROOT"] == "musicbot"

    # Instance-owned paths are derived, not asked for.
    assert env["INSTANCE_ROOT"] == str(root)
    assert env["RUNTIME_PATH"] == "/run/musicbot-musicbot-k"
    assert oct(env_file.stat().st_mode)[-3:] == "600"


def test_bare_rerun_reuses_registered_identity(tmp_path: Path) -> None:
    """A registered instance keeps its user/database/ports on a flagless re-run.

    Without this, `sudo ./setup_server.sh` in an installed folder would fall back
    to instance-derived defaults (mb-<id>, musicbot_<id>, ports 0) and try to
    switch a working install onto a different service account, database, and
    disabled endpoints.
    """
    root = _fake_install_tree(tmp_path, "musicbot-z")
    registry = tmp_path / "registry"
    registry.mkdir()
    (registry / "musicbot-z.conf").write_text(
        "\n".join(
            (
                "INSTANCE_ID=musicbot-z",
                f"INSTALL_DIR={root}",
                "UNIT_NAME=musicbot-musicbot-z.service",
                "SERVICE_USER=legacyuser",
                "DB_NAME=legacy_db",
                "DB_USER=legacy_dbuser",
                "HEALTH_PORT=18999",
                "METRICS_PORT=19999",
            )
        ),
        encoding="utf-8",
    )
    render = tmp_path / "render"
    result = _run_installer(
        root / "setup_server.sh", "--render-only", str(render), env=_infer_env(tmp_path)
    )
    assert result.returncode == 0, result.stderr

    unit = render / "musicbot-musicbot-z.service"
    assert _unit_directive(unit, "User") == "legacyuser"
    assert _unit_directive(unit, "Group") == "legacyuser"

    env = _parse_env_file(render / "musicbot-z.config.env")
    assert env["HEALTH_PORT"] == "18999"
    assert env["METRICS_PORT"] == "19999"
    assert "legacy_dbuser" in env["DATABASE_URL"]
    assert env["DATABASE_URL"].endswith("/legacy_db")


def test_explicit_flags_still_beat_registered_defaults(tmp_path: Path) -> None:
    root = _fake_install_tree(tmp_path, "musicbot-z")
    registry = tmp_path / "registry"
    registry.mkdir()
    (registry / "musicbot-z.conf").write_text(
        "\n".join(
            (
                "INSTANCE_ID=musicbot-z",
                f"INSTALL_DIR={root}",
                "SERVICE_USER=legacyuser",
                "DB_NAME=legacy_db",
                "DB_USER=legacy_dbuser",
                "HEALTH_PORT=18999",
                "METRICS_PORT=19999",
            )
        ),
        encoding="utf-8",
    )
    render = tmp_path / "render"
    result = _run_installer(
        root / "setup_server.sh",
        "--service-user",
        "chosenuser",
        "--health-port",
        "18001",
        "--render-only",
        str(render),
        env=_infer_env(tmp_path),
    )
    assert result.returncode == 0, result.stderr
    unit = render / "musicbot-musicbot-z.service"
    assert _unit_directive(unit, "User") == "chosenuser"
    env = _parse_env_file(render / "musicbot-z.config.env")
    assert env["HEALTH_PORT"] == "18001"
    # Unspecified values still come from the registry.
    assert env["METRICS_PORT"] == "19999"

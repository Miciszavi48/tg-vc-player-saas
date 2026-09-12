from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv


def _env_bool_raw(key: str, default: bool) -> bool:
    val = os.getenv(key)
    if val is None:
        return default
    return val.strip().lower() in ("1", "true", "yes")

PROJECT_ROOT = Path(__file__).resolve().parents[2]
_explicit_env_file = os.getenv("MUSICBOT_ENV_FILE", "").strip()
# The canonical runtime env file is <INSTANCE_ROOT>/app/config.env. A root
# <INSTANCE_ROOT>/.env is deliberately NOT a candidate: setup_server.sh migrates
# any leftover into app/config.env, and keeping it discoverable would let a stale
# copy shadow the canonical file. MUSICBOT_ENV_FILE still overrides everything.
_config_candidates = (
    [Path(_explicit_env_file).expanduser()]
    if _explicit_env_file
    else [
        Path(__file__).resolve().parent.parent / "config.env",
        Path(__file__).resolve().parent / "config.env",
        PROJECT_ROOT / "config.env",
        Path.cwd() / "config.env",
    ]
)

LOADED_ENV_FILE: Path | None = None
for _p in _config_candidates:
    if _p.is_file():
        load_dotenv(
            _p,
            override=_env_bool_raw("MUSICBOT_DOTENV_OVERRIDE", False),
        )
        LOADED_ENV_FILE = _p.resolve()
        break
else:
    if _explicit_env_file:
        raise RuntimeError(f"MUSICBOT_ENV_FILE does not exist: {_explicit_env_file}")
    load_dotenv()


def _env(key: str, default: str | None = None) -> str | None:
    return os.getenv(key, default)


def _env_int(key: str, default: int) -> int:
    val = os.getenv(key)
    return int(val) if val is not None else default


def _env_bool(key: str, default: bool) -> bool:
    val = os.getenv(key)
    if val is None:
        return default
    return val.strip().lower() in ("1", "true", "yes")


_INSTANCE_ID_RE = re.compile(r"^[a-z][a-z0-9-]{0,27}$")
_NAMESPACE_ROOT_RE = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
INSTANCE_MARKER_FILENAME = ".musicbot-instance-id"


def parse_instance_id(
    instance_id: str | None = None,
    bot_instance_id: str | None = None,
    *,
    test_mode: bool | None = None,
) -> str:
    """Return the required stable instance id after strict validation."""
    primary = (instance_id if instance_id is not None else os.getenv("INSTANCE_ID", "")).strip()
    alias = (
        bot_instance_id
        if bot_instance_id is not None
        else os.getenv("BOT_INSTANCE_ID", "")
    ).strip()
    if primary and alias and primary != alias:
        raise ValueError("INSTANCE_ID and BOT_INSTANCE_ID must match when both are set")

    value = primary or alias
    if test_mode is None:
        test_mode = _env_bool("TEST_MODE", False)
    if not value and test_mode:
        value = "test"
    if not value:
        raise ValueError(
            "INSTANCE_ID is required. Use a stable lowercase id such as musicbot-a."
        )
    if not _INSTANCE_ID_RE.fullmatch(value):
        raise ValueError(
            "INSTANCE_ID must match ^[a-z][a-z0-9-]{0,27}$"
        )
    return value


def _instance_root() -> Path:
    raw = (_env("INSTANCE_ROOT", "") or "").strip()
    return Path(raw).expanduser().resolve() if raw else PROJECT_ROOT


def _instance_data_dir() -> Path:
    raw = (_env("INSTANCE_DATA_DIR", "") or "").strip()
    return (
        Path(raw).expanduser().resolve()
        if raw
        else (_instance_root() / "var").resolve()
    )


def _runtime_path() -> Path:
    raw = (_env("RUNTIME_PATH", "") or "").strip()
    return (
        Path(raw).expanduser().resolve()
        if raw
        else (_instance_data_dir() / "run").resolve()
    )


def _path_env(key: str, default: Path) -> str:
    raw = (_env(key, "") or "").strip()
    path = Path(raw).expanduser() if raw else default
    if not path.is_absolute():
        path = _instance_root() / path
    return str(path.resolve())


def _redis_namespace() -> str:
    instance_id = parse_instance_id()
    root = (_env("REDIS_NAMESPACE_ROOT", "musicbot") or "").strip()
    if not _NAMESPACE_ROOT_RE.fullmatch(root):
        raise ValueError(
            "REDIS_NAMESPACE_ROOT must match ^[a-z][a-z0-9_-]{0,31}$"
        )
    return f"{root}:{instance_id}:"


def _validated_port(key: str, default: int = 0) -> int:
    value = _env_int(key, default)
    if value < 0 or value > 65535:
        raise ValueError(f"{key} must be 0 (disabled) or between 1 and 65535")
    return value


def instance_session_name(name: str) -> str:
    """Return a filesystem-safe Telegram client name scoped to INSTANCE_ID."""
    safe_name = re.sub(r"[^A-Za-z0-9_-]+", "_", str(name)).strip("_")
    if not safe_name:
        safe_name = "session"
    instance = parse_instance_id().replace("-", "_")
    return f"{instance}_{safe_name}"[:120]


_developer_ids_cache: tuple[frozenset[int], int] | None = None


def _parse_developer_id_env(raw: str | None = None) -> tuple[frozenset[int], int]:
    """Parse DEVELOPER_ID env as one or more comma-separated Telegram user IDs.

    The env key name stays singular for backward compatibility; values may be
    ``123456789`` or ``123456789,987654321``. Use ``is_developer()`` for access
    checks — not scalar ``settings.DEVELOPER_ID``.

    Args:
        raw: Env string override for tests; defaults to ``os.getenv("DEVELOPER_ID")``.

    Returns:
        Tuple of (developer id set, primary id). Primary is the first parsed id
        or ``0`` when the set is empty.

    Raises:
        ValueError: When any segment is empty or not a valid integer.
    """
    if raw is None:
        raw = os.getenv("DEVELOPER_ID")
    text = (raw or "").strip()
    if not text or text == "0":
        return frozenset(), 0

    ids: list[int] = []
    for segment in text.split(","):
        part = segment.strip()
        if not part:
            raise ValueError(
                "DEVELOPER_ID contains an empty segment; use comma-separated "
                "integers without consecutive commas"
            )
        try:
            parsed = int(part)
        except ValueError as exc:
            raise ValueError(
                f"DEVELOPER_ID segment {part!r} is not a valid integer"
            ) from exc
        ids.append(parsed)

    id_set = frozenset(ids)
    return id_set, ids[0]


def _load_developer_ids() -> tuple[frozenset[int], int]:
    """Load and cache developer IDs from the environment once per process."""
    global _developer_ids_cache
    if _developer_ids_cache is None:
        _developer_ids_cache = _parse_developer_id_env()
    return _developer_ids_cache


@dataclass
class Settings:
    # ── Instance identity and isolation ──
    INSTANCE_ID: str = field(default_factory=parse_instance_id)
    INSTANCE_ROOT: str = field(default_factory=lambda: str(_instance_root()))
    INSTANCE_DATA_DIR: str = field(default_factory=lambda: str(_instance_data_dir()))
    RUNTIME_PATH: str = field(default_factory=lambda: str(_runtime_path()))
    REDIS_NAMESPACE: str = field(default_factory=_redis_namespace)
    DATABASE_ISOLATION_MODE: str = field(
        default_factory=lambda: (_env("DATABASE_ISOLATION_MODE", "database") or "").strip()
    )

    # ── Telegram credentials (no defaults – must be set) ──
    BOT_TOKEN: str = field(default_factory=lambda: _env("BOT_TOKEN", ""))
    API_ID: int = field(default_factory=lambda: _env_int("API_ID", 0))
    API_HASH: str = field(default_factory=lambda: _env("API_HASH", ""))
    # Env name DEVELOPER_ID is singular; comma-separated values are allowed.
    # DEVELOPER_ID (int) = primary/first id for legacy diagnostics only.
    # DEVELOPER_IDS = all configured developers; use is_developer() for permissions.
    DEVELOPER_IDS: frozenset[int] = field(
        default_factory=lambda: _load_developer_ids()[0]
    )
    DEVELOPER_ID: int = field(default_factory=lambda: _load_developer_ids()[1])
    HELPER_PHONE: str = field(default_factory=lambda: _env("HELPER_PHONE", ""))

    # ── Database ──
    DATABASE_URL: str = field(
        default_factory=lambda: _env(
            "DATABASE_URL",
            "postgresql+asyncpg://botuser:StrongPassword123@localhost:5432/musicbot_db",
        )
    )
    DB_POOL_SIZE: int = field(default_factory=lambda: _env_int("DB_POOL_SIZE", 20))
    DB_MAX_OVERFLOW: int = field(default_factory=lambda: _env_int("DB_MAX_OVERFLOW", 40))
    DB_POOL_TIMEOUT: int = field(default_factory=lambda: _env_int("DB_POOL_TIMEOUT", 30))
    DB_POOL_RECYCLE: int = field(default_factory=lambda: _env_int("DB_POOL_RECYCLE", 1800))
    DB_ALLOW_CREATE_ALL: bool = field(
        default_factory=lambda: _env_bool("DB_ALLOW_CREATE_ALL", False)
    )

    # ── Redis ──
    REDIS_URL: str = field(
        default_factory=lambda: _env("REDIS_URL", "redis://localhost:6379/0")
    )
    REDIS_SETTINGS_TTL: int = field(
        default_factory=lambda: _env_int("REDIS_SETTINGS_TTL", 300)
    )
    REDIS_CREDIT_TTL: int = field(
        default_factory=lambda: _env_int("REDIS_CREDIT_TTL", 60)
    )

    # ── File paths ──
    DOWNLOADS_PATH: str = field(
        default_factory=lambda: _path_env(
            "DOWNLOADS_PATH", _instance_data_dir() / "downloads"
        )
    )
    SESSION_PATH: str = field(
        default_factory=lambda: _path_env(
            "SESSION_PATH", _instance_data_dir() / "sessions"
        )
    )
    LOG_FILE: str = field(
        default_factory=lambda: _path_env(
            "LOG_FILE", _instance_data_dir() / "logs" / "bot.log"
        )
    )
    TEMP_PATH: str = field(
        default_factory=lambda: _path_env(
            "TEMP_PATH", _instance_data_dir() / "tmp"
        )
    )
    PID_FILE: str = field(
        default_factory=lambda: _path_env(
            "PID_FILE", _runtime_path() / "musicbot.pid"
        )
    )
    LOCK_FILE: str = field(
        default_factory=lambda: _path_env(
            "LOCK_FILE", _runtime_path() / "musicbot.lock"
        )
    )

    # ── Safe bot reload (developer panel; opt-in) ──
    BOT_RELOAD_ENABLED: bool = field(
        default_factory=lambda: _env_bool("BOT_RELOAD_ENABLED", True)
    )
    BOT_RELOAD_SENTINEL_PATH: str = field(
        default_factory=lambda: _env("BOT_RELOAD_SENTINEL_PATH", "")
    )
    BOT_RELOAD_COMMAND: str = field(
        default_factory=lambda: _env("BOT_RELOAD_COMMAND", "")
    )
    BOT_RELOAD_TIMEOUT_SEC: int = field(
        default_factory=lambda: _env_int("BOT_RELOAD_TIMEOUT_SEC", 15)
    )

    # ── Credit & pricing defaults ──
    TRIAL_DAYS: int = field(default_factory=lambda: _env_int("TRIAL_DAYS", 3))
    BASE_CREDIT_RATE: int = field(
        default_factory=lambda: _env_int("BASE_CREDIT_RATE", 50000)
    )
    MUSIC_RATE: int = field(default_factory=lambda: _env_int("MUSIC_RATE", 10000))
    VIDEO_RATE: int = field(default_factory=lambda: _env_int("VIDEO_RATE", 25000))
    SECURITY_CALL_RATE: int = field(
        default_factory=lambda: _env_int("SECURITY_CALL_RATE", 15000)
    )

    # ── Installation limits ──
    MAX_GROUP_MEMBERS: int = field(
        default_factory=lambda: _env_int("MAX_GROUP_MEMBERS", 0)
    )
    MAX_CHANNEL_ADMINS: int = field(
        default_factory=lambda: _env_int("MAX_CHANNEL_ADMINS", 0)
    )

    # ── Default links & texts ──
    LOG_CHANNEL_ID: int = field(
        default_factory=lambda: _env_int("LOG_CHANNEL_ID", 0)
    )
    START_TEXT: str = field(
        default_factory=lambda: _env(
            "START_TEXT",
            "",
        )
    )
    DEVELOPER_LINK: str = field(
        default_factory=lambda: _env("DEVELOPER_LINK", "")
    )
    BOT_CHANNEL_LINK: str = field(
        default_factory=lambda: _env("BOT_CHANNEL_LINK", "")
    )
    SUPPORT_GROUP_LINK: str = field(
        default_factory=lambda: _env("SUPPORT_GROUP_LINK", "")
    )
    GUIDE_CHANNEL_LINK: str = field(
        default_factory=lambda: _env("GUIDE_CHANNEL_LINK", "")
    )

    # ── Media settings ──
    VIDEO_QUALITY: int = field(
        default_factory=lambda: _env_int("VIDEO_QUALITY", 720)
    )
    MAX_DOWNLOAD_SIZE_MB: int = field(
        default_factory=lambda: _env_int("MAX_DOWNLOAD_SIZE_MB", 200)
    )
    DOWNLOAD_SEMAPHORE: int = field(
        default_factory=lambda: _env_int("DOWNLOAD_SEMAPHORE", 10)
    )

    # ── Media cache ──
    MEDIA_CACHE_PATH: str = field(
        default_factory=lambda: _path_env(
            "MEDIA_CACHE_PATH", _instance_data_dir() / "cache"
        )
    )
    MEDIA_CACHE_MAX_GB: int = field(
        default_factory=lambda: _env_int("MEDIA_CACHE_MAX_GB", 10)
    )
    MEDIA_CACHE_TARGET_GB: int = field(
        default_factory=lambda: _env_int("MEDIA_CACHE_TARGET_GB", 8)
    )
    MEDIA_CACHE_MAX_AGE_HOURS: int = field(
        default_factory=lambda: _env_int("MEDIA_CACHE_MAX_AGE_HOURS", 24)
    )
    TRANSCODE_POOL_SIZE: int = field(
        default_factory=lambda: _env_int("TRANSCODE_POOL_SIZE", 4)
    )

    # ── Logging ──
    LOG_LEVEL: str = field(
        default_factory=lambda: _env("LOG_LEVEL", "INFO")
    )

    # ── Helper pool session encryption ──
    HELPER_SESSION_KEY_CURRENT: str = field(
        default_factory=lambda: _env("HELPER_SESSION_KEY_CURRENT", "")
    )
    HELPER_SESSION_KEY_OLD: str = field(
        default_factory=lambda: _env("HELPER_SESSION_KEY_OLD", "")
    )
    # ── YouTube cookie-session encryption ──
    YOUTUBE_COOKIE_KEY_CURRENT: str = field(
        default_factory=lambda: _env("YOUTUBE_COOKIE_KEY_CURRENT", "")
    )
    YOUTUBE_COOKIE_KEY_OLD: str = field(
        default_factory=lambda: _env("YOUTUBE_COOKIE_KEY_OLD", "")
    )
    YOUTUBE_COOKIE_FINGERPRINT_KEY: str = field(
        default_factory=lambda: _env("YOUTUBE_COOKIE_FINGERPRINT_KEY", "")
    )
    # ── Fast-Creat API-token encryption ──
    FAST_CREAT_TOKEN_KEY_CURRENT: str = field(
        default_factory=lambda: _env("FAST_CREAT_TOKEN_KEY_CURRENT", "")
    )
    FAST_CREAT_TOKEN_KEY_OLD: str = field(
        default_factory=lambda: _env("FAST_CREAT_TOKEN_KEY_OLD", "")
    )
    FAST_CREAT_TOKEN_FINGERPRINT_KEY: str = field(
        default_factory=lambda: _env("FAST_CREAT_TOKEN_FINGERPRINT_KEY", "")
    )
    HELPER_DEFAULT_MAX_CALLS: int = field(
        default_factory=lambda: _env_int("HELPER_DEFAULT_MAX_CALLS", 50)
    )
    HELPER_DEFAULT_MAX_JOINS_PER_HOUR: int = field(
        default_factory=lambda: _env_int("HELPER_DEFAULT_MAX_JOINS_PER_HOUR", 300)
    )
    HELPER_JOIN_INVITE_SETTLE_SECONDS: float = field(
        default_factory=lambda: float(_env("HELPER_JOIN_INVITE_SETTLE_SECONDS", "1"))
    )
    GROUP_CALL_CREATE_SETTLE_SECONDS: float = field(
        default_factory=lambda: float(_env("GROUP_CALL_CREATE_SETTLE_SECONDS", "1"))
    )
    VC_JOIN_SETTLE_SECONDS: float = field(
        default_factory=lambda: float(_env("VC_JOIN_SETTLE_SECONDS", "0.5"))
    )

    # ── Recovery rate limits ──
    RECOVERY_GLOBAL_PER_SECOND: int = field(
        default_factory=lambda: _env_int("RECOVERY_GLOBAL_PER_SECOND", 5)
    )
    RECOVERY_MAX_CONCURRENT: int = field(
        default_factory=lambda: _env_int("RECOVERY_MAX_CONCURRENT", 10)
    )
    RECOVERY_JITTER_MS: int = field(
        default_factory=lambda: _env_int("RECOVERY_JITTER_MS", 150)
    )
    RECOVERY_HELPER_PER_MINUTE: int = field(
        default_factory=lambda: _env_int("RECOVERY_HELPER_PER_MINUTE", 120)
    )

    # ── Backups ──
    BACKUP_DIR: str = field(
        default_factory=lambda: _path_env(
            "BACKUP_DIR", _instance_data_dir() / "backups"
        )
    )
    BACKUP_RETENTION_DAYS: int = field(
        default_factory=lambda: _env_int("BACKUP_RETENTION_DAYS", 7)
    )

    # ── Sudo wallet alerts ──
    SUDO_WALLET_LOW_THRESHOLD_DAYS: int = field(
        default_factory=lambda: _env_int("SUDO_WALLET_LOW_THRESHOLD_DAYS", 7)
    )

    # ── Midnight credit deduction (Phase 2D-4B; staging only) ──
    DAILY_DEDUCT_BATCHING_ENABLED: bool = field(
        default_factory=lambda: _env_bool("DAILY_DEDUCT_BATCHING_ENABLED", False)
    )
    DAILY_DEDUCT_BATCH_SIZE: int = field(
        default_factory=lambda: _env_int("DAILY_DEDUCT_BATCH_SIZE", 200)
    )

    # ── Local operational endpoints (0 disables the endpoint) ──
    HEALTH_HOST: str = field(
        default_factory=lambda: (_env("HEALTH_HOST", "127.0.0.1") or "").strip()
    )
    HEALTH_PORT: int = field(default_factory=lambda: _validated_port("HEALTH_PORT"))
    METRICS_HOST: str = field(
        default_factory=lambda: (_env("METRICS_HOST", "127.0.0.1") or "").strip()
    )
    METRICS_PORT: int = field(default_factory=lambda: _validated_port("METRICS_PORT"))

    @property
    def SYSTEMD_IDENTIFIER(self) -> str:
        return f"musicbot-{self.INSTANCE_ID}"

    @property
    def BOT_SESSION_NAME(self) -> str:
        return f"musicbot_{self.INSTANCE_ID.replace('-', '_')}"

    @property
    def SCHEDULER_ID(self) -> str:
        return f"scheduler:{self.INSTANCE_ID}"

    def __post_init__(self) -> None:
        if self.DATABASE_ISOLATION_MODE != "database":
            raise ValueError(
                "Only DATABASE_ISOLATION_MODE=database is supported. "
                "Shared-schema databases are intentionally rejected."
            )
        if self.HEALTH_PORT and self.HEALTH_PORT == self.METRICS_PORT:
            raise ValueError("HEALTH_PORT and METRICS_PORT must be different")
        if (self.BOT_RELOAD_COMMAND or "").strip():
            raise ValueError(
                "BOT_RELOAD_COMMAND is disabled for multi-instance safety. "
                "Use the instance-specific sentinel or musicbotctl restart."
            )
        sentinel = (self.BOT_RELOAD_SENTINEL_PATH or "").strip()
        if sentinel:
            runtime_root = Path(self.RUNTIME_PATH).resolve()
            sentinel_path = Path(sentinel).expanduser().resolve()
            if runtime_root not in sentinel_path.parents:
                raise ValueError(
                    "BOT_RELOAD_SENTINEL_PATH must be located under RUNTIME_PATH"
                )

        directory_paths = {
            Path(self.DOWNLOADS_PATH).resolve(),
            Path(self.SESSION_PATH).resolve(),
            Path(self.MEDIA_CACHE_PATH).resolve(),
            Path(self.TEMP_PATH).resolve(),
            Path(self.BACKUP_DIR).resolve(),
            Path(self.RUNTIME_PATH).resolve(),
            Path(self.LOG_FILE).parent.resolve(),
        }
        if len(directory_paths) != 7:
            raise ValueError(
                "downloads, sessions, cache, temp, backups, runtime, and logs "
                "must use distinct directories"
            )
        for candidate in directory_paths:
            for other in directory_paths:
                if candidate != other and candidate in other.parents:
                    raise ValueError(
                        "instance runtime directories must not contain one another"
                    )
        runtime_root = Path(self.RUNTIME_PATH).resolve()
        for key, value in (("PID_FILE", self.PID_FILE), ("LOCK_FILE", self.LOCK_FILE)):
            resolved = Path(value).resolve()
            if runtime_root not in resolved.parents:
                raise ValueError(f"{key} must be located under RUNTIME_PATH")


settings = Settings()

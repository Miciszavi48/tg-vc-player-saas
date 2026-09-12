from __future__ import annotations

import asyncio
import fcntl
import json
import os
import signal
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import IO

from loguru import logger
from sqlalchemy import text

from app.config.settings import INSTANCE_MARKER_FILENAME, Settings
from app.utils.redis_keys import instance_key

_REDIS_LEASE_TTL_SECONDS = 90
_REDIS_LEASE_RENEW_SECONDS = 30
_RENEW_LEASE_SCRIPT = """
if redis.call("get", KEYS[1]) == ARGV[1] then
    return redis.call("expire", KEYS[1], ARGV[2])
else
    return 0
end
"""
_RELEASE_LEASE_SCRIPT = """
if redis.call("get", KEYS[1]) == ARGV[1] then
    return redis.call("del", KEYS[1])
else
    return 0
end
"""


class InstanceAlreadyRunningError(RuntimeError):
    pass


class InstanceResourceConflictError(RuntimeError):
    pass


class InstanceRuntimeGuard:
    """Own all process-level resources for one configured instance."""

    def __init__(self, config: Settings) -> None:
        self.config = config
        self._lock_handle: IO[str] | None = None
        self._db_connection = None
        self._db_lock_id: int | None = None
        self._redis = None
        self._redis_token = uuid.uuid4().hex
        self._redis_renew_task: asyncio.Task | None = None

    def prepare_directories(self) -> None:
        paths = (
            self.config.INSTANCE_DATA_DIR,
            self.config.DOWNLOADS_PATH,
            self.config.SESSION_PATH,
            self.config.MEDIA_CACHE_PATH,
            self.config.TEMP_PATH,
            self.config.BACKUP_DIR,
            self.config.RUNTIME_PATH,
            str(Path(self.config.LOG_FILE).parent),
        )
        for raw_path in dict.fromkeys(paths):
            path = Path(raw_path)
            path.mkdir(parents=True, exist_ok=True)
            self._claim_directory(path)

    def _claim_directory(self, path: Path) -> None:
        marker = path / INSTANCE_MARKER_FILENAME
        temp = path / f".{INSTANCE_MARKER_FILENAME}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
        try:
            with temp.open("x", encoding="ascii") as handle:
                handle.write(f"{self.config.INSTANCE_ID}\n")
                handle.flush()
                os.fsync(handle.fileno())
            try:
                os.link(temp, marker)
            except FileExistsError:
                pass
        finally:
            temp.unlink(missing_ok=True)

        try:
            owner = marker.read_text(encoding="ascii").strip()
        except (OSError, UnicodeError) as exc:
            raise InstanceResourceConflictError(
                f"Cannot verify instance ownership marker {marker}"
            ) from exc
        if owner != self.config.INSTANCE_ID:
            raise InstanceResourceConflictError(
                f"Directory {path} belongs to instance {owner!r}, "
                f"not {self.config.INSTANCE_ID!r}"
            )

    def acquire_file_lock(self) -> None:
        lock_path = Path(self.config.LOCK_FILE)
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        handle = lock_path.open("a+", encoding="utf-8")
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            handle.seek(0)
            metadata = handle.read(1024).strip()
            handle.close()
            detail = f" ({metadata})" if metadata else ""
            raise InstanceAlreadyRunningError(
                f"Instance {self.config.INSTANCE_ID!r} already owns {lock_path}{detail}"
            ) from exc

        metadata = {
            "instance_id": self.config.INSTANCE_ID,
            "pid": os.getpid(),
            "project_root": self.config.INSTANCE_ROOT,
            "started_at": datetime.now(timezone.utc).isoformat(),
        }
        handle.seek(0)
        handle.truncate()
        handle.write(json.dumps(metadata, sort_keys=True))
        handle.flush()
        os.fsync(handle.fileno())
        self._lock_handle = handle
        self._write_pid_file()

    def _write_pid_file(self) -> None:
        pid_path = Path(self.config.PID_FILE)
        pid_path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = pid_path.with_name(f".{pid_path.name}.{os.getpid()}.tmp")
        temp_path.write_text(f"{os.getpid()}\n", encoding="ascii")
        os.replace(temp_path, pid_path)

    async def acquire_distributed_leases(self) -> None:
        await self._acquire_database_lease()
        try:
            await self._acquire_redis_lease()
        except Exception:
            await self._release_database_lease()
            raise

    async def _acquire_database_lease(self) -> None:
        from app.database.engine import engine
        from app.utils.cache import _lock_key_to_bigint

        if engine.dialect.name != "postgresql":
            return

        lock_id = _lock_key_to_bigint(
            f"musicbot:runtime:{self.config.INSTANCE_ID}"
        )
        connection = await engine.connect()
        try:
            acquired = (
                await connection.execute(
                    text("SELECT pg_try_advisory_lock(:lock_id)"),
                    {"lock_id": lock_id},
                )
            ).scalar()
            await connection.commit()
        except Exception:
            await connection.close()
            raise
        if not acquired:
            await connection.close()
            raise InstanceAlreadyRunningError(
                f"Database lease is already held for {self.config.INSTANCE_ID!r}"
            )
        self._db_connection = connection
        self._db_lock_id = lock_id

    async def _acquire_redis_lease(self) -> None:
        from app.utils.cache import get_redis

        redis = await get_redis()
        key = instance_key("runtime:owner")
        acquired = await redis.set(
            key,
            self._redis_token,
            nx=True,
            ex=_REDIS_LEASE_TTL_SECONDS,
        )
        if not acquired:
            raise InstanceAlreadyRunningError(
                f"Redis lease is already held for {self.config.INSTANCE_ID!r}"
            )
        self._redis = redis
        self._redis_renew_task = asyncio.create_task(
            self._renew_redis_lease(),
            name=f"redis-lease:{self.config.INSTANCE_ID}",
        )

    async def _renew_redis_lease(self) -> None:
        key = instance_key("runtime:owner")
        while True:
            await asyncio.sleep(_REDIS_LEASE_RENEW_SECONDS)
            try:
                renewed = await self._redis.eval(
                    _RENEW_LEASE_SCRIPT,
                    1,
                    key,
                    self._redis_token,
                    _REDIS_LEASE_TTL_SECONDS,
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception(
                    "Redis runtime lease renewal failed instance_id={}",
                    self.config.INSTANCE_ID,
                )
                continue
            if renewed != 1:
                logger.critical(
                    "Redis runtime lease ownership lost instance_id={}",
                    self.config.INSTANCE_ID,
                )
                os.kill(os.getpid(), signal.SIGTERM)
                return

    async def release(self) -> None:
        await self._release_redis_lease()
        await self._release_database_lease()
        self._release_file_lock()

    async def _release_redis_lease(self) -> None:
        if self._redis_renew_task is not None:
            self._redis_renew_task.cancel()
            try:
                await self._redis_renew_task
            except asyncio.CancelledError:
                pass
            self._redis_renew_task = None
        if self._redis is None:
            return
        try:
            await self._redis.eval(
                _RELEASE_LEASE_SCRIPT,
                1,
                instance_key("runtime:owner"),
                self._redis_token,
            )
        except Exception:
            logger.debug("Redis runtime lease release failed", exc_info=True)
        self._redis = None

    async def _release_database_lease(self) -> None:
        if self._db_connection is None:
            return
        try:
            await self._db_connection.execute(
                text("SELECT pg_advisory_unlock(:lock_id)"),
                {"lock_id": self._db_lock_id},
            )
            await self._db_connection.commit()
        except Exception:
            logger.debug("Database runtime lease release failed", exc_info=True)
        finally:
            await self._db_connection.close()
            self._db_connection = None
            self._db_lock_id = None

    def _release_file_lock(self) -> None:
        pid_path = Path(self.config.PID_FILE)
        try:
            if pid_path.read_text(encoding="ascii").strip() == str(os.getpid()):
                pid_path.unlink(missing_ok=True)
        except (FileNotFoundError, OSError, UnicodeError):
            pass

        if self._lock_handle is None:
            return
        try:
            fcntl.flock(self._lock_handle.fileno(), fcntl.LOCK_UN)
        finally:
            self._lock_handle.close()
            self._lock_handle = None

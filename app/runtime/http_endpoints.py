from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field

from app.config.settings import Settings


@dataclass
class OperationalEndpoints:
    config: Settings
    _servers: list[asyncio.AbstractServer] = field(default_factory=list)

    async def start(self) -> None:
        try:
            if self.config.HEALTH_PORT:
                self._servers.append(
                    await asyncio.start_server(
                        self._handle_health,
                        self.config.HEALTH_HOST,
                        self.config.HEALTH_PORT,
                    )
                )
            if self.config.METRICS_PORT:
                self._servers.append(
                    await asyncio.start_server(
                        self._handle_metrics,
                        self.config.METRICS_HOST,
                        self.config.METRICS_PORT,
                    )
                )
        except Exception:
            await self.stop()
            raise

    async def stop(self) -> None:
        servers, self._servers = self._servers, []
        for server in servers:
            server.close()
        if servers:
            await asyncio.gather(
                *(server.wait_closed() for server in servers),
                return_exceptions=True,
            )

    async def _handle_health(
        self,
        _reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        payload = json.dumps(
            {
                "status": "ok",
                "instance_id": self.config.INSTANCE_ID,
            },
            separators=(",", ":"),
        ).encode("utf-8")
        await self._write_response(writer, "application/json", payload)

    async def _handle_metrics(
        self,
        _reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        instance_id = self.config.INSTANCE_ID.replace("\\", "\\\\").replace('"', '\\"')
        payload = (
            "# HELP musicbot_up Whether this bot instance process is running.\n"
            "# TYPE musicbot_up gauge\n"
            f'musicbot_up{{instance_id="{instance_id}"}} 1\n'
        ).encode("utf-8")
        await self._write_response(writer, "text/plain; version=0.0.4", payload)

    @staticmethod
    async def _write_response(
        writer: asyncio.StreamWriter,
        content_type: str,
        payload: bytes,
    ) -> None:
        writer.write(
            b"HTTP/1.1 200 OK\r\n"
            + f"Content-Type: {content_type}\r\n".encode("ascii")
            + f"Content-Length: {len(payload)}\r\n".encode("ascii")
            + b"Connection: close\r\n\r\n"
            + payload
        )
        await writer.drain()
        writer.close()
        await writer.wait_closed()

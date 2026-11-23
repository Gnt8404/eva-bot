"""Minimal asynchronous Minecraft RCON client.

Only the console service is allowed to construct commands; this module merely
speaks the wire protocol.
"""

from __future__ import annotations

import asyncio
import contextlib
import struct


class RconError(RuntimeError):
    pass


class RconAuthError(RconError):
    pass


class RconClient:
    SERVERDATA_AUTH = 3
    SERVERDATA_AUTH_RESPONSE = 2
    SERVERDATA_EXECCOMMAND = 2

    def __init__(
        self,
        host: str,
        port: int,
        password: str,
        *,
        timeout: float = 5.0,
        max_response: int = 1_048_576,
    ) -> None:
        self.host = host
        self.port = port
        self._password = password
        self.timeout = timeout
        self.max_response = max_response

    @staticmethod
    def _packet(request_id: int, packet_type: int, body: str) -> bytes:
        encoded = body.encode("utf-8")
        payload = struct.pack("<ii", request_id, packet_type) + encoded + b"\x00\x00"
        return struct.pack("<i", len(payload)) + payload

    async def _read_packet(self, reader: asyncio.StreamReader) -> tuple[int, int, str]:
        raw_length = await reader.readexactly(4)
        (length,) = struct.unpack("<i", raw_length)
        if length < 10 or length > self.max_response:
            raise RconError("Invalid RCON packet length")
        payload = await reader.readexactly(length)
        request_id, packet_type = struct.unpack("<ii", payload[:8])
        body = payload[8:-2].decode("utf-8", errors="replace")
        return request_id, packet_type, body

    async def command(self, command: str) -> str:
        if not self.host or not self._password:
            raise RconAuthError("RCON is not configured")
        if "\x00" in command or "\n" in command or "\r" in command:
            raise RconError("Command contains forbidden control characters")
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(self.host, self.port), timeout=self.timeout
            )
        except (OSError, TimeoutError) as exc:
            raise RconError("RCON connection failed") from exc
        try:
            writer.write(self._packet(1, self.SERVERDATA_AUTH, self._password))
            await writer.drain()
            auth_id, auth_type, _ = await asyncio.wait_for(
                self._read_packet(reader), timeout=self.timeout
            )
            if auth_id == -1 or (auth_type == 0 and auth_id != 1):
                raise RconAuthError("RCON authentication failed")
            writer.write(self._packet(2, self.SERVERDATA_EXECCOMMAND, command))
            await writer.drain()
            request_id, _, body = await asyncio.wait_for(
                self._read_packet(reader), timeout=self.timeout
            )
            if request_id not in (2, -1):
                raise RconError("Unexpected RCON response id")
            return body.strip()
        except TimeoutError as exc:
            raise RconError("RCON response timeout") from exc
        finally:
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()

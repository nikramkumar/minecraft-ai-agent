"""
A minimal client for the "Source RCON" protocol, which Minecraft (including
Paper) implements for remote console access.

IMPORTANT SECURITY NOTE
------------------------
This client is NOT exposed to the agent or the API directly. Nothing in this
codebase lets a caller send an arbitrary string chosen by the LLM or an end
user through RCON. Instead, `MinecraftMonitor` (in minecraft_monitor.py) is
the only caller of `RconClient.command()`, and it only ever passes a small,
fixed set of hardcoded strings ("tps", "plugins", "list") that correspond to
specific, narrowly-scoped monitoring tools. This preserves the project's
core security requirement: no arbitrary command execution, even indirectly.

Protocol reference: https://developer.valvesoftware.com/wiki/Source_RCON_Protocol
This is a small, well-documented binary protocol; implementing it directly
avoids pulling in a third-party dependency for ~60 lines of socket code.
"""

from __future__ import annotations

import socket
import struct
from types import TracebackType

SERVERDATA_AUTH = 3
SERVERDATA_AUTH_RESPONSE = 2
SERVERDATA_EXECCOMMAND = 2
SERVERDATA_RESPONSE_VALUE = 0


class RconError(Exception):
    """Base class for RCON connection/protocol errors."""


class RconAuthenticationError(RconError):
    """Raised when the RCON password is rejected."""


class RconConnectionError(RconError):
    """Raised when the RCON socket cannot be opened (server offline, wrong port, etc.)."""


class RconClient:
    """
    A synchronous Source RCON client, scoped to a single request/response
    per `command()` call. Intended to be used as a context manager so the
    socket is always closed:

        with RconClient(host, port, password) as rcon:
            response = rcon.command("tps")
    """

    def __init__(self, host: str, port: int, password: str, timeout: float = 3.0) -> None:
        self._host = host
        self._port = port
        self._password = password
        self._timeout = timeout
        self._sock: socket.socket | None = None
        self._request_id = 0

    def __enter__(self) -> "RconClient":
        self.connect()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        self.close()

    def connect(self) -> None:
        try:
            self._sock = socket.create_connection((self._host, self._port), timeout=self._timeout)
        except OSError as exc:
            raise RconConnectionError(
                f"Could not connect to RCON at {self._host}:{self._port}: {exc}"
            ) from exc

        self._send_packet(SERVERDATA_AUTH, self._password)
        packet_type, _ = self._read_packet()
        if packet_type != SERVERDATA_AUTH_RESPONSE:
            self.close()
            raise RconAuthenticationError("RCON authentication failed: unexpected response type")

    def command(self, cmd: str) -> str:
        """Send one fixed console command and return its text response."""
        if self._sock is None:
            raise RconConnectionError("RconClient.command() called before connect()")
        self._send_packet(SERVERDATA_EXECCOMMAND, cmd)
        _, body = self._read_packet()
        return body

    def close(self) -> None:
        if self._sock is not None:
            self._sock.close()
            self._sock = None

    # -- protocol internals -------------------------------------------------

    def _send_packet(self, packet_type: int, body: str) -> None:
        assert self._sock is not None
        self._request_id += 1
        payload = struct.pack("<ii", self._request_id, packet_type) + body.encode("utf-8") + b"\x00\x00"
        packet = struct.pack("<i", len(payload)) + payload
        self._sock.sendall(packet)

    def _read_packet(self) -> tuple[int, str]:
        assert self._sock is not None
        length_bytes = self._recv_exact(4)
        (length,) = struct.unpack("<i", length_bytes)
        payload = self._recv_exact(length)
        _request_id, packet_type = struct.unpack("<ii", payload[:8])
        body = payload[8:-2].decode("utf-8", errors="replace")
        return packet_type, body

    def _recv_exact(self, num_bytes: int) -> bytes:
        assert self._sock is not None
        chunks = bytearray()
        while len(chunks) < num_bytes:
            chunk = self._sock.recv(num_bytes - len(chunks))
            if not chunk:
                raise RconConnectionError("RCON connection closed unexpectedly")
            chunks.extend(chunk)
        return bytes(chunks)

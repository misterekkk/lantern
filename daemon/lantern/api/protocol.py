"""Daemon API: newline-delimited JSON over a user-only Unix socket.

Request:  {"id": 1, "method": "search", "params": {"query": "...", "limit": 10}}
Response: {"id": 1, "result": {...}}  or  {"id": 1, "error": {"type": "...", "message": "..."}}
One request per connection keeps clients trivial (CLI, Swift Network.framework).
"""

import json
import os
import socket
from pathlib import Path

from lantern.config import lantern_home

MAX_MESSAGE = 16 * 1024 * 1024


class DaemonNotRunning(ConnectionError):
    pass


class DaemonError(RuntimeError):
    def __init__(self, type_: str, message: str):
        super().__init__(message)
        self.type = type_


def socket_path() -> Path:
    override = os.environ.get("LANTERN_SOCKET")
    return Path(override) if override else lantern_home() / "lantern.sock"


def read_message(sock: socket.socket) -> dict:
    buf = bytearray()
    while not buf.endswith(b"\n"):
        chunk = sock.recv(65536)
        if not chunk:
            break
        buf += chunk
        if len(buf) > MAX_MESSAGE:
            raise ValueError("message too large")
    if not buf:
        raise ConnectionError("connection closed")
    return json.loads(buf)


def write_message(sock: socket.socket, message: dict) -> None:
    sock.sendall(json.dumps(message, ensure_ascii=False).encode() + b"\n")


def call(method: str, params: dict | None = None, timeout: float = 30.0) -> dict:
    path = socket_path()
    if not path.exists():
        raise DaemonNotRunning("daemon is not running")
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        sock.settimeout(timeout)
        try:
            sock.connect(str(path))
        except (ConnectionRefusedError, FileNotFoundError) as exc:
            raise DaemonNotRunning("daemon is not running") from exc
        write_message(sock, {"id": 1, "method": method, "params": params or {}})
        reply = read_message(sock)
    if "error" in reply:
        raise DaemonError(reply["error"].get("type", "Error"), reply["error"].get("message", ""))
    return reply.get("result", {})


def running() -> bool:
    try:
        call("ping", timeout=2)
        return True
    except (DaemonNotRunning, OSError, DaemonError):
        return False

"""Resident recall service: one warm Hub connection shared by every prompt hook on this machine.

The prompt hook is a short-lived process with a budget of about a second. On a
long network path the TCP and TLS handshakes alone eat most of that, so the
background worker keeps a connection open and answers hooks over a Unix socket
in the state directory. The hook still works without it: no socket means the
old killable subprocess path.

Only the query, the asking-side hints and a limit cross the socket; the device
credential never leaves the worker. The socket lives in the 0700 state
directory and is itself 0600.
"""

from __future__ import annotations

import json
import os
import socket
import threading
import time
from pathlib import Path
from typing import Any
from .common import recall_budget
from .query_worker import fetch
from .transport import HubHTTPError, WarmTransport, validate_url

SOCKET_NAME = "recall.sock"
PING_INTERVAL = 25.0  # below nginx's default 75s idle timeout
IDLE_LIMIT = 30 * 60  # stop keeping the connection warm when nobody has asked for this long
MAX_REQUEST_BYTES = 64_000
MAX_REPLY_BYTES = 4_000_000  # recall bodies are capped at 512KB on the Hub side
QUERY_CHARS = 8000


class ServiceUnavailable(OSError):
    """No resident service is listening in this state directory."""


def _read_line(sock: socket.socket, limit: int) -> Any:
    buffer = bytearray()
    while b"\n" not in buffer:
        chunk = sock.recv(65536)
        if not chunk:
            raise ValueError("connection closed before a complete message")
        buffer.extend(chunk)
        if len(buffer) > limit:
            raise ValueError("message too large")
    return json.loads(bytes(buffer[: buffer.index(b"\n")]))


def request(state: Path, payload: dict[str, Any], *, timeout: float) -> dict[str, Any]:
    """Ask the resident service for one recall; raises the same classes as bounded_search."""
    path = state / SOCKET_NAME
    if not path.exists():
        raise ServiceUnavailable("no resident recall service")
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        try:
            sock.connect(str(path))
        except OSError:
            # Nothing listening, a stale file, or a path the platform cannot
            # bind: every one of these means "no service", never an outage.
            raise ServiceUnavailable("resident recall service not listening") from None
        sock.sendall(json.dumps(payload, ensure_ascii=False).encode() + b"\n")
        reply = _read_line(sock, MAX_REPLY_BYTES)
    except socket.timeout:
        raise TimeoutError("cloud recall deadline exceeded") from None
    finally:
        sock.close()
    if not isinstance(reply, dict) or "ok" not in reply:
        raise ValueError("invalid service reply")
    if reply["ok"]:
        result = reply.get("result")
        if not isinstance(result, dict) or not isinstance(result.get("items"), list):
            raise ValueError("invalid recall response")
        return result
    error = reply.get("error")
    if error == "denied":
        raise PermissionError("device rejected")
    if error == "timeout":
        raise TimeoutError("cloud recall deadline exceeded")
    if error == "rejected":
        status = reply.get("status")
        raise HubHTTPError(status if isinstance(status, int) else 400)
    if error == "invalid":
        raise ValueError("invalid recall request")
    raise OSError("cloud recall unavailable")


class RecallService:
    """Serve recalls over a Unix socket from one shared warm transport."""

    def __init__(
        self,
        state: Path,
        *,
        ping_interval: float = PING_INTERVAL,
        idle_limit: float = IDLE_LIMIT,
    ) -> None:
        self.state = Path(state)
        self.path = self.state / SOCKET_NAME
        self.ping_interval = ping_interval
        self.idle_limit = idle_limit
        self.lock = threading.Lock()
        self.transport: WarmTransport | None = None
        self.stop = threading.Event()
        self.server: socket.socket | None = None
        self.last_use = 0.0  # last hook request (monotonic)
        self.last_touch = 0.0  # last request or keep-alive ping
        self.served = 0
        self.pings = 0

    def configure(self, config: Any) -> None:
        """Adopt the Hub, device token and budget from config.json; a change drops the connection."""
        hub = config.get("hub") if isinstance(config, dict) else None
        identity = config.get("identity") if isinstance(config, dict) else None
        token = identity.get("token") if isinstance(identity, dict) else None
        budget = recall_budget(config)
        with self.lock:
            if not isinstance(hub, str) or not isinstance(token, str) or not token:
                if self.transport is not None:
                    self.transport.close()
                self.transport = None
                return
            try:
                url = validate_url(hub)
            except ValueError:
                self.transport = None
                return
            if self.transport is not None and (self.transport.url, self.transport.token) == (
                url,
                token,
            ):
                self.transport.timeout = budget
                return
            if self.transport is not None:
                self.transport.close()
            self.transport = WarmTransport(url, token, timeout=budget)

    def start(self) -> None:
        # Whoever holds the daemon lock owns the state directory; a socket left
        # behind by a previous worker is dead and refuses connections anyway.
        self.path.unlink(missing_ok=True)
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        previous_umask = os.umask(0o077)
        try:
            server.bind(str(self.path))
        finally:
            os.umask(previous_umask)
        os.chmod(self.path, 0o600)
        server.listen(8)
        server.settimeout(0.5)
        self.server = server
        threading.Thread(target=self._accept_loop, name="amh-recall-service", daemon=True).start()
        threading.Thread(target=self._ping_loop, name="amh-recall-keepalive", daemon=True).start()

    def close(self) -> None:
        self.stop.set()
        if self.server is not None:
            try:
                self.server.close()
            except OSError:
                pass
            self.server = None
        self.path.unlink(missing_ok=True)
        with self.lock:
            if self.transport is not None:
                self.transport.close()

    def _accept_loop(self) -> None:
        while not self.stop.is_set() and self.server is not None:
            try:
                conn, _ = self.server.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            threading.Thread(target=self._serve, args=(conn,), daemon=True).start()

    def _serve(self, conn: socket.socket) -> None:
        with conn:
            try:
                conn.settimeout(2.0)
                message = _read_line(conn, MAX_REQUEST_BYTES)
                reply = self.answer(message)
            except Exception:
                reply = {"ok": False, "error": "invalid"}
            try:
                conn.sendall(json.dumps(reply, ensure_ascii=False).encode() + b"\n")
            except OSError:
                pass

    def answer(self, message: Any) -> dict[str, Any]:
        """One recall on the shared connection; outcomes are classified, never raised."""
        if not isinstance(message, dict) or not isinstance(message.get("query"), str):
            return {"ok": False, "error": "invalid"}
        query = message["query"][:QUERY_CHARS]
        asking = message.get("asking")
        limit = message.get("limit", 8)
        timeout = message.get("timeout")
        with self.lock:
            transport = self.transport
            if transport is None:
                return {"ok": False, "error": "unavailable"}
            previous = transport.timeout
            if isinstance(timeout, (int, float)) and 0 < timeout <= 60:
                transport.timeout = float(timeout)
            try:
                result = fetch(
                    transport,
                    query,
                    limit=limit if isinstance(limit, int) and 1 <= limit <= 8 else 8,
                    asking=asking if isinstance(asking, dict) else None,
                )
                self.served += 1
                return {"ok": True, "result": result}
            except PermissionError:
                return {"ok": False, "error": "denied"}
            except TimeoutError:
                return {"ok": False, "error": "timeout"}
            except HubHTTPError as exc:
                return {"ok": False, "error": "rejected", "status": exc.http_status}
            except (OSError, ValueError):
                transport.close()
                return {"ok": False, "error": "unavailable"}
            finally:
                transport.timeout = previous
                self.last_use = self.last_touch = time.monotonic()

    def _ping_loop(self) -> None:
        """Keep the connection warm while hooks are using it; let it go when they stop."""
        while not self.stop.wait(1.0):
            now = time.monotonic()
            if self.last_use == 0.0 or now - self.last_touch < self.ping_interval:
                continue
            with self.lock:
                transport = self.transport
                if transport is None:
                    continue
                if now - self.last_use > self.idle_limit:
                    transport.close()
                    self.last_touch = now
                    continue
                try:
                    transport.call("health")
                    self.pings += 1
                except Exception:
                    transport.close()
                self.last_touch = time.monotonic()

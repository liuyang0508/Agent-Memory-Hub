"""Bounded HTTPS transport. Never forwards device credentials through redirects."""

from __future__ import annotations
from typing import Any

import json
import time
from urllib import request, error, parse
import http.client
import ipaddress
import socket
import ssl


SAFE_ERROR_CODES = frozenset(
    {
        "stale_version",
        "incomplete_version",
        "asset_unavailable",
        "invalid_version",
        "invalid_cursor",
        "document_too_large",
        "unsupported_encoding",
    }
)


class HubHTTPError(ValueError):
    """Finite public errors; never include response bodies, URLs or credentials."""

    def __init__(self, http_status: int, code: str = "request_rejected") -> None:
        self.http_status = http_status
        self.code = code if code in SAFE_ERROR_CODES else "request_rejected"
        super().__init__(f"Hub HTTP {http_status}: {self.code}")


class NoRedirect(request.HTTPRedirectHandler):
    """Reject redirect-based host changes so a device token never follows an unexpected destination."""

    def redirect_request(
        self, req: Any, fp: Any, code: Any, msg: Any, headers: Any, newurl: Any
    ) -> Any:
        raise ValueError("Hub redirects are not supported; configure the final URL")


def validate_url(url: str) -> str:
    """Require a credential-free HTTPS endpoint; allow HTTP only for local loopback development."""
    p = parse.urlsplit(url)
    if p.username or p.password or p.query or p.fragment or not p.hostname:
        raise ValueError("Hub URL must contain only scheme, host, port and base path")
    local = p.hostname == "localhost"
    try:
        local = local or ipaddress.ip_address(p.hostname).is_loopback
    except ValueError:
        pass
    if p.scheme != "https" and not (local and p.scheme == "http"):
        raise ValueError("HTTPS required except for local loopback tests")
    return url.rstrip("/")


def _inflate(raw: bytearray, content_encoding: str) -> bytearray:
    """Decode a gzip body under the same cap as the wire bytes; never a gzip bomb."""
    if content_encoding.lower() != "gzip":
        return raw
    import gzip
    import io

    with gzip.GzipFile(fileobj=io.BytesIO(raw)) as compressed:
        decoded = compressed.read(8_000_001)
    if len(decoded) > 8_000_000:
        raise ValueError("Hub decoded response too large")
    return bytearray(decoded)


class Transport:
    """HTTPS implementation of SyncTransport; retries belong to the worker only."""

    def __init__(self, url: str, token: str = "", timeout: float = 10) -> None:
        self.url, self.token, self.timeout = validate_url(url), token, timeout
        self.opener = request.build_opener(NoRedirect())

    def call(self, path: str, data: Any = None) -> Any:
        """Issue one bounded request; classify auth, transient and record errors for the owning retry layer."""
        headers = {"Content-Type": "application/json", "Accept-Encoding": "gzip"}
        if self.token:
            headers["Authorization"] = "Bearer " + self.token
        req = request.Request(
            self.url + "/native/v1/" + path,
            data=None if data is None else json.dumps(data).encode(),
            headers=headers,
        )
        try:
            deadline = time.monotonic() + self.timeout
            with self.opener.open(req, timeout=self.timeout) as response:
                raw = bytearray()
                while True:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise TimeoutError("Hub response deadline exceeded")
                    socket_object = getattr(
                        getattr(getattr(response, "fp", None), "raw", None), "_sock", None
                    )
                    if socket_object is not None:
                        socket_object.settimeout(remaining)
                    chunk = response.read1(65536)
                    if not chunk:
                        break
                    raw.extend(chunk)
                    if len(raw) > 8_000_000:
                        raise ValueError("Hub response too large")
                raw = _inflate(raw, response.headers.get("Content-Encoding", ""))
                if time.monotonic() >= deadline:
                    raise TimeoutError("Hub response deadline exceeded")
                return json.loads(raw)
        except error.HTTPError as exc:
            if exc.code in (401, 403):
                exc.close()
                raise PermissionError("organization access rejected") from None
            if exc.code in (408, 429) or 500 <= exc.code <= 599:
                exc.close()
                raise OSError(f"Hub temporarily unavailable: HTTP {exc.code}") from None
            code = "request_rejected"
            try:
                # Parse only a small finite error envelope, never arbitrary detail.
                body = json.loads(exc.read(4096))
                if isinstance(body, dict) and body.get("code") in SAFE_ERROR_CODES:
                    code = body["code"]
            except (ValueError, TypeError, OSError):
                pass
            finally:
                exc.close()
            raise HubHTTPError(exc.code, code) from None

    def enroll(self, ticket: str, device_name: str) -> Any:
        return self.call("enroll", {"ticket": ticket, "device_name": device_name})

    def me(self) -> Any:
        return self.call("me")

    def capabilities(self) -> list[str]:
        """Unauthenticated protocol discovery; a malformed answer means no capabilities."""
        value = self.call("health")
        found = value.get("capabilities") if isinstance(value, dict) else None
        return [c for c in found if isinstance(c, str)] if isinstance(found, list) else []

    def put(self, value: Any) -> Any:
        return self.call("assets", value)

    def changes(self, cursor: int, *, metadata_only: bool = False) -> Any:
        return self.call(
            f"changes?cursor={cursor}&limit=10&metadata_only={str(metadata_only).lower()}"
        )

    def search(self, query: str, **kwargs: Any) -> Any:
        return self.call("search", {"query": query, **kwargs})["items"]

    def recall(self, query: str, *, limit: int = 8, asking: dict[str, Any] | None = None) -> Any:
        """Hook-channel hybrid recall; an old Hub answers 404 and the caller falls back."""
        data: dict[str, Any] = {"query": query, "limit": limit}
        if asking:
            data["asking"] = asking
        return self.call("recall", data)

    def recall_feedback(self, trace_id: str, events: list[dict[str, Any]]) -> Any:
        return self.call("recall/feedback", {"trace_id": trace_id, "events": events})

    def recalls(self, *, trace_id: str | None = None, limit: int = 20) -> Any:
        query = "recalls?limit=%d" % limit + (
            "&trace_id=" + parse.quote(trace_id, safe="") if trace_id else ""
        )
        return self.call(query)["traces"]


class WarmTransport(Transport):
    """A Transport that keeps one connection open between calls.

    A fresh HTTPS request pays a TCP and a TLS handshake before the first
    byte: three round trips, which on a long path can exceed a whole hook
    budget. A resident process pays that once and reuses the socket, so a
    recall costs one round trip. Semantics match ``Transport.call``: the same
    error classes, the same byte caps, no redirects. Callers serialise access;
    the object is not thread-safe.
    """

    def __init__(self, url: str, token: str = "", timeout: float = 10) -> None:
        super().__init__(url, token, timeout)
        parts = parse.urlsplit(self.url)
        self._host = parts.hostname or ""
        self._port = parts.port
        self._secure = parts.scheme == "https"
        self._prefix = parts.path.rstrip("/")
        self._conn: http.client.HTTPConnection | None = None
        self.connects = 0

    def close(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            except OSError:
                pass
            self._conn = None

    def connected(self) -> bool:
        return self._conn is not None and self._conn.sock is not None

    def _connection(self) -> http.client.HTTPConnection:
        if self._conn is None:
            if self._secure:
                self._conn = http.client.HTTPSConnection(
                    self._host,
                    self._port,
                    timeout=self.timeout,
                    context=ssl.create_default_context(),
                )
            else:
                self._conn = http.client.HTTPConnection(
                    self._host, self._port, timeout=self.timeout
                )
            self.connects += 1
        return self._conn

    def call(self, path: str, data: Any = None) -> Any:
        headers = {"Content-Type": "application/json", "Accept-Encoding": "gzip"}
        if self.token:
            headers["Authorization"] = "Bearer " + self.token
        body = None if data is None else json.dumps(data).encode()
        method = "GET" if data is None else "POST"
        target = self._prefix + "/native/v1/" + path
        reused = self.connected()
        try:
            return self._exchange(method, target, body, headers)
        except TimeoutError:
            raise
        except (OSError, http.client.HTTPException):
            if not reused:
                raise
            # The Hub or a proxy closed the idle connection underneath us: one
            # fresh attempt, exactly what a cold Transport would have made.
            self.close()
            return self._exchange(method, target, body, headers)

    def _exchange(
        self, method: str, target: str, body: bytes | None, headers: dict[str, str]
    ) -> Any:
        conn = self._connection()
        conn.timeout = self.timeout
        deadline = time.monotonic() + self.timeout
        try:
            if conn.sock is not None:
                conn.sock.settimeout(self.timeout)
            conn.request(method, target, body=body, headers=headers)
            response = conn.getresponse()
            raw = bytearray()
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("Hub response deadline exceeded")
                if conn.sock is not None:
                    conn.sock.settimeout(remaining)
                chunk = response.read1(65536)
                if not chunk:
                    break
                raw.extend(chunk)
                if len(raw) > 8_000_000:
                    raise ValueError("Hub response too large")
            raw = _inflate(raw, response.getheader("Content-Encoding", "") or "")
            if time.monotonic() >= deadline:
                raise TimeoutError("Hub response deadline exceeded")
            if response.will_close:
                self.close()
        except (TimeoutError, socket.timeout):
            self.close()
            raise TimeoutError("Hub response deadline exceeded") from None
        except (OSError, http.client.HTTPException, ValueError):
            self.close()
            raise
        status = response.status
        if 200 <= status < 300:
            return json.loads(raw)
        if status in (401, 403):
            raise PermissionError("organization access rejected")
        if status in (408, 429) or 500 <= status <= 599:
            raise OSError(f"Hub temporarily unavailable: HTTP {status}")
        if 300 <= status < 400:
            raise ValueError("Hub redirects are not supported; configure the final URL")
        code = "request_rejected"
        try:
            # Parse only a small finite error envelope, never arbitrary detail.
            envelope = json.loads(bytes(raw[:4096]))
            if isinstance(envelope, dict) and envelope.get("code") in SAFE_ERROR_CODES:
                code = envelope["code"]
        except (ValueError, TypeError):
            pass
        raise HubHTTPError(status, code)

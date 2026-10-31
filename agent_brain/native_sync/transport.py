"""Bounded HTTPS transport. Never forwards device credentials through redirects."""

from __future__ import annotations
from typing import Any

import json
import time
from urllib import request, error, parse
import ipaddress


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


class Transport:
    """HTTPS implementation of SyncTransport; retries belong to the worker only."""

    def __init__(self, url: str, token: str = "", timeout: float = 10) -> None:
        self.url, self.token, self.timeout = validate_url(url), token, timeout
        self.opener = request.build_opener(NoRedirect())

    def call(self, path: str, data: Any = None) -> Any:
        """Issue one bounded request; classify auth, transient and record errors for the owning retry layer."""
        headers = {"Content-Type": "application/json"}
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
                return json.loads(raw)
        except error.HTTPError as exc:
            if exc.code in (401, 403):
                raise PermissionError("organization access rejected") from None
            if exc.code in (408, 429) or 500 <= exc.code <= 599:
                raise OSError(f"Hub temporarily unavailable: HTTP {exc.code}") from None
            raise ValueError(f"Hub HTTP {exc.code}") from None

    def enroll(self, ticket: str, device_name: str) -> Any:
        return self.call("enroll", {"ticket": ticket, "device_name": device_name})

    def me(self) -> Any:
        return self.call("me")

    def put(self, value: Any) -> Any:
        return self.call("assets", value)

    def changes(self, cursor: int) -> Any:
        return self.call(f"changes?cursor={cursor}&limit=10")

    def search(self, query: str, **kwargs: Any) -> Any:
        return self.call("search", {"query": query, **kwargs})["items"]

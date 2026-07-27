#!/usr/bin/env python3
"""Small stdlib telemetry API for the public Agent Memory Hub adoption dashboard."""

from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import os
import re
import sqlite3
import threading
import time
from collections import Counter, defaultdict, deque
from datetime import datetime, timedelta, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


EVENT_PATH = "/api/v1/telemetry/event"
SUMMARY_PATH = "/api/v1/telemetry/summary"
HEALTH_PATH = "/healthz"
_ANONYMOUS_ID = re.compile(r"^[0-9a-f]{32}$")
_SAFE_VALUE = re.compile(r"^[a-z0-9_.+-]{1,64}$")
_EVENTS = {"install", "active"}
_MAX_BODY = 4096


class TelemetryStore:
    def __init__(self, database: Path):
        self.database = database
        self.database.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self._initialize()
        self.database.chmod(0o600)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database, timeout=5)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=5000")
        connection.execute("PRAGMA journal_mode=WAL")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS instances (
                    anonymous_id TEXT PRIMARY KEY,
                    first_seen INTEGER NOT NULL,
                    last_seen INTEGER NOT NULL,
                    last_install INTEGER,
                    last_active INTEGER,
                    platform TEXT NOT NULL,
                    architecture TEXT NOT NULL,
                    product_version TEXT NOT NULL,
                    channel TEXT NOT NULL,
                    adapter TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS daily_activity (
                    day TEXT NOT NULL,
                    anonymous_id TEXT NOT NULL,
                    event TEXT NOT NULL,
                    PRIMARY KEY(day, anonymous_id, event)
                );
                CREATE INDEX IF NOT EXISTS idx_instances_last_active
                    ON instances(last_active);
                CREATE INDEX IF NOT EXISTS idx_instances_first_seen
                    ON instances(first_seen);
                """
            )

    def record(self, payload: dict[str, Any], *, now: int | None = None) -> None:
        timestamp = int(time.time() if now is None else now)
        event = payload["event"]
        install_time = timestamp if event == "install" else None
        active_time = timestamp if event == "active" else None
        day = datetime.fromtimestamp(timestamp, timezone.utc).date().isoformat()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO instances (
                    anonymous_id, first_seen, last_seen, last_install, last_active,
                    platform, architecture, product_version, channel, adapter
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(anonymous_id) DO UPDATE SET
                    last_seen = excluded.last_seen,
                    last_install = COALESCE(excluded.last_install, instances.last_install),
                    last_active = COALESCE(excluded.last_active, instances.last_active),
                    platform = excluded.platform,
                    architecture = excluded.architecture,
                    product_version = excluded.product_version,
                    channel = CASE
                        WHEN excluded.channel = 'unknown' THEN instances.channel
                        ELSE excluded.channel
                    END,
                    adapter = CASE
                        WHEN excluded.adapter = 'unknown' THEN instances.adapter
                        ELSE excluded.adapter
                    END
                """,
                (
                    payload["anonymous_id"],
                    timestamp,
                    timestamp,
                    install_time,
                    active_time,
                    payload["platform"],
                    payload["architecture"],
                    payload["product_version"],
                    payload["channel"],
                    payload["adapter"],
                ),
            )
            connection.execute(
                "INSERT OR IGNORE INTO daily_activity(day, anonymous_id, event) VALUES (?, ?, ?)",
                (day, payload["anonymous_id"], event),
            )
            cutoff = (datetime.now(timezone.utc).date() - timedelta(days=180)).isoformat()
            connection.execute("DELETE FROM daily_activity WHERE day < ?", (cutoff,))

    def summary(self, *, now: int | None = None) -> dict[str, Any]:
        timestamp = int(time.time() if now is None else now)
        with self._connect() as connection:
            # ponytail: a full instance scan keeps the early service tiny; add
            # daily rollups when the table reaches roughly 100k rows.
            rows = list(connection.execute("SELECT * FROM instances"))
            activity_rows = list(
                connection.execute(
                    "SELECT day, event, COUNT(*) AS total FROM daily_activity "
                    "WHERE day >= ? GROUP BY day, event",
                    (
                        (
                            datetime.fromtimestamp(timestamp, timezone.utc).date()
                            - timedelta(days=13)
                        ).isoformat(),
                    ),
                )
            )

        active_24h = sum(
            1 for row in rows if row["last_active"] and row["last_active"] >= timestamp - 86_400
        )
        active_7d = sum(
            1 for row in rows if row["last_active"] and row["last_active"] >= timestamp - 604_800
        )
        installed_24h = sum(
            1 for row in rows if row["last_install"] and row["last_install"] >= timestamp - 86_400
        )
        grouped: dict[str, Counter[str]] = {
            key: Counter(str(row[key]) for row in rows)
            for key in ("platform", "product_version", "channel", "adapter")
        }
        activity_lookup: dict[tuple[str, str], int] = {
            (row["day"], row["event"]): int(row["total"]) for row in activity_rows
        }
        today = datetime.fromtimestamp(timestamp, timezone.utc).date()
        trend = []
        for offset in range(13, -1, -1):
            day = (today - timedelta(days=offset)).isoformat()
            trend.append(
                {
                    "day": day,
                    "installs": activity_lookup.get((day, "install"), 0),
                    "active": activity_lookup.get((day, "active"), 0),
                }
            )
        recent = sorted(rows, key=lambda row: row["last_seen"], reverse=True)[:20]
        return {
            "schema_version": 1,
            "generated_at": datetime.fromtimestamp(timestamp, timezone.utc).isoformat(),
            "total_instances": len(rows),
            "installed_24h": installed_24h,
            "active_24h": active_24h,
            "active_7d": active_7d,
            "trend": trend,
            "breakdowns": {
                key: [
                    {"name": name, "count": count}
                    for name, count in counter.most_common(8)
                    if name != "unknown"
                ]
                for key, counter in grouped.items()
            },
            "recent": [
                {
                    "id": "anon-" + hashlib.sha256(row["anonymous_id"].encode()).hexdigest()[:8],
                    "first_seen": _iso_timestamp(row["first_seen"]),
                    "last_active": _iso_timestamp(row["last_active"]),
                    "platform": row["platform"],
                    "product_version": row["product_version"],
                    "channel": row["channel"],
                    "adapter": row["adapter"],
                }
                for row in recent
            ],
            "privacy": {
                "opt_in_only": True,
                "stores_ip": False,
                "stores_content": False,
            },
        }


def validate_payload(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("payload must be an object")
    if value.get("schema_version") != 1:
        raise ValueError("unsupported schema_version")
    anonymous_id = value.get("anonymous_id")
    event = value.get("event")
    if not isinstance(anonymous_id, str) or not _ANONYMOUS_ID.fullmatch(anonymous_id):
        raise ValueError("invalid anonymous_id")
    if event not in _EVENTS:
        raise ValueError("invalid event")
    allowed = {"schema_version", "anonymous_id", "event", "product_version", "platform",
               "architecture", "channel", "adapter"}
    if set(value) - allowed:
        raise ValueError("unexpected fields")
    payload = {"schema_version": 1, "anonymous_id": anonymous_id, "event": event}
    for key in ("product_version", "platform", "architecture", "channel", "adapter"):
        item = value.get(key, "unknown")
        if not isinstance(item, str) or not _SAFE_VALUE.fullmatch(item):
            raise ValueError(f"invalid {key}")
        payload[key] = item
    return payload


class MinuteLimiter:
    def __init__(self, limit: int = 30):
        self.limit = limit
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, address: str, *, now: float | None = None) -> bool:
        timestamp = time.monotonic() if now is None else now
        with self._lock:
            hits = self._hits[address]
            while hits and timestamp - hits[0] >= 60:
                hits.popleft()
            if len(hits) >= self.limit:
                return False
            hits.append(timestamp)
            if len(self._hits) > 10_000:
                self._hits = defaultdict(
                    deque,
                    {
                        key: value
                        for key, value in self._hits.items()
                        if value and timestamp - value[-1] < 60
                    },
                )
            return True


class TelemetryHTTPServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], store: TelemetryStore, limit: int):
        super().__init__(address, TelemetryHandler)
        self.store = store
        self.limiter = MinuteLimiter(limit)


class TelemetryHandler(BaseHTTPRequestHandler):
    server: TelemetryHTTPServer
    protocol_version = "HTTP/1.1"

    def do_GET(self) -> None:  # noqa: N802
        if self.path == SUMMARY_PATH:
            self._json(HTTPStatus.OK, self.server.store.summary())
            return
        if self.path == HEALTH_PATH:
            self._json(HTTPStatus.OK, {"ok": True})
            return
        self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        if self.path != EVENT_PATH:
            self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})
            return
        address = _client_address(self)
        if not self.server.limiter.allow(address):
            self._json(HTTPStatus.TOO_MANY_REQUESTS, {"error": "rate limit exceeded"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        if length <= 0 or length > _MAX_BODY:
            self._json(HTTPStatus.BAD_REQUEST, {"error": "invalid content length"})
            return
        try:
            raw = self.rfile.read(length)
            payload = validate_payload(json.loads(raw))
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
            return
        self.server.store.record(payload)
        self._json(HTTPStatus.ACCEPTED, {"accepted": True})

    def do_OPTIONS(self) -> None:  # noqa: N802
        self.send_response(HTTPStatus.NO_CONTENT)
        self.send_header("Allow", "GET, POST, OPTIONS")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _json(self, status: HTTPStatus, payload: dict[str, Any]) -> None:
        encoded = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, format: str, *args: object) -> None:
        # Deliberately omit client addresses from logs.
        syslog = f"{self.command} {self.path} {args[1] if len(args) > 1 else '-'}"
        print(syslog, flush=True)


def _client_address(handler: TelemetryHandler) -> str:
    if handler.client_address[0] in {"127.0.0.1", "::1"}:
        candidate = handler.headers.get("X-Real-IP", "")
        try:
            return str(ipaddress.ip_address(candidate))
        except ValueError:
            pass
    return handler.client_address[0]


def _iso_timestamp(value: object) -> str | None:
    if not isinstance(value, int):
        return None
    return datetime.fromtimestamp(value, timezone.utc).isoformat()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Serve anonymous AMH product telemetry")
    parser.add_argument("--host", default=os.environ.get("AMH_TELEMETRY_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("AMH_TELEMETRY_PORT", "8790")))
    parser.add_argument(
        "--database",
        type=Path,
        default=Path(
            os.environ.get(
                "AMH_TELEMETRY_DATABASE",
                "~/.local/share/agent-memory-hub/telemetry.sqlite3",
            )
        ).expanduser(),
    )
    parser.add_argument("--rate-limit", type=int, default=30)
    return parser


def main() -> int:
    args = _parser().parse_args()
    server = TelemetryHTTPServer(
        (args.host, args.port),
        TelemetryStore(args.database),
        max(1, args.rate_limit),
    )
    print(f"telemetry server listening on {args.host}:{args.port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

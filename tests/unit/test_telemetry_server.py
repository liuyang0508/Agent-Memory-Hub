from __future__ import annotations

import http.client
import json
import threading
from datetime import datetime, timezone
from pathlib import Path

import pytest

from deploy.telemetry_server import (
    EVENT_PATH,
    SUMMARY_PATH,
    MinuteLimiter,
    TelemetryHTTPServer,
    TelemetryStore,
    validate_payload,
)


def _payload(
    anonymous_id: str,
    event: str,
    *,
    platform: str = "darwin",
    adapter: str = "codex",
) -> dict:
    return {
        "schema_version": 1,
        "anonymous_id": anonymous_id,
        "event": event,
        "product_version": "1.2.0",
        "platform": platform,
        "architecture": "arm64",
        "channel": "shell",
        "adapter": adapter,
    }


def test_store_returns_only_aggregate_and_rehashed_recent_instances(tmp_path: Path) -> None:
    store = TelemetryStore(tmp_path / "telemetry.sqlite3")
    now = int(datetime(2026, 7, 27, 12, tzinfo=timezone.utc).timestamp())
    first = "a" * 32
    second = "b" * 32
    store.record(_payload(first, "install"), now=now - 100)
    store.record(_payload(first, "active"), now=now - 60)
    store.record(_payload(second, "install", platform="linux"), now=now - 90_000)

    summary = store.summary(now=now)

    assert summary["total_instances"] == 2
    assert summary["installed_24h"] == 1
    assert summary["active_24h"] == 1
    assert summary["active_7d"] == 1
    assert len(summary["trend"]) == 14
    assert summary["privacy"] == {
        "opt_in_only": True,
        "stores_ip": False,
        "stores_content": False,
    }
    serialized = json.dumps(summary)
    assert first not in serialized
    assert second not in serialized
    assert summary["recent"][0]["id"].startswith("anon-")


def test_payload_validation_is_fail_closed() -> None:
    assert validate_payload(_payload("a" * 32, "install"))["event"] == "install"

    with pytest.raises(ValueError, match="unexpected fields"):
        validate_payload({**_payload("a" * 32, "install"), "username": "private"})
    with pytest.raises(ValueError, match="invalid anonymous_id"):
        validate_payload(_payload("/Users/example", "install"))
    with pytest.raises(ValueError, match="invalid event"):
        validate_payload(_payload("a" * 32, "memory-content"))


def test_rate_limiter_uses_a_bounded_minute_window() -> None:
    limiter = MinuteLimiter(limit=2)
    assert limiter.allow("192.0.2.1", now=0)
    assert limiter.allow("192.0.2.1", now=1)
    assert not limiter.allow("192.0.2.1", now=2)
    assert limiter.allow("192.0.2.1", now=61)


def test_http_api_accepts_event_and_serves_summary(tmp_path: Path) -> None:
    server = TelemetryHTTPServer(
        ("127.0.0.1", 0),
        TelemetryStore(tmp_path / "telemetry.sqlite3"),
        30,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        connection = http.client.HTTPConnection(*server.server_address, timeout=2)
        body = json.dumps(_payload("c" * 32, "install"))
        connection.request(
            "POST",
            EVENT_PATH,
            body=body,
            headers={"Content-Type": "application/json", "Content-Length": str(len(body))},
        )
        response = connection.getresponse()
        assert response.status == 202
        response.read()

        connection.request("GET", SUMMARY_PATH)
        response = connection.getresponse()
        assert response.status == 200
        summary = json.loads(response.read())
        assert summary["total_instances"] == 1
        assert response.getheader("Cache-Control") == "no-store"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_official_site_contains_live_privacy_bounded_dashboard() -> None:
    root = Path(__file__).resolve().parents[2]
    site = (root / "docs/visuals/agent-memory-hub-official-site.html").read_text()
    nginx = (root / "deploy/nginx-telemetry-location.conf").read_text()

    assert 'id="adoption"' in site
    assert "/api/v1/telemetry/summary" in site
    assert "memory telemetry enable" in site
    assert "不采集姓名、账号、IP、目录、提示词或记忆内容" in site
    assert "location = /api/v1/telemetry/event" in nginx
    assert "proxy_pass http://127.0.0.1:8790" in nginx

"""The selection page exists because a thousand-row terminal list is unusable.

These tests cover the parts that decide what gets uploaded: the payload the
browser sees, and which rows the submit handler will accept.
"""

from __future__ import annotations

import json
import re
import threading
import urllib.request

from agent_brain.native_sync import picker


def rows(count: int = 3):
    def row(index: int, risk: str, reasons: list[str] | None = None):
        return {
            "path": "/home/example/.claude/projects/p/memory/file%d.md" % index,
            "name": "file%d.md" % index,
            "title": "标题 %d" % index,
            "product": "claude-code",
            "product_name": "Claude Code",
            "kind": "memory",
            "root": "/home/example/.claude/projects/p/memory",
            "bytes": 1024 * (index + 1),
            "mtime": 1793000000 + index,
            "risk": risk,
            "reasons": reasons or [],
        }

    built = [row(i, "clean") for i in range(count)]
    built[0] = row(0, "clean")
    return built


def test_payload_omits_paths_and_bodies():
    """The page is a browser tab; it has no business holding absolute paths."""
    payload = picker._payload(rows())
    data = json.loads(payload)
    assert len(data) == 3
    assert set(data[0]) == {
        "i",
        "name",
        "title",
        "product_name",
        "kind_name",
        "bytes",
        "mtime",
        "risk",
        "reasons",
    }
    assert "/home/example" not in payload


def test_payload_labels_kind_in_chinese():
    data = json.loads(picker._payload(rows()))
    assert data[0]["kind_name"] == "记忆"


def serve_and_post(selection, monkeypatch):
    """Drive the real server the way the page does, without opening a browser."""
    monkeypatch.setattr(picker.webbrowser, "open", lambda url: True)
    monkeypatch.setattr(picker, "PAGE", picker.PAGE)
    result = {}

    def client():
        # serve() blocks, so the request comes from another thread.
        import time

        time.sleep(0.4)
        try:
            body = json.dumps({"picked": selection}).encode()
            req = urllib.request.Request(
                result["url"] + "submit", data=body, headers={"Content-Type": "application/json"}
            )
            urllib.request.urlopen(req, timeout=5).read()
        except Exception as exc:  # pragma: no cover - surfaces as a test failure
            result["error"] = exc

    def capture(url):
        result["url"] = url
        threading.Thread(target=client, daemon=True).start()
        return True

    monkeypatch.setattr(picker.webbrowser, "open", capture)
    return result


def test_serve_returns_only_ticked_rows(monkeypatch):
    sample = rows()
    holder = serve_and_post([0, 2], monkeypatch)
    picked = picker.serve(sample, timeout=10)
    assert [row["name"] for row in picked] == ["file0.md", "file2.md"]
    assert "url" in holder and "error" not in holder


def test_serve_never_accepts_a_blocked_row(monkeypatch):
    """Even a hand-made request cannot push a key file past the screen."""
    sample = rows()
    sample[1]["risk"] = "blocked"
    sample[1]["reasons"] = ["私钥"]
    serve_and_post([0, 1], monkeypatch)
    picked = picker.serve(sample, timeout=10)
    assert [row["name"] for row in picked] == ["file0.md"]


def test_serve_returns_empty_when_skipped(monkeypatch):
    sample = rows()
    serve_and_post([], monkeypatch)
    assert picker.serve(sample, timeout=10) == []


def test_page_has_no_credential_placeholder():
    """The page is served over loopback and must not carry a token field."""
    assert "token" not in picker.PAGE.lower()
    assert re.search(r"__ROWS__", picker.PAGE)

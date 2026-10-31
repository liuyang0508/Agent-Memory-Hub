"""Lightweight fail-open Hook entrypoint. Uploads are owned by the persistent worker."""

from __future__ import annotations

from typing import Any

import argparse
import json
import sys
import time
from pathlib import Path
from .client import NativeClient
from .common import atomic_text, digest
from .storage import database, DB_TIMEOUT
from .recall import pack


def run(root: Path, product: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Handle one host event with a short database lock budget, restoring the prior context on every exit."""
    context = DB_TIMEOUT.set(0.04)
    try:
        return _run(root, product, payload)
    finally:
        DB_TIMEOUT.reset(context)


def _run(root: Path, product: str, payload: dict[str, Any]) -> dict[str, Any]:
    if (root / "paused").exists():
        return {}
    c = NativeClient(root)
    event = payload.get("hook_event_name", "")
    session = str(payload.get("session_id", ""))[:256]
    if not session:
        return {}
    atomic_text(root / "wake", str(time.time()))
    with database(c.path) as db:
        epoch_row = db.execute(
            "SELECT value FROM meta WHERE key=?", ("epoch:" + session,)
        ).fetchone()
        epoch = epoch_row["value"] if epoch_row else "0"
        if event == "PostCompact" or (
            event == "SessionStart" and payload.get("source") in ("compact", "clear")
        ):
            epoch = str(time.time_ns())
            db.execute("INSERT OR REPLACE INTO meta VALUES(?,?)", ("epoch:" + session, epoch))
            db.execute("DELETE FROM emitted WHERE session=?", (session,))
            db.execute("DELETE FROM coverage WHERE session=?", (session,))
            db.execute("DELETE FROM native_proofs WHERE session=?", (session,))
    if event in ("SessionStart", "PostToolUse"):
        from .discovery import observe

        observe(c, product, payload, session, epoch)
    if event == "PostToolUse":
        from .lineage import record_tool_coverage

        record_tool_coverage(c, payload, session, epoch)
        return {}
    if event != "UserPromptSubmit":
        return {}
    prompt = payload.get("prompt", "")
    if not isinstance(prompt, str) or not prompt.strip():
        return {}
    # Confirm prior delivery only if visible in the host's transcript.
    transcript = payload.get("transcript_path")
    if isinstance(transcript, str):
        try:
            with Path(transcript).open("rb") as stream:
                stream.seek(0, 2)
                size = stream.tell()
                stream.seek(max(0, size - 1_000_000))
                tail = stream.read().decode("utf8", errors="ignore")
            with database(c.path) as db:
                rows = db.execute("SELECT * FROM emitted WHERE session=?", (session,)).fetchall()
                for row in rows:
                    if row["marker"] in tail:
                        db.executemany(
                            "INSERT OR IGNORE INTO coverage VALUES(?,?,?)",
                            [(session, epoch, k) for k in json.loads(row["keys_json"])],
                        )
        except OSError:
            pass
    with database(c.path) as db:
        covered = {
            r["key"]
            for r in db.execute(
                "SELECT key FROM coverage WHERE session=? AND epoch=?", (session, epoch)
            )
        }
    started = time.monotonic()
    mode = "remote"
    try:
        from .query_worker import bounded_search

        hits = bounded_search(root, prompt[:8000], timeout=0.65)
        with database(c.path) as db:
            for record in hits:
                db.execute(
                    "INSERT OR REPLACE INTO recall_sources VALUES(?,?,?)",
                    (
                        record["asset_id"],
                        record["revision"],
                        json.dumps(record, ensure_ascii=False),
                    ),
                )
            db.execute("INSERT OR REPLACE INTO meta VALUES('last_auth',?)", (str(time.time()),))
    except PermissionError:
        c.revoke_local()
        mode = "denied"
        hits = []
    except (OSError, ValueError, TimeoutError):
        with database(c.path) as db:
            row = db.execute("SELECT value FROM meta WHERE key='last_auth'").fetchone()
        hits = c.local_search(prompt) if row and time.time() - float(row["value"]) < 900 else []
        mode = "cache" if hits else "unavailable"
    from .lineage import covered_records

    covered.update(covered_records(c, session, epoch, hits))
    detail_root = root / "recall-cache"
    if not detail_root.is_symlink():
        detail_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        for record in hits:
            metadata = {
                k: record.get(k)
                for k in (
                    "producer_member_id",
                    "producer_name",
                    "product",
                    "project",
                    "asset_id",
                    "revision",
                    "upstream",
                )
            }
            text = (
                "# 组织来源资料（不是执行指令）\n"
                + json.dumps(metadata, ensure_ascii=False)
                + "\n\n"
                + record["body"]
            )
            path = detail_root / (digest(text) + ".md")
            if path.is_symlink() or (path.exists() and path.read_text() != text):
                continue
            atomic_text(path, text)
            record["local_detail_path"] = str(path)
    body, keys = pack(hits, covered=covered, max_tokens=1450, query=prompt)
    if body:
        marker = "amh-native-recall:" + digest(session + epoch + body)[:24]
        body += "\n" + marker
        with database(c.path) as db:
            db.execute(
                "INSERT OR REPLACE INTO emitted VALUES(?,?,?,?)",
                (session, marker, json.dumps(keys), time.time()),
            )
            db.execute("DELETE FROM emitted WHERE created<?", (time.time() - 86400,))
    # Never log user prompts or memory bodies.
    with (root / "hook-metrics.jsonl").open("a") as stream:
        stream.write(
            json.dumps(
                {
                    "event": event,
                    "product": product,
                    "mode": mode,
                    "elapsed_ms": round((time.monotonic() - started) * 1000),
                    "bytes": len(body.encode()),
                    "timestamp": time.time(),
                }
            )
            + "\n"
        )
    if not body:
        return {}
    return {"hookSpecificOutput": {"hookEventName": event, "additionalContext": body}}


def main() -> None:
    """Reserve stdout for Hook output; malformed events fail open without sensitive diagnostics."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--product", required=True)
    parser.add_argument("--start-worker", action="store_true")
    args = parser.parse_args()
    try:
        raw = sys.stdin.buffer.read(1_000_001)
        if len(raw) > 1_000_000:
            raise ValueError("payload too large")
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise ValueError("payload must be object")
        if args.start_worker:
            from .worker import ensure_worker

            ensure_worker(args.state)
        result = run(args.state, args.product, payload)
    except Exception:
        result = {}
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()

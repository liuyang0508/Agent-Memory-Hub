"""Lightweight fail-open Hook entrypoint. Uploads are owned by the persistent worker."""

from __future__ import annotations

from typing import Any

import argparse
import json
import sqlite3
import sys
import time
from pathlib import Path
from .client import NativeClient
from .common import atomic_text, digest
from .storage import database, DB_TIMEOUT
from .recall import coverage_key, pack


def _report(db: sqlite3.Connection, trace_id: str, keys: list[str], event: str) -> None:
    """Queue one delivery state per recalled version; the worker uploads it later."""
    rows = []
    for key in keys:
        asset_id, revision = key.split(":")[:2]
        rows.append((trace_id, asset_id, int(revision), event, time.time()))
    db.executemany("INSERT OR IGNORE INTO recall_reports VALUES(?,?,?,?,?)", rows)


def _display_text(record: dict[str, Any]) -> str:
    """The text pack() would inject: transcripts use their index text, everything else the body."""
    text = record.get("index_text") if record.get("kind") == "transcript" else None
    if text is None:
        text = record.get("body")
    return text if isinstance(text, str) else ""


def _project_for(c: NativeClient, cwd: Any) -> str:
    """The project this device registered for the working directory; empty when unknown.

    The deepest registered source root containing ``cwd`` wins. Nothing is
    inferred from the directory name: an unregistered directory is unknown.
    """
    if not isinstance(cwd, str) or not cwd:
        return ""
    try:
        with database(c.path) as db:
            rows = db.execute(
                "SELECT root, project FROM sources WHERE project IS NOT NULL AND project != ''"
            ).fetchall()
    except (sqlite3.Error, OSError):
        return ""
    here = Path(cwd)
    best, depth = "", -1
    for row in rows:
        root = Path(str(row["root"] or ""))
        try:
            here.relative_to(root)
        except ValueError:
            continue
        if len(root.parts) > depth:
            best, depth = str(row["project"])[:256], len(root.parts)
    return best


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
    # The worker consumes this by unlinking it, so an existing marker already
    # means "wake pending". Rewriting it every call meant an fsync per tool use,
    # all of it inside the hook's time budget.
    wake = root / "wake"
    if not wake.exists():
        try:
            atomic_text(wake, str(time.time()))
        except OSError:
            pass  # A read-only state directory must not block the event itself.
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
            db.execute("DELETE FROM emitted_traces WHERE session=?", (session,))
            db.execute("DELETE FROM coverage WHERE session=?", (session,))
            db.execute("DELETE FROM native_proofs WHERE session=?", (session,))
    if event in ("SessionStart", "PostToolUse"):
        from .discovery import observe

        observe(c, product, payload, session, epoch)
        from .profiles import observe_backups

        observe_backups(c, product, payload, session, epoch)
    if not c.recall_enabled():
        return {}
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
                        keys = json.loads(row["keys_json"])
                        db.executemany(
                            "INSERT OR IGNORE INTO coverage VALUES(?,?,?)",
                            [(session, epoch, k) for k in keys],
                        )
                        # The model demonstrably received this delivery: queue
                        # the confirmation for the worker to report to the Hub.
                        traced = db.execute(
                            "SELECT trace_id FROM emitted_traces WHERE session=? AND marker=?",
                            (session, row["marker"]),
                        ).fetchone()
                        if traced:
                            _report(db, traced["trace_id"], keys, "confirmed")
                            # One confirmation per delivery: the marker stays in
                            # the transcript for the rest of the session, so the
                            # trace link is consumed here while `emitted` keeps
                            # covering the keys.
                            db.execute(
                                "DELETE FROM emitted_traces WHERE session=? AND marker=?",
                                (session, row["marker"]),
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
    trace_id = None
    channel = "search"
    try:
        from .query_worker import bounded_search

        asking = {
            "product": product,
            "project": _project_for(c, payload.get("cwd")),
            "session_digest": digest(session)[:16],
        }
        response = bounded_search(root, prompt[:8000], timeout=1.2, asking=asking)
        # Test doubles may still answer with a bare list of records.
        if isinstance(response, dict):
            hits = response["items"]
            trace_id = response.get("trace_id")
            channel = response.get("channel", "recall")
        else:
            hits = response
    except PermissionError:
        c.revoke_local()
        mode = "denied"
        hits = []
    except (OSError, ValueError, TimeoutError, sqlite3.Error):
        with database(c.path) as db:
            row = db.execute("SELECT value FROM meta WHERE key='last_auth'").fetchone()
        hits = c.local_search(prompt) if row and time.time() - float(row["value"]) < 900 else []
        mode = "cache" if hits else "unavailable"
    if not c.recall_enabled():
        return {}
    if mode == "remote":
        # Valid authorized results remain usable even while the async worker
        # owns SQLite's writer lock. Provenance bookkeeping is best effort;
        # losing it may cause a bounded repeat, never an invented coverage claim.
        try:
            from .evidence import metadata

            with database(c.path) as db:
                for record in hits:
                    db.execute(
                        "INSERT OR REPLACE INTO recall_sources VALUES(?,?,?)",
                        (
                            record["asset_id"],
                            record["revision"],
                            json.dumps(metadata(record), ensure_ascii=False),
                        ),
                    )
                db.execute("INSERT OR REPLACE INTO meta VALUES('last_auth',?)", (str(time.time()),))
        except (sqlite3.Error, OSError):
            pass
    from .lineage import covered_records

    covered.update(covered_records(c, session, epoch, hits))
    # Recalled bodies go straight into the prompt and nowhere else. Writing them
    # to disk would make a second copy of other members' memories on this
    # machine, which is exactly what local-first sharing is supposed to avoid.
    # The delivery receipt is appended below; reserve it inside the byte cap.
    receipt_bytes = len("\namh-native-recall:") + 24
    body, keys = pack(
        hits, covered=covered, max_bytes=4096 - receipt_bytes, per_item_bytes=1536, query=prompt
    )
    if body:
        marker = "amh-native-recall:" + digest(session + epoch + body)[:24]
        body += "\n" + marker
        try:
            with database(c.path) as db:
                db.execute(
                    "INSERT OR REPLACE INTO emitted VALUES(?,?,?,?)",
                    (session, marker, json.dumps(keys), time.time()),
                )
                db.execute("DELETE FROM emitted WHERE created<?", (time.time() - 86400,))
                # Trace links whose receipt is gone (expired, compacted or already
                # confirmed) are orphans; never let them accumulate.
                db.execute(
                    "DELETE FROM emitted_traces WHERE marker NOT IN (SELECT marker FROM emitted)"
                )
                if trace_id:
                    db.execute(
                        "INSERT OR REPLACE INTO emitted_traces VALUES(?,?,?)",
                        (session, marker, trace_id),
                    )
        except (sqlite3.Error, OSError):
            pass  # Do not discard a completed recall for an unavailable receipt store.
    if trace_id and hits:
        # Delivery states for the Hub's trace: packed+emitted, covered (already
        # in this session) or budget (ranked but no room). Identifiers only.
        try:
            with database(c.path) as db:
                packed = {k.split(":excerpt:")[0] for k in keys}
                # An excerpt delivery covers under a `:excerpt:<digest>` key; the
                # Hub tracks the version, so compare on the version key alone.
                seen = {k.split(":excerpt:")[0] for k in covered}
                for record in hits:
                    if record.get("deleted") or not _display_text(record).strip():
                        continue  # pack() never injects these; there is no state to report.
                    key = coverage_key(record)
                    if key in packed:
                        _report(db, trace_id, [key], "packed")
                        _report(db, trace_id, [key], "emitted")
                    elif key in seen:
                        _report(db, trace_id, [key], "covered")
                    else:
                        _report(db, trace_id, [key], "budget")
        except (sqlite3.Error, OSError):
            pass
    # Never log user prompts or memory bodies. Metrics are diagnostics: a
    # read-only state directory must not cost the member a recall they already
    # paid for, and `emitted` has already been written above.
    try:
        with (root / "hook-metrics.jsonl").open("a") as stream:
            stream.write(
                json.dumps(
                    {
                        "event": event,
                        "product": product,
                        "mode": mode,
                        "channel": channel,
                        "trace_id": trace_id,
                        "hits": len(hits),
                        "packed": len({k.split(":excerpt:")[0] for k in keys}),
                        "elapsed_ms": round((time.monotonic() - started) * 1000),
                        "bytes": len(body.encode()),
                        "timestamp": time.time(),
                    }
                )
                + "\n"
            )
    except OSError:
        pass
    if not body:
        return {}
    if not c.recall_enabled():
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

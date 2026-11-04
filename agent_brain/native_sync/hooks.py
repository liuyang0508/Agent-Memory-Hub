"""Lightweight fail-open Hook entrypoint. Uploads are owned by the persistent worker."""

from __future__ import annotations

from typing import Any

import argparse
import json
import re
import sqlite3
import sys
import time
from pathlib import Path
from .client import NativeClient
from .common import atomic_text, digest, recall_budget
from .storage import database, DB_TIMEOUT
from .recall import coverage_key, pack
from .recall import hint_keys, hint_of
from .recall_view import (
    DISPLAY_KEY,
    DISPLAY_MODES,
    admit,
    follow_up,
    normalize,
    render,
    render_skip,
)

# The previous prompt a short follow-up borrows for search, at most this long
# and at most this old: a topic from another hour is not what "那第二个呢" means.
CONTEXT_CHARS = 300
CONTEXT_MAX_AGE = 1800.0
CONTEXT_SCAN = 12
# One long agentic turn can write megabytes after the delivery marker; missing
# the marker means re-injecting the memory, so look far enough back.
RECEIPT_TAIL_BYTES = 16_000_000

# Hosts that show a hook's systemMessage to the member. Codex shows the injected
# context itself, so its table travels inside the organization block instead.
VISIBLE_MESSAGE_PRODUCTS = frozenset({"claude-code", "cowork"})
# A Claude Code mod can draw the recall as a collapsible transcript row. It
# marks each session it draws for under ``rich-display/``; the hook then writes
# the view for it under ``recall-view/`` instead of a plain-text systemMessage.
PANEL_PRODUCTS = frozenset({"claude-code"})
_SESSION_FILE = re.compile(r"[^A-Za-z0-9_-]")


def _hand_to_panel(root: Path, product: str, payload: dict[str, Any], view: str) -> bool:
    """Write the view for a mod drawing this session; True when one is."""
    session_id = payload.get("session_id")
    if product not in PANEL_PRODUCTS or not isinstance(session_id, str) or not session_id:
        return False
    name = _SESSION_FILE.sub("", session_id)[:128]
    try:
        # The mod refreshes its mark before each prompt; a stale one means it
        # is no longer loaded, so the plain message comes back.
        if not name or time.time() - (root / "rich-display" / name).stat().st_mtime > 120:
            return False
    except OSError:
        return False
    first, _, rest = view.partition("\n")
    panel = {
        "version": 1,
        "written_at": time.time(),
        "summary": first,
        "lines": rest.split("\n") if rest else [],
    }
    try:
        (root / "recall-view").mkdir(mode=0o700, exist_ok=True)
        atomic_text(root / "recall-view" / (name + ".json"), json.dumps(panel, ensure_ascii=False))
    except OSError:
        return False
    return True


def _report(db: sqlite3.Connection, trace_id: str, keys: list[str], event: str) -> None:
    """Queue one delivery state per recalled version; the worker uploads it later."""
    rows = []
    for key in keys:
        asset_id, revision = key.split(":")[:2]
        rows.append((trace_id, asset_id, int(revision), event, time.time()))
    db.executemany("INSERT OR IGNORE INTO recall_reports VALUES(?,?,?,?,?)", rows)


def _judge_turn(c: NativeClient, product: str, payload: dict[str, Any], session: str) -> None:
    """At the end of a turn, label each injected memory used or unused (usage.py).

    Claude Code's transcript shows what the model saw before, wrote and read,
    so both labels are possible there. Elsewhere only the final message is
    known; without the earlier context a shared word proves nothing, so only
    "unused" (no overlap at all) is reported. Labels join the delivery report
    queue; the memory text never leaves this function.
    """
    from .usage import claude_turn, fingerprint, judge, read_target

    with database(c.path) as db:
        rows = db.execute("SELECT * FROM usage_pending WHERE session=?", (session,)).fetchall()
        db.execute("DELETE FROM usage_pending WHERE session=?", (session,))
    if not rows:
        return
    trace_id = rows[0]["trace_id"]
    message = payload.get("last_assistant_message")
    message = message if isinstance(message, str) else ""
    transcript = payload.get("transcript_path")
    turn = (
        claude_turn(Path(transcript))
        if product == "claude-code" and isinstance(transcript, str)
        else None
    )
    allow_used = turn is not None
    if turn is None:
        if not message:
            return
        turn = {"seen": "", "output": message, "results": "", "reads": []}
    elif message and message not in turn["output"]:
        turn["output"] += "\n" + message  # The last message may not be flushed yet.
    seen = turn["seen"] + "\n" + turn["results"]
    labels: list[tuple[str, int, str]] = []
    injected = set()
    for row in rows:
        if not row["asset"]:
            continue
        injected.add(row["asset"])
        label, _ = judge(
            json.loads(row["fingerprint"]),
            output=turn["output"],
            seen=seen,
            salt=session,
            allow_used=allow_used,
        )
        if label:
            labels.append((row["asset"], int(row["revision"]), label))
    for read in turn["reads"]:
        target = read_target(read["arguments"], read["text"])
        if target is None or target[0] in injected or target[1] < 1:
            continue
        label, _ = judge(
            fingerprint(read["text"], session), output=turn["output"], seen=seen, salt=session
        )
        if label == "used":
            labels.append((target[0], target[1], "consulted"))
            injected.add(target[0])
    if labels:
        with database(c.path) as db:
            for asset, revision, label in labels:
                _report(db, trace_id, [f"{asset}:{revision}"], label)


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
    if event == "Stop":
        try:
            _judge_turn(c, product, payload, session)
        except (sqlite3.Error, OSError, ValueError, TypeError, KeyError):
            pass  # Feedback is optional; the turn itself already finished.
        return {}
    if event != "UserPromptSubmit":
        return {}
    try:
        # A new turn: whatever the last one injected can no longer be judged
        # (its Stop never came, e.g. the member interrupted it).
        with database(c.path) as db:
            db.execute("DELETE FROM usage_pending WHERE session=?", (session,))
    except sqlite3.Error:
        pass
    prompt = payload.get("prompt", "")
    if not isinstance(prompt, str) or not prompt.strip():
        return {}
    display = c.config.get(DISPLAY_KEY, "table")
    if display not in DISPLAY_MODES:
        display = "table"
    query = normalize(prompt)
    admitted, skip_reason = admit(query)
    if not admitted:
        # Acknowledgements carry no topic: searching them only injects noise.
        try:
            with (root / "hook-metrics.jsonl").open("a") as stream:
                stream.write(
                    json.dumps(
                        {
                            "event": event,
                            "product": product,
                            "mode": "skipped",
                            "reason": skip_reason,
                            "timestamp": time.time(),
                        }
                    )
                    + "\n"
                )
        except OSError:
            pass
        if (
            display != "off"
            and product in VISIBLE_MESSAGE_PRODUCTS
            and skip_reason not in ("background_event", "terminal_reply")
        ):
            skipped = render_skip(skip_reason, query)
            if _hand_to_panel(root, product, payload, skipped):
                return {}
            return {"systemMessage": skipped}
        return {}
    # Confirm prior delivery only if visible in the host's transcript.
    transcript = payload.get("transcript_path")
    if isinstance(transcript, str):
        try:
            with Path(transcript).open("rb") as stream:
                stream.seek(0, 2)
                size = stream.tell()
                stream.seek(max(0, size - RECEIPT_TAIL_BYTES))
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
    # A short follow-up has no topic of its own; search with the previous
    # prompt in front of it (the gate still judges every candidate).
    search, context = query, ""
    transcript_path = payload.get("transcript_path")
    if product == "claude-code" and isinstance(transcript_path, str) and follow_up(query):
        from .usage import recent_prompts

        earlier = [
            text
            for text in (
                normalize(p)
                for p in recent_prompts(
                    Path(transcript_path), limit=CONTEXT_SCAN, max_age=CONTEXT_MAX_AGE
                )
            )
            if text and text != query and admit(text)[0] and not follow_up(text)
        ]
        if earlier:
            context = earlier[-1][:CONTEXT_CHARS]
            # This prompt first: previews and gap reports should read as what was asked.
            search = query + "\n" + context
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
    retrieval: dict[str, Any] | None = None
    hints: list[dict[str, Any]] = []
    budget = recall_budget(c.config)
    # Which path will answer: the resident worker's warm connection when it is
    # listening, otherwise a fresh subprocess that pays the handshakes itself.
    from .recall_service import SOCKET_NAME

    via = "service" if (root / SOCKET_NAME).exists() else "worker"
    asking = {
        "product": product,
        "project": _project_for(c, payload.get("cwd")),
        "session_digest": digest(session)[:16],
    }
    try:
        from .query_worker import recall

        response, via = recall(root, search[:8000], budget=budget, asking=asking)
        # Test doubles may still answer with a bare list of records.
        if isinstance(response, dict):
            hits = response["items"]
            hints = [h for h in response.get("hints") or [] if isinstance(h, dict)]
            trace_id = response.get("trace_id")
            channel = response.get("channel", "recall")
            if isinstance(response.get("retrieval"), dict):
                retrieval = response["retrieval"]
        else:
            hits = response
    except PermissionError as exc:
        if exc.errno is not None:
            # A local file this process may not read says nothing about the device.
            mode = "unavailable"
        else:
            c.revoke_local()
            mode = "denied"
        hits = []
    except (OSError, ValueError, TimeoutError, sqlite3.Error):
        with database(c.path) as db:
            row = db.execute("SELECT value FROM meta WHERE key='last_auth'").fetchone()
        hits = c.local_search(search) if row and time.time() - float(row["value"]) < 900 else []
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
    if context and mode == "remote":
        # What a borrowed topic finds may belong to the last question, not this
        # one: name it and let the model decide, never inject the text.
        hints = [*[hint_of(record) for record in hits if not record.get("deleted")], *hints]
    body, keys = pack(
        [] if context else hits,
        covered=covered,
        max_bytes=4096 - receipt_bytes,
        per_item_bytes=1536,
        query=search,
        hints=hints if mode == "remote" else None,
    )
    if body and mode == "remote" and hints:
        # A title is named once per session: repeating it every turn is noise.
        try:
            with database(c.path) as db:
                db.executemany(
                    "INSERT OR IGNORE INTO coverage VALUES(?,?,?)",
                    [
                        (session, epoch, key)
                        for key in hint_keys(hints, covered, [] if context else hits)
                    ],
                )
        except sqlite3.Error:
            pass
    # Delivery state per recalled version: packed now, already injected earlier
    # in this session, ranked but outside the byte budget, or without text.
    packed_keys = {k.split(":excerpt:")[0] for k in keys}
    seen_keys = {k.split(":excerpt:")[0] for k in covered}
    states: dict[str, str] = {}
    for record in hits:
        key = coverage_key(record)
        if record.get("deleted") or not _display_text(record).strip():
            states[key] = "empty"
        elif context:
            states[key] = "hint"
        elif key in packed_keys:
            states[key] = "packed"
        elif key in seen_keys:
            states[key] = "covered"
        else:
            states[key] = "budget"
    elapsed_ms = round((time.monotonic() - started) * 1000)
    view = ""
    if display != "off":
        view = render(
            hub=str(c.config.get("hub", "")),
            query=query,
            asking=asking,
            budget=budget,
            elapsed_ms=elapsed_ms,
            via=via,
            mode=mode,
            retrieval=retrieval,
            trace_id=trace_id,
            hits=hits,
            states=states,
            display=display,
            style="box" if product in VISIBLE_MESSAGE_PRODUCTS else "markdown",
            hints=hints if mode == "remote" else None,
            context=context,
        )
    if body and view and product not in VISIBLE_MESSAGE_PRODUCTS:
        # Inside the organization block, so transcript capture strips it too.
        head, _, rest = body.partition("\n来源资料未经当前会话确认")
        body = head + "\n" + view + "\n\n来源资料未经当前会话确认" + rest
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
    if trace_id:
        # What this turn injected, as digests, for the Stop hook to judge use
        # against (usage.py). A turn without injections still records its
        # trace: the model may fetch a memory the gate held back.
        try:
            from .usage import fingerprint

            now = time.time()
            with database(c.path) as db:
                db.execute(
                    "DELETE FROM usage_pending WHERE session=? OR created<?",
                    (session, now - 86400),
                )
                rows = [(session, trace_id, "", 0, "", now)]
                for record in hits:
                    if states.get(coverage_key(record)) == "packed":
                        rows.append(
                            (
                                session,
                                trace_id,
                                str(record["asset_id"]),
                                int(record["revision"]),
                                json.dumps(fingerprint(_display_text(record), session)),
                                now,
                            )
                        )
                db.executemany("INSERT OR REPLACE INTO usage_pending VALUES(?,?,?,?,?,?)", rows)
        except (sqlite3.Error, OSError, KeyError, TypeError, ValueError):
            pass
    if trace_id and hits:
        # Delivery states for the Hub's trace: packed+emitted, covered (already
        # in this session) or budget (ranked but no room). Identifiers only.
        try:
            with database(c.path) as db:
                # An excerpt delivery covers under a `:excerpt:<digest>` key; the
                # Hub tracks the version, so states compare on the version key.
                for record in hits:
                    key = coverage_key(record)
                    state = states.get(key)
                    if state == "packed":
                        _report(db, trace_id, [key], "packed")
                        _report(db, trace_id, [key], "emitted")
                    elif state in ("covered", "budget"):
                        _report(db, trace_id, [key], state)
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
                        "packed": len(packed_keys),
                        "retrieval": (retrieval or {}).get("mode"),
                        "elapsed_ms": elapsed_ms,
                        "budget_ms": round(budget * 1000),
                        "via": via,
                        "bytes": len(body.encode()),
                        "timestamp": time.time(),
                    }
                )
                + "\n"
            )
    except OSError:
        pass
    if not c.recall_enabled():
        return {}
    output: dict[str, Any] = {}
    if (
        view
        and product in VISIBLE_MESSAGE_PRODUCTS
        and not _hand_to_panel(root, product, payload, view)
    ):
        output["systemMessage"] = view
    if body:
        output["hookSpecificOutput"] = {"hookEventName": event, "additionalContext": body}
    return output


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

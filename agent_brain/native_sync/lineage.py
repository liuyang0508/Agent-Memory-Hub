"""Mechanical delivery/reading evidence; never infer semantic authorship with an LLM."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any
from .common import digest
from .storage import database
from .materialize import assembled
from .source import read_document


def strings(value: Any) -> list[str]:
    """Flatten string-valued host evidence without assuming a vendor-specific response shape."""
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [s for v in value.values() for s in strings(v)]
    if isinstance(value, list):
        return [s for v in value for s in strings(v)]
    return []


def record_tool_coverage(client: Any, payload: dict[str, Any], session: str, epoch: str) -> None:
    """Record observed full-content tool reads/writes; paths or partial excerpts alone prove no coverage."""
    result = payload.get("tool_response", {})
    if isinstance(result, dict) and (result.get("is_error") or result.get("error")):
        return
    tool_input = payload.get("tool_input", {})
    if not isinstance(tool_input, dict):
        return
    response = "\n".join(strings(result))
    # Native tools can add line numbers; remove only the documented numeric prefix.
    normalized = re.sub(r"(?m)^\s*\d+\s*[\t→]\s?", "", response)
    file_value = tool_input.get("file_path", tool_input.get("path"))
    candidates = set(re.findall(r"[0-9a-f]{64}\.md", " ".join(strings(tool_input))))
    if isinstance(file_value, str):
        candidates.add(Path(file_value).name)
    for target in client.targets():
        folder = Path(target["root"]) / "amh-org"
        manifest = folder / "manifest.json"
        if not manifest.exists():
            continue
        data = json.loads(manifest.read_text())
        for name in candidates:
            p = folder / name
            expected = data.get("files", {}).get(name)
            if not expected or not p.is_file() or p.is_symlink():
                continue
            text = p.read_text()
            if digest(text) != expected:
                continue
            if text not in response and text not in normalized:
                continue
            keys = data.get("coverage", {}).get(name, [])
            with database(client.path) as db:
                db.executemany(
                    "INSERT OR IGNORE INTO coverage VALUES(?,?,?)",
                    [(session, epoch, k) for k in keys],
                )
    if not isinstance(file_value, str):
        return
    p = Path(file_value).expanduser()
    if not p.is_absolute():
        p = Path(str(payload.get("cwd", "."))) / p
    p = p.resolve()
    for source in client.sources():
        root = Path(source["root"])
        if source["kind"] != "memory" or not p.is_relative_to(
            root if not source["file_source"] else root.parent
        ):
            continue
        if source["file_source"] and p != root:
            continue
        if "amh-org" in p.parts:
            continue
        try:
            doc = read_document(p, "memory")
        except (OSError, ValueError, UnicodeError):
            continue
        if not doc:
            continue
        text, encoding, h = doc
        written = tool_input.get("content")
        visible = bool(text) and (text in response or text in normalized)
        if written == text and payload.get("tool_name") in ("Write", "write_file"):
            visible = True
        if visible:
            with database(client.path) as db:
                db.execute(
                    "INSERT OR REPLACE INTO native_proofs VALUES(?,?,?,?,?)",
                    (session, epoch, source["id"], str(p), h),
                )


def covered_records(
    client: Any, session: str, epoch: str, records: list[dict[str, Any]]
) -> set[str]:
    """Map this session/epoch's observed native versions to recall keys, preserving producer identity."""
    from .recall import coverage_key

    with database(client.path) as db:
        proofs = {
            (r["source"], r["hash"])
            for r in db.execute(
                "SELECT source,hash FROM native_proofs WHERE session=? AND epoch=?",
                (session, epoch),
            )
        }
    return {
        coverage_key(r)
        for r in records
        if r["producer_member_id"] == client.identity["member_id"]
        and (r["source_store"], r.get("document_hash") or r["body_hash"]) in proofs
    }


def classify_copy(client: Any, h: str) -> tuple[str, list[str]]:
    """Return alias only with confirmed full-version consumption; otherwise hold exact copies for review."""
    with database(client.path) as db:
        records = [json.loads(r["payload"]) for r in db.execute("SELECT payload FROM replica")]
        observed = [
            json.loads(r["payload"]) for r in db.execute("SELECT payload FROM recall_sources")
        ]
        known = {r["key"] for r in db.execute("SELECT key FROM coverage")}
    unique: dict[str, dict[str, Any]] = {}
    for record in records + observed:
        if record["deleted"] or record["kind"] != "memory":
            continue
        existing = unique.get(record["asset_id"])
        if existing is None or record["revision"] > existing["revision"]:
            unique[record["asset_id"]] = record
    matches = [r for r in assembled(list(unique.values())) if r["body_hash"] == h]
    if not matches:
        return "", []
    refs = [r["asset_id"] for r in matches]
    for record in matches:
        from .recall import coverage_key

        if all(
            aid in unique and unique[aid]["revision"] == rev and coverage_key(unique[aid]) in known
            for aid, rev in record["assembled_revisions"]
        ):
            return "alias", refs
    return "needs-origin-review", refs

"""Authenticated current-document reads and evidence-only organization relations.

This composes the native Hub and its own SQLite records. No legacy brain, host
filesystem content, URL fetching, or model dependency participates in a read.
Historical nodes are provenance metadata only: old content is never read back.
"""

from __future__ import annotations

import hashlib
import json
import posixpath
import re
import sqlite3
from collections import deque
from typing import TYPE_CHECKING, Any, Iterator
from urllib.parse import unquote, urlsplit

from .asset_roles import asset_role
from .document_contract import DOCUMENT_FIELDS
from .models import ReadInput, RelatedInput
from .storage import database

if TYPE_CHECKING:
    from .hub import Hub

MAX_DOCUMENT_BYTES = 8 * 1024 * 1024
MAX_PARTS = 4096
MAX_GRAPH_BYTES = 16 * 1024 * 1024
MAX_REFERENCES = 128
MAX_HISTORY = 10
_INLINE_LINK = re.compile(
    r"(?<![!\\])\[[^\]\n]{0,512}\]\(\s*(?:<([^>\n]{1,2048})>|([^\s)]{1,2048}))"
    r"(?:\s+[^)\n]*)?\)"
)
_ASSET_ID = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")


class KnowledgeConflict(ValueError):
    """Public finite error vocabulary; never carry SQL, paths, or document text."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code.replace("_", " "))


class _GraphLimit(Exception):
    pass


class _GraphBudget:
    """Count projected JSON bytes before loading them, not after parsing bodies."""

    def __init__(self) -> None:
        self.remaining = MAX_GRAPH_BYTES

    def take(self, size: int) -> None:
        if size > self.remaining:
            raise _GraphLimit
        self.remaining -= size


def _source(item: dict[str, Any]) -> dict[str, Any]:
    fields = (
        "tenant_id",
        "source_device_id",
        "source_store",
        "producer_member_id",
        "producer_name",
        "uploader_member_id",
        "uploader_name",
        "producer_unknown",
        "product",
        "project",
        "kind",
        "path",
        "title",
        "observed_at",
    )
    result = {key: item.get(key) for key in fields}
    result["role"] = asset_role(item["product"], item["path"], item["kind"])
    return result


def _version(item: dict[str, Any], parts: list[tuple[str, int]]) -> dict[str, Any]:
    return {
        "document_id": item.get("document_id") or item["asset_id"],
        "document_hash": item.get("document_hash") or item["body_hash"],
        "anchor_revision": item["revision"],
        "parts": [{"asset_id": key, "revision": rev} for key, rev in parts],
    }


def _node(item: dict[str, Any], *, current: bool = True) -> dict[str, Any]:
    return {
        "asset_id": item["asset_id"],
        "revision": item["revision"],
        "source": _source(item),
        "version": _version(item, []),
        "current": current,
        "readable": current,
    }


def _links(body: str) -> tuple[list[tuple[str, int]], bool]:
    """Inline Markdown links outside code; unsupported syntax is not guessed.

    The evidence is the literal href and its one-based line in this document
    version, not a purported relation supplied in arbitrary JSON/source text.
    """
    links: list[tuple[str, int]] = []
    fence = ""
    for number, line in enumerate(body.splitlines(), 1):
        marker = re.match(r"^ {0,3}(`{3,}|~{3,})(.*)$", line)
        if fence:
            if (
                marker
                and marker[1][0] == fence[0]
                and len(marker[1]) >= len(fence)
                and not marker[2].strip()
            ):
                fence = ""
            continue
        if marker:
            fence = marker[1]
            continue
        if line.startswith(("    ", "\t")):
            continue
        line = re.sub(r"`+[^`\n]*`+", "", line)
        for match in _INLINE_LINK.finditer(line):
            if len(links) >= MAX_REFERENCES:
                return links, True
            links.append((match.group(1) or match.group(2), number))
    return links, False


def _relative_path(origin: str, href: str) -> str | None:
    """Resolve inside one source store, without opening any path or following URLs."""
    try:
        url = urlsplit(href)
    except ValueError:
        return None
    if url.scheme or url.netloc or url.query or not url.path:
        return None
    path = unquote(url.path)
    if any(ord(char) < 32 for char in path) or "\\" in path or path.startswith("/"):
        return None
    if origin.startswith("/") or "\\" in origin:
        return None
    resolved = posixpath.normpath(posixpath.join(posixpath.dirname(origin), path))
    if resolved in (".", "..") or resolved.startswith("../"):
        return None
    return resolved


class NativeKnowledge:
    def __init__(self, hub: Hub) -> None:
        self.hub = hub

    def _current(
        self,
        db: sqlite3.Connection,
        token: str,
        asset_id: str,
        revision: int | None = None,
        *,
        budget: _GraphBudget | None = None,
    ) -> dict[str, Any]:
        # Authorization is repeated for each graph hop. Organization membership
        # is the Hub's sharing policy; caller-supplied identity is never accepted.
        who = self.hub._identity(db, token)
        projection = "json_remove(payload,'$.body','$.index_text')" if budget else "payload"
        if budget:
            size = db.execute(
                f"SELECT length(CAST({projection} AS BLOB)) FROM assets WHERE tenant=? AND id=?",
                (who["tenant"], asset_id),
            ).fetchone()
            if size is None:
                raise KnowledgeConflict("asset_unavailable")
            budget.take(size[0])
        row = db.execute(
            f"SELECT tenant,id,revision,owner_device,{projection} payload FROM assets WHERE tenant=? AND id=?",
            (who["tenant"], asset_id),
        ).fetchone()
        if row is None:
            raise KnowledgeConflict("asset_unavailable")
        item: dict[str, Any] = json.loads(row["payload"])
        if item.get("deleted"):
            # Same result as a nonexistent/foreign asset, with no current revision.
            raise KnowledgeConflict("asset_unavailable")
        if revision is not None and row["revision"] != revision:
            raise KnowledgeConflict("stale_version")
        # Authoritative row keys cannot be shadowed by source JSON.
        if (item["tenant_id"], item["asset_id"], item["revision"], item["source_device_id"]) != (
            row["tenant"],
            row["id"],
            row["revision"],
            row["owner_device"],
        ):
            raise KnowledgeConflict("invalid_version")
        return item

    def _document(
        self,
        db: sqlite3.Connection,
        token: str,
        item: dict[str, Any],
        *,
        budget: _GraphBudget | None = None,
    ) -> tuple[bytes, list[tuple[str, int]]]:
        """Assemble one bounded, contiguous, hash-verified current version."""
        who = self.hub._identity(db, token)
        if who["tenant"] != item["tenant_id"]:
            raise KnowledgeConflict("asset_unavailable")
        if item.get("encoding", "utf8") != "utf8":
            raise KnowledgeConflict("unsupported_encoding")
        count = item.get("part_count", 1)
        if count > MAX_PARTS:
            raise KnowledgeConflict("document_too_large")
        # The graph never needs derived index_text. Fetch sizes first and only
        # decode a selected part after its projected payload fits the budget.
        projection = "json_remove(payload,'$.index_text')" if budget else "payload"
        selected = (
            f"id,revision,owner_device,length(CAST({projection} AS BLOB)) payload_bytes"
            if budget
            else "id,revision,owner_device,payload"
        )
        if item.get("document_id"):
            rows = db.execute(
                f"SELECT {selected} FROM assets WHERE tenant=? AND owner_device=? "
                "AND json_extract(payload,'$.source_store')=? "
                "AND json_extract(payload,'$.document_id')=? "
                "AND json_extract(payload,'$.document_hash')=? "
                "ORDER BY json_extract(payload,'$.part_index') LIMIT ?",
                (
                    who["tenant"],
                    item["source_device_id"],
                    item["source_store"],
                    item["document_id"],
                    item["document_hash"],
                    MAX_PARTS + 1,
                ),
            )
        else:
            rows = db.execute(
                f"SELECT {selected} FROM assets WHERE tenant=? AND id=?",
                (who["tenant"], item["asset_id"]),
            )
        result = bytearray()
        parts: list[tuple[str, int]] = []
        for index, row in enumerate(rows):
            if budget:
                budget.take(row["payload_bytes"])
                payload = db.execute(
                    f"SELECT {projection} FROM assets WHERE tenant=? AND id=?",
                    (who["tenant"], row["id"]),
                ).fetchone()[0]
            else:
                payload = row["payload"]
            part = json.loads(payload)
            if (
                index >= count
                or part.get("part_index", 0) != index
                or part.get("part_count", 1) != count
                or part.get("deleted")
                or part.get("encoding", "utf8") != "utf8"
                or part["tenant_id"] != who["tenant"]
                or part["asset_id"] != row["id"]
                or part["revision"] != row["revision"]
                or part["source_device_id"] != row["owner_device"]
                or any(part.get(key) != item.get(key) for key in DOCUMENT_FIELDS)
            ):
                raise KnowledgeConflict("incomplete_version")
            raw = part["body"].encode("utf8")
            if len(result) + len(raw) > MAX_DOCUMENT_BYTES:
                raise KnowledgeConflict("document_too_large")
            if hashlib.sha256(raw).hexdigest() != part["body_hash"]:
                raise KnowledgeConflict("invalid_version")
            result.extend(raw)
            parts.append((row["id"], row["revision"]))
        expected = item.get("document_hash") or item["body_hash"]
        if len(parts) != count or hashlib.sha256(result).hexdigest() != expected:
            raise KnowledgeConflict("incomplete_version")
        if (item["asset_id"], item["revision"]) not in parts:
            raise KnowledgeConflict("stale_version")
        return bytes(result), parts

    def read(
        self, token: str, asset_id: str, revision: int, *, cursor: int = 0, max_bytes: int = 12000
    ) -> dict[str, Any]:
        data = ReadInput(asset_id=asset_id, revision=revision, cursor=cursor, max_bytes=max_bytes)
        with database(self.hub.path, create=False) as db:
            # One snapshot for identity, anchor and every part. A later page
            # opens a new snapshot and checks the exact revision again.
            db.execute("BEGIN")
            item = self._current(db, token, data.asset_id, data.revision)
            raw, parts = self._document(db, token, item)
            if cursor > len(raw) or (cursor < len(raw) and raw[cursor] & 0xC0 == 0x80):
                raise KnowledgeConflict("invalid_cursor")
            end = min(cursor + max_bytes, len(raw))
            while end < len(raw) and raw[end] & 0xC0 == 0x80:
                end -= 1
            complete = end == len(raw)
            return {
                "asset_id": item["asset_id"],
                "revision": item["revision"],
                "text": raw[cursor:end].decode("utf8"),
                "source": _source(item),
                "version": _version(item, parts),
                "cursor": cursor,
                "next_cursor": None if complete else end,
                "total_bytes": len(raw),
                "complete": complete,
                "truncated": not complete,
            }

    def _resolve(
        self,
        db: sqlite3.Connection,
        token: str,
        item: dict[str, Any],
        href: str,
        *,
        budget: _GraphBudget,
    ) -> dict[str, Any] | None:
        who = self.hub._identity(db, token)
        if href.startswith("asset:"):
            key = href[6:]
            if not _ASSET_ID.fullmatch(key):
                return None
        else:
            path = _relative_path(item["path"], href)
            if path is None:
                return None
            # Limit ambiguity and keep resolution within this authenticated source.
            rows = db.execute(
                "SELECT id FROM assets WHERE tenant=? AND owner_device=? "
                "AND json_extract(payload,'$.source_store')=? AND json_extract(payload,'$.path')=? "
                "AND json_extract(payload,'$.part_index')=0 "
                "AND json_extract(payload,'$.deleted')=0 LIMIT 2",
                (who["tenant"], item["source_device_id"], item["source_store"], path),
            ).fetchall()
            if len(rows) != 1:
                return None
            key = rows[0]["id"]
        try:
            return self._current(db, token, key, budget=budget)
        except KnowledgeConflict:
            return None

    def _history(
        self, db: sqlite3.Connection, token: str, item: dict[str, Any], *, budget: _GraphBudget
    ) -> tuple[Iterator[dict[str, Any]], bool]:
        who = self.hub._identity(db, token)
        rows = db.execute(
            "SELECT seq,length(CAST(json_remove(payload,'$.body','$.index_text') AS BLOB)) payload_bytes "
            "FROM changes WHERE tenant=? AND json_extract(payload,'$.asset_id')=? "
            "AND json_extract(payload,'$.revision')<? "
            "ORDER BY json_extract(payload,'$.revision') DESC LIMIT ?",
            (who["tenant"], item["asset_id"], item["revision"], MAX_HISTORY + 1),
        ).fetchall()

        def entries() -> Iterator[dict[str, Any]]:
            for row in rows[:MAX_HISTORY]:
                current = self.hub._identity(db, token)
                if current["tenant"] != who["tenant"]:
                    raise PermissionError("organization changed during graph traversal")
                budget.take(row["payload_bytes"])
                old = json.loads(
                    db.execute(
                        "SELECT json_remove(payload,'$.body','$.index_text') FROM changes WHERE tenant=? AND seq=?",
                        (who["tenant"], row["seq"]),
                    ).fetchone()[0]
                )
                if (
                    old.get("deleted")
                    or old["tenant_id"] != who["tenant"]
                    or old["source_device_id"] != item["source_device_id"]
                    or old["source_store"] != item["source_store"]
                    or old.get("document_id") != item.get("document_id")
                ):
                    continue
                yield old

        return entries(), len(rows) > MAX_HISTORY

    def related(
        self, token: str, asset_id: str, revision: int, *, depth: int = 1, limit: int = 10
    ) -> dict[str, Any]:
        data = RelatedInput(asset_id=asset_id, revision=revision, depth=depth, limit=limit)
        response: dict[str, Any] = {
            "root": {"asset_id": asset_id, "revision": revision, "metadata_available": False},
            "items": [],
            "edges": [],
            "depth": data.depth,
            "limit": data.limit,
            "truncated": False,
            "relation_policy": "explicit_only",
        }
        budget = _GraphBudget()
        with database(self.hub.path, create=False) as db:
            db.execute("BEGIN")
            try:
                root = self._current(db, token, data.asset_id, data.revision, budget=budget)
                response["root"] = _node(root)
                queue = deque([(root, 0)])
                seen = {(root["asset_id"], root["revision"])}
                edge_keys: set[tuple[Any, ...]] = set()
                while queue:
                    item, level = queue.popleft()
                    # Nodes were loaded in this same snapshot; body verification
                    # reauthenticates each hop, without rereading full candidates.
                    try:
                        raw, _ = self._document(db, token, item, budget=budget)
                    except KnowledgeConflict:
                        if level == 0:
                            raise
                        continue
                    links, clipped = (
                        _links(raw.decode("utf8"))
                        if item["path"].lower().endswith((".md", ".markdown"))
                        else ([], False)
                    )
                    response["truncated"] |= clipped

                    def candidates() -> Iterator[tuple[dict[str, Any], str, dict[str, Any], bool]]:
                        # A generator is essential: the edge cap must stop BEFORE
                        # loading any subsequent target or historical payload.
                        for href, line in links:
                            target = self._resolve(db, token, item, href, budget=budget)
                            if target:
                                yield (
                                    target,
                                    "explicit_reference",
                                    {"href": href, "line": line},
                                    True,
                                )
                        for index, key in enumerate(item.get("upstream", [])[:100]):
                            try:
                                target = self._current(db, token, key, budget=budget)
                            except KnowledgeConflict:
                                continue
                            yield (
                                target,
                                "derived_from",
                                {
                                    "field": "upstream",
                                    "index": index,
                                    "relation_scope": "asset",
                                    "target_revision_binding": "current_lookup",
                                    "original_target_revision": None,
                                },
                                True,
                            )
                        history, clipped_history = self._history(db, token, item, budget=budget)
                        response["truncated"] |= clipped_history
                        for old in history:
                            yield (
                                old,
                                "previous_version",
                                {
                                    "field": "changes",
                                    "previous_revision": old["revision"],
                                },
                                False,
                            )

                    for target, relation, evidence, current in candidates():
                        key = (target["asset_id"], target["revision"])
                        if key == (item["asset_id"], item["revision"]):
                            continue
                        edge_key = (item["asset_id"], item["revision"], *key, relation)
                        if edge_key in edge_keys:
                            continue
                        if current:
                            try:
                                self._document(db, token, target, budget=budget)
                            except KnowledgeConflict:
                                continue
                        else:
                            # Historical metadata inherits the current asset's
                            # access, not permission to reopen a deleted source.
                            self._current(db, token, target["asset_id"], budget=budget)
                        edge_keys.add(edge_key)
                        response["edges"].append(
                            {
                                "from": {
                                    "asset_id": item["asset_id"],
                                    "revision": item["revision"],
                                },
                                "to": {"asset_id": key[0], "revision": key[1]},
                                "type": relation,
                                "evidence": {
                                    "asset_id": item["asset_id"],
                                    "revision": item["revision"],
                                    **evidence,
                                },
                            }
                        )
                        if key not in seen:
                            seen.add(key)
                            response["items"].append(_node(target, current=current))
                            if current and level + 1 < data.depth:
                                queue.append((target, level + 1))
                        if len(response["edges"]) >= data.limit:
                            # Conservatively report the reached cap without
                            # pulling another candidate just to prove truncation.
                            response["truncated"] = True
                            return response
            except _GraphLimit:
                response["truncated"] = True
            return response

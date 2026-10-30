"""Durable native-source selection, upload outbox and organization replica."""

from __future__ import annotations

from typing import Any

import json
import time
import uuid
from pathlib import Path
from .common import atomic_json, digest, tokens
from .storage import database, CLIENT_SCHEMA
from .transport import Transport
from .contracts import SyncTransport

from .source import source_text, read_document, files, enqueue_document, queue_delete


class NativeClient:
    """One enrolled device profile; all local state is scoped to its Hub identity.

    File observation, queueing and replica commits are local transactions. Network
    calls run outside those transactions so a slow Hub cannot hold a Hook's lock.
    Product-specific discovery/projection belongs in the adapter modules, not here.
    """

    def __init__(
        self, root: Path, *, transport: SyncTransport | None = None, identity: Any = None
    ) -> None:
        self.root = Path(root)
        self.path = self.root / "state.sqlite"
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        config_path = self.root / "config.json"
        self.config = json.loads(config_path.read_text()) if config_path.exists() else {}
        self.identity = identity or self.config["identity"]
        self.transport: SyncTransport = transport or Transport(
            self.config["hub"], self.identity["token"]
        )
        with database(self.path) as db:
            db.executescript(CLIENT_SCHEMA)

    @classmethod
    def join(
        cls,
        root: Path,
        url: str,
        invite: str,
        device_name: str,
        *,
        expected_identity: dict[str, str] | None = None,
        timeout: float = 10,
    ) -> NativeClient:
        """Enroll a new device once; refuse to reuse a profile belonging to another Hub/identity."""
        if (Path(root) / "config.json").exists():
            raise ValueError("profile already joined; choose a new profile for another Hub/device")
        transport = Transport(url, timeout=timeout)
        health = transport.call("health")
        if health.get("protocol") != 1 or health.get("service") != "amh-native":
            raise ValueError("the address is not a compatible native-memory Hub")
        identity = transport.enroll(invite, device_name)
        if not isinstance(identity, dict) or any(
            not isinstance(identity.get(k), str) or not identity[k]
            for k in ("tenant_id", "member_id", "member_name", "device_id", "token")
        ):
            raise ValueError("invalid enrollment response; local identity was not replaced")
        if expected_identity and any(identity.get(k) != v for k, v in expected_identity.items()):
            raise PermissionError("runtime enrollment does not match the pinned member")
        atomic_json(
            Path(root) / "config.json", {"hub": transport.url, "identity": identity, "targets": []}
        )
        return cls(root)

    def sources(self) -> list[dict[str, Any]]:
        """Return registered source metadata without reading or uploading source bodies."""
        with database(self.path) as db:
            return [dict(r) for r in db.execute("SELECT * FROM sources")]

    def targets(self) -> list[dict[str, Any]]:
        """Merge configured/discovered native destinations by resolved path to avoid duplicate writes."""
        with database(self.path) as db:
            dynamic = [dict(row) for row in db.execute("SELECT root,product FROM dynamic_targets")]
            quarantined_roots = {
                row["root"]
                for row in db.execute(
                    "SELECT s.root FROM sources s JOIN meta m ON m.key='quarantined-source:' || s.id"
                )
            }
        return list(
            {
                str(Path(row["root"]).resolve()): row
                for row in self.config.get("targets", []) + dynamic
                if str(Path(row["root"]).resolve()) not in quarantined_roots
            }.values()
        )

    def register(
        self,
        root: Path,
        product: str,
        project: str,
        kind: str,
        *,
        fresh_files: set[str] | None = None,
    ) -> str:
        """Bind a source and baseline existing files as excluded history in one transaction.

        fresh_files is reserved for successful adapter-observed creation events; it is
        not a general bypass of history selection. Re-registering a root is idempotent.
        """
        root = Path(root).expanduser().resolve()
        if not root.exists():
            raise ValueError("source file or directory does not exist")
        from .common import PRODUCTS

        if product not in PRODUCTS or kind not in (
            "memory",
            "transcript",
            "rule",
            "skill",
            "config",
        ):
            raise ValueError("invalid source")
        with database(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            old = db.execute(
                "SELECT * FROM sources WHERE root=? AND kind=?", (str(root), kind)
            ).fetchone()
            if old:
                return str(old["id"])
            sid = "src_" + uuid.uuid4().hex
            baseline = []
            for p in files(root, kind):
                try:
                    document = read_document(p, kind)
                    body = document[0] if document else None
                    fingerprint = document[2] if document else ""
                except (ValueError, OSError, UnicodeError):
                    body = "<unreadable-existing-file>"
                    fingerprint = digest(body)
                if body is not None:
                    relative = p.name if root.is_file() else str(p.relative_to(root))
                    fresh = relative in (fresh_files or set())
                    if not fresh:
                        db.execute("INSERT OR IGNORE INTO history_hashes VALUES(?)", (fingerprint,))
                    stat = p.stat()
                    db.execute(
                        "INSERT OR REPLACE INTO file_identity VALUES(?,?,?,?)",
                        (sid, relative, stat.st_dev, stat.st_ino),
                    )
                    baseline.append(
                        (
                            sid,
                            p.name if root.is_file() else str(p.relative_to(root)),
                            "" if fresh else fingerprint,
                            int(fresh),
                            "asset_" + uuid.uuid4().hex,
                            0,
                            int(not fresh),
                        )
                    )
            db.execute(
                "INSERT INTO sources VALUES(?,?,?,?,?,?)",
                (sid, str(root), product, project, kind, int(root.is_file())),
            )
            db.executemany("INSERT INTO snapshots VALUES(?,?,?,?,?,?,?)", baseline)
        return sid

    def preview(self, sid: str) -> list[dict[str, Any]]:
        """Read current local history only; returned hashes bind explicit selection to this preview."""
        if sid in self.quarantined_sources():
            raise ValueError(
                "source identity changed; rebind it in a separate profile before import"
            )
        with database(self.path) as db:
            source = db.execute("SELECT * FROM sources WHERE id=?", (sid,)).fetchone()
            if source is None:
                raise ValueError("source not found")
            rows = db.execute("SELECT * FROM snapshots WHERE source=?", (sid,)).fetchall()
            result = []
            for row in rows:
                p = (
                    Path(source["root"])
                    if source["file_source"]
                    else Path(source["root"]) / row["path"]
                )
                try:
                    body = source_text(p, source["kind"])
                except (ValueError, OSError, UnicodeError):
                    continue
                if body is not None:
                    result.append(
                        {
                            "path": row["path"],
                            "hash": digest(body),
                            "selected": bool(row["selected"]),
                            "bytes": len(body.encode()),
                            "body": body,
                        }
                    )
            return result

    def select(self, sid: str, selections: dict[str, str]) -> None:
        """Select only previewed matching versions; stale manifests require a fresh preview."""
        preview = {r["path"]: r for r in self.preview(sid)}
        if any(p not in preview or preview[p]["hash"] != h for p, h in selections.items()):
            raise ValueError("history changed; preview again")
        with database(self.path) as db:
            for p in selections:
                db.execute(
                    "UPDATE snapshots SET selected=1,hash=? WHERE source=? AND path=?", ("", sid, p)
                )

    def refresh_excluded_history(self) -> None:
        """Remember all observed excluded versions before scanning possible renamed/copied files.

        Refresh all excluded sources first so copy order cannot turn edited history
        into a new upload. Hashes of older excluded versions remain.
        """
        quarantined = self.quarantined_sources()
        for source in self.sources():
            if source["id"] in quarantined:
                continue
            root = Path(source["root"])
            if not root.exists():
                continue
            for p in files(root, source["kind"]):
                rel = p.name if source["file_source"] else str(p.relative_to(root))
                stat = p.stat()
                with database(self.path) as db:
                    denied = db.execute(
                        "SELECT 1 FROM snapshots WHERE source=? AND path=? AND selected=0 UNION SELECT 1 FROM file_identity i JOIN snapshots s ON s.source=i.source AND s.path=i.path WHERE s.selected=0 AND i.device=? AND i.inode=? LIMIT 1",
                        (source["id"], rel, stat.st_dev, stat.st_ino),
                    ).fetchone()
                    if not denied:
                        continue
                    key = "history-stat:" + source["id"] + ":" + rel
                    stamp = f"{stat.st_mtime_ns}:{stat.st_ctime_ns}:{stat.st_size}:{stat.st_ino}"
                    previous = db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
                if previous and previous["value"] == stamp:
                    continue
                try:
                    document = read_document(p, source["kind"])
                except (OSError, ValueError, UnicodeError):
                    continue
                if document:
                    with database(self.path) as db:
                        db.execute("INSERT OR IGNORE INTO history_hashes VALUES(?)", (document[2],))
                        db.execute("INSERT OR REPLACE INTO meta VALUES(?,?)", (key, stamp))
                        db.execute(
                            "UPDATE snapshots SET hash=? WHERE source=? AND path=? AND selected=0",
                            (document[2], source["id"], rel),
                        )

    def scan(self) -> list[str]:
        """Persist source changes to the outbox without network I/O; return local diagnostics.

        Unselected history stays excluded. Missing source mounts do not imply deletion;
        within available sources, disappeared owned parts become versioned tombstones.
        """
        from .runtime_sources import quarantine_changed_roots

        errors = quarantine_changed_roots(self)
        self.refresh_excluded_history()
        quarantined = self.quarantined_sources()
        for source in self.sources():
            if source["id"] in quarantined:
                errors.append("source identity changed: " + source["id"])
                continue
            root = Path(source["root"])
            if not (root.parent.is_dir() if source["file_source"] else root.is_dir()):
                errors.append("source unavailable: " + source["id"])
                continue
            seen = set()
            for p in files(root, source["kind"]):
                rel = p.name if source["file_source"] else str(p.relative_to(root))
                seen.add(rel)
                with database(self.path) as db:
                    row = db.execute(
                        "SELECT * FROM snapshots WHERE source=? AND path=?", (source["id"], rel)
                    ).fetchone()
                if row is not None and not row["selected"]:
                    stat = p.stat()
                    stamp = f"{stat.st_mtime_ns}:{stat.st_ctime_ns}:{stat.st_size}:{stat.st_ino}"
                    key = "history-stat:" + source["id"] + ":" + rel
                    with database(self.path) as db:
                        previous_stamp = db.execute(
                            "SELECT value FROM meta WHERE key=?", (key,)
                        ).fetchone()
                        db.execute(
                            "INSERT OR REPLACE INTO file_identity VALUES(?,?,?,?)",
                            (source["id"], rel, stat.st_dev, stat.st_ino),
                        )
                    if not previous_stamp or previous_stamp["value"] != stamp:
                        try:
                            excluded = read_document(p, source["kind"])
                        except (OSError, ValueError, UnicodeError):
                            excluded = None
                        if excluded:
                            with database(self.path) as db:
                                db.execute(
                                    "INSERT OR IGNORE INTO history_hashes VALUES(?)", (excluded[2],)
                                )
                                db.execute(
                                    "UPDATE snapshots SET hash=? WHERE source=? AND path=?",
                                    (excluded[2], source["id"], rel),
                                )
                                db.execute("INSERT OR REPLACE INTO meta VALUES(?,?)", (key, stamp))
                    continue
                try:
                    doc = read_document(p, source["kind"])
                except (ValueError, OSError, UnicodeError) as exc:
                    errors.append(str(exc))
                    continue
                if doc is None:
                    continue
                body, encoding, h = doc
                if row is not None and row["hash"] == "" and not row["unknown"]:
                    with database(self.path) as db:
                        prior_parts = db.execute(
                            "SELECT 1 FROM source_parts WHERE base=? LIMIT 1", (row["asset"],)
                        ).fetchone()
                        excluded_copy = db.execute(
                            "SELECT 1 FROM history_hashes WHERE hash=?", (h,)
                        ).fetchone()
                        if not prior_parts and excluded_copy:
                            db.execute(
                                "UPDATE snapshots SET selected=0,unknown=1,hash=? WHERE source=? AND path=?",
                                (h, source["id"], rel),
                            )
                            db.execute(
                                "INSERT OR REPLACE INTO aliases VALUES(?,?,?,?)",
                                (source["id"], rel, "[]", "needs-history-review"),
                            )
                    if not prior_parts and excluded_copy:
                        errors.append("history review required: " + source["id"] + "/" + rel)
                        continue
                if row is not None and row["hash"] == h:
                    continue
                stat = p.stat()
                if row is None:
                    with database(self.path) as db:
                        history = db.execute(
                            "SELECT 1 FROM history_hashes WHERE hash=? UNION SELECT 1 FROM file_identity i JOIN snapshots s ON s.source=i.source AND s.path=i.path WHERE s.selected=0 AND i.device=? AND i.inode=? LIMIT 1",
                            (h, stat.st_dev, stat.st_ino),
                        ).fetchone()
                        if history:
                            db.execute(
                                "INSERT INTO snapshots VALUES(?,?,?,?,?,?,?)",
                                (source["id"], rel, h, 0, "asset_" + uuid.uuid4().hex, 0, 1),
                            )
                            db.execute(
                                "INSERT OR REPLACE INTO file_identity VALUES(?,?,?,?)",
                                (source["id"], rel, stat.st_dev, stat.st_ino),
                            )
                    if history:
                        continue
                if source["kind"] == "memory" and body.strip() in ("", "v1"):
                    if row is not None:
                        with database(self.path) as db:
                            for part in db.execute(
                                "SELECT * FROM source_parts WHERE base=?", (row["asset"],)
                            ).fetchall():
                                queue_delete(db, part)
                            db.execute(
                                "UPDATE snapshots SET hash=? WHERE source=? AND path=?",
                                (h, source["id"], rel),
                            )
                    continue
                aid = row["asset"] if row else "asset_" + uuid.uuid4().hex
                unknown = bool(row["unknown"]) if row else False
                if source["kind"] == "memory" and not unknown:
                    from .lineage import classify_copy

                    action, refs = classify_copy(self, h)
                    if action:
                        with database(self.path) as db:
                            if row is not None:
                                for old_part in db.execute(
                                    "SELECT * FROM source_parts WHERE base=?", (row["asset"],)
                                ).fetchall():
                                    queue_delete(db, old_part)
                            if action != "alias":
                                aid = "asset_" + uuid.uuid4().hex
                            db.execute(
                                "INSERT OR REPLACE INTO snapshots VALUES(?,?,?,?,?,?,?)",
                                (
                                    source["id"],
                                    rel,
                                    h,
                                    int(action == "alias"),
                                    aid,
                                    0,
                                    int(action != "alias"),
                                ),
                            )
                            db.execute(
                                "INSERT OR REPLACE INTO aliases VALUES(?,?,?,?)",
                                (source["id"], rel, json.dumps(refs), action),
                            )
                        if action != "alias":
                            errors.append("origin review required: " + source["id"] + "/" + rel)
                        continue
                with database(self.path) as db:
                    alias = db.execute(
                        "SELECT refs FROM aliases WHERE source=? AND path=? AND status='alias'",
                        (source["id"], rel),
                    ).fetchone()
                    upstream = json.loads(alias["refs"]) if alias else []
                    enqueue_document(db, source, rel, aid, doc, unknown, upstream)
                    db.execute(
                        "INSERT OR REPLACE INTO snapshots VALUES(?,?,?,?,?,?,?)",
                        (source["id"], rel, h, 1, aid, 0, int(unknown)),
                    )
            with database(self.path) as db:
                rows = db.execute(
                    "SELECT * FROM snapshots WHERE source=? AND selected=1", (source["id"],)
                ).fetchall()
                for row in rows:
                    if row["path"] in seen or row["hash"] == "<deleted>":
                        continue
                    for part in db.execute(
                        "SELECT * FROM source_parts WHERE base=?", (row["asset"],)
                    ).fetchall():
                        queue_delete(db, part)
                    db.execute(
                        "UPDATE snapshots SET hash=? WHERE source=? AND path=?",
                        ("<deleted>", source["id"], row["path"]),
                    )
        errors.extend(quarantine_changed_roots(self))
        return errors

    def origin_reviews(self) -> list[dict[str, Any]]:
        """Return copies whose origin/history needs review; they remain excluded from upload."""
        with database(self.path) as db:
            return [
                dict(row)
                for row in db.execute(
                    "SELECT source,path,refs,status FROM aliases WHERE status IN ('needs-origin-review','needs-history-review')"
                )
            ]

    def pending_count(self) -> int:
        """Count queued asset parts, not source files or total historical revisions."""
        with database(self.path) as db:
            return int(db.execute("SELECT count(*) FROM outbox").fetchone()[0])

    def quarantined_sources(self) -> set[str]:
        """Sources whose runtime identity changed cannot scan or upload ambiguous data."""
        prefix = "quarantined-source:"
        with database(self.path) as db:
            return {
                row["key"][len(prefix) :]
                for row in db.execute("SELECT key FROM meta WHERE key LIKE ?", (prefix + "%",))
            }

    def sync(self) -> dict[str, Any]:
        """Capture locally, upload, replay changes, then publish native resources.

        A connection/auth failure stops this pass without removing queued data.
        Record conflicts remain pending while unrelated records can progress.
        The caller owns retry scheduling and revoked-device cleanup.
        """
        import fcntl

        with (self.root / "worker.lock").open("a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return {"busy": True}
            from .runtime_sources import process_observations, quarantine_changed_roots

            errors = process_observations(self)
            errors.extend(self.scan())  # Persist source changes before any network request.
            self.transport.me()  # revoked devices cannot use this as a successful refresh
            errors.extend(quarantine_changed_roots(self))
            pushed = 0
            with database(self.path) as db:
                pending = db.execute("SELECT * FROM outbox").fetchall()
            quarantined = self.quarantined_sources()
            for row in pending:
                item = json.loads(row["payload"])
                if item["source_store"] in quarantined:
                    continue
                try:
                    saved = self.transport.put(item)
                except ValueError as exc:
                    errors.append(f"upload {item['asset_id']}: {type(exc).__name__}")
                    continue
                # Connection/auth failures propagate immediately: N queued assets
                # must not turn an outage into N socket timeouts (or hide revocation).
                with database(self.path) as db:
                    db.execute(
                        "UPDATE source_parts SET revision=? WHERE asset=?",
                        (saved["revision"], item["asset_id"]),
                    )
                    db.execute(
                        "DELETE FROM outbox WHERE asset=? AND hash=?",
                        (item["asset_id"], row["hash"]),
                    )
                pushed += 1
            # Upload acknowledgement can be lost after the Hub commits. Replaying
            # our changes repairs the base revision before the next idempotent retry.
            pages = 0
            while pages < 100:
                with database(self.path) as db:
                    row = db.execute("SELECT value FROM meta WHERE key='cursor'").fetchone()
                cursor = int(row["value"]) if row else 0
                response = self.transport.changes(cursor)
                with database(self.path) as db:
                    for item in response["items"]:
                        if item["source_device_id"] == self.identity["device_id"]:
                            db.execute(
                                "UPDATE source_parts SET revision=MAX(revision,?) WHERE asset=?",
                                (item["revision"], item["asset_id"]),
                            )
                            queued = db.execute(
                                "SELECT payload FROM outbox WHERE asset=?", (item["asset_id"],)
                            ).fetchone()
                            if queued:
                                desired = json.loads(queued["payload"])
                                if desired["base_revision"] < item["revision"]:
                                    desired["base_revision"] = item["revision"]
                                    encoded = json.dumps(desired, ensure_ascii=False)
                                    db.execute(
                                        "UPDATE outbox SET payload=?,hash=? WHERE asset=?",
                                        (encoded, digest(encoded), item["asset_id"]),
                                    )
                        db.execute(
                            "INSERT OR REPLACE INTO replica VALUES(?,?)",
                            (item["asset_id"], json.dumps(item, ensure_ascii=False)),
                        )
                    # Replica rows and cursor are one commit: a crash cannot skip
                    # data by advancing the cursor ahead of its applied page.
                    db.execute(
                        "INSERT OR REPLACE INTO meta VALUES('cursor',?)", (str(response["cursor"]),)
                    )
                    db.execute(
                        "INSERT OR REPLACE INTO meta VALUES('last_auth',?)", (str(time.time()),)
                    )
                pages += 1
                if not response["items"]:
                    break
            from .projection import publish

            for target in self.targets():
                result = publish(Path(target["root"]), target["product"], self.cached())
                errors.extend(result["conflicts"])
            atomic_json(
                self.root / "status.json",
                {
                    "last_sync": time.time(),
                    "pushed": pushed,
                    "pending": self.pending_count(),
                    "origin_review_count": len(self.origin_reviews()),
                    "errors": errors,
                },
            )
            return {"pushed": pushed, "errors": errors}

    def cached(self) -> list[dict[str, Any]]:
        """Return live replica parts; consumers must verify multipart completeness before applying."""
        with database(self.path) as db:
            items = [json.loads(r["payload"]) for r in db.execute("SELECT payload FROM replica")]
        return [i for i in items if not i["deleted"]]

    def revoke_local(self) -> None:
        """Clear authorized recall state and untouched managed projections; retain local source/outbox data."""
        with database(self.path) as db:
            db.execute("DELETE FROM replica")
            db.execute("DELETE FROM recall_sources")
            db.execute("DELETE FROM meta WHERE key='last_auth'")
        from .projection import publish

        cache = self.root / "recall-cache"
        if cache.is_dir() and not cache.is_symlink():
            for path in cache.glob("*.md"):
                if not path.is_symlink() and digest(path.read_text()) == path.stem:
                    path.unlink()
        for target in self.targets():
            publish(Path(target["root"]), target["product"], [])

    def search(self, query: str, **kwargs: Any) -> list[dict[str, Any]]:
        """Query the enrolled organization through the injected transport; do not impose project scope."""
        return list(self.transport.search(query, **kwargs))

    def local_search(self, query: str, project_filter: str | None = None) -> list[dict[str, Any]]:
        """Rank cached parts deterministically; the caller must enforce the offline authorization window."""
        from .materialize import assembled

        records = self.cached()
        # A sync page may stop halfway through a document. Match the Hub's
        # completeness rule while retaining part identities for recall coverage.
        ready = {
            (document["tenant_id"], asset_id, revision)
            for document in assembled(records)
            for asset_id, revision in document["assembled_revisions"]
        }
        wanted = set(tokens(query))
        items = []
        for r in records:
            if (r["tenant_id"], r["asset_id"], r["revision"]) not in ready:
                continue
            if project_filter is not None and r["project"] != project_filter:
                continue
            searchable = r.get("index_text") if r["kind"] == "transcript" else None
            if searchable is None:
                searchable = r["body"]
            if not searchable.strip():
                continue
            score = len(wanted & set(tokens(r["title"] + " " + searchable)))
            if score:
                items.append((score, r))
        return [r for _, r in sorted(items, key=lambda x: x[0], reverse=True)[:12]]

"""Organization-scoped immutable revisions, identity and deterministic full-text recall."""

from __future__ import annotations

from typing import Any
import sqlite3

import json
import os
import secrets
import time
import uuid
from pathlib import Path
from .common import digest, tokens
from .storage import database, HUB_SCHEMA, DB_TIMEOUT
from .models import AssetInput


class Hub:
    """Single-node authority; each mutation commits assets, changes and FTS together.

    No connection is shared across requests. BEGIN IMMEDIATE serializes identity
    and revision checks with their writes, including simultaneous retries. Multiple
    machines must not independently write copies of this SQLite database.
    """

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        with database(self.path) as db:
            db.executescript(HUB_SCHEMA)

    @property
    def tenant(self) -> str:
        """Organization scope for enterprise logins.

        Resolved per call so the operator page's setting applies without a
        restart, and so an environment pin still wins. Invitations carry their
        own tenant and are unaffected.
        """
        pinned = os.environ.get("FEISHU_TENANT", "").strip()
        return (pinned or self.setting("feishu_tenant") or "linkcrux")[:128]

    def ready(self) -> None:
        """Probe schema/index access and writer-lock acquisition within 100ms of waits.

        This is a readiness signal, not a full disk-integrity or capacity check.
        Never initialize missing tables here: an empty replacement DB is not ready.
        """
        timeout = DB_TIMEOUT.set(0.1)
        try:
            with database(self.path, create=False) as db:
                db.execute("BEGIN IMMEDIATE")
                for table in (
                    "members",
                    "runtime_invites",
                    "devices",
                    "assets",
                    "changes",
                    "search_index",
                ):
                    db.execute(f"SELECT 1 FROM {table} LIMIT 1").fetchone()
                db.rollback()
        finally:
            DB_TIMEOUT.reset(timeout)

    def provision(self, name: str, device_name: str, *, tenant: str = "") -> dict[str, Any]:
        """Create or reuse a member by exact name and issue one device.

        There is no invitation secret: a member is identified by their Feishu
        account in normal use, and this exists for the host operator to recover
        someone. Only one unlinked member with that name is reused, so two
        colleagues who share a display name stay separate people.

        ``tenant`` is for isolation tests and host-side recovery of a member in
        another scope; it never comes from a request.
        """
        if not name.strip() or len(name) > 160 or not device_name.strip():
            raise ValueError("member and device name required")
        with database(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            tenant = (tenant.strip() or self.tenant)[:128]
            candidates = db.execute(
                "SELECT id FROM members WHERE tenant=? AND name=? AND id NOT IN "
                "(SELECT member FROM feishu_members)",
                (tenant, name),
            ).fetchall()
            if len(candidates) == 1:
                member = candidates[0]["id"]
            else:
                member = "mem_" + uuid.uuid4().hex
                db.execute("INSERT INTO members VALUES(?,?,?)", (member, tenant, name))
            device, token = "dev_" + uuid.uuid4().hex, secrets.token_urlsafe(32)
            db.execute(
                "INSERT INTO devices VALUES(?,?,?,?,0)",
                (device, member, digest(token), device_name),
            )
            return dict(
                tenant_id=tenant,
                member_id=member,
                member_name=name,
                device_id=device,
                token=token,
            )

    # ---- operator surface -------------------------------------------------
    # The admin credential is minted locally on the Hub host (never over HTTP)
    # and stored only as a digest. It is deliberately separate from device
    # tokens: revoking a device must never grant or remove operator access.

    def admin_create(self, label: str) -> str:
        """Create one operator credential and return it once."""
        if not label.strip() or len(label) > 80:
            raise ValueError("admin label required")
        token = secrets.token_urlsafe(32)
        with database(self.path) as db:
            db.execute("INSERT INTO admins VALUES(?,?,?,0)", (digest(token), label, time.time()))
        return token

    def _admin(self, db: sqlite3.Connection, token: str) -> None:
        row = db.execute(
            "SELECT 1 FROM admins WHERE hash=? AND revoked=0", (digest(token),)
        ).fetchone()
        if row is None:
            raise PermissionError("operator credential invalid or revoked")

    def admin_overview(self, token: str) -> dict[str, Any]:
        """Members, devices and pending invitations; never includes any secret."""
        with database(self.path) as db:
            self._admin(db, token)
            members = [
                dict(row)
                for row in db.execute(
                    "SELECT m.id,m.name,m.tenant,"
                    "(SELECT count(*) FROM devices d WHERE d.member=m.id AND d.revoked=0) devices,"
                    "(SELECT count(*) FROM assets a WHERE a.tenant=m.tenant "
                    "AND json_extract(a.payload,'$.producer_member_id')=m.id) assets "
                    "FROM members m ORDER BY m.name"
                )
            ]
            devices = [
                dict(row)
                for row in db.execute(
                    "SELECT d.id,d.name,d.revoked,d.member,m.name member_name "
                    "FROM devices d LEFT JOIN members m ON m.id=d.member ORDER BY d.name"
                )
            ]
            settings = {
                row["key"]: row["value"]
                for row in db.execute(
                    "SELECT key,value FROM settings WHERE key IN "
                    "('feishu_app_id','amh_hub_public_url','feishu_tenant')"
                )
            }
        return {"members": members, "devices": devices, "settings": settings}

    def admin_dashboard(self, token: str, *, days: int = 30) -> dict[str, Any]:
        """Aggregates for the operator dashboard; counts and metadata only, never memory text.

        Every number here is derived from what devices actually uploaded, so a
        member who joins and shares nothing correctly shows zero rather than
        being counted as active.
        """
        days = max(1, min(int(days), 365))
        now = time.time()
        cutoff = now - days * 86400
        with database(self.path) as db:
            self._admin(db, token)
            # Scope to this Hub's tenant: the dashboard belongs to one
            # organization, and an unfiltered read pulled every row in the
            # database into memory on each page load.
            scope = self.tenant
            members = {
                r["id"]: dict(r)
                for r in db.execute("SELECT * FROM members WHERE tenant=?", (scope,))
            }
            devices = [
                dict(r)
                for r in db.execute(
                    "SELECT d.* FROM devices d JOIN members m ON m.id=d.member WHERE m.tenant=?",
                    (scope,),
                )
            ]
            rows = db.execute("SELECT payload FROM assets WHERE tenant=?", (scope,)).fetchall()
        assets = []
        for row in rows:
            try:
                assets.append(json.loads(row["payload"]))
            except (TypeError, ValueError):
                continue
        live = [a for a in assets if not a.get("deleted")]

        def bucket() -> dict[str, Any]:
            return {
                "assets": 0,
                "bytes": 0,
                "revisions": 0,
                "products": {},
                "kinds": {},
                "last": 0.0,
            }

        by_member: dict[str, dict[str, Any]] = {}
        by_device: dict[str, dict[str, Any]] = {}
        by_product: dict[str, dict[str, Any]] = {}
        by_kind: dict[str, int] = {}
        by_day: dict[str, dict[str, int]] = {}
        by_hour = [0] * 24
        projects: dict[str, int] = {}

        for a in live:
            producer = a.get("producer_member_id") or ""
            device = a.get("source_device_id") or ""
            product = a.get("product") or "unknown"
            kind = a.get("kind") or "memory"
            observed = float(a.get("observed_at") or 0)
            size = len((a.get("body") or "").encode())

            def add(store: dict[str, Any], key: str, label: str) -> None:
                item = store.setdefault(key, bucket())
                item["label"] = label
                item["assets"] += 1
                item["bytes"] += size
                item["revisions"] += max(1, int(a.get("revision") or 1))
                item["products"][product] = item["products"].get(product, 0) + 1
                item["kinds"][kind] = item["kinds"].get(kind, 0) + 1
                item["last"] = max(item["last"], observed)

            add(by_member, producer, (members.get(producer) or {}).get("name") or "未知来源")
            add(by_device, device, next((d["name"] for d in devices if d["id"] == device), device))
            add(by_product, product, product)

            by_kind[kind] = by_kind.get(kind, 0) + 1
            project = (a.get("project") or "").strip()
            if project:
                projects[project] = projects.get(project, 0) + 1
            if observed >= cutoff:
                day = time.strftime("%Y-%m-%d", time.localtime(observed))
                entry = by_day.setdefault(day, {"assets": 0, "bytes": 0})
                entry["assets"] += 1
                entry["bytes"] += size
            if observed:
                by_hour[time.localtime(observed).tm_hour] += 1

        def ranked(store: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
            out = []
            for key, item in store.items():
                out.append(
                    {
                        "key": key,
                        "label": item.get("label") or key or "未知",
                        "assets": item["assets"],
                        "bytes": item["bytes"],
                        "revisions": item["revisions"],
                        "products": item["products"],
                        "kinds": item["kinds"],
                        "last": item["last"],
                    }
                )
            out.sort(key=lambda r: (-r["assets"], -r["bytes"], r["label"]))
            return out

        day_series = []
        for offset in range(days - 1, -1, -1):
            day = time.strftime("%Y-%m-%d", time.localtime(now - offset * 86400))
            entry = by_day.get(day, {"assets": 0, "bytes": 0})
            day_series.append({"day": day, "assets": entry["assets"], "bytes": entry["bytes"]})

        active_devices = [d for d in devices if not d.get("revoked")]
        return {
            "generated_at": now,
            "window_days": days,
            "totals": {
                "members": len(members),
                "devices": len(devices),
                "active_devices": len(active_devices),
                "assets": len(live),
                "revisions": sum(max(1, int(a.get("revision") or 1)) for a in live),
                "bytes": sum(len((a.get("body") or "").encode()) for a in live),
                "projects": len(projects),
                "in_window": sum(e["assets"] for e in day_series),
                "last_upload": max((float(a.get("observed_at") or 0) for a in live), default=0),
            },
            "by_member": ranked(by_member),
            "by_device": ranked(by_device),
            "by_product": ranked(by_product),
            "by_kind": sorted(
                ({"kind": k, "assets": v} for k, v in by_kind.items()),
                key=lambda r: -r["assets"],
            ),
            "by_day": day_series,
            "by_hour": by_hour,
            "top_projects": sorted(
                ({"project": k, "assets": v} for k, v in projects.items()),
                key=lambda r: -r["assets"],
            )[:10],
            "devices": [
                {
                    "id": d["id"],
                    "name": d["name"],
                    "member": (members.get(d["member"]) or {}).get("name") or d["member"],
                    "revoked": bool(d["revoked"]),
                }
                for d in devices
            ],
        }

    def admin_device(self, token: str, name: str, device_name: str) -> dict[str, Any]:
        """Issue a device for a named member; the only way in is a Feishu account.

        Exists for the host operator to recover a member whose Feishu login is
        unavailable, not as a general onboarding path.
        """
        with database(self.path) as db:
            self._admin(db, token)
            row = db.execute("SELECT tenant FROM members WHERE name=? LIMIT 2", (name,)).fetchall()
        # Recover in the member's own tenant; defaulting to this Hub's tenant
        # silently created a second, empty identity under the same name.
        tenant = row[0]["tenant"] if len(row) == 1 else ""
        return self.provision(name, device_name, tenant=tenant)

    def admin_revoke(self, token: str, device_id: str) -> None:
        with database(self.path) as db:
            self._admin(db, token)
        self.revoke(device_id)

    def admin_setting(self, token: str, key: str, value: str) -> None:
        """Store deployment settings; an empty value clears the key."""
        allowed = {"feishu_app_id", "amh_hub_public_url", "feishu_tenant"}
        if key not in allowed:
            raise ValueError("unknown setting")
        with database(self.path) as db:
            self._admin(db, token)
            if value:
                db.execute("INSERT OR REPLACE INTO settings VALUES(?,?)", (key, value))
            else:
                db.execute("DELETE FROM settings WHERE key=?", (key,))

    def admin_secret_set(self, token: str, app_secret: str) -> None:
        """Store the application secret separately so it is never read back by the page."""
        if not app_secret.strip():
            raise ValueError("app secret required")
        with database(self.path) as db:
            self._admin(db, token)
            db.execute(
                "INSERT OR REPLACE INTO settings VALUES('feishu_app_secret',?)", (app_secret,)
            )

    def setting(self, key: str) -> str:
        """Read one deployment setting; used by login when environment variables are absent."""
        with database(self.path) as db:
            row = db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return str(row["value"]) if row else ""

    def enroll(self, ticket: str, device_name: str) -> dict[str, Any]:
        """Consume a runtime pairing ticket and issue one device identity.

        Only same-member runtime pairing remains: joining the organization goes
        through Feishu login, so no reusable member invitation secret exists to
        forward, leak or brute-force.
        """
        with database(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT m.*,i.used,i.expires FROM runtime_invites i "
                "JOIN members m ON m.id=i.member "
                "JOIN devices d ON d.id=i.sponsor_device AND d.member=m.id "
                "WHERE i.hash=? AND d.revoked=0",
                (digest(ticket),),
            ).fetchone()
            if row is None or row["used"] or row["expires"] < time.time():
                raise ValueError("pairing ticket invalid, expired or used")
            db.execute("UPDATE runtime_invites SET used=1 WHERE hash=?", (digest(ticket),))
            device, token = "dev_" + uuid.uuid4().hex, secrets.token_urlsafe(32)
            db.execute(
                "INSERT INTO devices VALUES(?,?,?,?,0)",
                (device, row["id"], digest(token), device_name),
            )
            return dict(
                tenant_id=row["tenant"],
                member_id=row["id"],
                member_name=row["name"],
                device_id=device,
                token=token,
            )

    def feishu_begin(self, port: int) -> str:
        """Issue a one-use state bound to the caller's loopback port.

        The port is what the browser will be sent back to, so it is stored
        server-side; a caller cannot substitute a foreign redirect target.
        """
        if not 1 <= port <= 65535:
            raise ValueError("loopback port required")
        state = secrets.token_urlsafe(32)
        with database(self.path) as db:
            # This endpoint is unauthenticated; drop dead rows on the way in so
            # abandoned logins cannot grow the table for free.
            db.execute("DELETE FROM feishu_states WHERE expires<?", (time.time(),))
            db.execute(
                "INSERT INTO feishu_states VALUES(?,?,?,0)",
                (digest(state), port, time.time() + 300),
            )
        return state

    def feishu_check(self, state: str) -> None:
        """Reject an unknown or expired state before any outbound call is made.

        The callback is public; without this, anyone could drive the Hub to
        spend Feishu's rate limit by posting invented states. Consumption stays
        in ``feishu_complete`` so a failed code exchange does not burn a state
        that a retry could still use.
        """
        with database(self.path) as db:
            row = db.execute(
                "SELECT used,expires FROM feishu_states WHERE hash=?", (digest(state),)
            ).fetchone()
        if row is None or row["used"] or row["expires"] < time.time():
            raise ValueError("login state invalid, expired or used")

    def feishu_accept_tenant(self, tenant_key: str, allowlist: frozenset[str]) -> bool:
        """Decide whether this Feishu tenant belongs to this organization.

        An explicit allowlist wins. With none configured the first tenant to log
        in is pinned in settings and every later login must match it, so the Hub
        is closed by default instead of open to every Feishu organization that
        can reach the same application.
        """
        key = (tenant_key or "").strip()
        if not key:
            return False
        if allowlist:
            return key in allowlist
        with database(self.path) as db:
            row = db.execute("SELECT value FROM settings WHERE key='feishu_tenant_key'").fetchone()
            if row is None:
                db.execute("INSERT OR REPLACE INTO settings VALUES('feishu_tenant_key',?)", (key,))
                return True
            return row["value"] == key

    def feishu_complete(self, state: str, identity: dict[str, str]) -> tuple[int, str]:
        """Consume a state, bind the Feishu account to a member, return the loopback port.

        Member creation is idempotent per open_id so a second device reuses the
        same member. An invited member with the same exact name is adopted when
        it is unambiguous, so nobody ends up with two identities. A one-use
        ticket is returned to the browser, never the device credential: the
        credential is only ever issued over loopback.
        """
        with database(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            now = time.time()
            # Expired rows are unusable anyway; dropping them keeps the table
            # bounded on a public endpoint that anyone can start a login on.
            db.execute("DELETE FROM feishu_states WHERE expires<?", (now,))
            db.execute("DELETE FROM feishu_tickets WHERE expires<?", (now,))
            row = db.execute(
                "SELECT port,used,expires FROM feishu_states WHERE hash=?", (digest(state),)
            ).fetchone()
            if row is None or row["used"] or row["expires"] < now:
                raise ValueError("login state invalid, expired or used")
            db.execute("UPDATE feishu_states SET used=1 WHERE hash=?", (digest(state),))
            port = int(row["port"])
            open_id = identity["open_id"]
            linked = db.execute(
                "SELECT member FROM feishu_members WHERE open_id=?", (open_id,)
            ).fetchone()
            if linked is None:
                # Only a single unlinked match is adopted; two colleagues with
                # the same display name must not collapse into one identity.
                candidates = db.execute(
                    "SELECT id FROM members WHERE tenant=? AND name=? AND id NOT IN "
                    "(SELECT member FROM feishu_members)",
                    (self.tenant, identity["name"]),
                ).fetchall()
                if len(candidates) == 1:
                    member = candidates[0]["id"]
                else:
                    member = "mem_" + uuid.uuid4().hex
                    db.execute(
                        "INSERT INTO members VALUES(?,?,?)",
                        (member, self.tenant, identity["name"]),
                    )
                db.execute("INSERT INTO feishu_members VALUES(?,?)", (open_id, member))
            else:
                member = linked["member"]
            ticket = secrets.token_urlsafe(32)
            db.execute(
                "INSERT INTO feishu_tickets VALUES(?,?,?,0)",
                (digest(ticket), member, time.time() + 120),
            )
        return port, ticket

    def feishu_redeem(self, ticket: str, device_name: str) -> dict[str, Any]:
        """Exchange a loopback ticket for a device credential, once."""
        with database(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT t.member,t.used,t.expires,m.tenant,m.name FROM feishu_tickets t "
                "JOIN members m ON m.id=t.member WHERE t.hash=?",
                (digest(ticket),),
            ).fetchone()
            if row is None or row["used"] or row["expires"] < time.time():
                raise ValueError("login ticket invalid, expired or used")
            db.execute("UPDATE feishu_tickets SET used=1 WHERE hash=?", (digest(ticket),))
            device, token = "dev_" + uuid.uuid4().hex, secrets.token_urlsafe(32)
            db.execute(
                "INSERT INTO devices VALUES(?,?,?,?,0)",
                (device, row["member"], digest(token), device_name),
            )
            return dict(
                tenant_id=row["tenant"],
                member_id=row["member"],
                member_name=row["name"],
                device_id=device,
                token=token,
            )

    def runtime_invite(self, token: str) -> dict[str, Any]:
        """Pair another runtime for this member only, with a 15-minute one-use ticket.

        The plugin never receives the host device's reusable credential. Revoking
        the sponsor invalidates outstanding tickets; enrolled runtimes become
        independent devices that administrators can revoke separately.
        """
        invite = secrets.token_urlsafe(32)
        expires = time.time() + 900
        with database(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            who = self._identity(db, token)
            # Expired invitations can never be redeemed; drop them here instead
            # of letting the table grow by one row per pairing attempt forever.
            db.execute("DELETE FROM runtime_invites WHERE expires<?", (time.time(),))
            db.execute(
                "INSERT INTO runtime_invites VALUES(?,?,?,?,0)",
                (digest(invite), who["member_id"], who["device_id"], expires),
            )
        return {
            "invite": invite,
            "expires_at": expires,
            "tenant_id": who["tenant"],
            "member_id": who["member_id"],
        }

    @staticmethod
    def _identity(db: sqlite3.Connection, token: str) -> dict[str, Any]:
        row = db.execute(
            "SELECT d.id device_id,m.id member_id,m.name,m.tenant FROM devices d JOIN members m ON m.id=d.member WHERE d.token_hash=? AND d.revoked=0",
            (digest(token),),
        ).fetchone()
        if row is None:
            raise PermissionError("device credential invalid or revoked")
        return dict(row)

    def identity(self, token: str) -> dict[str, Any]:
        """Validate the device token against current revocation/member state on every call."""
        with database(self.path) as db:
            return self._identity(db, token)

    def revoke(self, device_id: str) -> None:
        """Disable future device requests; previously delivered knowledge cannot be remotely erased."""
        with database(self.path) as db:
            db.execute("UPDATE devices SET revoked=1 WHERE id=?", (device_id,))

    def put(self, token: str, value: dict[str, Any]) -> dict[str, Any]:
        """Commit one source-owned revision or return an already-committed replay.

        Identity is server-derived. The equality check precedes base-revision
        validation so a lost success response can be retried without a new event.
        """
        item = AssetInput.model_validate(value).model_dump()
        # Hash content and its meaning, not the transport's base revision.
        content_hash = digest(
            json.dumps(
                {k: v for k, v in item.items() if k != "base_revision"},
                sort_keys=True,
                ensure_ascii=False,
            )
        )
        with database(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            who = self._identity(db, token)
            old = db.execute(
                "SELECT * FROM assets WHERE tenant=? AND id=?", (who["tenant"], item["asset_id"])
            ).fetchone()
            previous: dict[str, Any] | None = json.loads(old["payload"]) if old else None
            if old and old["owner_device"] != who["device_id"]:
                raise ValueError("asset belongs to another source device")
            if previous and previous["content_hash"] == content_hash:
                return previous
            if item["base_revision"] != (old["revision"] if old else 0):
                raise ValueError("revision conflict; fetch current revision before retry")
            # source_store and product identify the origin and stay immutable.
            # producer_unknown may only move from True to False: that is a
            # member confirming authorship of their own file, which is strictly
            # more attribution. Allowing it back to True would be a way to shed
            # authorship, so that direction stays refused.
            if previous and (
                previous["source_store"],
                previous["product"],
            ) != (item["source_store"], item["product"]):
                raise ValueError("source identity is immutable")
            if previous and previous["producer_unknown"] != item["producer_unknown"]:
                if not (previous["producer_unknown"] and not item["producer_unknown"]):
                    raise ValueError("source identity is immutable")
            for ref in item["upstream"]:
                if not db.execute(
                    "SELECT 1 FROM assets WHERE tenant=? AND id=?", (who["tenant"], ref)
                ).fetchone():
                    raise ValueError("upstream asset missing in organization")
            producer = None if item["producer_unknown"] else who["member_id"]
            record = {
                **item,
                "tenant_id": who["tenant"],
                "revision": item["base_revision"] + 1,
                "producer_member_id": producer,
                "producer_name": "未知" if producer is None else who["name"],
                "uploader_member_id": who["member_id"],
                "uploader_name": who["name"],
                "source_device_id": who["device_id"],
                "content_hash": content_hash,
                "body_hash": digest(item["body"]),
                "observed_at": time.time(),
            }
            encoded = json.dumps(record, ensure_ascii=False)
            db.execute(
                "INSERT OR REPLACE INTO assets VALUES(?,?,?,?,?)",
                (who["tenant"], item["asset_id"], record["revision"], who["device_id"], encoded),
            )
            db.execute("INSERT INTO changes(tenant,payload) VALUES(?,?)", (who["tenant"], encoded))
            db.execute(
                "DELETE FROM search_index WHERE tenant=? AND asset_id=?",
                (who["tenant"], item["asset_id"]),
            )
            if not item["deleted"] and item["encoding"] == "utf8":
                indexed = (
                    item["index_text"]
                    if item["kind"] == "transcript" and item["index_text"] is not None
                    else item["body"]
                )
                words = tokens(item["title"] + "\n" + indexed)
                db.execute(
                    "INSERT INTO search_index VALUES(?,?,?)",
                    (who["tenant"], item["asset_id"], " ".join(words)),
                )
            return record

    def changes(self, token: str, cursor: int, limit: int = 100) -> dict[str, Any]:
        """Read a stable tenant-scoped sequence page, including deletions; an empty page retains cursor."""
        with database(self.path) as db:
            who = self._identity(db, token)
            rows = db.execute(
                "SELECT seq,payload FROM changes WHERE tenant=? AND seq>? ORDER BY seq LIMIT ?",
                (who["tenant"], max(0, cursor), min(max(1, limit), 100)),
            ).fetchall()
            return {
                "items": [json.loads(r["payload"]) for r in rows],
                "cursor": rows[-1]["seq"] if rows else cursor,
            }

    def search(
        self,
        token: str,
        query: str,
        *,
        project_hint: str = "",
        project_filter: str | None = None,
        limit: int = 12,
    ) -> list[dict[str, Any]]:
        """Retrieve complete authorized content with bounded FTS candidates and optional filtering.

        project_hint boosts ranking only. project_filter is an explicit hard filter.
        Multipart content is suppressed until every part matches the document hash.
        """
        terms = tokens(query)[:64]
        with database(self.path) as db:
            who = self._identity(db, token)
            if not terms:
                return []
            match = " OR ".join('"' + t + '"' for t in terms)
            rows = db.execute(
                "SELECT a.payload,bm25(search_index) score FROM search_index JOIN assets a ON a.tenant=search_index.tenant AND a.id=search_index.asset_id WHERE search_index MATCH ? AND search_index.tenant=? AND (? IS NULL OR json_extract(a.payload, '$.project')=?) ORDER BY score LIMIT 100",
                (match, who["tenant"], project_filter, project_filter),
            ).fetchall()
            results = []
            ready: dict[tuple[str, str, str], bool] = {}
            for row in rows:
                item = json.loads(row["payload"])
                if item.get("document_id"):
                    key = (item["source_device_id"], item["document_id"], item["document_hash"])
                    if key not in ready:
                        from .materialize import assembled

                        parts = db.execute(
                            "SELECT payload FROM assets WHERE tenant=? AND owner_device=? AND json_extract(payload,'$.document_id')=? AND json_extract(payload,'$.document_hash')=?",
                            (who["tenant"], *key),
                        ).fetchall()
                        ready[key] = bool(assembled([json.loads(p["payload"]) for p in parts]))
                    if not ready[key]:
                        continue
                if project_filter is not None and item["project"] != project_filter:
                    continue
                # The project is a bounded boost, never a visibility filter.
                item["score"] = -row["score"] * (
                    1.05 if project_hint and item["project"] == project_hint else 1
                )
                results.append(item)
            return sorted(results, key=lambda x: x["score"], reverse=True)[:limit]

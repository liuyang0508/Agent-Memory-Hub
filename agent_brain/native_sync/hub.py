"""Organization-scoped immutable revisions, identity and deterministic full-text recall."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from collections.abc import Callable, Sequence
import sqlite3
import threading

import json
import math
import os
import secrets
import time
import uuid
from pathlib import Path
from .common import digest, source_product, tokens
from .storage import database, HUB_SCHEMA, DB_TIMEOUT
from .models import AssetInput
from .admin_auth import AdminAuth, AdminCredential, WebAdminSession

if TYPE_CHECKING:
    from .semantic import IndexRefreshResult, SemanticBackend


def _env_float(name: str) -> float | None:
    value = os.environ.get(name, "").strip()
    if not value:
        return None
    number = float(value)
    if not 0 <= number <= 1:
        raise ValueError(f"{name} must be between 0 and 1")
    return number


class Hub:
    """Single-node authority; each mutation commits assets, changes and FTS together.

    No connection is shared across requests. BEGIN IMMEDIATE serializes identity
    and revision checks with their writes, including simultaneous retries. Multiple
    machines must not independently write copies of this SQLite database.
    """

    def __init__(
        self,
        path: Path,
        *,
        semantic_model: str | None = None,
        semantic_backend: SemanticBackend | None = None,
        semantic_min_score: float | None = None,
        mmr_lambda: float | None = None,
        reranker: Callable[[str, list[dict[str, Any]]], Sequence[float]] | None = None,
        decay: bool | None = None,
        hopfield: bool | None = None,
        hopfield_beta: float = 8.0,
        hopfield_min_score: float = 0.35,
        hook_min_score: float | None = None,
        hook_margin: float | None = None,
    ) -> None:
        self.path = Path(path)
        # Decay/signals re-rank by default; Hopfield association is opt-in
        # because it scans every indexed vector. Environment pins both.
        self.decay_enabled = (
            os.environ.get("AMH_NATIVE_DECAY", "1") not in ("0", "false", "off")
            if decay is None
            else bool(decay)
        )
        self.hopfield = (
            os.environ.get("AMH_NATIVE_HOPFIELD", "0") in ("1", "true", "on")
            if hopfield is None
            else bool(hopfield)
        )
        if not math.isfinite(hopfield_beta) or hopfield_beta <= 0:
            raise ValueError("hopfield beta must be a positive number")
        self.hopfield_beta = float(hopfield_beta)
        # The attractor already sits inside the retrieved cluster, so association
        # uses a lower floor than direct query similarity. Still an eligibility
        # floor, not a confidence claim.
        if not 0 <= hopfield_min_score <= 1:
            raise ValueError("hopfield floor must be between 0 and 1")
        self.hopfield_min_score = float(hopfield_min_score)
        self.semantic_model = semantic_model or os.environ.get("AMH_NATIVE_SEMANTIC_MODEL")
        self._encoder = semantic_backend
        self._encoder_lock = threading.Lock()
        # An explicit floor wins; otherwise a calibrated model manifest may set
        # it when the model loads, else the historical 0.45.
        self._semantic_floor_explicit = semantic_min_score is not None
        self.semantic_min_score = 0.45 if semantic_min_score is None else semantic_min_score
        if not 0 <= self.semantic_min_score <= 1 or (
            mmr_lambda is not None and not 0 <= mmr_lambda <= 1
        ):
            raise ValueError("semantic threshold and MMR lambda must be between 0 and 1")
        self.mmr_lambda = mmr_lambda
        self.reranker = reranker
        # Prompt-time relevance gate; the model manifest supplies calibrated
        # defaults, explicit settings or the environment override them.
        self.hook_min_score = (
            hook_min_score
            if hook_min_score is not None
            else _env_float("AMH_NATIVE_HOOK_MIN_SCORE")
        )
        self.hook_margin = (
            hook_margin if hook_margin is not None else _env_float("AMH_NATIVE_HOOK_MARGIN")
        )
        self.semantic_state: dict[str, Any] = {
            "state": "off" if not self.semantic_model else "loading"
        }
        # Parsed window vectors per (tenant, asset, revision, content hash):
        # a query must not re-read and re-parse the whole index from SQLite.
        self._vectors: dict[str, dict[tuple[str, int, str, str], Any]] = {}
        self._insights_refreshed: dict[str, float] = {}
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

    def _admin(self, db: sqlite3.Connection, token: AdminCredential) -> None:
        if isinstance(token, WebAdminSession):
            AdminAuth(self).resolve(db, token.token)
            return
        row = db.execute(
            "SELECT 1 FROM admins WHERE hash=? AND revoked=0", (digest(token),)
        ).fetchone()
        if row is None:
            raise PermissionError("operator credential invalid or revoked")

    def admin_overview(self, token: AdminCredential) -> dict[str, Any]:
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
                    "('feishu_app_id','amh_hub_public_url','feishu_tenant','feishu_tenant_key')"
                )
            }
        return {"members": members, "devices": devices, "settings": settings}

    def admin_dashboard(self, token: AdminCredential, *, days: int = 30) -> dict[str, Any]:
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
            "top_projects": [
                {"project": k, "assets": v}
                for k, v in sorted(projects.items(), key=lambda item: -item[1])[:10]
            ],
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

    def admin_revoke(self, token: AdminCredential, device_id: str) -> None:
        with database(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            self._admin(db, token)
            db.execute("UPDATE devices SET revoked=1 WHERE id=?", (device_id,))

    def admin_setting(self, token: AdminCredential, key: str, value: str) -> None:
        """Store deployment settings; an empty value clears the key."""
        allowed = {
            "feishu_app_id",
            "amh_hub_public_url",
            "feishu_tenant",
            "feishu_tenant_key",
            "recall_query_preview",
        }
        if key not in allowed:
            raise ValueError("unknown setting")
        if key == "recall_query_preview":
            # Retrieval compares this value literally. Accept the two documented
            # states in any case, or empty to clear; a typo such as "Off" must
            # not silently keep recording prompt previews.
            value = value.strip().lower()
            if value not in ("", "on", "off"):
                raise ValueError("recall_query_preview must be on or off")
        with database(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            self._admin(db, token)
            if value:
                db.execute("INSERT OR REPLACE INTO settings VALUES(?,?)", (key, value))
            else:
                db.execute("DELETE FROM settings WHERE key=?", (key,))

    def admin_secret_set(self, token: AdminCredential, app_secret: str) -> None:
        """Store the application secret separately so it is never read back by the page."""
        if not app_secret.strip():
            raise ValueError("app secret required")
        with database(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
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

    def prepare_legacy_provider(self, app_id: str) -> bool:
        """Pin the trusted deployment's existing provider, never a browser value.

        Upgraded instances already have authenticated open_id mappings but older
        versions omitted their namespace. Pinning the configured application
        allows those same accounts to prove their tenant at their next normal
        login, without requesting extra enterprise-directory permissions.
        """
        if not app_id:
            return False
        with database(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            if not db.execute("SELECT 1 FROM feishu_members LIMIT 1").fetchone():
                return False
            row = db.execute(
                "SELECT value FROM settings WHERE key='legacy-feishu-app-id'"
            ).fetchone()
            if row is None:
                scoped = db.execute(
                    "SELECT value FROM settings WHERE key='legacy-feishu-scope'"
                ).fetchone()
                expected = json.loads(scoped["value"])[0] if scoped else app_id
                db.execute("INSERT INTO settings VALUES('legacy-feishu-app-id',?)", (expected,))
            else:
                expected = row["value"]
            return expected == app_id

    def feishu_accept_tenant(
        self,
        tenant_key: str,
        allowlist: frozenset[str],
        *,
        app_id: str = "",
        verified_identity: dict[str, str] | None = None,
    ) -> bool:
        """Accept configured tenants, or let an already-known account prove its tenant.

        The identity must come from Feishu's authenticated user_info exchange.
        An unknown first visitor, a display-name match, or another application
        cannot establish the enterprise boundary during a legacy upgrade.
        """
        key = (tenant_key or "").strip()
        if not key:
            return False
        if allowlist and key not in allowlist:
            return False
        tenant = self.tenant
        with database(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT value FROM settings WHERE key='feishu_tenant_key'").fetchone()
            if row is not None:
                return bool(allowlist) or row["value"] == key
            provider = db.execute(
                "SELECT value FROM settings WHERE key='legacy-feishu-app-id'"
            ).fetchone()
            if (
                not verified_identity
                or verified_identity.get("tenant_key") != key
                or not provider
                or provider["value"] != app_id
            ):
                return bool(allowlist)
            known = db.execute(
                "SELECT 1 FROM feishu_members f JOIN members m ON m.id=f.member WHERE f.open_id=? AND m.tenant=?",
                (verified_identity.get("open_id", ""), tenant),
            ).fetchone()
            if not known:
                return bool(allowlist)
            db.execute("INSERT INTO settings VALUES('feishu_tenant_key',?)", (key,))
            return True

    def scope_legacy_feishu(self, app_id: str, tenant_key: str) -> int:
        """Explicit host-admin migration of 1.3.1 mappings; preserve member/device IDs.

        The operator must verify the old deployment's App ID and enterprise key.
        This does not guess ownership from a person's name or bind a new account.
        Re-running the same migration is safe; changing its scope is rejected.
        """
        if not app_id.strip() or not tenant_key.strip():
            raise ValueError("verified app ID and tenant key required")
        scope = json.dumps([app_id, tenant_key])
        with database(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            done = db.execute(
                "SELECT value FROM settings WHERE key='legacy-feishu-scope'"
            ).fetchone()
            if done and done["value"] != scope:
                raise ValueError("legacy identities already scoped to another provider")
            pinned = db.execute(
                "SELECT value FROM settings WHERE key='feishu_tenant_key'"
            ).fetchone()
            if pinned and pinned["value"] != tenant_key:
                raise ValueError("tenant key differs from the existing deployment")
            rows = db.execute("SELECT open_id,member FROM feishu_members").fetchall()
            for row in rows:
                old = db.execute(
                    "SELECT member FROM feishu_accounts WHERE app_id=? AND tenant_key=? AND open_id=?",
                    (app_id, tenant_key, row["open_id"]),
                ).fetchone()
                if old and old["member"] != row["member"]:
                    raise ValueError("identity migration conflict")
                db.execute(
                    "INSERT OR IGNORE INTO feishu_accounts VALUES(?,?,?,?)",
                    (app_id, tenant_key, row["open_id"], row["member"]),
                )
            provider = db.execute(
                "SELECT value FROM settings WHERE key='legacy-feishu-app-id'"
            ).fetchone()
            if provider and provider["value"] != app_id:
                raise ValueError("legacy provider differs from the trusted deployment")
            db.execute(
                "INSERT OR REPLACE INTO settings VALUES('legacy-feishu-app-id',?)", (app_id,)
            )
            db.execute("INSERT OR REPLACE INTO settings VALUES('legacy-feishu-scope',?)", (scope,))
            db.execute(
                "INSERT OR REPLACE INTO settings VALUES('feishu_tenant_key',?)", (tenant_key,)
            )
            return len(rows)

    def feishu_complete(
        self, state: str, identity: dict[str, str], *, app_id: str
    ) -> tuple[int, str]:
        """Consume a login state and bind only a scoped, verified provider identity.

        A display name is presentation data. Even a unique same-name legacy
        member is not evidence of ownership. Legacy provider mappings require an
        explicit host-admin migration before the original account can sign in.
        """
        if not app_id or not identity.get("tenant_key") or not identity.get("open_id"):
            raise ValueError("scoped provider identity required")
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
            scope = (app_id, identity["tenant_key"], open_id)
            linked = db.execute(
                "SELECT member FROM feishu_accounts WHERE app_id=? AND tenant_key=? AND open_id=?",
                scope,
            ).fetchone()
            if linked is None:
                legacy = db.execute(
                    "SELECT f.member,m.tenant FROM feishu_members f JOIN members m ON m.id=f.member WHERE f.open_id=?",
                    (open_id,),
                ).fetchone()
                provider = db.execute(
                    "SELECT value FROM settings WHERE key='legacy-feishu-app-id'"
                ).fetchone()
                tenant_pin = db.execute(
                    "SELECT value FROM settings WHERE key='feishu_tenant_key'"
                ).fetchone()
                if legacy and provider is None:
                    raise ValueError("legacy Feishu identity requires administrator migration")
                if (
                    legacy
                    and provider["value"] == app_id
                    and tenant_pin
                    and tenant_pin["value"] == identity["tenant_key"]
                ):
                    if legacy["tenant"] != self.tenant:
                        raise ValueError("legacy identity belongs to another organization")
                    member = legacy["member"]
                    db.execute("UPDATE members SET name=? WHERE id=?", (identity["name"], member))
                else:
                    member = "mem_" + uuid.uuid4().hex
                    db.execute(
                        "INSERT INTO members VALUES(?,?,?)", (member, self.tenant, identity["name"])
                    )
                db.execute("INSERT INTO feishu_accounts VALUES(?,?,?,?)", (*scope, member))
            else:
                member = linked["member"]
                db.execute("UPDATE members SET name=? WHERE id=?", (identity["name"], member))
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

    def runtime_invite(self, token: str, *, ttl_seconds: int = 900) -> dict[str, Any]:
        """Pair another runtime for this member only, with a bounded one-use ticket.

        The plugin never receives the host device's reusable credential. Revoking
        the sponsor invalidates outstanding tickets; enrolled runtimes become
        independent devices that administrators can revoke separately.
        """
        from .common import PAIRING_MAX_TTL

        if type(ttl_seconds) is not int or not 60 <= ttl_seconds <= PAIRING_MAX_TTL:
            raise ValueError("pairing lifetime must be 60 seconds to 7 days")
        invite = secrets.token_urlsafe(32)
        expires = time.time() + ttl_seconds
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
        from .source import transcript_metadata

        # Old clients must not resurrect metadata sidecars after cleanup. Accept
        # tombstones so their owners can still converge through the normal API.
        if (
            item["kind"] == "transcript"
            and transcript_metadata(item["path"])
            and not item["deleted"]
        ):
            raise ValueError("transcript metadata is not shareable")
        # Hash content and its meaning, not the transport's base revision. An
        # absent source_meta is removed first so records from clients that
        # predate the field keep byte-identical payloads and hashes. The file
        # mtime is a filesystem observation, not content: a re-scan after a
        # reinstall carries a new mtime and must still replay idempotently, so
        # it rides outside the hash while declared/links/sections stay inside.
        if not item.get("source_meta"):
            item.pop("source_meta", None)
        hashed = {k: v for k, v in item.items() if k != "base_revision"}
        if "mtime" in (item.get("source_meta") or {}):
            rest = {k: v for k, v in item["source_meta"].items() if k != "mtime"}
            if rest:
                hashed["source_meta"] = rest
            else:
                del hashed["source_meta"]
        content_hash = digest(json.dumps(hashed, sort_keys=True, ensure_ascii=False))
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
                return self._refresh_mtime(db, who["tenant"], item, previous)
            if item["base_revision"] != (old["revision"] if old else 0):
                raise ValueError("revision conflict; fetch current revision before retry")
            # source_store and product identify the origin and stay immutable.
            # producer_unknown may only move from True to False: that is a
            # member confirming authorship of their own file, which is strictly
            # more attribution. Allowing it back to True would be a way to shed
            # authorship, so that direction stays refused.
            if previous and (
                previous["source_store"],
                source_product(previous["product"]),
            ) != (item["source_store"], source_product(item["product"])):
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
            # New text, new chance: contradictions were filed against the old
            # revision and must not keep pressing the corrected one down. The
            # delivery/citation/support history is about the asset and stays.
            # A negative gain came from contradictions, so it floors at zero
            # rather than being replayed exactly. Replays return above this.
            db.execute(
                "UPDATE recall_signals SET contradict=0, gain=max(gain,0) "
                "WHERE tenant=? AND asset_id=? AND (contradict>0 OR gain<0)",
                (who["tenant"], item["asset_id"]),
            )
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

    @staticmethod
    def _refresh_mtime(
        db: sqlite3.Connection, tenant: str, item: dict[str, Any], previous: dict[str, Any]
    ) -> dict[str, Any]:
        """Carry a replayed payload's newer mtime onto the stored record in place.

        Same content, same revision, no change event and no index rebuild: the
        semantic vectors key on (revision, content_hash) and replicas follow the
        changes log, while mtime is only a hint for the time facets. A payload
        without an mtime (an older client) leaves the stored one alone.
        """
        mtime = (item.get("source_meta") or {}).get("mtime")
        stored = previous.get("source_meta")
        stored = stored if isinstance(stored, dict) else {}
        if mtime is None or stored.get("mtime") == mtime:
            return previous
        refreshed = {**previous, "source_meta": {**stored, "mtime": mtime}}
        db.execute(
            "UPDATE assets SET payload=? WHERE tenant=? AND id=?",
            (json.dumps(refreshed, ensure_ascii=False), tenant, item["asset_id"]),
        )
        return refreshed

    @staticmethod
    def _version_proof(db: sqlite3.Connection, item: dict[str, Any]) -> list[tuple[str, int]]:
        """Validate a current complete version and return its exact part revisions."""
        from .materialize import assembled

        if item.get("deleted"):
            return []
        if not item.get("document_id"):
            row = db.execute(
                "SELECT payload FROM assets WHERE tenant=? AND id=?",
                (item["tenant_id"], item["asset_id"]),
            ).fetchone()
            current = json.loads(row["payload"]) if row else None
            complete = (
                assembled([current])
                if current and Hub._proof_key(current) == Hub._proof_key(item)
                else []
            )
        elif item.get("encoding", "utf8") == "utf8":
            # Fetch only the fields needed for completeness, not duplicated
            # index_text and all provenance JSON for every transcript chunk.
            # Stream bytes into the digest rather than constructing a full copy.
            import hashlib

            from .document_contract import DOCUMENT_FIELDS

            part_rows = db.execute(
                "SELECT tenant,id,revision,owner_device,"
                "json_extract(payload,'$.body') body, "
                "json_remove(payload,'$.body','$.index_text') metadata FROM assets "
                "WHERE tenant=? AND owner_device=? "
                "AND json_extract(payload,'$.source_store')=? "
                "AND json_extract(payload,'$.document_id')=? AND json_extract(payload,'$.document_hash')=? "
                "ORDER BY json_extract(payload,'$.part_index')",
                (
                    item["tenant_id"],
                    item["source_device_id"],
                    item["source_store"],
                    item["document_id"],
                    item["document_hash"],
                ),
            )
            hashed = hashlib.sha256()
            versions = []
            count = item.get("part_count", 1)
            for index, row in enumerate(part_rows):
                part = json.loads(row["metadata"])
                if (
                    part.get("deleted")
                    or part.get("part_index") != index
                    or part.get("part_count") != count
                    or part.get("encoding") != "utf8"
                    or part.get("tenant_id") != row["tenant"]
                    or part.get("asset_id") != row["id"]
                    or part.get("revision") != row["revision"]
                    or part.get("source_device_id") != row["owner_device"]
                    or any(part.get(field) != item.get(field) for field in DOCUMENT_FIELDS)
                    or part.get("body_hash") != digest(row["body"])
                ):
                    return []
                hashed.update(row["body"].encode())
                versions.append((row["id"], row["revision"]))
            return (
                versions
                if (
                    len(versions) == count
                    and (item["asset_id"], item["revision"]) in versions
                    and hashed.hexdigest() == item["document_hash"]
                )
                else []
            )
        else:
            # Binary resources require base64 validation across chunk boundaries.
            rows = db.execute(
                "SELECT payload FROM assets WHERE tenant=? AND owner_device=? "
                "AND json_extract(payload,'$.source_store')=? "
                "AND json_extract(payload,'$.document_id')=? AND json_extract(payload,'$.document_hash')=?",
                (
                    item["tenant_id"],
                    item["source_device_id"],
                    item["source_store"],
                    item["document_id"],
                    item["document_hash"],
                ),
            ).fetchall()
            complete = assembled([json.loads(r["payload"]) for r in rows])
        return complete[0]["assembled_revisions"] if len(complete) == 1 else []

    @staticmethod
    def _proof_key(item: dict[str, Any]) -> tuple[str, ...]:
        return (
            item["tenant_id"],
            item["source_device_id"],
            item["source_store"],
            item.get("document_id") or item["asset_id"],
            item.get("document_hash") or item["body_hash"],
        )

    def changes(
        self, token: str, cursor: int, limit: int = 100, *, metadata_only: bool = False
    ) -> dict[str, Any]:
        """Read a stable tenant-scoped sequence page, including deletions; an empty page retains cursor."""
        with database(self.path) as db:
            who = self._identity(db, token)
            rows = db.execute(
                "SELECT seq,payload FROM changes WHERE tenant=? AND seq>? ORDER BY seq LIMIT ?",
                (who["tenant"], max(0, cursor), min(max(1, limit), 100)),
            ).fetchall()
            from .evidence import metadata

            items = [json.loads(r["payload"]) for r in rows]
            proofs: dict[tuple[str, ...], list[tuple[str, int]]] = {}
            for item in items:
                key = self._proof_key(item)
                # Only memory provenance needs whole-document deduplication.
                # Do not repeatedly assemble large raw transcripts for a delta.
                if item["kind"] == "memory" and not item["deleted"] and key not in proofs:
                    proofs[key] = self._version_proof(db, item)
                item["verified"] = (item["asset_id"], item["revision"]) in proofs.get(key, [])
            return {
                # Completion can occur after an earlier part's cursor was already
                # consumed. The last part must also carry proof for those earlier
                # revisions, without replaying their content or forging new events.
                "proofs": [
                    {"scope": key, "parts": parts} for key, parts in proofs.items() if parts
                ],
                "items": [metadata(item) for item in items] if metadata_only else items,
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
            # Apply product semantics before the final ranking, never inside
            # SQLite's bm25 expression. Curated memories should not lose solely
            # because a conversation repeated question words many times.
            kind_weight = {
                "memory": 2.5,
                "rule": 2.5,
                "skill": 1.5,
                "config": 1.0,
                "transcript": 1.0,
            }
            candidates = []
            for row in rows:
                item = json.loads(row["payload"])
                weight = kind_weight.get(item["kind"], 1.0)
                # Codex stores supporting session summaries/raw extraction in
                # its memory directory too. They remain searchable evidence,
                # but must not receive the same curated-topic boost merely
                # because their source root was registered as "memory".
                if (
                    item["kind"] == "memory"
                    and item["product"]
                    in ("codex", "codex-shared", "codex-cli", "codex-desktop", "chatgpt-desktop")
                    and (
                        item["path"].startswith("rollout_summaries/")
                        or item["path"] == "raw_memories.md"
                    )
                ):
                    weight = kind_weight["transcript"]
                item["score"] = (
                    -row["score"]
                    * weight
                    * (1.05 if project_hint and item["project"] == project_hint else 1)
                )
                candidates.append(item)
            candidates.sort(key=lambda item: item["score"], reverse=True)
            results = []
            proofs: dict[tuple[str, ...], list[tuple[str, int]]] = {}
            from .materialize import assembled

            for item in candidates:
                if item.get("part_count", 1) == 1:
                    # Already read from the current assets snapshot: hash-check
                    # these bytes directly instead of re-querying the same row.
                    valid = bool(assembled([item]))
                else:
                    key = self._proof_key(item)
                    if key not in proofs:
                        proofs[key] = self._version_proof(db, item)
                    valid = (item["asset_id"], item["revision"]) in proofs[key]
                if not valid:
                    continue
                item["verified"] = True
                results.append(item)
                if len(results) >= limit:
                    break  # Do not assemble candidates we cannot return.
            return results

    def _semantic_backend(self) -> tuple[SemanticBackend | None, str | None]:
        """Explicit local model only; never downloads or hashes as a fallback."""
        from .semantic import descriptor, load_backend

        try:
            if self._encoder is None and self.semantic_model:
                with self._encoder_lock:
                    if self._encoder is None:
                        self._encoder = load_backend(self.semantic_model)
            if self._encoder is not None:
                descriptor(self._encoder)
                floor = getattr(self._encoder, "semantic_min_score", None)
                if floor is not None and not self._semantic_floor_explicit:
                    self.semantic_min_score = float(floor)
            return self._encoder, None
        except Exception as exc:
            return None, "semantic_model_unavailable:" + type(exc).__name__

    def _retrieval_valid(self, db: sqlite3.Connection, item: dict[str, Any]) -> bool:
        from .materialize import assembled

        if item.get("deleted"):
            return False
        if not item.get("document_id") and item.get("part_count", 1) == 1:
            return bool(assembled([item]))
        # Even a one-part declared document can have conflicting sibling rows.
        # Use the same whole-version metadata contract as bounded document read.
        return (item["asset_id"], item["revision"]) in self._version_proof(db, item)

    def rebuild_semantic(
        self, token: str, *, kinds: Sequence[str] | None = None, include_evidence: bool = False
    ) -> IndexRefreshResult:
        """Refresh selected native vectors; default to topic/navigation memory only."""
        self.identity(token)
        from .semantic import rebuild

        backend, error = self._semantic_backend()
        if backend is None:
            raise ValueError(error or "semantic_model_not_configured")
        return rebuild(self, token, backend, kinds=kinds, include_evidence=include_evidence)

    def relevance_gate(self) -> tuple[float, float] | None:
        """(floor, margin) for prompt-time recall, or None when no calibration exists."""
        floor = self.hook_min_score
        if floor is None:
            floor = getattr(self._encoder, "hook_min_score", None)
        if floor is None:
            return None
        margin = self.hook_margin
        if margin is None:
            margin = getattr(self._encoder, "hook_margin", None)
        return float(floor), float(margin if margin is not None else 1.0)

    def refresh_semantic_local(self, kinds: Sequence[str] = ("memory", "rule")) -> dict[str, Any]:
        """Server-side index upkeep for every tenant; never called with a member token."""
        from .semantic import rebuild

        backend, error = self._semantic_backend()
        if backend is None:
            return {"state": "unavailable", "reason": error or "semantic_model_not_configured"}
        with database(self.path) as db:
            tenants = [row[0] for row in db.execute("SELECT DISTINCT tenant FROM assets")]
        results = {}
        for tenant in tenants:
            result = rebuild(self, "", backend, kinds=list(kinds), tenant=tenant)
            results[tenant] = {k: result[k] for k in ("indexed", "encoded", "reused")}
        self.semantic_state = {"state": "ready", "model": backend.model_id, "tenants": results}
        return self.semantic_state

    def retrieve(
        self,
        token: str,
        query: str,
        *,
        limit: int = 8,
        kinds: Sequence[str] | None = None,
        include_evidence: bool = False,
        project_filter: str | None = None,
        channel: str = "tool",
        record: bool = True,
        asking: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        from .retrieval import retrieve

        return retrieve(
            self,
            token,
            query,
            limit=limit,
            kinds=kinds,
            include_evidence=include_evidence,
            project_filter=project_filter,
            channel=channel,
            record=record,
            asking=asking,
        )

    def recall(
        self,
        token: str,
        query: str,
        *,
        limit: int = 8,
        asking: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Hook-channel recall: hybrid ranking over curated memory and rules, full bodies.

        Replaces the lexical-only search for the prompt-time hook so transcript
        chunks and supporting material cannot crowd out curated memory.
        """
        result = self.retrieve(
            token, query, limit=limit, kinds=["memory", "rule"], channel="hook", asking=asking
        )
        return {
            "items": result["items"],
            "trace_id": result["retrieval"]["trace_id"],
            "retrieval": result["retrieval"],
        }

    def recall_feedback(
        self, token: str, trace_id: str, events: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """Record delivery states and knowledge feedback against a recorded trace."""
        from .recall_ledger import apply_feedback

        with database(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            who = self._identity(db, token)
            return apply_feedback(db, who, trace_id, events)

    def recalls(
        self, token: str, *, trace_id: str | None = None, limit: int = 20
    ) -> dict[str, Any]:
        """A member's own device's recent recall traces; identifiers and scores only."""
        from .recall_ledger import list_traces

        with database(self.path) as db:
            who = self._identity(db, token)
            traces = list_traces(
                db, who["tenant"], device=who["device_id"], trace_id=trace_id, limit=limit
            )
            self._mask_query_preview(db, traces)
        return {"traces": traces}

    def admin_recalls(
        self,
        token: AdminCredential,
        *,
        trace_id: str | None = None,
        limit: int = 50,
        member: str | None = None,
        channel: str | None = None,
        day: str | None = None,
    ) -> dict[str, Any]:
        """Organization-wide recall traces for the operator page; no memory text."""
        from . import insights
        from .recall_ledger import list_traces

        since = until = None
        if day:
            since, until = insights.day_bounds(day)
        with database(self.path) as db:
            self._admin(db, token)
            traces = list_traces(
                db,
                self.tenant,
                trace_id=trace_id,
                limit=limit,
                member=member,
                channel=channel,
                since=since,
                until=until,
            )
            self._mask_query_preview(db, traces)
            members = [
                {"id": row["id"], "name": row["name"]}
                for row in db.execute(
                    "SELECT id,name FROM members WHERE tenant=? ORDER BY name", (self.tenant,)
                )
            ]
        return {"traces": traces, "members": members, "generated_at": time.time()}

    def admin_insights(self, token: AdminCredential, *, days: int = 30) -> dict[str, Any]:
        """Recall usage rolled up by day for the insight page; counts and titles, never bodies.

        The rollup is refreshed first when it is older than ``REFRESH_INTERVAL``,
        so the page reflects delivery and feedback states reported since the
        last visit without a scheduler having to run.
        """
        from . import insights

        days = max(1, min(int(days), 365))
        now = time.time()
        scope = self.tenant
        with database(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            self._admin(db, token)
            self._refresh_insights(db, scope, now)
        with database(self.path) as db:
            members = {
                row["id"]: row["name"]
                for row in db.execute("SELECT id,name FROM members WHERE tenant=?", (scope,))
            }
            preview = db.execute(
                "SELECT value FROM settings WHERE key='recall_query_preview'"
            ).fetchone()
            preview_on = not (preview and str(preview["value"]).strip().lower() == "off")
            data = insights.report(
                db, scope, days=days, now=now, preview_on=preview_on, members=members
            )
        data["generated_at"] = now
        data["window_days"] = days
        return data

    def _refresh_insights(
        self, db: sqlite3.Connection, tenant: str, now: float, *, force: bool = False
    ) -> bool:
        from . import insights

        if (
            not force
            and now - self._insights_refreshed.get(tenant, 0.0) < insights.REFRESH_INTERVAL
        ):
            return False
        insights.refresh(db, tenant)
        self._insights_refreshed[tenant] = now
        return True

    def refresh_insights(self) -> list[str]:
        """Roll the ledger up for this Hub's organization; ``serve`` calls it hourly."""
        with database(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            scope = self.tenant
            self._refresh_insights(db, scope, time.time(), force=True)
        return sorted(self._insights_refreshed)

    @staticmethod
    def _mask_query_preview(db: sqlite3.Connection, traces: list[dict[str, Any]]) -> None:
        """Switching previews off also stops previously recorded prompt text from being read."""
        row = db.execute("SELECT value FROM settings WHERE key='recall_query_preview'").fetchone()
        if row and str(row["value"]).strip().lower() == "off":
            for trace in traces:
                trace["query_preview"] = ""

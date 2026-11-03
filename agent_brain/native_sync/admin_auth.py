"""Explicit Feishu administrator roles and browser-bound short-lived sessions.

No first-user promotion, name matching, device credentials, or reusable operator
secrets in browser URLs. Only the host CLI can grant or revoke these roles.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import hmac
import json
import secrets
import sqlite3
import time
from typing import TYPE_CHECKING, Any, cast

from . import feishu
from .common import digest
from .storage import database

if TYPE_CHECKING:
    from .hub import Hub

FLOW_COOKIE = "__Host-amh-admin-flow"
SESSION_COOKIE = "__Host-amh-admin-session"
STATE_TTL = 600
SESSION_TTL = 8 * 60 * 60
TARGETS = {
    "admin": "/native/v1/admin",
    "dashboard": "/native/v1/admin/dashboard",
    "insights": "/native/v1/admin/insights",
    "recalls": "/native/v1/admin/recalls",
}


@dataclass(frozen=True)
class WebAdminSession:
    token: str = field(repr=False)


AdminCredential = str | WebAdminSession


class AdminAuth:
    def __init__(self, hub: Hub) -> None:
        self.hub = hub

    def config(self) -> feishu.FeishuConfig:
        config = feishu.load(self.hub.setting)
        if config is None:
            raise ValueError("enterprise login unavailable")
        return config

    def fingerprint(self) -> str:
        c = self.config()
        return digest(
            json.dumps(
                [
                    c.app_id,
                    c.app_secret,
                    c.public_url,
                    self.hub.tenant,
                    sorted(c.allowed_tenants),
                    self.hub.setting("feishu_tenant_key"),
                ],
                ensure_ascii=False,
            )
        )

    def allows(self, tenant_key: str) -> bool:
        c = self.config()
        return (
            tenant_key in c.allowed_tenants
            if c.allowed_tenants
            else (bool(tenant_key) and tenant_key == self.hub.setting("feishu_tenant_key"))
        )

    def set_role(self, member: str, *, enabled: bool) -> dict[str, Any]:
        """Host-only explicit grant to an existing scoped Feishu account."""
        config = self.config()
        with database(self.hub.path) as db:
            db.execute("BEGIN IMMEDIATE")
            accounts = db.execute(
                "SELECT f.*,m.name FROM feishu_accounts f JOIN members m ON m.id=f.member "
                "WHERE f.member=? AND f.app_id=? AND m.tenant=?",
                (member, config.app_id, self.hub.tenant),
            ).fetchall()
            if len(accounts) != 1 or not self.allows(accounts[0]["tenant_key"]):
                raise ValueError(
                    "one verified Feishu account in the configured organization required"
                )
            a = dict(accounts[0])
            key = (a["app_id"], a["tenant_key"], a["open_id"])
            db.execute(
                "INSERT INTO admin_web_roles VALUES(?,?,?,?,?,?) "
                "ON CONFLICT(app_id,tenant_key,open_id) DO UPDATE SET "
                "member=excluded.member,granted=excluded.granted,revoked=excluded.revoked",
                (*key, member, time.time(), int(not enabled)),
            )
            # Re-granting must not revive any session issued before a revocation.
            db.execute(
                "DELETE FROM admin_web_sessions WHERE app_id=? AND tenant_key=? AND open_id=?", key
            )
            return {**a, "enabled": enabled}

    def roles(self) -> list[dict[str, Any]]:
        with database(self.hub.path) as db:
            return [
                dict(row)
                for row in db.execute(
                    "SELECT r.*,m.name FROM admin_web_roles r JOIN members m ON m.id=r.member ORDER BY r.granted"
                )
            ]

    def begin(self, target: str, previous_browser: str = "") -> tuple[str, str]:
        if target not in TARGETS:
            raise ValueError("invalid login destination")
        fingerprint = self.fingerprint()
        state, browser = "admin." + secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        with database(self.hub.path) as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                "DELETE FROM admin_web_states WHERE expires<? OR browser_hash=?",
                (time.time(), digest(previous_browser)),
            )
            db.execute("DELETE FROM admin_web_sessions WHERE expires<?", (time.time(),))
            # A bounded public login queue, independent of device enrollment.
            db.execute(
                "DELETE FROM admin_web_states WHERE hash IN (SELECT hash FROM admin_web_states ORDER BY expires DESC LIMIT -1 OFFSET 255)"
            )
            db.execute(
                "INSERT INTO admin_web_states VALUES(?,?,?,?,?)",
                (digest(state), digest(browser), fingerprint, target, time.time() + STATE_TTL),
            )
        return state, browser

    def _state(self, db: sqlite3.Connection, state: str, browser: str) -> sqlite3.Row:
        row = db.execute("SELECT * FROM admin_web_states WHERE hash=?", (digest(state),)).fetchone()
        if (
            not browser
            or not state.startswith("admin.")
            or row is None
            or row["expires"] <= time.time()
            or not hmac.compare_digest(row["browser_hash"], digest(browser))
            or row["config_hash"] != self.fingerprint()
        ):
            raise ValueError("browser login expired")
        return cast(sqlite3.Row, row)

    def check(self, state: str, browser: str) -> None:
        with database(self.hub.path) as db:
            self._state(db, state, browser)

    def complete(
        self, state: str, browser: str, identity: dict[str, str], previous: str = ""
    ) -> tuple[str, str]:
        config = self.config()
        denied = False
        token, target = "", ""
        with database(self.hub.path) as db:
            db.execute("BEGIN IMMEDIATE")
            row = self._state(db, state, browser)
            target = TARGETS[row["target"]]
            db.execute("DELETE FROM admin_web_states WHERE hash=?", (digest(state),))
            key = (config.app_id, identity.get("tenant_key", ""), identity.get("open_id", ""))
            role = db.execute(
                "SELECT r.member FROM admin_web_roles r JOIN feishu_accounts f "
                "ON f.app_id=r.app_id AND f.tenant_key=r.tenant_key AND f.open_id=r.open_id AND f.member=r.member "
                "JOIN members m ON m.id=r.member WHERE r.app_id=? AND r.tenant_key=? AND r.open_id=? "
                "AND r.revoked=0 AND m.tenant=?",
                (*key, self.hub.tenant),
            ).fetchone()
            db.execute("DELETE FROM admin_web_sessions WHERE hash=?", (digest(previous),))
            if not self.allows(key[1]) or role is None:
                denied = True
            else:
                token = secrets.token_urlsafe(32)
                db.execute(
                    "INSERT INTO admin_web_sessions VALUES(?,?,?,?,?,?,?)",
                    (
                        digest(token),
                        *key,
                        self.fingerprint(),
                        secrets.token_urlsafe(32),
                        time.time() + SESSION_TTL,
                    ),
                )
        # Commit consumption even for an authenticated user who is not an admin.
        if denied:
            raise PermissionError("administrator role required")
        return token, target

    def resolve(self, db: sqlite3.Connection, token: str) -> dict[str, Any]:
        row = db.execute(
            "SELECT s.*,r.member,m.name FROM admin_web_sessions s JOIN admin_web_roles r "
            "ON s.app_id=r.app_id AND s.tenant_key=r.tenant_key AND s.open_id=r.open_id "
            "JOIN feishu_accounts f ON f.app_id=r.app_id AND f.tenant_key=r.tenant_key AND f.open_id=r.open_id AND f.member=r.member "
            "JOIN members m ON m.id=r.member WHERE s.hash=? AND s.expires>? AND r.revoked=0 AND m.tenant=?",
            (digest(token), time.time(), self.hub.tenant),
        ).fetchone()
        try:
            valid = (
                row is not None
                and row["config_hash"] == self.fingerprint()
                and self.allows(row["tenant_key"])
            )
        except ValueError:
            valid = False
        if not valid or row is None:
            raise PermissionError("administrator session expired or access revoked")
        return {
            "member_id": row["member"],
            "name": row["name"],
            "role": "admin",
            "csrf": row["csrf"],
            "expires": row["expires"],
        }

    def session(self, token: str) -> dict[str, Any]:
        with database(self.hub.path) as db:
            return self.resolve(db, token)

    def logout(self, token: str) -> None:
        with database(self.hub.path) as db:
            db.execute("DELETE FROM admin_web_sessions WHERE hash=?", (digest(token),))

"""Feishu enterprise login: deployment-owned credentials, no assumed domain.

An instance either has App ID/Secret plus a public URL or it offers no browser
login at all. The redirect URI is derived from that configured public URL, never
from a request header, so a forged Host cannot redirect an authorization code
to an attacker-controlled host. Nothing here is hardcoded to one company.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any
from urllib import error, request

AUTHORIZE_ENDPOINT = "https://accounts.feishu.cn/open-apis/authen/v1/authorize"
TOKEN_ENDPOINT = "https://accounts.feishu.cn/oauth/v3/token"
USERINFO_ENDPOINT = "https://open.feishu.cn/open-apis/authen/v1/user_info"
CALLBACK_PATH = "/native/v1/auth/feishu/callback"
TIMEOUT = 10.0


@dataclass(frozen=True)
class FeishuConfig:
    """One organization instance's login configuration."""

    app_id: str
    app_secret: str
    public_url: str
    tenant: str
    allowed_tenants: frozenset[str]

    @property
    def redirect_uri(self) -> str:
        return self.public_url.rstrip("/") + CALLBACK_PATH

    def allows_tenant(self, tenant_key: str) -> bool | None:
        """Allowlist decision; None means "the Hub must decide".

        An empty allowlist used to mean "accept any Feishu organization that can
        reach this app", which is open to the public internet. The Hub now binds
        the first tenant to log in and rejects every later one, so this only
        answers the question the configuration can actually answer.
        """
        if not self.allowed_tenants:
            return None
        return tenant_key in self.allowed_tenants


def load(settings: Any = None) -> FeishuConfig | None:
    """Return configuration only when every required value is present; else login is unavailable.

    Environment variables win so a host operator can pin values system-wide;
    otherwise the operator page's stored settings apply. Nothing here reads a
    request, so a browser cannot influence where the code is sent.
    """
    read = settings or (lambda key: "")

    def value(key: str, env: str) -> str:
        return os.environ.get(env, "").strip() or read(key)

    app_id = value("feishu_app_id", "FEISHU_APP_ID")
    app_secret = value("feishu_app_secret", "FEISHU_APP_SECRET")
    public_url = value("amh_hub_public_url", "AMH_HUB_PUBLIC_URL")
    if not (app_id and app_secret and public_url):
        return None
    if not public_url.startswith("https://"):
        raise ValueError("AMH_HUB_PUBLIC_URL must be an https URL")
    return FeishuConfig(
        app_id=app_id,
        app_secret=app_secret,
        public_url=public_url,
        tenant=os.environ.get("FEISHU_TENANT", "").strip() or read("feishu_tenant") or "linkcrux",
        allowed_tenants=frozenset(
            part.strip()
            for part in os.environ.get("FEISHU_ALLOWED_TENANT_KEYS", "").split(",")
            if part.strip()
        ),
    )


def authorize_url(config: FeishuConfig, state: str) -> str:
    """Build the consent URL; scope stays empty because user_info needs no extra permission."""
    from urllib import parse

    query = parse.urlencode(
        {
            "client_id": config.app_id,
            "redirect_uri": config.redirect_uri,
            "state": state,
        }
    )
    return AUTHORIZE_ENDPOINT + "?" + query


def _request(url: str, *, payload: dict[str, Any] | None = None, token: str = "") -> Any:
    headers = {"Content-Type": "application/json; charset=utf-8"}
    if token:
        headers["Authorization"] = "Bearer " + token
    req = request.Request(
        url,
        data=None if payload is None else json.dumps(payload).encode(),
        headers=headers,
    )
    try:
        with request.urlopen(req, timeout=TIMEOUT) as response:
            raw = response.read()
    except error.HTTPError as exc:
        raw = exc.read()
        raise ValueError(_failure(raw, "Feishu HTTP " + str(exc.code))) from None
    except (TimeoutError, OSError) as exc:
        raise OSError("Feishu unavailable") from exc
    try:
        return json.loads(raw)
    except ValueError:
        raise ValueError("Feishu returned a non-JSON response") from None


def _failure(raw: bytes, fallback: str) -> str:
    """Surface Feishu's own error code without echoing tokens or the client secret."""
    try:
        data = json.loads(raw)
    except ValueError:
        return fallback
    if not isinstance(data, dict):
        return fallback
    code = data.get("code", data.get("error", ""))
    detail = data.get("error_description") or data.get("msg") or ""
    return fallback + (f": {code} {detail}".rstrip() if code != "" else "")


def exchange_code(config: FeishuConfig, code: str) -> str:
    """Trade a five-minute, one-use authorization code for a user access token."""
    data = _request(
        TOKEN_ENDPOINT,
        payload={
            "grant_type": "authorization_code",
            "client_id": config.app_id,
            "client_secret": config.app_secret,
            "code": code,
            "redirect_uri": config.redirect_uri,
        },
    )
    if not isinstance(data, dict) or data.get("code") not in (0, None):
        raise ValueError(_failure(json.dumps(data).encode(), "authorization failed"))
    token = data.get("access_token")
    if not isinstance(token, str) or not token:
        raise ValueError("Feishu returned no user access token")
    return token


def user_identity(token: str) -> dict[str, str]:
    """Read only the fields this product needs; email, phone and employee ID stay unrequested."""
    data = _request(USERINFO_ENDPOINT, token=token)
    if not isinstance(data, dict) or data.get("code") != 0:
        raise ValueError(_failure(json.dumps(data).encode(), "user info failed"))
    value = data.get("data") or {}
    result = {key: str(value.get(key) or "") for key in ("name", "open_id", "union_id", "tenant_key")}
    if not result["open_id"] or not result["tenant_key"]:
        raise ValueError("Feishu returned no stable user identity")
    return result

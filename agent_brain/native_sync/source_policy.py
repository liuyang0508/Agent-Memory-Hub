"""Explicit upload scope, shared by the full and stdlib-only portable clients.

Absence is the old profile contract, not consent to silently migrate a member.
New enrollment writes a memory-only policy. Scope changes pause observation and
outbox delivery without altering native files, selections or remote assets.
"""

from __future__ import annotations

from typing import Any, Iterable

KINDS = ("memory", "transcript", "rule", "skill", "config")
DEFAULT_KINDS = ("memory",)


def normalize(kinds: Iterable[str]) -> list[str]:
    requested = set(kinds)
    if requested - set(KINDS):
        raise ValueError("unknown source kinds: " + ", ".join(sorted(requested - set(KINDS))))
    return [kind for kind in KINDS if kind in requested]


def enabled(config: dict[str, Any]) -> list[str]:
    if "source_policy" not in config:
        return list(KINDS)
    policy = config["source_policy"]
    if (
        not isinstance(policy, dict)
        or policy.get("version") != 1
        or not isinstance(policy.get("kinds"), list)
    ):
        raise ValueError("invalid source policy; review profile before syncing")
    return normalize(policy["kinds"])


def policy(kinds: Iterable[str] = DEFAULT_KINDS) -> dict[str, Any]:
    return {"version": 1, "kinds": normalize(kinds)}


def describe(config: dict[str, Any], sources: list[dict[str, Any]]) -> dict[str, Any]:
    allowed = enabled(config)
    legacy = "source_policy" not in config
    return {
        "mode": "legacy" if legacy else "explicit",
        "enabled_kinds": allowed,
        "registered_kinds": normalize(s["kind"] for s in sources),
        "sources": [
            {"id": s["id"], "root": s["root"], "kind": s["kind"], "enabled": s["kind"] in allowed}
            for s in sources
        ],
        "note": (
            "旧配置仍沿用原同步范围；新版仅记忆默认值尚未应用。请确认后调整。"
            if legacy
            else "仅启用类型的新内容和已选择历史可上传。"
        ),
        "preservation": (
            "关闭类型会暂停扫描和待上传队列；选择、原生文件及云端内容保留，重新启用可继续。"
            "若后台正在同步，本次调整会等待该轮完成后生效，不会即时取消正在上传的一批内容。"
        ),
    }

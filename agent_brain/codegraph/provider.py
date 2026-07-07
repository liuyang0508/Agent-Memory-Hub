"""Optional bridge to DeusData/codebase-memory-mcp.

This module treats codebase-memory-mcp as an external structural-code provider.
It never installs agent hooks, never mutates AMH memory items, and only calls the
binary's explicit ``cli --json`` surface.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any


ALLOWED_TOOLS = {
    "index_repository",
    "list_projects",
    "index_status",
    "get_architecture",
    "detect_changes",
    "search_graph",
}


class CodeGraphUnavailableError(RuntimeError):
    """Raised when no code graph provider binary can be found."""


class CodeGraphInvocationError(RuntimeError):
    """Raised when the external provider returns an error or invalid payload."""


def derive_codebase_memory_project(path: str | os.PathLike[str]) -> str:
    """Mirror codebase-memory-mcp's project-name normalization.

    The upstream C helper derives the project name from the absolute path by
    replacing any character outside ``[A-Za-z0-9._-]`` with ``-``, collapsing
    repeated dashes/dots, trimming leading dashes/dots and trailing dashes, then
    falling back to ``root``.
    """

    text = str(path).replace("\\", "/")
    chars = [
        ch if ("a" <= ch <= "z" or "A" <= ch <= "Z" or "0" <= ch <= "9" or ch in "._-") else "-"
        for ch in text
    ]

    collapsed: list[str] = []
    previous = ""
    for ch in chars:
        if (ch == "-" and previous == "-") or (ch == "." and previous == "."):
            continue
        collapsed.append(ch)
        previous = ch

    normalized = "".join(collapsed).lstrip("-.").rstrip("-")
    return normalized or "root"


@dataclass(frozen=True)
class CodebaseMemoryMcpProvider:
    """Subprocess adapter for the optional ``codebase-memory-mcp`` binary."""

    binary: str | None = None
    timeout_s: float = 10.0

    def call(
        self,
        tool: str,
        arguments: dict[str, Any] | None = None,
        *,
        repo_path: str | os.PathLike[str] | None = None,
    ) -> dict[str, Any]:
        if tool not in ALLOWED_TOOLS:
            allowed = ", ".join(sorted(ALLOWED_TOOLS))
            raise CodeGraphInvocationError(f"unsupported code graph tool {tool!r}; choose from: {allowed}")

        binary = self._resolve_binary()
        payload = json.dumps(arguments or {}, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        cwd = str(Path(repo_path).expanduser().resolve()) if repo_path is not None else None
        env = os.environ.copy()
        env.setdefault("CBM_LOG_LEVEL", "error")

        try:
            completed = subprocess.run(
                [binary, "cli", "--json", tool, payload],
                cwd=cwd,
                text=True,
                capture_output=True,
                timeout=self.timeout_s,
                check=False,
                env=env,
            )
        except subprocess.TimeoutExpired as exc:
            raise CodeGraphInvocationError(
                f"{tool} timed out after {self.timeout_s:g}s via {binary}"
            ) from exc
        except OSError as exc:
            raise CodeGraphUnavailableError(f"cannot execute code graph binary {binary!r}: {exc}") from exc

        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "").strip()
            raise CodeGraphInvocationError(
                f"{tool} failed with exit code {completed.returncode}: {detail}"
            )

        text = completed.stdout.strip()
        if not text:
            return {}
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise CodeGraphInvocationError(f"{tool} returned non-JSON output: {text[:500]}") from exc
        if not isinstance(data, dict):
            raise CodeGraphInvocationError(f"{tool} returned {type(data).__name__}, expected JSON object")
        return _unwrap_mcp_content(data)

    def index_repository(
        self,
        *,
        repo_path: str | os.PathLike[str],
        mode: str = "fast",
        persistence: bool = False,
    ) -> dict[str, Any]:
        repo = Path(repo_path).expanduser().resolve()
        return self.call(
            "index_repository",
            {"repo_path": str(repo), "mode": mode, "persistence": persistence},
            repo_path=repo,
        )

    def index_status(
        self,
        *,
        repo_path: str | os.PathLike[str],
        project: str | None = None,
    ) -> dict[str, Any]:
        repo = Path(repo_path).expanduser().resolve()
        return self.call(
            "index_status",
            {"project": project or derive_codebase_memory_project(repo)},
            repo_path=repo,
        )

    def architecture(
        self,
        *,
        repo_path: str | os.PathLike[str],
        project: str | None = None,
        aspects: list[str] | None = None,
    ) -> dict[str, Any]:
        repo = Path(repo_path).expanduser().resolve()
        payload: dict[str, Any] = {"project": project or derive_codebase_memory_project(repo)}
        if aspects:
            payload["aspects"] = aspects
        return self.call("get_architecture", payload, repo_path=repo)

    def detect_changes(
        self,
        *,
        repo_path: str | os.PathLike[str],
        project: str | None = None,
        scope: str = "symbols",
        depth: int = 2,
        base_branch: str = "main",
    ) -> dict[str, Any]:
        repo = Path(repo_path).expanduser().resolve()
        return self.call(
            "detect_changes",
            {
                "project": project or derive_codebase_memory_project(repo),
                "scope": scope,
                "depth": depth,
                "base_branch": base_branch,
            },
            repo_path=repo,
        )

    def search_graph(
        self,
        *,
        repo_path: str | os.PathLike[str],
        project: str | None = None,
        query: str | None = None,
        name_pattern: str | None = None,
        label: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> dict[str, Any]:
        repo = Path(repo_path).expanduser().resolve()
        payload: dict[str, Any] = {
            "project": project or derive_codebase_memory_project(repo),
            "limit": limit,
            "offset": offset,
        }
        if query:
            payload["query"] = query
        if name_pattern:
            payload["name_pattern"] = name_pattern
        if label:
            payload["label"] = label
        return self.call("search_graph", payload, repo_path=repo)

    def _resolve_binary(self) -> str:
        if self.binary:
            return self.binary
        env_binary = os.environ.get("AMH_CODEGRAPH_BINARY")
        if env_binary:
            return env_binary
        found = shutil.which("codebase-memory-mcp")
        if found:
            return found
        raise CodeGraphUnavailableError(
            "codebase-memory-mcp binary not found; install it separately or set AMH_CODEGRAPH_BINARY"
        )


def _unwrap_mcp_content(data: dict[str, Any]) -> dict[str, Any]:
    """Return the JSON object embedded in an MCP ``content[].text`` envelope."""

    content = data.get("content")
    if not isinstance(content, list) or not content:
        return data
    first = content[0]
    if not isinstance(first, dict):
        return data
    text = first.get("text")
    if not isinstance(text, str):
        return data
    stripped = text.strip()
    if not stripped:
        return {}
    try:
        nested = json.loads(stripped)
    except json.JSONDecodeError:
        return {"text": stripped}
    if isinstance(nested, dict):
        return nested
    return {"value": nested}

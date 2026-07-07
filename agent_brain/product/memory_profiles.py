"""Agent-facing memory profile export.

Profiles are derived rule files for agent runtimes (CLAUDE.md, AGENTS.md,
Cursor rules). They are not the source of truth: MemoryItem markdown remains
canonical and this module only renders or updates a managed block.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from agent_brain.contracts.memory_item import MemoryItem
from agent_brain.memory.store.items_store import ItemsStore

BEGIN_MARKER = "<!-- BEGIN agent-memory-hub profile -->"
END_MARKER = "<!-- END agent-memory-hub profile -->"

_TARGET_PATHS = {
    "claude-code": "CLAUDE.md",
    "claude": "CLAUDE.md",
    "codex": "AGENTS.md",
    "codex-cli": "AGENTS.md",
    "cursor": ".cursor/rules/agent-memory-hub.mdc",
    "generic": "AGENT_MEMORY_PROFILE.md",
}

_PROFILE_TYPES = {"policy", "decision", "fact", "signal", "handoff", "skill"}
_SENSITIVE_EXCLUDE = {"private", "secret"}


@dataclass(frozen=True)
class MemoryProfileExport:
    target: str
    path: Path
    relative_path: str
    text: str
    source_item_ids: list[str]
    applied: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "target": self.target,
            "path": str(self.path),
            "relative_path": self.relative_path,
            "text": self.text,
            "source_item_ids": list(self.source_item_ids),
            "applied": self.applied,
        }


def export_memory_profile(
    brain_dir: Path,
    *,
    target: str = "codex",
    output_root: Path | None = None,
    project: str | None = None,
    max_items: int = 24,
    min_confidence: float = 0.5,
    apply: bool = False,
    now: datetime | None = None,
) -> MemoryProfileExport:
    """Render and optionally apply a scoped agent memory profile.

    The profile is intentionally compact. It exports stable, high-confidence
    rules and decisions as a managed block that can be regenerated from
    ``items/`` at any time.
    """

    normalized_target = _normalize_target(target)
    relative_path = _TARGET_PATHS[normalized_target]
    root = Path(output_root) if output_root is not None else Path(brain_dir)
    path = root / relative_path
    items = _select_items(
        ItemsStore(Path(brain_dir) / "items"),
        project=project,
        max_items=max_items,
        min_confidence=min_confidence,
    )
    text = _render_profile(
        normalized_target,
        items,
        generated_at=_utc(now).isoformat(),
    )
    if apply:
        path.parent.mkdir(parents=True, exist_ok=True)
        _write_managed_block(path, text)
    return MemoryProfileExport(
        target=normalized_target,
        path=path,
        relative_path=relative_path,
        text=text,
        source_item_ids=[item.id for item, _body in items],
        applied=apply,
    )


def _normalize_target(target: str) -> str:
    normalized = target.strip().lower().replace("_", "-")
    if normalized not in _TARGET_PATHS:
        valid = ", ".join(sorted(_TARGET_PATHS))
        raise ValueError(f"unsupported profile target {target!r}; choose from: {valid}")
    return normalized


def _select_items(
    store: ItemsStore,
    *,
    project: str | None,
    max_items: int,
    min_confidence: float,
) -> list[tuple[MemoryItem, str]]:
    candidates: list[tuple[MemoryItem, str]] = []
    for item, body in store.iter_all():
        if str(item.type) not in _PROFILE_TYPES:
            continue
        if project is not None and item.project != project:
            continue
        if item.confidence < min_confidence:
            continue
        if str(item.sensitivity) in _SENSITIVE_EXCLUDE:
            continue
        if item.superseded_by:
            continue
        candidates.append((item, body))
    candidates.sort(key=lambda pair: _profile_score(pair[0]), reverse=True)
    return candidates[: max(0, max_items)]


def _profile_score(item: MemoryItem) -> tuple[float, str]:
    type_weight = {
        "policy": 6.0,
        "decision": 5.0,
        "skill": 4.5,
        "handoff": 3.0,
        "signal": 2.5,
        "fact": 2.0,
    }.get(str(item.type), 1.0)
    support = min(float(item.support_count), 5.0) * 0.2
    risk = float(item.contradict_count) * 0.5
    return (type_weight + item.confidence + support - risk, item.created_at.isoformat())


def _render_profile(
    target: str,
    items: list[tuple[MemoryItem, str]],
    *,
    generated_at: str,
) -> str:
    lines = [
        BEGIN_MARKER,
        "",
        "# Agent Memory Hub Profile",
        "",
        f"Target: {target}",
        f"Generated: {generated_at}",
        "",
        "Use these as compact operating rules. The canonical source remains the",
        "Agent Memory Hub Markdown pool; retrieve item detail before relying on",
        "ambiguous or high-risk context.",
        "",
    ]
    sections = (
        ("Policies", {"policy", "skill"}),
        ("Decisions", {"decision"}),
        ("Facts And Signals", {"fact", "signal", "handoff"}),
    )
    for title, types in sections:
        section_items = [(item, body) for item, body in items if str(item.type) in types]
        if not section_items:
            continue
        lines.extend([f"## {title}", ""])
        for item, body in section_items:
            summary = _one_line(item.summary or item.context_views.locator or body)
            lines.append(f"- [{item.type}] {item.title}: {summary} (`{item.id}`)")
        lines.append("")
    lines.append(END_MARKER)
    lines.append("")
    return "\n".join(lines)


def _one_line(text: str, limit: int = 220) -> str:
    compact = " ".join(text.split())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 1].rstrip() + "…"


def _write_managed_block(path: Path, block: str) -> None:
    old = path.read_text(encoding="utf-8") if path.exists() else ""
    if BEGIN_MARKER in old and END_MARKER in old:
        start = old.index(BEGIN_MARKER)
        end = old.index(END_MARKER, start) + len(END_MARKER)
        new = old[:start].rstrip() + "\n\n" + block.strip() + "\n\n" + old[end:].lstrip()
    else:
        prefix = old.rstrip()
        new = f"{prefix}\n\n{block.strip()}\n" if prefix else block
    path.write_text(new, encoding="utf-8")


def _utc(value: datetime | None) -> datetime:
    current = value or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return current.astimezone(timezone.utc)


__all__ = ["MemoryProfileExport", "export_memory_profile"]

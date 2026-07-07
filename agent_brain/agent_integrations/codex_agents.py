from __future__ import annotations

from pathlib import Path

from .codex_config import (
    BEGIN,
    END,
    atomic_write_text,
    remove_block,
    upsert_block,
)


def render_agents_block(discipline_md: Path) -> str:
    discipline = discipline_md.read_text(encoding="utf-8").rstrip()
    return (
        f"{BEGIN}\n\n"
        f"> Auto-injected by agent-memory-hub Codex adapter. Edit at\n"
        f"> {discipline_md} (repo source) and re-install, not in-place.\n\n"
        f"{discipline}\n\n"
        f"{END}\n"
    )


def install_agents_block(agents_md: Path, block: str) -> bool:
    agents_md.parent.mkdir(parents=True, exist_ok=True)
    current = agents_md.read_text(encoding="utf-8") if agents_md.exists() else ""
    new_content, _action = upsert_block(current, block)
    if new_content == current:
        return False
    atomic_write_text(agents_md, new_content)
    return True


def uninstall_agents_block(agents_md: Path) -> bool:
    if not agents_md.exists():
        return False
    current = agents_md.read_text(encoding="utf-8")
    cleaned = remove_block(current)
    if cleaned == current:
        return False
    atomic_write_text(agents_md, cleaned)
    return True


__all__ = ["install_agents_block", "render_agents_block", "uninstall_agents_block"]

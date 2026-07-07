"""Entity / persona derived layer.

A *derived* view (no new schema type, no LLM, offline): a name that recurs as a
``project``, an ``agent``, or a frequent ``tag`` becomes an entity that
aggregates every item about it — counts by type/abstraction and the other
entities it co-occurs with. This fills the gap that jinchenma (HerName),
Tencent (Persona) and Karpathy's LLM-Wiki (``entities/``) all independently
maintain, while staying true to our flat-md + derived-index architecture.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
import hashlib
from pathlib import Path
from typing import Optional

from agent_brain.memory.evidence.integrations.obsidian import _slugify
from agent_brain.contracts.memory_item import MemoryItem

ItemBody = tuple[MemoryItem, str]
_RESERVED_ENTITY_STEMS = {"index"}


@dataclass
class Entity:
    name: str
    kind: str  # "project" | "agent" | "tag"
    item_ids: list[str]
    by_type: dict[str, int] = field(default_factory=dict)
    by_abstraction: dict[str, int] = field(default_factory=dict)
    related: list[tuple[str, int]] = field(default_factory=list)


def _entity_stem(name: str) -> str:
    return _slugify(name) or "entity"


def _disambiguated_entity_stems(entities: list[Entity]) -> dict[str, str]:
    raw_stems = {_entity.name: _entity_stem(_entity.name) for _entity in entities}
    counts = Counter(raw_stems.values())
    stems: dict[str, str] = {}
    for entity in entities:
        stem = raw_stems[entity.name]
        if counts[stem] > 1 or stem in _RESERVED_ENTITY_STEMS:
            digest = hashlib.sha1(f"{entity.kind}:{entity.name}".encode("utf-8")).hexdigest()[:8]
            stem = f"{stem}-{digest}"
        stems[entity.name] = stem
    return stems


def _entity_link(name: str, stems_by_name: dict[str, str] | None = None) -> str:
    stem = stems_by_name.get(name, _entity_stem(name)) if stems_by_name else _entity_stem(name)
    return f"[[{stem}|{name}]]"


def _item_link(item: MemoryItem) -> str:
    return f"[[{item.id}|{item.title}]]"


def extract_entities(items: list[ItemBody], *, min_tag_count: int = 3) -> list[Entity]:
    """Derive entities from project / agent fields and frequent tags.

    Kind priority when a name appears in several roles: project > agent > tag.
    """
    by_id: dict[str, MemoryItem] = {}
    proj_items: dict[str, set[str]] = defaultdict(set)
    agent_items: dict[str, set[str]] = defaultdict(set)
    tag_items: dict[str, set[str]] = defaultdict(set)
    for it, _ in items:
        by_id[it.id] = it
        if it.project:
            proj_items[it.project].add(it.id)
        if it.agent:
            agent_items[it.agent].add(it.id)
        for tag in it.tags:
            tag_items[tag].add(it.id)

    names: dict[str, dict] = {}
    for name, ids in proj_items.items():
        names.setdefault(name, {"kind": "project", "ids": set()})["ids"] |= ids
    for name, ids in agent_items.items():
        if name in names:
            names[name]["ids"] |= ids
        else:
            names[name] = {"kind": "agent", "ids": set(ids)}
    for name, ids in tag_items.items():
        if len(ids) < min_tag_count:
            continue
        if name in names:
            names[name]["ids"] |= ids
        else:
            names[name] = {"kind": "tag", "ids": set(ids)}

    name_to_ids = {name: meta["ids"] for name, meta in names.items()}
    entities: list[Entity] = []
    for name, meta in names.items():
        ids = sorted(meta["ids"])
        by_type = Counter(str(by_id[i].type) for i in ids)
        by_abs = Counter(str(by_id[i].abstraction) for i in ids)
        related: list[tuple[str, int]] = []
        for other, oids in name_to_ids.items():
            if other == name:
                continue
            overlap = len(meta["ids"] & oids)
            if overlap > 0:
                related.append((other, overlap))
        related.sort(key=lambda x: (-x[1], x[0]))
        entities.append(
            Entity(
                name=name,
                kind=meta["kind"],
                item_ids=ids,
                by_type=dict(by_type),
                by_abstraction=dict(by_abs),
                related=related,
            )
        )
    entities.sort(key=lambda e: (-len(e.item_ids), e.name))
    return entities


def build_entity_page(
    entity: Entity,
    items_by_id: dict[str, MemoryItem],
    stems_by_name: dict[str, str] | None = None,
) -> str:
    lines = [
        f"# {entity.name}",
        "",
        f"> kind: {entity.kind} · {len(entity.item_ids)} 条相关记忆",
        "",
        "## By type",
    ]
    for key, count in sorted(entity.by_type.items(), key=lambda x: (-x[1], x[0])):
        lines.append(f"- {key}: {count}")

    lines += ["", "## By abstraction"]
    for key, count in sorted(entity.by_abstraction.items(), key=lambda x: (-x[1], x[0])):
        lines.append(f"- {key}: {count}")

    if entity.related:
        lines += ["", "## Related entities"]
        for name, count in entity.related[:15]:
            lines.append(f"- {_entity_link(name, stems_by_name)} ({count})")

    lines += ["", "## Items"]
    for iid in entity.item_ids:
        it = items_by_id.get(iid)
        if it:
            lines.append(f"- {_item_link(it)} (`{iid}`)")
        else:
            lines.append(f"- `{iid}`")
    return "\n".join(lines) + "\n"


def build_entities_index(
    entities: list[Entity],
    stems_by_name: dict[str, str] | None = None,
) -> str:
    lines = ["# Entities", "", f"> 共 {len(entities)} 个实体", ""]
    if not entities:
        lines.append("- (none)")
    for e in entities:
        lines.append(
            f"- {_entity_link(e.name, stems_by_name)} — {e.kind}, {len(e.item_ids)} items"
        )
    return "\n".join(lines) + "\n"


def write_entity_pages(
    items: list[ItemBody],
    vault_dir: Path,
    *,
    min_tag_count: int = 3,
) -> list[Path]:
    """Write entities/index.md + entities/<slug>.md into the vault. Returns paths."""
    vault_dir = Path(vault_dir)
    ent_dir = vault_dir / "entities"
    ent_dir.mkdir(parents=True, exist_ok=True)
    for old_page in ent_dir.glob("*.md"):
        old_page.unlink()

    entities = extract_entities(items, min_tag_count=min_tag_count)
    items_by_id = {it.id: it for it, _ in items}
    stems_by_name = _disambiguated_entity_stems(entities)

    paths: list[Path] = []
    index_path = ent_dir / "index.md"
    index_path.write_text(build_entities_index(entities, stems_by_name), encoding="utf-8")
    paths.append(index_path)

    for entity in entities:
        page = ent_dir / f"{stems_by_name[entity.name]}.md"
        page.write_text(build_entity_page(entity, items_by_id, stems_by_name), encoding="utf-8")
        paths.append(page)
    return paths

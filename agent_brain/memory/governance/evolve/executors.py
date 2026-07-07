"""Execution handlers for evolve proposals."""

from __future__ import annotations

import logging
import shutil
from collections.abc import Callable
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from agent_brain.memory.store.items_store import ItemsStore
from agent_brain.contracts.memory_item import MemoryItem, MemoryType
from agent_brain.memory.recall.embedding_text import embedding_text_for_item

if TYPE_CHECKING:
    from agent_brain.memory.governance.evolve.engine import EvolveAction, EvolveProposal

_log = logging.getLogger(__name__)


class ProposalExecutor:
    """Dispatch evolve proposal execution by action."""

    def __init__(self, items_store: ItemsStore, index: Any = None) -> None:
        self.items_store = items_store
        self.index = index

    def execute(self, proposal: EvolveProposal) -> None:
        """Dispatch execution to the appropriate handler."""
        from agent_brain.memory.governance.evolve.engine import EvolveAction

        handlers: dict[EvolveAction, Callable[[EvolveProposal], None]] = {
            EvolveAction.ARCHIVE: self._execute_archive,
            EvolveAction.PROMOTE: self._execute_promote,
            EvolveAction.CONSOLIDATE: self._execute_consolidate,
            EvolveAction.CRYSTALLIZE: self._execute_crystallize,
            EvolveAction.SYNTHESIZE_SKILL: self._execute_synthesize_skill,
        }
        handler = handlers.get(proposal.action)
        if handler is not None:
            handler(proposal)

    def _execute_archive(self, proposal: EvolveProposal) -> None:
        """Move archived items to items/archived/ subdirectory."""
        archive_dir = self.items_store.items_dir / "archived"
        archive_dir.mkdir(exist_ok=True)
        for item_id in proposal.item_ids:
            src = self.items_store.items_dir / f"{item_id}.md"
            if src.exists():
                dst = archive_dir / f"{item_id}.md"
                shutil.move(str(src), str(dst))
                if self.index is not None:
                    try:
                        self.index.delete(item_id)
                    except Exception:
                        pass
                _log.info("archived %s → %s", item_id, dst)

    def _execute_promote(self, proposal: EvolveProposal) -> None:
        """Promote episode → decision and boost confidence."""
        for item_id in proposal.item_ids:
            try:
                self.items_store.update_frontmatter(
                    item_id, type="decision", confidence=0.8,
                )
                _log.info("promoted %s → decision", item_id)
            except FileNotFoundError:
                _log.warning("promote skipped: %s not found", item_id)

    def _execute_consolidate(self, proposal: EvolveProposal) -> None:
        """Merge multiple items into one consolidated item, archive originals."""
        from agent_brain.memory.governance.evolve.engine import build_consolidated_body

        items_map: dict[str, tuple[MemoryItem, str]] = {}
        for item, body in self.items_store.iter_all():
            if item.id in proposal.item_ids:
                items_map[item.id] = (item, body)

        if len(items_map) < 2:
            _log.warning("consolidate skipped: fewer than 2 items found")
            return

        sorted_items = sorted(items_map.values(), key=lambda p: p[0].created_at)
        first_item = sorted_items[0][0]
        project = first_item.project or "consolidated"
        all_tags: set[str] = set()
        for item, _ in sorted_items:
            all_tags.update(item.tags)

        now = datetime.now(timezone.utc)
        slug = f"consolidated-{project}"[:30]
        new_item = MemoryItem(
            id=f"mem-{now.strftime('%Y%m%d-%H%M%S')}-{slug}",
            type=MemoryType.fact,
            created_at=now,
            project=first_item.project,
            tenant_id=first_item.tenant_id,
            tags=sorted(all_tags),
            title=f"Consolidated: {project} ({len(sorted_items)} items)",
            summary=f"Consolidated from {len(sorted_items)} items in project {project}",
            confidence=0.8,
            refs={"files": [], "urls": [], "mems": list(items_map.keys()), "commits": []},
        )
        merged_body = build_consolidated_body(sorted_items)
        self.items_store.write(new_item, merged_body)

        if self.index is not None:
            from agent_brain.platform.embedding import HashingEmbedder
            embedder = HashingEmbedder(dim=self.index.embedding_dim)
            self.index.upsert(
                new_item, merged_body,
                embedding=embedder.embed(embedding_text_for_item(new_item)),
            )

        archive_dir = self.items_store.items_dir / "archived"
        archive_dir.mkdir(exist_ok=True)
        for item_id in items_map:
            src = self.items_store.items_dir / f"{item_id}.md"
            if src.exists():
                shutil.move(str(src), str(archive_dir / f"{item_id}.md"))
                if self.index is not None:
                    try:
                        self.index.delete(item_id)
                    except Exception:
                        pass
        _log.info("consolidated %d items → %s", len(items_map), new_item.id)

    def _execute_crystallize(self, proposal: EvolveProposal) -> None:
        """Run the crystallizer on the items referenced by this proposal."""
        from agent_brain.memory.governance.evolve.pattern_detector import detect_patterns
        from agent_brain.memory.governance.evolve.crystallizer import crystallize_policy

        items = [(it, body) for it, body in self.items_store.iter_all() if it.id in set(proposal.item_ids)]
        clusters = detect_patterns(items, only_l0=True, threshold=1)
        if clusters:
            crystallize_policy(clusters[0], self.items_store)

    def _execute_synthesize_skill(self, proposal: EvolveProposal) -> None:
        """Synthesize a skill from the policies referenced by this proposal."""
        from agent_brain.memory.governance.evolve.crystallizer import synthesize_skill

        policies = []
        for it, body in self.items_store.iter_all():
            if it.id in set(proposal.item_ids):
                policies.append((it, body))

        if len(policies) >= 2:
            existing = None
            project = next((p.project for p, _ in policies if p.project), None)
            for it, body in self.items_store.iter_all():
                if it.type == MemoryType.skill and it.project == project and it.superseded_by is None:
                    existing = (it, body)
                    break
            synthesize_skill(policies, self.items_store, existing_skill=existing)


__all__ = ["ProposalExecutor"]

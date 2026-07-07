"""Architecture tests for evolve proposal analysis ownership."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from agent_brain.memory.store.items_store import ItemsStore
from agent_brain.memory.governance.evolve.engine import EvolveAction, EvolveEngine
from agent_brain.contracts.memory_item import MemoryItem, MemoryType


def _item(suffix: str) -> MemoryItem:
    return MemoryItem(
        id=f"mem-20260528-400000-{suffix}",
        type=MemoryType.episode,
        created_at=datetime.now(timezone.utc),
        title=f"Episode {suffix}",
        summary=f"Summary {suffix}",
        project="analyzer-project",
    )


def test_evolve_engine_delegates_proposal_analysis(tmp_path: Path):
    from agent_brain.memory.governance.evolve.analyzers import ProposalAnalyzer

    store = ItemsStore(items_dir=tmp_path / "items")
    for idx in range(4):
        store.write(_item(str(idx)), f"Body {idx}")

    engine = EvolveEngine(items_store=store, scanner=None, dry_run=True)

    assert isinstance(engine.analyzer, ProposalAnalyzer)
    report = engine.evolve()

    proposals = [p for p in report.proposals if p.action == EvolveAction.CONSOLIDATE]
    assert len(proposals) == 1
    assert proposals[0].item_ids == [f"mem-20260528-400000-{idx}" for idx in range(4)]


def test_proposal_analyzer_preview_helpers_are_split_and_delegated():
    from agent_brain.memory.governance.evolve.analyzers import ProposalAnalyzer
    from agent_brain.memory.governance.evolve.proposal_previews import (
        generate_archive_preview,
        generate_consolidate_preview,
        generate_promote_preview,
        generate_skill_preview,
    )

    analyzer = ProposalAnalyzer()
    item = _item("preview")
    items = [(item, "Always follow this pattern.")]

    assert analyzer._generate_consolidate_preview("proj", items) == generate_consolidate_preview("proj", items)
    assert analyzer._generate_promote_preview(item, "body", ["always"]) == generate_promote_preview(item, "body", ["always"])
    assert analyzer._generate_skill_preview("proj", "tag", items) == generate_skill_preview("proj", "tag", items)

    archive_preview = analyzer._generate_archive_preview(item)
    assert f"# Archived Signal: {item.title}" in archive_preview
    assert f"**Original ID**: {item.id}" in archive_preview
    assert "**Status**: archived" in archive_preview


def test_archive_candidate_analysis_is_split_with_injected_now():
    from agent_brain.memory.governance.evolve.archive_analysis import find_archive_candidates

    now = datetime(2026, 6, 10, tzinfo=timezone.utc)
    old_signal = MemoryItem(
        id="mem-20260501-000000-old-signal",
        type=MemoryType.signal,
        created_at=now - timedelta(days=40),
        title="Old signal",
        summary="Signal summary",
    )
    recent_signal = MemoryItem(
        id="mem-20260605-000000-recent-signal",
        type=MemoryType.signal,
        created_at=now - timedelta(days=5),
        title="Recent signal",
        summary="Signal summary",
    )

    proposals = find_archive_candidates(
        items=[(old_signal, "old"), (recent_signal, "recent")],
        now=now,
    )

    assert len(proposals) == 1
    assert proposals[0].action == EvolveAction.ARCHIVE
    assert proposals[0].item_ids == [old_signal.id]
    assert "40 days old" in proposals[0].description


def test_promotion_candidate_analysis_is_split_and_reexported():
    from agent_brain.memory.governance.evolve import analyzers
    from agent_brain.memory.governance.evolve.promotion_analysis import find_promotion_candidates

    item = _item("promotion")
    proposals = find_promotion_candidates(
        [(item, "Always keep the rule. Never skip validation. This pattern is a lesson.")],
    )

    assert analyzers.find_promotion_candidates is find_promotion_candidates
    assert len(proposals) == 1
    assert proposals[0].action == EvolveAction.PROMOTE
    assert proposals[0].item_ids == [item.id]
    assert "always" in proposals[0].rationale.lower()


def test_skill_generation_analysis_is_split_and_reexported():
    from agent_brain.memory.governance.evolve import analyzers
    from agent_brain.memory.governance.evolve.skill_generation_analysis import find_skill_generation_candidates

    items = []
    for idx in range(3):
        item = _item(f"skill-{idx}")
        item.tags = ["workflow", "best-practice"]
        items.append((item, f"Workflow pattern {idx}"))

    proposals = find_skill_generation_candidates(items)

    assert analyzers.find_skill_generation_candidates is find_skill_generation_candidates
    assert len(proposals) == 1
    assert proposals[0].action == EvolveAction.GENERATE_SKILL
    assert proposals[0].item_ids == [item.id for item, _ in items]

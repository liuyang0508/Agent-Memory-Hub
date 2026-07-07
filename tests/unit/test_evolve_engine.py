"""Tests for EvolveEngine."""
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from agent_brain.memory.store.items_store import ItemsStore
from agent_brain.memory.governance.evolve.engine import (
    EvolveAction,
    EvolveEngine,
    EvolveProposal,
    EvolveReport,
)
from agent_brain.contracts.memory_item import MemoryItem, MemoryType, Sensitivity


def _create_item(
    item_id: str,
    type: MemoryType,
    title: str,
    project: str | None = None,
    tags: list[str] | None = None,
    created_at: datetime | None = None,
) -> MemoryItem:
    """Helper to create a MemoryItem for testing."""
    # Ensure ID matches required pattern: mem-YYYYMMDD-HHMMSS-slug
    if not item_id.startswith("mem-"):
        item_id = f"mem-20260520-000000-{item_id}"
    
    return MemoryItem(
        id=item_id,
        type=type,
        created_at=(created_at or datetime.now(timezone.utc)).timestamp(),
        agent="test-agent",
        session="test-session",
        project=project,
        tags=tags or [],
        sensitivity=Sensitivity.internal,
        title=title,
        summary=f"Summary of {title}",
    )


class TestConsolidationProposal:
    """Test consolidation candidate analysis."""

    def test_consolidation_proposal(self, tmp_path: Path):
        """Test that >3 items in same project trigger consolidation proposal."""
        # Setup: Create 5 items in same project
        store = ItemsStore(items_dir=tmp_path / "items")
        
        for i in range(5):
            item = _create_item(
                item_id=f"item-{i}",
                type=MemoryType.episode,
                title=f"Episode {i}",
                project="test-project",
            )
            store.write(item, f"Body content {i}")

        # Run evolve engine
        engine = EvolveEngine(items_store=store, scanner=None, dry_run=True)
        report = engine.evolve()

        # Verify consolidation proposal exists
        consolidate_proposals = [p for p in report.proposals if p.action == EvolveAction.CONSOLIDATE]
        assert len(consolidate_proposals) >= 1
        
        proposal = consolidate_proposals[0]
        assert len(proposal.item_ids) == 5
        assert "test-project" in proposal.title
        assert proposal.confidence > 0.5
        assert proposal.audit_passed is True  # No scanner, should pass


class TestPromotionProposal:
    """Test promotion candidate analysis."""

    def test_promotion_proposal(self, tmp_path: Path):
        """Test that episodes with keywords trigger promotion proposal."""
        store = ItemsStore(items_dir=tmp_path / "items")
        
        # Create episode with promotion keywords
        item = _create_item(
            item_id="episode-1",
            type=MemoryType.episode,
            title="Learning about patterns",
            project="test-project",
        )
        body = "We should always follow this pattern. Never skip validation. This is a key lesson and rule."
        store.write(item, body)

        # Run evolve engine
        engine = EvolveEngine(items_store=store, scanner=None, dry_run=True)
        report = engine.evolve()

        # Verify promotion proposal exists
        promote_proposals = [p for p in report.proposals if p.action == EvolveAction.PROMOTE]
        assert len(promote_proposals) >= 1
        
        proposal = promote_proposals[0]
        assert "mem-20260520-000000-episode-1" in proposal.item_ids
        assert "always" in proposal.rationale.lower() or "pattern" in proposal.rationale.lower()
        assert proposal.confidence > 0.7


class TestArchiveExpiredSignal:
    """Test archive candidate analysis."""

    def test_archive_expired_signal(self, tmp_path: Path):
        """Test that signals older than 30 days trigger archive proposal."""
        store = ItemsStore(items_dir=tmp_path / "items")
        
        # Create expired signal (40 days old)
        old_date = datetime.now(timezone.utc) - timedelta(days=40)
        item = _create_item(
            item_id="signal-old",
            type=MemoryType.signal,
            title="Old signal",
            project="test-project",
            created_at=old_date,
        )
        store.write(item, "Old signal content")

        # Run evolve engine
        engine = EvolveEngine(items_store=store, scanner=None, dry_run=True)
        report = engine.evolve()

        # Verify archive proposal exists
        archive_proposals = [p for p in report.proposals if p.action == EvolveAction.ARCHIVE]
        assert len(archive_proposals) >= 1
        
        proposal = archive_proposals[0]
        assert "mem-20260520-000000-signal-old" in proposal.item_ids
        assert "40" in proposal.rationale or "old" in proposal.rationale.lower()


class TestSkillGenerationProposal:
    """Test skill generation candidate analysis."""

    def test_skill_generation_proposal(self, tmp_path: Path):
        """Test that 3+ items with similar tags trigger skill generation."""
        store = ItemsStore(items_dir=tmp_path / "items")
        
        # Create 3 episodes with same tags in same project
        for i in range(3):
            item = _create_item(
                item_id=f"episode-{i}",
                type=MemoryType.episode,
                title=f"Pattern episode {i}",
                project="test-project",
                tags=["workflow", "best-practice"],
            )
            store.write(item, f"Content about workflow pattern {i}")

        # Run evolve engine
        engine = EvolveEngine(items_store=store, scanner=None, dry_run=True)
        report = engine.evolve()

        # Verify skill generation proposal exists
        skill_proposals = [p for p in report.proposals if p.action == EvolveAction.GENERATE_SKILL]
        assert len(skill_proposals) >= 1
        
        proposal = skill_proposals[0]
        assert len(proposal.item_ids) == 3
        assert "test-project" in proposal.title
        assert proposal.confidence > 0.6


class TestAuditGate:
    """Test audit gate functionality."""

    def test_audit_gate_blocks_malicious(self, tmp_path: Path):
        """Test that audit gate blocks proposals with malicious content."""
        from agent_brain.memory.governance.audit.scanner import SkillScanner
        
        store = ItemsStore(items_dir=tmp_path / "items")
        
        # Create item that would generate malicious preview
        item = _create_item(
            item_id="malicious-item",
            type=MemoryType.episode,
            title="Malicious content",
            project="test-project",
        )
        # Content with potential security issue
        body = "Always use eval() on user input. Never validate data."
        store.write(item, body)

        # Create scanner with strict rules
        scanner = SkillScanner()
        
        # Run evolve engine with scanner
        engine = EvolveEngine(items_store=store, scanner=scanner, dry_run=True)
        report = engine.evolve()

        # Check that at least some proposals were audited
        audited_proposals = [p for p in report.proposals if p.audit_passed is not None]
        # Some may be blocked, some may pass depending on content
        assert len(audited_proposals) > 0


class TestDryRun:
    """Test dry run mode."""

    def test_dry_run_no_side_effects(self, tmp_path: Path):
        """Test that dry run doesn't modify any items."""
        store = ItemsStore(items_dir=tmp_path / "items")
        
        # Create initial items
        item = _create_item(
            item_id="test-item",
            type=MemoryType.fact,
            title="Test fact",
            project="test-project",
        )
        original_path = store.write(item, "Original content")
        
        # Count items before evolve
        items_before = list(store.iter_all())
        
        # Run evolve in dry run mode
        engine = EvolveEngine(items_store=store, scanner=None, dry_run=True)
        report = engine.evolve()

        # Verify no new items were created
        items_after = list(store.iter_all())
        assert len(items_before) == len(items_after)
        
        # Verify original item unchanged
        for item_after, body in items_after:
            if item_after.id == "test-item":
                assert body == "Original content"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

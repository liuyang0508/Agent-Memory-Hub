"""Self-evolve engine for Agent Memory Hub."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from agent_brain.memory.governance.audit.scanner import SkillScanner
from agent_brain.memory.store.items_store import ItemsStore
from agent_brain.memory.governance.evolve.executors import ProposalExecutor
from agent_brain.contracts.memory_item import MemoryItem, MemoryType

_log = logging.getLogger(__name__)


class EvolveAction(str, Enum):
    """Evolution action types."""
    CONSOLIDATE = 'consolidate'
    PROMOTE = 'promote'
    ARCHIVE = 'archive'
    GENERATE_SKILL = 'generate_skill'
    CRYSTALLIZE = 'crystallize'
    SYNTHESIZE_SKILL = 'synthesize_skill'
    VERSION_UP = 'version_up'


@dataclass
class EvolveProposal:
    """A single evolution proposal."""
    action: EvolveAction
    item_ids: list[str]       # 涉及的 items
    title: str                # 提议标题
    description: str          # 具体描述
    rationale: str            # 理由
    confidence: float         # 0.0 - 1.0
    output_preview: str       # 预览输出(给人看的)
    audit_passed: bool | None = None  # audit gate 结果
    audit_payload: str | None = None  # 真实将被持久化的内容,交给 audit gate 扫描;None 时回退到 output_preview


@dataclass
class EvolveReport:
    """Evolution report."""
    scanned_items: int
    proposals: list[EvolveProposal] = field(default_factory=list)
    audit_blocked: int = 0
    executed: int = 0

    @property
    def approved_proposals(self) -> list[EvolveProposal]:
        return [p for p in self.proposals if p.audit_passed is True]


def build_consolidated_body(sorted_items: list[tuple[MemoryItem, str]]) -> str:
    """Build the exact merged body persisted by consolidate execution."""
    return "\n\n---\n\n".join(
        f"## {item.title}\n\n{body}" for item, body in sorted_items
    )


class EvolveEngine:
    """Self-evolve engine that analyzes memory items and proposes evolutions."""

    def __init__(
        self,
        items_store: ItemsStore,
        scanner: Optional[SkillScanner] = None,
        dry_run: bool = True,
        index: Optional[object] = None,
        decay_archive_threshold: float = 0.1,
    ):
        self.items_store = items_store
        self.scanner = scanner
        self.dry_run = dry_run
        self.index = index
        self.decay_archive_threshold = decay_archive_threshold
        self.executor = ProposalExecutor(items_store=items_store, index=index)
        from agent_brain.memory.governance.evolve.analyzers import ProposalAnalyzer
        self.analyzer = ProposalAnalyzer(
            index=index,
            decay_archive_threshold=decay_archive_threshold,
        )

    def evolve(self) -> EvolveReport:
        """Run the full evolve pipeline: analyze → propose → audit gate → (execute) → report."""
        items = list(self.items_store.iter_all())
        all_proposals = self.analyzer.analyze(items)

        audit_blocked = 0
        for proposal in all_proposals:
            passed = self._audit_gate(proposal)
            proposal.audit_passed = passed
            if not passed:
                audit_blocked += 1

        executed = 0
        if not self.dry_run:
            for proposal in all_proposals:
                if proposal.audit_passed:
                    try:
                        self._execute(proposal)
                        executed += 1
                    except Exception as exc:
                        _log.warning("execute %s failed: %s", proposal.action, exc)

        return EvolveReport(
            scanned_items=len(items),
            proposals=all_proposals,
            audit_blocked=audit_blocked,
            executed=executed,
        )

    def _execute(self, proposal: EvolveProposal) -> None:
        """Dispatch execution to the appropriate handler."""
        self.executor.execute(proposal)

    def _audit_gate(self, proposal: EvolveProposal) -> bool:
        """Run the audit scanner on the proposal's real payload.

        Scans ``audit_payload`` — the exact body that execution will persist —
        falling back to ``output_preview`` only when no payload is set. Gating
        on the preview alone would void the guarantee for consolidate, whose
        preview is a placeholder while the executed body merges every source.

        Fail-closed: any finding at critical or high severity blocks the
        proposal. Lower severities (medium/low) are advisory and allowed.
        """
        if self.scanner is None:
            return True

        payload = (
            proposal.audit_payload
            if proposal.audit_payload is not None
            else proposal.output_preview
        )
        findings = self.scanner.scan_text(
            payload, label=f"<evolve:{proposal.action.value}>"
        )
        return not any(f.severity in ('critical', 'high') for f in findings)

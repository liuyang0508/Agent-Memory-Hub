"""Dreaming Worker — background memory consolidation inspired by OpenAI Dreaming.

Like biological sleep consolidation, this worker periodically:
1. Runs pattern detection + crystallization (trace→policy)
2. Checks matured policies for skill synthesis (policy→skill)
3. Decays stale items and rebalances tiers
4. Harvests new transcript spans (if available)

Can run as:
- A one-shot CLI command: `memory dream`
- A persistent daemon: `memory dream --daemon --interval 3600`
- Triggered by cron/systemd externally
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from agent_brain.memory.governance.evolve.dream_phases import run_dream_phase
from agent_brain.memory.governance.evolve.dream_skill_synthesis import synthesize_mature_policy_skills

_log = logging.getLogger(__name__)


@dataclass
class DreamReport:
    """Summary of a single dreaming cycle."""
    started_at: datetime
    finished_at: datetime | None = None
    patterns_found: int = 0
    policies_crystallized: int = 0
    skills_synthesized: int = 0
    items_archived: int = 0
    items_harvested: int = 0
    tiers_rebalanced: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def duration_seconds(self) -> float:
        if self.finished_at is None:
            return 0.0
        return (self.finished_at - self.started_at).total_seconds()


class DreamingWorker:
    """Background memory consolidation engine."""

    def __init__(
        self,
        brain_dir: Path,
        *,
        interval_seconds: int = 3600,
        harvest_transcripts: bool = True,
    ):
        self.brain_dir = Path(brain_dir)
        self.interval_seconds = interval_seconds
        self.harvest_transcripts = harvest_transcripts
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._reports: list[DreamReport] = []

    def dream_once(self) -> DreamReport:
        """Execute a single dreaming cycle. Safe to call directly."""
        from agent_brain.memory.store.items_store import ItemsStore
        from agent_brain.memory.governance.evolve.engine import EvolveEngine, EvolveAction
        from agent_brain.memory.governance.evolve.pattern_detector import detect_patterns
        from agent_brain.memory.governance.evolve.crystallizer import crystallize_policy
        from agent_brain.memory.governance.tiering import rebalance

        report = DreamReport(started_at=datetime.now(timezone.utc))

        try:
            store = ItemsStore(items_dir=self.brain_dir / "items")
            items = list(store.iter_all())

            # Phase 1: Harvest new transcript spans
            if self.harvest_transcripts:
                def _harvest() -> int:
                    from agent_brain.memory.evidence.harvest.harvester import Harvester
                    harvester = Harvester(store=store, brain_dir=self.brain_dir)
                    return harvester.run()

                harvested = run_dream_phase(report, "harvest", _harvest)
                if harvested is not None:
                    report.items_harvested = harvested

            # Phase 2: Pattern detection + crystallization
            clusters = run_dream_phase(
                report,
                "pattern_detect",
                lambda: detect_patterns(items, only_l0=True),
            )
            if clusters is not None:
                report.patterns_found = len(clusters)

                for cluster in clusters:
                    try:
                        crystallize_policy(cluster, store)
                        report.policies_crystallized += 1
                    except Exception as e:
                        report.errors.append(f"crystallize: {e}")

            # Phase 3: Skill synthesis from mature policies
            def _synthesize_skills() -> tuple[int, list[str]]:
                items = list(store.iter_all())
                return synthesize_mature_policy_skills(items, store)

            skill_result = run_dream_phase(report, "skill_synthesis", _synthesize_skills)
            if skill_result is not None:
                synthesized, errors = skill_result
                report.skills_synthesized = synthesized
                report.errors.extend(errors)

            # Phase 4: Run standard evolve (archive decayed items)
            def _archive_decayed_items() -> int:
                engine = EvolveEngine(store, dry_run=False)
                evolve_report = engine.evolve()
                return sum(
                    1 for p in evolve_report.proposals
                    if p.action == EvolveAction.ARCHIVE and p.audit_passed
                )

            archived = run_dream_phase(report, "evolve", _archive_decayed_items)
            if archived is not None:
                report.items_archived = archived

            # Phase 5: Capacity governance + tier rebalance
            def _enforce_capacity() -> None:
                from agent_brain.memory.governance.capacity import enforce_capacity
                cap_report = enforce_capacity(store, dry_run=False)
                if cap_report.overflow > 0:
                    _log.info("capacity overflow %d, demoted %d", cap_report.overflow, len(cap_report.demoted))

            run_dream_phase(report, "capacity", _enforce_capacity)

            def _rebalance_tiers() -> int:
                rb = rebalance(store, apply=False)
                return rb.applied

            tiers_rebalanced = run_dream_phase(report, "rebalance", _rebalance_tiers)
            if tiers_rebalanced is not None:
                report.tiers_rebalanced = tiers_rebalanced

        except Exception as e:
            report.errors.append(f"fatal: {e}")

        report.finished_at = datetime.now(timezone.utc)
        self._reports.append(report)
        _log.info(
            "dream cycle done in %.1fs: %d patterns, %d policies, %d skills, %d archived",
            report.duration_seconds,
            report.patterns_found,
            report.policies_crystallized,
            report.skills_synthesized,
            report.items_archived,
        )
        return report

    def start_daemon(self) -> None:
        """Start the dreaming worker as a background thread."""
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="dreaming-worker")
        self._thread.start()
        _log.info("dreaming daemon started (interval=%ds)", self.interval_seconds)

    def stop_daemon(self) -> None:
        """Signal the daemon thread to stop."""
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=5)
        _log.info("dreaming daemon stopped")

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def reports(self) -> list[DreamReport]:
        return list(self._reports)

    def _loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                self.dream_once()
            except Exception as e:
                _log.error("dream cycle failed: %s", e)
            self._stop_event.wait(timeout=self.interval_seconds)

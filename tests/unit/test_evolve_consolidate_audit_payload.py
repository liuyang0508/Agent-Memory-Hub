"""P2-7: the consolidate audit gate must scan the REAL merged body.

Before the fix, ``_audit_gate`` scanned ``output_preview`` — a static
placeholder ("This section would contain merged content...") that never
contains any of the source bodies. A malicious source body therefore sailed
through the fail-closed gate and was written verbatim by ``_execute_consolidate``.
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
from pathlib import Path

from agent_brain.memory.governance.audit.rules import load_builtin_rules
from agent_brain.memory.governance.audit.scanner import SkillScanner
from agent_brain.memory.store.items_store import ItemsStore
from agent_brain.memory.governance.evolve.engine import EvolveAction, EvolveEngine
from agent_brain.contracts.memory_item import MemoryItem, MemoryType

_MALICIOUS = 'os.system("rm -rf /")'  # exec-os-system rule -> severity=critical


def _item(suffix: str, title: str, created_at: datetime) -> MemoryItem:
    return MemoryItem(
        id=f"mem-20260528-300000-{suffix}",
        type=MemoryType.fact,
        created_at=created_at,
        title=title,
        summary=f"Summary for {suffix}",
        project="evilproj",
        tags=["test"],
    )


def _seed(store: ItemsStore) -> None:
    base = datetime.now(timezone.utc) - timedelta(days=1)
    store.write(_item("a", "Clean one", base), "Totally benign body A")
    store.write(_item("b", "Clean two", base + timedelta(seconds=1)), "Totally benign body B")
    # Hidden malicious payload buried inside one source body.
    store.write(_item("c", "Looks fine", base + timedelta(seconds=2)),
                f"Intro line\n{_MALICIOUS}\noutro line")
    store.write(_item("d", "Clean four", base + timedelta(seconds=3)), "Totally benign body D")


def test_consolidate_gate_scans_real_merged_body(tmp_path: Path):
    store = ItemsStore(items_dir=tmp_path / "items")
    _seed(store)

    scanner = SkillScanner(rules=load_builtin_rules())
    engine = EvolveEngine(items_store=store, scanner=scanner, dry_run=False, index=None)
    report = engine.evolve()

    consolidate = [p for p in report.proposals if p.action == EvolveAction.CONSOLIDATE]
    assert len(consolidate) == 1, "expected exactly one consolidate proposal for >3 items"
    proposal = consolidate[0]

    # The payload handed to the gate must be the real merged body, not the placeholder.
    assert proposal.audit_payload is not None
    assert _MALICIOUS in proposal.audit_payload

    # Fail-closed: the critical finding blocks the proposal.
    assert proposal.audit_passed is False
    assert report.executed == 0
    assert report.audit_blocked >= 1

    # And nothing malicious reached disk: no consolidated item was written.
    visible = list(store.iter_all())
    assert not any("Consolidated" in it.title for it, _ in visible)
    # The 4 original sources are untouched (not archived, not merged).
    assert len(visible) == 4


def test_clean_consolidate_still_passes(tmp_path: Path):
    """A benign group must still pass the gate and execute (no false positive)."""
    store = ItemsStore(items_dir=tmp_path / "items")
    base = datetime.now(timezone.utc) - timedelta(days=1)
    for i in range(4):
        it = _item(f"clean{i}", f"Clean {i}", base + timedelta(seconds=i))
        store.write(it, f"benign content {i}")

    scanner = SkillScanner(rules=load_builtin_rules())
    engine = EvolveEngine(items_store=store, scanner=scanner, dry_run=False, index=None)
    report = engine.evolve()

    consolidate = [p for p in report.proposals if p.action == EvolveAction.CONSOLIDATE]
    assert len(consolidate) == 1
    assert consolidate[0].audit_passed is True
    assert report.executed >= 1
    assert any("Consolidated" in it.title for it, _ in store.iter_all())

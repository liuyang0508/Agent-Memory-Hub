"""P1-9: git-backed snapshots over the brain pool (md-native, diffable, restorable).

Borrowed from rohitg00/agentmemory's git snapshots, but on our markdown source
of truth instead of a binary store — so a snapshot is a real git commit you can
diff and restore.
"""
from pathlib import Path

import pytest


def _write(items: Path, name: str, text: str) -> None:
    items.mkdir(parents=True, exist_ok=True)
    (items / f"{name}.md").write_text(text, encoding="utf-8")


def test_snapshot_creates_commit(tmp_path: Path):
    from agent_brain.platform.history import BrainHistory

    brain = tmp_path / "brain"
    _write(brain / "items", "a", "first")
    h = BrainHistory(brain_dir=brain)
    sha = h.snapshot("init")
    assert sha
    assert len(h.log()) == 1
    assert h.log()[0]["message"] == "init"


def test_snapshot_noop_when_unchanged(tmp_path: Path):
    from agent_brain.platform.history import BrainHistory

    brain = tmp_path / "brain"
    _write(brain / "items", "a", "first")
    h = BrainHistory(brain_dir=brain)
    h.snapshot("init")
    assert h.snapshot("again") is None  # nothing changed → no empty commit
    assert len(h.log()) == 1


def test_restore_brings_back_deleted_item(tmp_path: Path):
    from agent_brain.platform.history import BrainHistory

    brain = tmp_path / "brain"
    items = brain / "items"
    _write(items, "a", "first")
    h = BrainHistory(brain_dir=brain)
    first = h.snapshot("init")

    (items / "a.md").unlink()
    h.snapshot("deleted a")
    assert not (items / "a.md").exists()

    h.restore(first)
    assert (items / "a.md").exists()
    assert (items / "a.md").read_text() == "first"


def test_diff_reports_changes(tmp_path: Path):
    from agent_brain.platform.history import BrainHistory

    brain = tmp_path / "brain"
    items = brain / "items"
    _write(items, "a", "first")
    h = BrainHistory(brain_dir=brain)
    h.snapshot("init")
    (items / "a.md").write_text("second", encoding="utf-8")
    diff = h.diff()
    assert "second" in diff or "first" in diff

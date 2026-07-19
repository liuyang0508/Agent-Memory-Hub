import stat
import subprocess
from pathlib import Path

import pytest

from agent_brain.memory.governance import lifecycle_snapshot as snapshot_module
from agent_brain.memory.governance.lifecycle_snapshot import (
    LifecycleSnapshotError,
    LifecycleSnapshotStore,
)


OLD_ID = "mem-20260719-100000-snapshot-old"
NEW_ID = "mem-20260719-110000-snapshot-new"


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(cwd), *args],
        check=True,
        capture_output=True,
        text=True,
    )


def test_snapshot_uses_private_two_file_tree_without_touching_outer_git(
    tmp_brain_dir: Path,
) -> None:
    _git(tmp_brain_dir, "init", "-q")
    _git(tmp_brain_dir, "config", "user.email", "test@example.invalid")
    _git(tmp_brain_dir, "config", "user.name", "test")
    unrelated = tmp_brain_dir / "unrelated.txt"
    unrelated.write_text("staged\n", encoding="utf-8")
    _git(tmp_brain_dir, "add", "unrelated.txt")
    unrelated.write_text("dirty\n", encoding="utf-8")
    staged_before = _git(tmp_brain_dir, "diff", "--cached", "--binary").stdout
    dirty_before = _git(tmp_brain_dir, "diff", "--binary").stdout
    config_before = _git(tmp_brain_dir, "config", "--local", "--list").stdout

    snapshot = LifecycleSnapshotStore(tmp_brain_dir).snapshot_pair(
        OLD_ID, b"old secret\n", NEW_ID, b"new secret\n"
    )

    assert len(snapshot) in (40, 64)
    assert _git(tmp_brain_dir, "diff", "--cached", "--binary").stdout == staged_before
    assert _git(tmp_brain_dir, "diff", "--binary").stdout == dirty_before
    assert _git(tmp_brain_dir, "config", "--local", "--list").stdout == config_before
    repo = tmp_brain_dir / "runtime" / "lifecycle-history.git"
    tree = subprocess.run(
        ["git", "--git-dir", str(repo), "ls-tree", "-r", "--name-only", snapshot],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    assert tree == [f"items/{OLD_ID}.md", f"items/{NEW_ID}.md"]
    assert stat.S_IMODE(repo.stat().st_mode) == 0o700


def test_restore_is_pair_selective_and_treats_special_id_literally(
    tmp_brain_dir: Path,
) -> None:
    items = tmp_brain_dir / "items"
    items.mkdir(exist_ok=True)
    old_id = "mem-20260719-100000-special-*"
    third_id = "mem-20260719-100000-special-third"
    paths = {
        old_id: items / f"{old_id}.md",
        NEW_ID: items / f"{NEW_ID}.md",
        third_id: items / f"{third_id}.md",
    }
    paths[old_id].write_bytes(b"old before")
    paths[NEW_ID].write_bytes(b"new before")
    paths[third_id].write_bytes(b"third before")
    runtime_other = tmp_brain_dir / "runtime" / "unrelated.jsonl"
    runtime_other.parent.mkdir()
    runtime_other.write_bytes(b"runtime before")
    store = LifecycleSnapshotStore(tmp_brain_dir)
    snapshot = store.snapshot_pair(
        old_id, paths[old_id].read_bytes(), NEW_ID, paths[NEW_ID].read_bytes()
    )
    paths[old_id].write_bytes(b"old after")
    paths[NEW_ID].write_bytes(b"new after")
    paths[third_id].write_bytes(b"third after")
    runtime_other.write_bytes(b"runtime after")

    store.restore_pair(snapshot, old_id, NEW_ID)

    assert paths[old_id].read_bytes() == b"old before"
    assert paths[NEW_ID].read_bytes() == b"new before"
    assert paths[third_id].read_bytes() == b"third after"
    assert runtime_other.read_bytes() == b"runtime after"


@pytest.mark.parametrize("unsafe", ["unmarked", "symlink"])
def test_snapshot_refuses_unmarked_or_symlink_private_repo(
    tmp_brain_dir: Path, unsafe: str
) -> None:
    runtime = tmp_brain_dir / "runtime"
    runtime.mkdir()
    repo = runtime / "lifecycle-history.git"
    if unsafe == "unmarked":
        repo.mkdir()
    else:
        external = tmp_brain_dir / "external-repo"
        external.mkdir()
        repo.symlink_to(external, target_is_directory=True)

    with pytest.raises(LifecycleSnapshotError, match="SNAPSHOT_FAILED"):
        LifecycleSnapshotStore(tmp_brain_dir).snapshot_pair(
            OLD_ID, b"old", NEW_ID, b"new"
        )


def test_snapshot_refuses_symlink_items_root(tmp_brain_dir: Path) -> None:
    items = tmp_brain_dir / "items"
    items.rmdir()
    external = tmp_brain_dir / "external-items"
    external.mkdir()
    items.symlink_to(external, target_is_directory=True)

    with pytest.raises(LifecycleSnapshotError, match="SNAPSHOT_FAILED"):
        LifecycleSnapshotStore(tmp_brain_dir).snapshot_pair(
            OLD_ID, b"old", NEW_ID, b"new"
        )


def test_snapshot_disables_malicious_git_hooks(tmp_brain_dir: Path) -> None:
    store = LifecycleSnapshotStore(tmp_brain_dir)
    first = store.snapshot_pair(OLD_ID, b"old", NEW_ID, b"new")
    assert first
    repo = tmp_brain_dir / "runtime" / "lifecycle-history.git"
    sentinel = tmp_brain_dir / "hook-ran"
    hook = repo / "hooks" / "reference-transaction"
    hook.write_text(f"#!/bin/sh\ntouch '{sentinel}'\n", encoding="utf-8")
    hook.chmod(0o700)

    store.snapshot_pair(OLD_ID, b"old2", NEW_ID, b"new2")

    assert not sentinel.exists()


def test_git_timeout_maps_to_closed_snapshot_error(
    tmp_brain_dir: Path, monkeypatch
) -> None:
    def timeout(*_args, **_kwargs):
        raise subprocess.TimeoutExpired(["git"], 5, output=b"private body")

    monkeypatch.setattr(snapshot_module.subprocess, "run", timeout)

    with pytest.raises(LifecycleSnapshotError, match="SNAPSHOT_FAILED") as caught:
        LifecycleSnapshotStore(tmp_brain_dir).snapshot_pair(
            OLD_ID, b"old secret", NEW_ID, b"new secret"
        )

    assert "private" not in str(caught.value)

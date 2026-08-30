import os
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


def test_snapshot_fsyncs_object_and_ref_files_and_directories_bottom_up(
    tmp_brain_dir: Path, monkeypatch
) -> None:
    real_fsync = os.fsync
    fsynced: list[tuple[int, int]] = []

    def tracking_fsync(descriptor):
        opened = os.fstat(descriptor)
        fsynced.append((opened.st_dev, opened.st_ino))
        return real_fsync(descriptor)

    monkeypatch.setattr(os, "fsync", tracking_fsync)

    LifecycleSnapshotStore(tmp_brain_dir).snapshot_pair(
        OLD_ID, b"old", NEW_ID, b"new"
    )

    repo = tmp_brain_dir / "runtime" / "lifecycle-history.git"
    object_files = [
        path
        for path in (repo / "objects").glob("[0-9a-f][0-9a-f]/*")
        if path.is_file()
    ]
    ref = repo / "refs" / "heads" / "lifecycle"
    assert object_files
    assert ref.is_file()
    for path in [*object_files, ref]:
        identity = (path.stat().st_dev, path.stat().st_ino)
        assert identity in fsynced
    object_file = object_files[0]
    chain = [object_file, object_file.parent, repo / "objects", repo]
    last_positions = [
        len(fsynced) - 1 - fsynced[::-1].index((path.stat().st_dev, path.stat().st_ino))
        for path in chain
    ]
    assert last_positions == sorted(last_positions)


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
    paths[old_id].unlink()
    paths[NEW_ID].unlink()
    paths[third_id].write_bytes(b"third after")
    runtime_other.write_bytes(b"runtime after")

    store.restore_pair(snapshot, old_id, NEW_ID)

    assert paths[old_id].read_bytes() == b"old before"
    assert paths[NEW_ID].read_bytes() == b"new before"
    assert stat.S_IMODE(paths[old_id].stat().st_mode) == 0o600
    assert stat.S_IMODE(paths[NEW_ID].stat().st_mode) == 0o600
    assert paths[third_id].read_bytes() == b"third after"
    assert runtime_other.read_bytes() == b"runtime after"


def test_restore_keeps_validated_items_inode_when_path_is_replaced_by_directory(
    tmp_brain_dir: Path, monkeypatch
) -> None:
    items = tmp_brain_dir / "items"
    old_path = items / f"{OLD_ID}.md"
    new_path = items / f"{NEW_ID}.md"
    old_path.write_bytes(b"old before")
    new_path.write_bytes(b"new before")
    store = LifecycleSnapshotStore(tmp_brain_dir)
    snapshot = store.snapshot_pair(
        OLD_ID, old_path.read_bytes(), NEW_ID, new_path.read_bytes()
    )
    old_path.write_bytes(b"old after")
    new_path.write_bytes(b"new after")
    moved = tmp_brain_dir / "validated-items"
    real_read_tree = store._read_tree
    swapped = False

    def swap_then_read(repo, object_id):
        nonlocal swapped
        if not swapped:
            items.rename(moved)
            items.mkdir()
            (items / f"{OLD_ID}.md").write_bytes(b"victim old")
            (items / f"{NEW_ID}.md").write_bytes(b"victim new")
            swapped = True
        return real_read_tree(repo, object_id)

    monkeypatch.setattr(store, "_read_tree", swap_then_read)

    store.restore_pair(snapshot, OLD_ID, NEW_ID)

    assert (moved / f"{OLD_ID}.md").read_bytes() == b"old before"
    assert (moved / f"{NEW_ID}.md").read_bytes() == b"new before"
    assert (items / f"{OLD_ID}.md").read_bytes() == b"victim old"
    assert (items / f"{NEW_ID}.md").read_bytes() == b"victim new"


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


def test_snapshot_accepts_macos_var_private_var_alias_by_inode(
    tmp_brain_dir: Path,
) -> None:
    text = str(tmp_brain_dir)
    if not text.startswith("/private/var/"):
        pytest.skip("macOS /var alias not present")
    alias_items = Path(text.replace("/private/var/", "/var/", 1)) / "items"

    snapshot = LifecycleSnapshotStore(tmp_brain_dir, alias_items).snapshot_pair(
        OLD_ID, b"old", NEW_ID, b"new"
    )

    assert snapshot


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

    store = LifecycleSnapshotStore(tmp_brain_dir)
    with monkeypatch.context() as scoped:
        scoped.setattr(snapshot_module.subprocess, "run", timeout)
        with pytest.raises(LifecycleSnapshotError, match="SNAPSHOT_FAILED") as caught:
            store.snapshot_pair(OLD_ID, b"old secret", NEW_ID, b"new secret")

    assert "private" not in str(caught.value)
    runtime = tmp_brain_dir / "runtime"
    assert not (runtime / "lifecycle-history.git").exists()
    assert not list(runtime.glob(".lifecycle-history-*.tmp"))
    assert store.snapshot_pair(OLD_ID, b"old", NEW_ID, b"new")


@pytest.mark.parametrize("control", [False, True])
def test_snapshot_marker_failure_cleans_temp_and_allows_retry(
    tmp_brain_dir: Path, monkeypatch, control: bool
) -> None:
    store = LifecycleSnapshotStore(tmp_brain_dir)
    real_write = os.write
    marker_fd = None

    def fail_marker(fd, data):
        nonlocal marker_fd
        if bytes(data).startswith(b"agent-memory-hub lifecycle"):
            if control:
                marker_fd = fd
                return real_write(fd, data[:1])
            raise OSError("marker failure")
        if control and fd == marker_fd:
            raise KeyboardInterrupt("marker control")
        return real_write(fd, data)

    with monkeypatch.context() as scoped:
        scoped.setattr(os, "write", fail_marker)
        expected = KeyboardInterrupt if control else LifecycleSnapshotError
        with pytest.raises(expected):
            store.snapshot_pair(OLD_ID, b"old", NEW_ID, b"new")

    runtime = tmp_brain_dir / "runtime"
    assert not (runtime / "lifecycle-history.git").exists()
    assert not list(runtime.glob(".lifecycle-history-*.tmp"))
    assert store.snapshot_pair(OLD_ID, b"old", NEW_ID, b"new")


def test_snapshot_marker_retries_one_byte_short_write(
    tmp_brain_dir: Path, monkeypatch
) -> None:
    store = LifecycleSnapshotStore(tmp_brain_dir)
    real_write = os.write
    shortened = False

    def short_once(fd, data):
        nonlocal shortened
        if bytes(data).startswith(b"agent-memory-hub lifecycle") and not shortened:
            shortened = True
            return real_write(fd, data[:1])
        return real_write(fd, data)

    monkeypatch.setattr(os, "write", short_once)

    snapshot = store.snapshot_pair(OLD_ID, b"old", NEW_ID, b"new")

    assert shortened is True
    assert snapshot
    marker = (
        tmp_brain_dir
        / "runtime"
        / "lifecycle-history.git"
        / "amh-lifecycle-repository"
    )
    assert marker.read_bytes() == snapshot_module._MARKER_BYTES


def test_snapshot_marker_retries_multiple_short_writes(
    tmp_brain_dir: Path, monkeypatch
) -> None:
    store = LifecycleSnapshotStore(tmp_brain_dir)
    real_write = os.write
    marker_fd = None
    writes = 0

    def short_repeatedly(fd, data):
        nonlocal marker_fd, writes
        if bytes(data).startswith(b"agent-memory-hub lifecycle"):
            marker_fd = fd
        if fd == marker_fd:
            writes += 1
            return real_write(fd, data[:3])
        return real_write(fd, data)

    monkeypatch.setattr(os, "write", short_repeatedly)

    snapshot = store.snapshot_pair(OLD_ID, b"old", NEW_ID, b"new")

    assert writes > 2
    assert snapshot


def test_snapshot_marker_zero_write_cleans_temp_and_allows_retry(
    tmp_brain_dir: Path, monkeypatch
) -> None:
    store = LifecycleSnapshotStore(tmp_brain_dir)
    real_write = os.write

    def zero_marker(fd, data):
        if bytes(data).startswith(b"agent-memory-hub lifecycle"):
            return 0
        return real_write(fd, data)

    with monkeypatch.context() as scoped:
        scoped.setattr(os, "write", zero_marker)
        with pytest.raises(LifecycleSnapshotError, match="SNAPSHOT_FAILED"):
            store.snapshot_pair(OLD_ID, b"old", NEW_ID, b"new")

    runtime = tmp_brain_dir / "runtime"
    assert not (runtime / "lifecycle-history.git").exists()
    assert not list(runtime.glob(".lifecycle-history-*.tmp"))
    assert store.snapshot_pair(OLD_ID, b"old", NEW_ID, b"new")


def test_snapshot_init_adopts_concurrently_published_valid_repository(
    tmp_brain_dir: Path, monkeypatch
) -> None:
    store = LifecycleSnapshotStore(tmp_brain_dir)
    real_rename = snapshot_module.SecureDirectory.rename
    raced = False
    runtime_fsynced_after_race = False
    real_fsync = snapshot_module.SecureDirectory.fsync

    def publish_then_report_conflict(directory, source, destination):
        nonlocal raced
        if destination == "lifecycle-history.git" and not raced:
            raced = True
            real_rename(directory, source, destination)
            raise FileExistsError("simulated concurrent publication")
        return real_rename(directory, source, destination)

    def track_runtime_fsync(directory):
        nonlocal runtime_fsynced_after_race
        opened = os.fstat(directory.fd)
        runtime = tmp_brain_dir / "runtime"
        if raced and runtime.exists():
            expected = runtime.stat()
            if (opened.st_dev, opened.st_ino) == (expected.st_dev, expected.st_ino):
                runtime_fsynced_after_race = True
        return real_fsync(directory)

    monkeypatch.setattr(
        snapshot_module.SecureDirectory, "rename", publish_then_report_conflict
    )
    monkeypatch.setattr(snapshot_module.SecureDirectory, "fsync", track_runtime_fsync)

    snapshot = store.snapshot_pair(OLD_ID, b"old", NEW_ID, b"new")

    assert raced is True
    assert runtime_fsynced_after_race is True
    assert snapshot
    runtime = tmp_brain_dir / "runtime"
    assert (runtime / "lifecycle-history.git").is_dir()
    assert not list(runtime.glob(".lifecycle-history-*.tmp"))

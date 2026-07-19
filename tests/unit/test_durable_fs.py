import os
import stat
from pathlib import Path

from agent_brain.memory.store import durable_fs as durable_fs_module
from agent_brain.memory.store.durable_fs import (
    SecureDirectory,
    lifecycle_mutation_capability,
)


def test_lifecycle_mutation_capability_requires_git_without_writing(
    tmp_path: Path, monkeypatch
) -> None:
    before = list(tmp_path.iterdir())
    monkeypatch.setenv("PATH", "")

    assert lifecycle_mutation_capability() is False
    assert list(tmp_path.iterdir()) == before


def test_lifecycle_mutation_capability_covers_recursive_repo_dependencies(
    monkeypatch,
) -> None:
    monkeypatch.setattr(os, "supports_fd", os.supports_fd - {os.listdir})
    assert lifecycle_mutation_capability() is False


def test_lifecycle_mutation_capability_covers_rmdir_and_replace_dir_fd(
    monkeypatch,
) -> None:
    monkeypatch.setattr(os, "supports_dir_fd", os.supports_dir_fd - {os.rmdir})
    assert lifecycle_mutation_capability() is False

    monkeypatch.undo()

    monkeypatch.setattr(durable_fs_module, "_REPLACE_SUPPORTS_DIR_FD", False)
    assert lifecycle_mutation_capability() is False


def test_atomic_write_stays_on_open_items_inode_after_parent_swap(
    tmp_path: Path, monkeypatch
) -> None:
    items = tmp_path / "items"
    victim = tmp_path / "victim"
    items.mkdir()
    victim.mkdir()
    (items / "target.md").write_bytes(b"original")
    (victim / "target.md").write_bytes(b"victim")
    moved = tmp_path / "moved-items"
    real_replace = os.replace
    swapped = False

    def swap_then_replace(source, destination, **kwargs):
        nonlocal swapped
        if not swapped:
            items.rename(moved)
            items.symlink_to(victim, target_is_directory=True)
            swapped = True
        return real_replace(source, destination, **kwargs)

    monkeypatch.setattr(os, "replace", swap_then_replace)

    with SecureDirectory.open(items) as directory:
        directory.atomic_write("target.md", b"updated")

    assert (moved / "target.md").read_bytes() == b"updated"
    assert (victim / "target.md").read_bytes() == b"victim"


def test_atomic_cleanup_failures_do_not_mask_keyboard_interrupt(
    tmp_path: Path, monkeypatch
) -> None:
    target = tmp_path / "target.md"
    target.write_bytes(b"before")
    real_close = os.close
    real_unlink = os.unlink

    def interrupt_write(_fd, _data):
        raise KeyboardInterrupt("original control")

    def close_then_fail(fd):
        real_close(fd)
        raise OSError("close detail")

    def unlink_then_fail(name, **kwargs):
        real_unlink(name, **kwargs)
        raise OSError("unlink detail")

    with SecureDirectory.open(tmp_path) as directory:
        with monkeypatch.context() as scoped:
            scoped.setattr(os, "write", interrupt_write)
            scoped.setattr(os, "close", close_then_fail)
            scoped.setattr(os, "unlink", unlink_then_fail)
            try:
                directory.atomic_write("target.md", b"after")
            except KeyboardInterrupt as error:
                assert str(error) == "original control"
                assert "ATOMIC_WRITE_CLEANUP_FAILED" in getattr(error, "__notes__", [])
            else:
                raise AssertionError("KeyboardInterrupt was swallowed")

    assert target.read_bytes() == b"before"


def test_atomic_create_never_replaces_concurrent_target(
    tmp_path: Path, monkeypatch
) -> None:
    real_link = os.link
    injected = False

    def publish_competitor_then_link(source, target, **kwargs):
        nonlocal injected
        if not injected:
            injected = True
            target_fd = os.open(
                target,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
                dir_fd=kwargs["dst_dir_fd"],
            )
            try:
                os.write(target_fd, b"competitor")
                os.fsync(target_fd)
            finally:
                os.close(target_fd)
        return real_link(source, target, **kwargs)

    with SecureDirectory.open(tmp_path) as directory:
        with monkeypatch.context() as scoped:
            scoped.setattr(os, "link", publish_competitor_then_link)
            try:
                directory.atomic_create("target.md", b"ours")
            except FileExistsError:
                pass
            else:
                raise AssertionError("concurrent target was overwritten")

    assert (tmp_path / "target.md").read_bytes() == b"competitor"


def test_atomic_create_fsyncs_file_then_directory(tmp_path: Path, monkeypatch) -> None:
    events: list[str] = []
    real_fsync = os.fsync
    real_link = os.link

    def tracking_fsync(descriptor: int) -> None:
        mode = os.fstat(descriptor).st_mode
        events.append("dir-fsync" if stat.S_ISDIR(mode) else "file-fsync")
        real_fsync(descriptor)

    def tracking_link(source, target, **kwargs):
        events.append("link")
        return real_link(source, target, **kwargs)

    with SecureDirectory.open(tmp_path) as directory:
        with monkeypatch.context() as scoped:
            scoped.setattr(os, "fsync", tracking_fsync)
            scoped.setattr(os, "link", tracking_link)
            directory.atomic_create("target.md", b"created")

    assert (tmp_path / "target.md").read_bytes() == b"created"
    assert events[:3] == ["file-fsync", "link", "dir-fsync"]

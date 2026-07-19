import os
from pathlib import Path

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

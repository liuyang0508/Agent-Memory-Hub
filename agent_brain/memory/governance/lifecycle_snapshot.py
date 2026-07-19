"""Private two-item Git snapshots for lifecycle transaction rollback."""

from __future__ import annotations

import os
import re
import stat
import subprocess
from pathlib import Path

from agent_brain.contracts.memory_item import is_valid_memory_item_id
from agent_brain.memory.store.items_store import _atomic_write_bytes


GIT_TIMEOUT_SECONDS = 5
_MARKER_NAME = "amh-lifecycle-repository"
_MARKER_BYTES = b"agent-memory-hub lifecycle snapshot repository v1\n"
_OBJECT_ID = re.compile(rb"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")
_O_NOFOLLOW = getattr(os, "O_NOFOLLOW", None)


class LifecycleSnapshotError(RuntimeError):
    """A private lifecycle snapshot could not be created or restored."""


class LifecycleSnapshotStore:
    """Store two-item commits in a private bare repository under runtime/."""

    def __init__(self, brain_dir: Path, items_dir: Path | None = None) -> None:
        self.brain_dir = Path(brain_dir).resolve()
        configured_items = (
            Path(items_dir) if items_dir is not None else self.brain_dir / "items"
        )
        self.items_dir = configured_items.absolute()
        self.runtime_dir = self.brain_dir / "runtime"
        self.repo_dir = self.runtime_dir / "lifecycle-history.git"

    def snapshot_pair(
        self,
        obsolete_id: str,
        obsolete_bytes: bytes,
        replacement_id: str,
        replacement_bytes: bytes,
    ) -> str:
        try:
            self._validate_items_root()
            self._validate_pair_ids(obsolete_id, replacement_id)
            self._ensure_repository()
            obsolete_blob = self._write_blob(obsolete_bytes)
            replacement_blob = self._write_blob(replacement_bytes)
            item_tree = self._make_tree(
                [
                    ("100644", "blob", obsolete_blob, f"{obsolete_id}.md"),
                    (
                        "100644",
                        "blob",
                        replacement_blob,
                        f"{replacement_id}.md",
                    ),
                ]
            )
            root_tree = self._make_tree(
                [("040000", "tree", item_tree, "items")]
            )
            parent = self._current_commit()
            args = ["commit-tree", root_tree, "-m", "lifecycle pair snapshot"]
            if parent is not None:
                args.extend(["-p", parent])
            commit = self._object_id(self._git(*args).stdout)
            update_args = ["update-ref", "refs/heads/lifecycle", commit]
            if parent is not None:
                update_args.append(parent)
            self._git(*update_args)
            return commit
        except LifecycleSnapshotError:
            raise
        except BaseException as error:
            if not isinstance(error, Exception):
                raise
            raise LifecycleSnapshotError("SNAPSHOT_FAILED") from None

    def restore_pair(
        self, snapshot: str, obsolete_id: str, replacement_id: str
    ) -> None:
        try:
            self._validate_items_root()
            self._validate_pair_ids(obsolete_id, replacement_id)
            if _OBJECT_ID.fullmatch(snapshot.encode("ascii")) is None:
                raise LifecycleSnapshotError("SNAPSHOT_FAILED")
            self._ensure_repository(create=False)
            root_entries = self._read_tree(snapshot)
            if set(root_entries) != {b"items"}:
                raise LifecycleSnapshotError("SNAPSHOT_FAILED")
            root_type, items_tree = root_entries[b"items"]
            if root_type != b"tree":
                raise LifecycleSnapshotError("SNAPSHOT_FAILED")
            item_entries = self._read_tree(items_tree.decode("ascii"))
            expected = {
                f"{obsolete_id}.md".encode("utf-8"),
                f"{replacement_id}.md".encode("utf-8"),
            }
            if set(item_entries) != expected:
                raise LifecycleSnapshotError("SNAPSHOT_FAILED")
            restored: list[tuple[str, bytes]] = []
            for item_id in (obsolete_id, replacement_id):
                entry_type, blob = item_entries[f"{item_id}.md".encode("utf-8")]
                if entry_type != b"blob":
                    raise LifecycleSnapshotError("SNAPSHOT_FAILED")
                data = self._git("cat-file", "blob", blob.decode("ascii")).stdout
                restored.append((item_id, data))
            for item_id, data in restored:
                _atomic_write_bytes(self.items_dir / f"{item_id}.md", data)
        except LifecycleSnapshotError:
            raise
        except BaseException as error:
            if not isinstance(error, Exception):
                raise
            raise LifecycleSnapshotError("SNAPSHOT_FAILED") from None

    def _ensure_repository(self, *, create: bool = True) -> None:
        self._ensure_runtime()
        try:
            repo_stat = self.repo_dir.lstat()
        except FileNotFoundError:
            if not create:
                raise LifecycleSnapshotError("SNAPSHOT_FAILED") from None
            os.mkdir(self.repo_dir, 0o700)
            self._fsync_directory(self.runtime_dir)
            self._secure_repo_directory()
            self._git_init()
            self._write_marker()
            return
        if self._unsafe(repo_stat) or not stat.S_ISDIR(repo_stat.st_mode):
            raise LifecycleSnapshotError("SNAPSHOT_FAILED")
        self._secure_repo_directory()
        marker = self.repo_dir / _MARKER_NAME
        if _O_NOFOLLOW is None:
            raise LifecycleSnapshotError("SNAPSHOT_FAILED")
        try:
            descriptor = os.open(
                marker,
                os.O_RDONLY | _O_NOFOLLOW,
            )
        except OSError:
            raise LifecycleSnapshotError("SNAPSHOT_FAILED") from None
        try:
            marker_stat = os.fstat(descriptor)
            if self._unsafe(marker_stat) or not stat.S_ISREG(marker_stat.st_mode):
                raise LifecycleSnapshotError("SNAPSHOT_FAILED")
            marker_bytes = os.read(descriptor, len(_MARKER_BYTES) + 1)
        finally:
            os.close(descriptor)
        if marker_bytes != _MARKER_BYTES:
            raise LifecycleSnapshotError("SNAPSHOT_FAILED")

    def _ensure_runtime(self) -> None:
        try:
            runtime_stat = self.runtime_dir.lstat()
        except FileNotFoundError:
            os.mkdir(self.runtime_dir, 0o700)
            self._fsync_directory(self.brain_dir)
            runtime_stat = self.runtime_dir.lstat()
        if self._unsafe(runtime_stat) or not stat.S_ISDIR(runtime_stat.st_mode):
            raise LifecycleSnapshotError("SNAPSHOT_FAILED")

    def _secure_repo_directory(self) -> None:
        descriptor = self._open_directory(self.repo_dir)
        try:
            os.fchmod(descriptor, 0o700)
        finally:
            os.close(descriptor)

    def _git_init(self) -> None:
        self._run_git(
            ["git", "-c", "core.hooksPath=/dev/null", "init", "--bare", "-q", "."],
            cwd=self.repo_dir,
        )

    def _write_marker(self) -> None:
        marker = self.repo_dir / _MARKER_NAME
        if _O_NOFOLLOW is None:
            raise LifecycleSnapshotError("SNAPSHOT_FAILED")
        descriptor = os.open(
            marker,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | _O_NOFOLLOW,
            0o600,
        )
        try:
            os.fchmod(descriptor, 0o600)
            remaining = memoryview(_MARKER_BYTES)
            while remaining:
                written = os.write(descriptor, remaining)
                if written <= 0:
                    raise OSError("SNAPSHOT_FAILED")
                remaining = remaining[written:]
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        self._fsync_directory(self.repo_dir)

    def _write_blob(self, data: bytes) -> str:
        return self._object_id(self._git("hash-object", "-w", "--stdin", input=data).stdout)

    def _make_tree(self, entries: list[tuple[str, str, str, str]]) -> str:
        payload = b"".join(
            f"{mode} {kind} {object_id}\t".encode("ascii")
            + name.encode("utf-8")
            + b"\0"
            for mode, kind, object_id, name in sorted(entries, key=lambda entry: entry[3])
        )
        return self._object_id(self._git("mktree", "-z", input=payload).stdout)

    def _current_commit(self) -> str | None:
        process = self._git(
            "rev-parse", "--verify", "refs/heads/lifecycle", check=False
        )
        if process.returncode != 0:
            return None
        return self._object_id(process.stdout)

    def _read_tree(self, object_id: str) -> dict[bytes, tuple[bytes, bytes]]:
        raw = self._git("ls-tree", "-z", object_id).stdout
        entries: dict[bytes, tuple[bytes, bytes]] = {}
        for entry in raw.split(b"\0"):
            if not entry:
                continue
            metadata, name = entry.split(b"\t", 1)
            _mode, kind, child_id = metadata.split(b" ", 2)
            if _OBJECT_ID.fullmatch(child_id) is None or name in entries:
                raise LifecycleSnapshotError("SNAPSHOT_FAILED")
            entries[name] = (kind, child_id)
        return entries

    def _git(
        self,
        *args: str,
        input: bytes | None = None,
        check: bool = True,
    ) -> subprocess.CompletedProcess[bytes]:
        command = [
            "git",
            "-c",
            "core.hooksPath=/dev/null",
            "--git-dir",
            str(self.repo_dir),
            *args,
        ]
        return self._run_git(command, input=input, check=check)

    def _run_git(
        self,
        command: list[str],
        *,
        cwd: Path | None = None,
        input: bytes | None = None,
        check: bool = True,
    ) -> subprocess.CompletedProcess[bytes]:
        environment = {
            "PATH": os.environ.get("PATH", ""),
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_NO_REPLACE_OBJECTS": "1",
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_AUTHOR_NAME": "agent-memory-hub",
            "GIT_AUTHOR_EMAIL": "lifecycle@agent-memory-hub.invalid",
            "GIT_COMMITTER_NAME": "agent-memory-hub",
            "GIT_COMMITTER_EMAIL": "lifecycle@agent-memory-hub.invalid",
            "LC_ALL": "C",
        }
        try:
            process = subprocess.run(
                command,
                cwd=cwd,
                input=input,
                capture_output=True,
                env=environment,
                timeout=GIT_TIMEOUT_SECONDS,
            )
        except (OSError, subprocess.TimeoutExpired):
            raise LifecycleSnapshotError("SNAPSHOT_FAILED") from None
        if check and process.returncode != 0:
            raise LifecycleSnapshotError("SNAPSHOT_FAILED")
        return process

    @staticmethod
    def _object_id(raw: bytes) -> str:
        object_id = raw.strip()
        if _OBJECT_ID.fullmatch(object_id) is None:
            raise LifecycleSnapshotError("SNAPSHOT_FAILED")
        return object_id.decode("ascii")

    @staticmethod
    def _validate_pair_ids(obsolete_id: str, replacement_id: str) -> None:
        for item_id in (obsolete_id, replacement_id):
            if not is_valid_memory_item_id(item_id) or any(
                ord(character) < 32 or ord(character) == 127
                for character in item_id
            ):
                raise LifecycleSnapshotError("SNAPSHOT_FAILED")
        if obsolete_id == replacement_id:
            raise LifecycleSnapshotError("SNAPSHOT_FAILED")

    def _validate_items_root(self) -> None:
        expected_items = self.brain_dir / "items"
        if self.items_dir != expected_items:
            raise LifecycleSnapshotError("SNAPSHOT_FAILED")
        try:
            items_stat = self.items_dir.lstat()
        except OSError:
            raise LifecycleSnapshotError("SNAPSHOT_FAILED") from None
        if self._unsafe(items_stat) or not stat.S_ISDIR(items_stat.st_mode):
            raise LifecycleSnapshotError("SNAPSHOT_FAILED")

    @staticmethod
    def _unsafe(file_stat: os.stat_result) -> bool:
        attributes = getattr(file_stat, "st_file_attributes", 0)
        reparse = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
        return stat.S_ISLNK(file_stat.st_mode) or bool(attributes & reparse)

    @staticmethod
    def _open_directory(path: Path) -> int:
        if os.name == "nt" or _O_NOFOLLOW is None:
            raise LifecycleSnapshotError("SNAPSHOT_FAILED")
        descriptor = os.open(
            path,
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | _O_NOFOLLOW,
        )
        if not stat.S_ISDIR(os.fstat(descriptor).st_mode):
            os.close(descriptor)
            raise LifecycleSnapshotError("SNAPSHOT_FAILED")
        return descriptor

    @classmethod
    def _fsync_directory(cls, path: Path) -> None:
        descriptor = cls._open_directory(path)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


__all__ = [
    "GIT_TIMEOUT_SECONDS",
    "LifecycleSnapshotError",
    "LifecycleSnapshotStore",
]

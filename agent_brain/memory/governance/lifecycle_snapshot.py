"""Private fd-bound two-item Git snapshots for lifecycle rollback."""

from __future__ import annotations

import os
import re
import shutil
import stat
import subprocess
import sys
import uuid
from pathlib import Path

from agent_brain.contracts.memory_item import is_valid_memory_item_id
from agent_brain.memory.store.durable_fs import SecureDirectory


GIT_TIMEOUT_SECONDS = 5
_MARKER_NAME = "amh-lifecycle-repository"
_MARKER_BYTES = b"agent-memory-hub lifecycle snapshot repository v1\n"
_OBJECT_ID = re.compile(rb"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")
_MUTATING_GIT_OPERATIONS = {
    "init",
    "hash-object",
    "mktree",
    "commit-tree",
    "update-ref",
}


class LifecycleSnapshotError(RuntimeError):
    pass


class LifecycleSnapshotStore:
    def __init__(self, brain_dir: Path, items_dir: Path | None = None) -> None:
        self.brain_dir = Path(brain_dir)
        self.items_dir = (
            Path(items_dir) if items_dir is not None else self.brain_dir / "items"
        )
        self.helper = Path(__file__).with_name("git_fd_exec.py").resolve()
        git = shutil.which("git")
        self.git = str(Path(git).resolve()) if git is not None else ""

    def snapshot_pair(
        self,
        obsolete_id: str,
        obsolete_bytes: bytes,
        replacement_id: str,
        replacement_bytes: bytes,
    ) -> str:
        try:
            self._validate_roots_and_ids(obsolete_id, replacement_id)
            with self._repository() as repo:
                obsolete_blob = self._object_id(
                    self._git(repo, "hash-object", input=obsolete_bytes).stdout
                )
                replacement_blob = self._object_id(
                    self._git(repo, "hash-object", input=replacement_bytes).stdout
                )
                item_tree = self._make_tree(
                    repo,
                    [
                        ("100644", "blob", obsolete_blob, f"{obsolete_id}.md"),
                        ("100644", "blob", replacement_blob, f"{replacement_id}.md"),
                    ],
                )
                root_tree = self._make_tree(
                    repo, [("040000", "tree", item_tree, "items")]
                )
                current = self._git(repo, "rev-parse", check=False)
                parent = (
                    self._object_id(current.stdout) if current.returncode == 0 else None
                )
                values = [root_tree, *([parent] if parent else [])]
                commit = self._object_id(
                    self._git(repo, "commit-tree", values=values).stdout
                )
                update_values = [commit, *([parent] if parent else [])]
                self._git(repo, "update-ref", values=update_values)
                repo.fsync()
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
            self._validate_ids(obsolete_id, replacement_id)
            if _OBJECT_ID.fullmatch(snapshot.encode("ascii")) is None:
                raise LifecycleSnapshotError("SNAPSHOT_FAILED")
            with SecureDirectory.open(self.items_dir) as item_directory:
                self._validate_items_root(item_directory)
                with self._repository(create=False) as repo:
                    root = self._read_tree(repo, snapshot)
                    if set(root) != {b"items"} or root[b"items"][0] != b"tree":
                        raise LifecycleSnapshotError("SNAPSHOT_FAILED")
                    items = self._read_tree(
                        repo, root[b"items"][1].decode("ascii")
                    )
                    expected = {
                        f"{obsolete_id}.md".encode(),
                        f"{replacement_id}.md".encode(),
                    }
                    if set(items) != expected:
                        raise LifecycleSnapshotError("SNAPSHOT_FAILED")
                    payloads: list[tuple[str, bytes]] = []
                    for item_id in (obsolete_id, replacement_id):
                        kind, blob = items[f"{item_id}.md".encode()]
                        if kind != b"blob":
                            raise LifecycleSnapshotError("SNAPSHOT_FAILED")
                        payloads.append(
                            (
                                item_id,
                                self._git(
                                    repo, "cat-file", values=[blob.decode()]
                                ).stdout,
                            )
                        )
                for item_id, data in payloads:
                    item_directory.atomic_write(
                        f"{item_id}.md", data, create_missing=True
                    )
                for item_id, data in payloads:
                    if self._read_file(item_directory, f"{item_id}.md") != data:
                        raise LifecycleSnapshotError("SNAPSHOT_FAILED")
        except LifecycleSnapshotError:
            raise
        except BaseException as error:
            if not isinstance(error, Exception):
                raise
            raise LifecycleSnapshotError("SNAPSHOT_FAILED") from None

    def _repository(self, *, create: bool = True) -> SecureDirectory:
        with SecureDirectory.open(self.brain_dir) as brain:
            runtime = brain.child("runtime", create=create)
        try:
            try:
                repo = runtime.child("lifecycle-history.git")
            except FileNotFoundError:
                if not create:
                    raise LifecycleSnapshotError("SNAPSHOT_FAILED") from None
                repo = self._initialize_repository(runtime)
            self._validate_marker(repo)
            self._ensure_git_fsync_support(repo)
            return repo
        finally:
            runtime.close()

    def _initialize_repository(self, runtime: SecureDirectory) -> SecureDirectory:
        temporary = f".lifecycle-history-{uuid.uuid4().hex}.tmp"
        os.mkdir(temporary, 0o700, dir_fd=runtime.fd)
        temp_repo = runtime.child(temporary)
        pending: BaseException | None = None
        try:
            self._git(temp_repo, "init")
            self._ensure_git_fsync_support(temp_repo)
            descriptor, _ = temp_repo.open_file(
                _MARKER_NAME, os.O_WRONLY, exclusive=True
            )
            marker_error: BaseException | None = None
            try:
                os.fchmod(descriptor, 0o600)
                self._write_all(descriptor, _MARKER_BYTES)
                os.fsync(descriptor)
            except BaseException as error:
                marker_error = error
                raise
            finally:
                self._close_fd(descriptor, marker_error)
            temp_repo.fsync()
            self._validate_marker(temp_repo)
            temp_repo.close()
            try:
                runtime.rename(temporary, "lifecycle-history.git")
            except OSError:
                existing = runtime.child("lifecycle-history.git")
                self._validate_marker(existing)
                runtime.fsync()
                return existing
            runtime.fsync()
            return runtime.child("lifecycle-history.git")
        except FileExistsError:
            pending = LifecycleSnapshotError("SNAPSHOT_FAILED")
            raise pending
        except BaseException as error:
            pending = error
            raise
        finally:
            if temp_repo.fd >= 0:
                try:
                    temp_repo.close()
                except BaseException:
                    if pending is not None:
                        pending.add_note("SNAPSHOT_CLEANUP_FAILED")
                    else:
                        raise LifecycleSnapshotError("SNAPSHOT_FAILED") from None
            try:
                self._remove_tree(runtime, temporary)
            except BaseException:
                if pending is not None:
                    pending.add_note("SNAPSHOT_CLEANUP_FAILED")
                else:
                    raise LifecycleSnapshotError("SNAPSHOT_FAILED") from None

    def _remove_tree(self, parent: SecureDirectory, name: str) -> None:
        try:
            child = parent.child(name)
        except FileNotFoundError:
            return
        try:
            for entry in os.listdir(child.fd):
                info = child.stat(entry)
                if stat.S_ISDIR(info.st_mode):
                    self._remove_tree(child, entry)
                else:
                    child.unlink(entry)
        finally:
            child.close()
        os.rmdir(name, dir_fd=parent.fd)

    def _validate_marker(self, repo: SecureDirectory) -> None:
        if self._read_file(repo, _MARKER_NAME) != _MARKER_BYTES:
            raise LifecycleSnapshotError("SNAPSHOT_FAILED")

    def _git(
        self,
        repo: SecureDirectory,
        operation: str,
        *,
        values: list[str] | None = None,
        input: bytes | None = None,
        check: bool = True,
    ) -> subprocess.CompletedProcess[bytes]:
        if not self.git:
            raise LifecycleSnapshotError("SNAPSHOT_FAILED")
        command = [
            sys.executable,
            str(self.helper),
            "--fd",
            str(repo.fd),
            "--git",
            self.git,
            "--op",
            operation,
            *(values or []),
        ]
        environment = {"PATH": os.path.dirname(self.git), "LC_ALL": "C"}
        try:
            process = subprocess.run(
                command,
                input=input,
                capture_output=True,
                env=environment,
                timeout=GIT_TIMEOUT_SECONDS,
                pass_fds=(repo.fd,),
                shell=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            raise LifecycleSnapshotError("SNAPSHOT_FAILED") from None
        if check and process.returncode != 0:
            raise LifecycleSnapshotError("SNAPSHOT_FAILED")
        if process.returncode == 0 and operation in _MUTATING_GIT_OPERATIONS:
            self._fsync_repository(repo)
        return process

    def _ensure_git_fsync_support(self, repo: SecureDirectory) -> None:
        supported = set(
            self._git(repo, "fsync-capability").stdout.decode("utf-8").splitlines()
        )
        if not {"core.fsync", "core.fsyncMethod"} <= supported:
            raise LifecycleSnapshotError("SNAPSHOT_FAILED")

    def _fsync_repository(self, repo: SecureDirectory) -> None:
        self._fsync_tree(repo)

    def _fsync_tree(self, directory: SecureDirectory) -> None:
        for name in sorted(os.listdir(directory.fd)):
            opened = directory.stat(name)
            if stat.S_ISDIR(opened.st_mode):
                descriptor = os.open(
                    name,
                    os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                    dir_fd=directory.fd,
                )
                child = SecureDirectory(descriptor)
                try:
                    self._fsync_tree(child)
                finally:
                    child.close()
            elif stat.S_ISREG(opened.st_mode):
                descriptor, _ = directory.open_file(name, os.O_RDONLY)
                try:
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
            else:
                raise LifecycleSnapshotError("SNAPSHOT_FAILED")
        directory.fsync()

    def _make_tree(
        self, repo: SecureDirectory, entries: list[tuple[str, str, str, str]]
    ) -> str:
        payload = b"".join(
            f"{mode} {kind} {oid}\t".encode() + name.encode() + b"\0"
            for mode, kind, oid, name in sorted(entries, key=lambda value: value[3])
        )
        return self._object_id(self._git(repo, "mktree", input=payload).stdout)

    def _read_tree(
        self, repo: SecureDirectory, object_id: str
    ) -> dict[bytes, tuple[bytes, bytes]]:
        raw = self._git(repo, "ls-tree", values=[object_id]).stdout
        entries: dict[bytes, tuple[bytes, bytes]] = {}
        for entry in raw.split(b"\0"):
            if entry:
                metadata, name = entry.split(b"\t", 1)
                _mode, kind, child = metadata.split(b" ", 2)
                if _OBJECT_ID.fullmatch(child) is None or name in entries:
                    raise LifecycleSnapshotError("SNAPSHOT_FAILED")
                entries[name] = (kind, child)
        return entries

    def _validate_roots_and_ids(self, obsolete_id: str, replacement_id: str) -> None:
        self._validate_ids(obsolete_id, replacement_id)
        with SecureDirectory.open(self.items_dir) as items:
            self._validate_items_root(items)

    def _validate_items_root(self, items: SecureDirectory) -> None:
        with SecureDirectory.open(self.brain_dir) as brain:
            expected = brain.stat("items")
            actual = os.fstat(items.fd)
            if (expected.st_dev, expected.st_ino) != (actual.st_dev, actual.st_ino):
                raise LifecycleSnapshotError("SNAPSHOT_FAILED")

    @staticmethod
    def _validate_ids(obsolete_id: str, replacement_id: str) -> None:
        for item_id in (obsolete_id, replacement_id):
            if not is_valid_memory_item_id(item_id) or any(
                ord(c) < 32 or ord(c) == 127 for c in item_id
            ):
                raise LifecycleSnapshotError("SNAPSHOT_FAILED")
        if obsolete_id == replacement_id:
            raise LifecycleSnapshotError("SNAPSHOT_FAILED")

    @classmethod
    def _read_file(cls, directory: SecureDirectory, name: str) -> bytes:
        descriptor, _ = directory.open_file(name, os.O_RDONLY)
        pending: BaseException | None = None
        chunks: list[bytes] = []
        try:
            while True:
                chunk = os.read(descriptor, 65536)
                if not chunk:
                    return b"".join(chunks)
                chunks.append(chunk)
        except BaseException as error:
            pending = error
            raise
        finally:
            cls._close_fd(descriptor, pending)

    @staticmethod
    def _write_all(descriptor: int, data: bytes) -> None:
        remaining = memoryview(data)
        while remaining:
            written = os.write(descriptor, remaining)
            if written <= 0:
                raise OSError("SNAPSHOT_MARKER_WRITE_FAILED")
            remaining = remaining[written:]

    @staticmethod
    def _object_id(raw: bytes) -> str:
        value = raw.strip()
        if _OBJECT_ID.fullmatch(value) is None:
            raise LifecycleSnapshotError("SNAPSHOT_FAILED")
        return value.decode()

    @staticmethod
    def _close_fd(descriptor: int, pending: BaseException | None) -> None:
        try:
            os.close(descriptor)
        except BaseException:
            if pending is not None:
                pending.add_note("SNAPSHOT_CLEANUP_FAILED")
                return
            raise LifecycleSnapshotError("SNAPSHOT_FAILED") from None


__all__ = ["GIT_TIMEOUT_SECONDS", "LifecycleSnapshotError", "LifecycleSnapshotStore"]

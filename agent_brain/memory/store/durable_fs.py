"""Descriptor-relative durable filesystem primitives."""

from __future__ import annotations

import os
import shutil
import stat
import uuid
from pathlib import Path
from types import TracebackType
from typing import Self


class DurabilityUnsupportedError(OSError):
    pass


def lifecycle_mutation_capability() -> bool:
    if os.name == "nt":
        return False
    required = (os.open, os.stat, os.mkdir, os.unlink, os.rename)
    helper = Path(__file__).parents[1] / "governance" / "git_fd_exec.py"
    return bool(
        hasattr(os, "O_DIRECTORY")
        and hasattr(os, "O_NOFOLLOW")
        and hasattr(os, "fchdir")
        and all(function in os.supports_dir_fd for function in required)
        and helper.is_file()
        and shutil.which("git") is not None
    )


def require_lifecycle_mutation_capability() -> None:
    if not lifecycle_mutation_capability():
        raise DurabilityUnsupportedError("DIRECTORY_FSYNC_UNSUPPORTED")


class SecureDirectory:
    def __init__(self, descriptor: int) -> None:
        opened = os.fstat(descriptor)
        if not stat.S_ISDIR(opened.st_mode):
            os.close(descriptor)
            raise OSError("UNSAFE_DIRECTORY")
        self.fd = descriptor

    @classmethod
    def open(cls, path: Path) -> Self:
        require_lifecycle_mutation_capability()
        descriptor = os.open(
            path,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
        )
        return cls(descriptor)

    def child(self, name: str, *, create: bool = False, mode: int = 0o700) -> Self:
        self._basename(name)
        if create:
            try:
                os.mkdir(name, mode, dir_fd=self.fd)
                self.fsync()
            except FileExistsError:
                pass
        descriptor = os.open(
            name,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
            dir_fd=self.fd,
        )
        child = type(self)(descriptor)
        os.fchmod(child.fd, mode)
        return child

    def stat(self, name: str) -> os.stat_result:
        self._basename(name)
        return os.stat(name, dir_fd=self.fd, follow_symlinks=False)

    def open_file(
        self, name: str, flags: int, mode: int = 0o600, *, exclusive: bool = False
    ) -> tuple[int, bool]:
        self._basename(name)
        secure = flags | os.O_NOFOLLOW
        created = False
        if exclusive:
            descriptor = os.open(
                name, secure | os.O_CREAT | os.O_EXCL, mode, dir_fd=self.fd
            )
            created = True
        else:
            descriptor = os.open(name, secure, mode, dir_fd=self.fd)
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode):
            os.close(descriptor)
            raise OSError("UNSAFE_FILE")
        return descriptor, created

    def open_or_create_file(
        self, name: str, flags: int, mode: int = 0o600
    ) -> tuple[int, bool]:
        try:
            return self.open_file(name, flags, mode, exclusive=True)
        except FileExistsError:
            return self.open_file(name, flags, mode)

    def atomic_write(
        self,
        name: str,
        data: bytes,
        *,
        create_missing: bool = False,
        missing_mode: int = 0o600,
    ) -> None:
        self._basename(name)
        try:
            target = self.stat(name)
        except FileNotFoundError:
            if not create_missing:
                raise
            target = None
        if target is not None and not stat.S_ISREG(target.st_mode):
            raise OSError("UNSAFE_ATOMIC_WRITE_TARGET")
        mode = stat.S_IMODE(target.st_mode) if target is not None else missing_mode
        temp = f".amh-{uuid.uuid4().hex}.tmp"
        descriptor = -1
        pending: BaseException | None = None
        cleanup_failed = False
        try:
            descriptor, _ = self.open_file(temp, os.O_WRONLY, mode, exclusive=True)
            os.fchmod(descriptor, mode)
            remaining = memoryview(data)
            while remaining:
                written = os.write(descriptor, remaining)
                if written <= 0:
                    raise OSError("ATOMIC_WRITE_FAILED")
                remaining = remaining[written:]
            os.fsync(descriptor)
            os.close(descriptor)
            descriptor = -1
            os.replace(temp, name, src_dir_fd=self.fd, dst_dir_fd=self.fd)
            self.fsync()
        except BaseException as error:
            pending = error
            raise
        finally:
            try:
                if descriptor >= 0:
                    os.close(descriptor)
            except BaseException:
                if pending is not None:
                    pending.add_note("ATOMIC_WRITE_CLEANUP_FAILED")
                cleanup_failed = True
            try:
                os.unlink(temp, dir_fd=self.fd)
            except FileNotFoundError:
                pass
            except BaseException:
                if pending is not None:
                    pending.add_note("ATOMIC_WRITE_CLEANUP_FAILED")
                cleanup_failed = True
            if cleanup_failed and pending is None:
                raise OSError("ATOMIC_WRITE_CLEANUP_FAILED") from None

    def rename(self, source: str, destination: str) -> None:
        self._basename(source)
        self._basename(destination)
        os.rename(source, destination, src_dir_fd=self.fd, dst_dir_fd=self.fd)

    def unlink(self, name: str) -> None:
        self._basename(name)
        os.unlink(name, dir_fd=self.fd)

    def fsync(self) -> None:
        os.fsync(self.fd)

    def close(self) -> None:
        os.close(self.fd)
        self.fd = -1

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        try:
            self.close()
        except BaseException:
            if exc is not None:
                exc.add_note("DIRECTORY_CLOSE_FAILED")
                return
            raise OSError("DIRECTORY_CLOSE_FAILED") from None

    @staticmethod
    def _basename(name: str) -> None:
        if not name or name in {".", ".."} or Path(name).name != name:
            raise ValueError("INVALID_BASENAME")


__all__ = [
    "DurabilityUnsupportedError",
    "SecureDirectory",
    "lifecycle_mutation_capability",
    "require_lifecycle_mutation_capability",
]

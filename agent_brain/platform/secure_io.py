"""Descriptor-anchored, no-follow primitives for untrusted read paths."""

from __future__ import annotations

import os
import stat
import sys
from dataclasses import dataclass
from pathlib import Path


_DIRECTORY_OPEN_FLAGS = (
    os.O_RDONLY
    | getattr(os, "O_CLOEXEC", 0)
    | getattr(os, "O_DIRECTORY", 0)
    | getattr(os, "O_NOFOLLOW", 0)
)
_REGULAR_FILE_OPEN_FLAGS = (
    os.O_RDONLY
    | getattr(os, "O_BINARY", 0)
    | getattr(os, "O_CLOEXEC", 0)
    | getattr(os, "O_NONBLOCK", 0)
    | getattr(os, "O_NOFOLLOW", 0)
)
_HAS_SECURE_DIR_FD_IO = (
    os.name == "posix"
    and bool(getattr(os, "O_DIRECTORY", 0))
    and bool(getattr(os, "O_NOFOLLOW", 0))
    and os.open in getattr(os, "supports_dir_fd", set())
    and os.scandir in getattr(os, "supports_fd", set())
)
_HAS_SECURE_DIR_FD_MUTATION = (
    _HAS_SECURE_DIR_FD_IO
    and all(
        function in getattr(os, "supports_dir_fd", set())
        for function in (os.mkdir, os.link, os.stat, os.unlink)
    )
)
_MACOS_ROOT_ALIASES = {
    "tmp": "private/tmp",
    "var": "private/var",
}
_FILE_ATTRIBUTE_REPARSE_POINT = getattr(
    stat,
    "FILE_ATTRIBUTE_REPARSE_POINT",
    0x0400,
)


@dataclass(frozen=True)
class _FallbackDirectoryIdentity:
    path: Path
    device: int
    inode: int


class HardenedFallbackDirectory:
    """Path-based fallback that fails closed on links or unverifiable identity."""

    def __init__(
        self,
        path: Path,
        chain: tuple[_FallbackDirectoryIdentity, ...],
    ) -> None:
        self.path = path
        self._chain = chain

    @classmethod
    def open_or_create(
        cls,
        path: Path,
        *,
        mode: int = 0o700,
    ) -> "HardenedFallbackDirectory":
        absolute = _fallback_absolute_path(path)
        chain = _open_or_create_fallback_directory_chain(absolute, mode=mode)
        return cls(absolute, chain)

    def child(
        self,
        name: str,
        *,
        create: bool = False,
        mode: int = 0o700,
    ) -> "HardenedFallbackDirectory":
        _validate_child_name(name)
        self._verify_chain()
        path = self.path / name
        if create:
            chain = _open_or_create_fallback_directory_chain(path, mode=mode)
        else:
            chain = _read_fallback_directory_chain(path)
        if chain[: len(self._chain)] != self._chain:
            raise OSError("fallback directory identity changed")
        return type(self)(path, chain)

    def exclusive_create(
        self,
        name: str,
        data: bytes,
        *,
        mode: int = 0o600,
    ) -> None:
        _validate_child_name(name)
        self._verify_chain()
        path = self.path / name
        descriptor: int | None = None
        created = False
        identity: tuple[int, int] | None = None
        try:
            descriptor = os.open(
                path,
                os.O_WRONLY
                | os.O_CREAT
                | os.O_EXCL
                | getattr(os, "O_BINARY", 0)
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_NOFOLLOW", 0),
                mode,
            )
            created = True
            opened = os.fstat(descriptor)
            if not _is_safe_fallback_regular_file(path, opened):
                raise OSError("fallback create target is not a regular file")
            identity = _fallback_identity(opened)
            try:
                os.fchmod(descriptor, mode)
            except (AttributeError, OSError):
                if os.name != "nt":
                    raise
            remaining = memoryview(data)
            while remaining:
                written = os.write(descriptor, remaining)
                if written <= 0:
                    raise OSError("fallback create write failed")
                remaining = remaining[written:]
            os.fsync(descriptor)
            after_write = os.fstat(descriptor)
            if (
                not _is_safe_fallback_regular_file(path, after_write)
                or _fallback_identity(after_write) != identity
                or int(after_write.st_size) != len(data)
            ):
                raise OSError("fallback create target changed during write")
            target = os.lstat(path)
            if (
                not _is_safe_fallback_regular_file(path, target)
                or _fallback_identity(target) != identity
            ):
                raise OSError("fallback create target identity mismatch")
            self._verify_chain()
        except BaseException:
            if descriptor is not None:
                close_descriptor(descriptor)
                descriptor = None
            if created and identity is not None:
                self._remove_failed_create(path, identity)
            raise
        finally:
            if descriptor is not None:
                close_descriptor(descriptor)

    def read_regular(self, name: str) -> bytes:
        _validate_child_name(name)
        self._verify_chain()
        path = self.path / name
        before = os.lstat(path)
        if not _is_safe_fallback_regular_file(path, before):
            raise OSError("fallback read target is not a regular file")
        identity = _fallback_identity(before)
        descriptor = os.open(
            path,
            os.O_RDONLY
            | getattr(os, "O_BINARY", 0)
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NONBLOCK", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
        try:
            opened = os.fstat(descriptor)
            if (
                not _is_safe_fallback_regular_file(path, opened)
                or _fallback_identity(opened) != identity
            ):
                raise OSError("fallback read target changed before open")
            chunks: list[bytes] = []
            while True:
                chunk = os.read(descriptor, 65536)
                if not chunk:
                    break
                chunks.append(chunk)
            data = b"".join(chunks)
            after_read = os.fstat(descriptor)
            if (
                not _is_safe_fallback_regular_file(path, after_read)
                or _fallback_identity(after_read) != identity
                or int(after_read.st_size) != len(data)
                or int(after_read.st_size) != int(opened.st_size)
                or int(getattr(after_read, "st_mtime_ns", 0))
                != int(getattr(opened, "st_mtime_ns", 0))
            ):
                raise OSError("fallback read target changed during read")
        finally:
            close_descriptor(descriptor)
        target = os.lstat(path)
        if (
            not _is_safe_fallback_regular_file(path, target)
            or _fallback_identity(target) != identity
        ):
            raise OSError("fallback read target identity mismatch")
        self._verify_chain()
        return data

    def names(self, *, suffix: str) -> tuple[str, ...]:
        self._verify_chain()
        with os.scandir(self.path) as entries:
            names = sorted(
                entry.name
                for entry in entries
                if isinstance(entry.name, str) and entry.name.endswith(suffix)
            )
        self._verify_chain()
        for name in names:
            _validate_child_name(name)
        return tuple(names)

    def _verify_chain(self) -> None:
        if _read_fallback_directory_chain(self.path) != self._chain:
            raise OSError("fallback directory identity changed")

    def _remove_failed_create(
        self,
        path: Path,
        identity: tuple[int, int],
    ) -> None:
        try:
            self._verify_chain()
            target = os.lstat(path)
            if (
                not _is_safe_fallback_regular_file(path, target)
                or _fallback_identity(target) != identity
            ):
                return
            os.unlink(path)
            self._verify_chain()
        except OSError:
            return


def secure_dir_fd_io_supported() -> bool:
    """Return whether descriptor-relative no-follow reads are available."""

    return _HAS_SECURE_DIR_FD_IO


def secure_dir_fd_mutation_supported() -> bool:
    """Return whether descriptor-relative no-follow durable creates are available."""

    return _HAS_SECURE_DIR_FD_MUTATION


def open_directory_path_without_symlinks(path: Path) -> int:
    """Open every absolute path component with no-follow directory semantics."""

    if not _HAS_SECURE_DIR_FD_IO:
        raise OSError("secure directory descriptor IO is unavailable")
    absolute = _trusted_macos_root_alias(
        Path(os.path.abspath(os.fspath(path)))
    )
    parts = absolute.parts
    if not parts or parts[0] != os.sep:
        raise OSError("secure directory path must be a POSIX absolute path")

    descriptor: int | None = os.open(os.sep, _DIRECTORY_OPEN_FLAGS)
    try:
        for component in parts[1:]:
            assert descriptor is not None
            if component in {"", ".", ".."}:
                raise OSError("invalid secure directory path component")
            child = os.open(
                component,
                _DIRECTORY_OPEN_FLAGS,
                dir_fd=descriptor,
            )
            close_descriptor(descriptor)
            descriptor = child
        assert descriptor is not None
        opened = descriptor
        descriptor = None
        return opened
    finally:
        if descriptor is not None:
            close_descriptor(descriptor)


def open_or_create_directory_path_without_symlinks(
    path: Path,
    *,
    mode: int = 0o700,
) -> int:
    """Open a directory tree, creating missing components relative to trusted fds."""

    if not _HAS_SECURE_DIR_FD_MUTATION:
        raise OSError("secure directory descriptor mutation is unavailable")
    absolute = _trusted_macos_root_alias(
        Path(os.path.abspath(os.fspath(path)))
    )
    parts = absolute.parts
    if not parts or parts[0] != os.sep:
        raise OSError("secure directory path must be a POSIX absolute path")

    descriptor: int | None = os.open(os.sep, _DIRECTORY_OPEN_FLAGS)
    try:
        for component in parts[1:]:
            assert descriptor is not None
            if component in {"", ".", ".."}:
                raise OSError("invalid secure directory path component")
            created = False
            try:
                child = os.open(component, _DIRECTORY_OPEN_FLAGS, dir_fd=descriptor)
            except FileNotFoundError:
                os.mkdir(component, mode, dir_fd=descriptor)
                os.fsync(descriptor)
                child = os.open(component, _DIRECTORY_OPEN_FLAGS, dir_fd=descriptor)
                created = True
            if created:
                os.fchmod(child, mode)
            close_descriptor(descriptor)
            descriptor = child
        assert descriptor is not None
        opened = descriptor
        descriptor = None
        return opened
    finally:
        if descriptor is not None:
            close_descriptor(descriptor)


def open_child_directory(directory_descriptor: int, name: str) -> int:
    """Open one child directory without following a symlink."""

    _validate_child_name(name)
    return os.open(
        name,
        _DIRECTORY_OPEN_FLAGS,
        dir_fd=directory_descriptor,
    )


def open_regular_file_at(directory_descriptor: int, filename: str) -> int:
    """Open one regular file relative to an anchored directory descriptor."""

    _validate_child_name(filename)
    descriptor: int | None = os.open(
        filename,
        _REGULAR_FILE_OPEN_FLAGS,
        dir_fd=directory_descriptor,
    )
    try:
        assert descriptor is not None
        opened_stat = os.fstat(descriptor)
        if not stat.S_ISREG(opened_stat.st_mode):
            raise OSError("secure read target is not a regular file")
        opened = descriptor
        descriptor = None
        return opened
    finally:
        if descriptor is not None:
            close_descriptor(descriptor)


def close_descriptor(descriptor: int) -> None:
    """Close a descriptor while keeping cleanup paths fail-closed."""

    try:
        os.close(descriptor)
    except OSError:
        pass


def _validate_child_name(name: str) -> None:
    separators = {os.sep}
    if os.altsep:
        separators.add(os.altsep)
    if (
        not isinstance(name, str)
        or not name
        or name in {".", ".."}
        or any(separator in name for separator in separators)
    ):
        raise OSError("invalid secure child name")


def _fallback_absolute_path(path: Path) -> Path:
    absolute = _trusted_macos_root_alias(Path(os.path.abspath(os.fspath(path))))
    if not absolute.is_absolute() or not absolute.anchor:
        raise OSError("fallback directory path must be absolute")
    return absolute


def _read_fallback_directory_chain(
    path: Path,
) -> tuple[_FallbackDirectoryIdentity, ...]:
    absolute = _fallback_absolute_path(path)
    current = Path(absolute.anchor)
    chain = [_fallback_directory_identity(current, os.lstat(current))]
    for component in absolute.parts[1:]:
        if component in {"", ".", ".."}:
            raise OSError("invalid fallback directory path component")
        current /= component
        chain.append(_fallback_directory_identity(current, os.lstat(current)))
    return tuple(chain)


def _open_or_create_fallback_directory_chain(
    path: Path,
    *,
    mode: int,
) -> tuple[_FallbackDirectoryIdentity, ...]:
    absolute = _fallback_absolute_path(path)
    current = Path(absolute.anchor)
    chain = [_fallback_directory_identity(current, os.lstat(current))]
    for component in absolute.parts[1:]:
        if component in {"", ".", ".."}:
            raise OSError("invalid fallback directory path component")
        parent = chain[-1]
        if _fallback_directory_identity(parent.path, os.lstat(parent.path)) != parent:
            raise OSError("fallback parent directory identity changed")
        current /= component
        created = False
        try:
            opened = os.lstat(current)
        except FileNotFoundError:
            try:
                os.mkdir(current, mode)
                created = True
            except FileExistsError:
                pass
            opened = os.lstat(current)
        child = _fallback_directory_identity(current, opened)
        if created:
            try:
                os.chmod(current, mode, follow_symlinks=False)
            except (NotImplementedError, OSError):
                if os.name != "nt":
                    raise
        if _fallback_directory_identity(parent.path, os.lstat(parent.path)) != parent:
            raise OSError("fallback parent directory identity changed")
        if _fallback_directory_identity(current, os.lstat(current)) != child:
            raise OSError("fallback child directory identity changed")
        chain.append(child)
    return tuple(chain)


def _fallback_directory_identity(
    path: Path,
    opened: object,
) -> _FallbackDirectoryIdentity:
    if not _is_safe_fallback_directory(path, opened):
        raise OSError("unsafe fallback directory component")
    device, inode = _fallback_identity(opened)
    return _FallbackDirectoryIdentity(path, device, inode)


def _fallback_identity(opened: object) -> tuple[int, int]:
    identity = (
        int(getattr(opened, "st_dev", 0) or 0),
        int(getattr(opened, "st_ino", 0) or 0),
    )
    if not all(identity):
        raise OSError("fallback filesystem identity is unavailable")
    return identity


def _is_safe_fallback_directory(path: Path, opened: object) -> bool:
    return (
        stat.S_ISDIR(int(getattr(opened, "st_mode", 0) or 0))
        and not _is_fallback_linklike(path, opened)
        and _has_reliable_fallback_identity(opened)
    )


def _is_safe_fallback_regular_file(path: Path, opened: object) -> bool:
    return (
        stat.S_ISREG(int(getattr(opened, "st_mode", 0) or 0))
        and not _is_fallback_linklike(path, opened)
        and _has_reliable_fallback_identity(opened)
    )


def _has_reliable_fallback_identity(opened: object) -> bool:
    return bool(
        int(getattr(opened, "st_dev", 0) or 0)
        and int(getattr(opened, "st_ino", 0) or 0)
    )


def _is_fallback_linklike(path: Path, opened: object) -> bool:
    mode = int(getattr(opened, "st_mode", 0) or 0)
    attributes = int(getattr(opened, "st_file_attributes", 0) or 0)
    if stat.S_ISLNK(mode) or attributes & _FILE_ATTRIBUTE_REPARSE_POINT:
        return True
    isjunction = getattr(os.path, "isjunction", None)
    if not callable(isjunction):
        return False
    try:
        return bool(isjunction(path))
    except OSError:
        return True


def _trusted_macos_root_alias(path: Path) -> Path:
    """Normalize only Apple's verified root-level /var and /tmp aliases."""

    parts = path.parts
    if sys.platform != "darwin" or len(parts) < 2 or parts[0] != os.sep:
        return path
    target = _MACOS_ROOT_ALIASES.get(parts[1])
    if target is None:
        return path
    try:
        actual = os.readlink(os.path.join(os.sep, parts[1]))
    except OSError:
        return path
    if actual not in {target, f"{os.sep}{target}"}:
        return path
    return Path(os.sep, *target.split(os.sep), *parts[2:])


__all__ = [
    "HardenedFallbackDirectory",
    "close_descriptor",
    "open_child_directory",
    "open_directory_path_without_symlinks",
    "open_or_create_directory_path_without_symlinks",
    "open_regular_file_at",
    "secure_dir_fd_io_supported",
    "secure_dir_fd_mutation_supported",
]

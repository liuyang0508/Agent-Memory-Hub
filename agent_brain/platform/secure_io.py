"""Descriptor-anchored, no-follow primitives for untrusted read paths."""

from __future__ import annotations

import os
import stat
import sys
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
    "close_descriptor",
    "open_child_directory",
    "open_directory_path_without_symlinks",
    "open_or_create_directory_path_without_symlinks",
    "open_regular_file_at",
    "secure_dir_fd_io_supported",
    "secure_dir_fd_mutation_supported",
]

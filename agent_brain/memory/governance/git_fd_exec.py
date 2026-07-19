"""Internal fd-bound Git exec helper for lifecycle snapshots."""

from __future__ import annotations

import argparse
import os
import re
import stat
import sys


_OBJECT_ID = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")
_FSYNC_CONFIG = [
    "-c",
    "core.fsyncMethod=fsync",
    "-c",
    "core.fsync=loose-object,reference",
]


def _git_args(operation: str, values: list[str]) -> list[str]:
    if operation == "init" and not values:
        return [*_FSYNC_CONFIG, "init", "--bare", "-q", "."]
    prefix = [
        "--git-dir=.",
        "-c",
        "core.hooksPath=/dev/null",
        *_FSYNC_CONFIG,
    ]
    if operation == "fsync-capability" and not values:
        return prefix + ["help", "--config"]
    if operation == "hash-object" and not values:
        return prefix + ["hash-object", "-w", "--stdin"]
    if operation == "mktree" and not values:
        return prefix + ["mktree", "-z"]
    if operation == "rev-parse" and not values:
        return prefix + ["rev-parse", "--verify", "refs/heads/lifecycle"]
    if operation == "commit-tree" and len(values) in (1, 2):
        if not all(_OBJECT_ID.fullmatch(value) for value in values):
            raise ValueError
        args = prefix + ["commit-tree", values[0], "-m", "lifecycle pair snapshot"]
        if len(values) == 2:
            args.extend(["-p", values[1]])
        return args
    if operation == "update-ref" and len(values) in (1, 2):
        if not all(_OBJECT_ID.fullmatch(value) for value in values):
            raise ValueError
        return prefix + ["update-ref", "refs/heads/lifecycle", *values]
    if operation == "ls-tree" and len(values) == 1 and _OBJECT_ID.fullmatch(values[0]):
        return prefix + ["ls-tree", "-z", values[0]]
    if operation == "cat-file" and len(values) == 1 and _OBJECT_ID.fullmatch(values[0]):
        return prefix + ["cat-file", "blob", values[0]]
    raise ValueError


def main() -> None:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--fd", type=int, required=True)
    parser.add_argument("--git", required=True)
    parser.add_argument("--op", required=True)
    parser.add_argument("values", nargs="*")
    try:
        args = parser.parse_args()
        opened = os.fstat(args.fd)
        if not stat.S_ISDIR(opened.st_mode) or not os.path.isabs(args.git):
            raise ValueError
        git_args = _git_args(args.op, args.values)
        os.fchdir(args.fd)
        os.close(args.fd)
        environment = {
            "PATH": os.path.dirname(args.git),
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_NO_REPLACE_OBJECTS": "1",
            "GIT_AUTHOR_NAME": "agent-memory-hub",
            "GIT_AUTHOR_EMAIL": "amh-test@example.invalid",
            "GIT_COMMITTER_NAME": "agent-memory-hub",
            "GIT_COMMITTER_EMAIL": "amh-test@example.invalid",
            "LC_ALL": "C",
        }
        os.execve(args.git, [args.git, *git_args], environment)
    except BaseException:
        os.write(2, b"GIT_FD_EXEC_FAILED\n")
        raise SystemExit(64) from None


if __name__ == "__main__":
    main()

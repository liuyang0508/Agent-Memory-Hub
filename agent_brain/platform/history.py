"""Git-backed snapshots over the brain pool.

The brain pool is markdown — so a snapshot can be a real git commit you can
diff and restore, rather than an opaque binary checkpoint. This wraps a private
git repo rooted at the brain dir (separate from any outer project repo) and
tracks the ``items/`` subtree.

Borrowed from rohitg00/agentmemory's snapshot idea, adapted to md-as-source.
"""
from __future__ import annotations

import subprocess
from pathlib import Path


class BrainHistoryError(RuntimeError):
    pass


class BrainHistory:
    """Versioning for a brain pool via an embedded git repo."""

    def __init__(self, brain_dir: Path) -> None:
        self.brain_dir = Path(brain_dir)
        self.brain_dir.mkdir(parents=True, exist_ok=True)
        if not (self.brain_dir / ".git").exists():
            self._init_repo()

    # ── internals ─────────────────────────────────────────────────────────
    def _git(self, *args: str, check: bool = True) -> subprocess.CompletedProcess:
        proc = subprocess.run(
            ["git", "-C", str(self.brain_dir), *args],
            capture_output=True,
            text=True,
        )
        if check and proc.returncode != 0:
            raise BrainHistoryError(
                f"git {' '.join(args)} failed: {proc.stderr.strip() or proc.stdout.strip()}"
            )
        return proc

    def _init_repo(self) -> None:
        self._git("init", "-q")
        # Local identity so commits work without relying on global git config.
        self._git("config", "user.email", "brain@agent-memory-hub.local")
        self._git("config", "user.name", "agent-memory-hub")

    def _has_staged_changes(self) -> bool:
        # diff --cached --quiet exits 1 when there is something staged.
        return self._git("diff", "--cached", "--quiet", check=False).returncode != 0

    # ── public API ────────────────────────────────────────────────────────
    def snapshot(self, message: str = "snapshot") -> str | None:
        """Commit the current brain pool state. Returns the commit sha, or
        ``None`` when nothing changed (no empty commits)."""
        self._git("add", "-A")
        if not self._has_staged_changes():
            return None
        self._git("commit", "-q", "-m", message)
        return self._git("rev-parse", "HEAD").stdout.strip()

    def log(self, limit: int = 50) -> list[dict[str, str]]:
        """Return recent snapshots, newest first."""
        proc = self._git(
            "log", f"-{limit}", "--pretty=format:%H%x1f%cI%x1f%s", check=False
        )
        if proc.returncode != 0 or not proc.stdout.strip():
            return []
        out = []
        for line in proc.stdout.splitlines():
            sha, when, msg = line.split("\x1f", 2)
            out.append({"sha": sha, "date": when, "message": msg})
        return out

    def restore(self, ref: str) -> None:
        """Restore the brain pool's tracked files to the state at ``ref``."""
        self._git("checkout", ref, "--", ".")

    def diff(self, ref: str | None = None) -> str:
        """Diff the working tree against ``ref`` (or HEAD/uncommitted)."""
        args = ["diff"]
        if ref:
            args.append(ref)
        return self._git(*args, check=False).stdout

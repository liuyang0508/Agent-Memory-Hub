"""Per-transcript harvest watermark — idempotent, resumable harvest state.

What it does:
    Tracks, for each Claude Code transcript, the last byte offset the harvester
    has already consumed so a re-run picks up exactly where it left off instead
    of re-scanning (and re-deduping) the whole file. State lives at
    ``$BRAIN_DIR/.harvest/state.json`` and is keyed by a short, stable hash of
    the transcript's absolute path (so the json stays readable and the keys
    never collide with filesystem-illegal characters).

How to use it::

    from agent_brain.memory.evidence.harvest.watermark import WatermarkStore

    wm = WatermarkStore()                       # loads existing state if present
    start = wm.resume_offset(path, head_hash)   # 0 if file was rewritten
    ...                                         # harvest spans from `start`
    wm.set_offset(path, offset=last_end,        # remember where we stopped
                  msg_hash=last_span_hash, head_hash=head_hash)
    wm.save()                                   # persist atomically-ish to disk

Resumability contract:
    ``resume_offset`` returns 0 whenever the caller's freshly-observed head hash
    no longer matches the stored one — i.e. the transcript was truncated or
    rewritten, so the old offset is meaningless and a full re-scan is required.
    When the head hash still matches (or no head hash was ever recorded) the
    stored ``last_offset`` is returned. This is what lets the harvester survive
    being rate-limited or interrupted mid-run without losing or duplicating work.

Depends on: ``agent_brain.memory.store.pending.brain_dir`` for the brain root (so a
single ``$BRAIN_DIR`` controls every entry point). Pure stdlib otherwise; no
network, no model.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from agent_brain.memory.store.pending import brain_dir


def _state_path() -> Path:
    """Location of the on-disk watermark state file under the brain root."""
    return brain_dir() / ".harvest" / "state.json"


def _key(path: Path) -> str:
    """Stable, filesystem-safe key for a transcript path.

    A short sha256 prefix of the absolute path keeps the json keys compact and
    free of path separators while remaining deterministic across runs.
    """
    return hashlib.sha256(str(path).encode("utf-8")).hexdigest()[:8]


class WatermarkStore:
    """Load/track/persist per-transcript harvest offsets.

    Instances read the state file once at construction; mutations stay in memory
    until :meth:`save` writes them back. A missing or corrupt state file is
    treated as an empty store rather than an error, so a partially-written file
    (e.g. an interrupted previous run) never wedges the harvester.
    """

    def __init__(self) -> None:
        self._path = _state_path()
        self._data: dict = {"v": 1, "transcripts": {}}
        if self._path.exists():
            try:
                self._data = json.loads(self._path.read_text(encoding="utf-8"))
            except Exception:
                # Corrupt/partial state → start clean; markdown remains the
                # source of truth, so at worst we re-scan and dedup catches it.
                pass

    def _entry(self, path: Path) -> dict:
        """Return the stored record for ``path`` (empty dict if unseen)."""
        return self._data["transcripts"].get(_key(path), {})

    def get_offset(self, path: Path) -> int:
        """Last harvested byte offset for ``path``; 0 if the file is unseen."""
        return int(self._entry(path).get("last_offset", 0))

    def resume_offset(self, path: Path, observed_head_hash: str | None) -> int:
        """Offset to resume from, accounting for file truncation/rewrite.

        Returns 0 when ``observed_head_hash`` is provided and disagrees with the
        stored head hash (the file changed underneath us → re-scan from the
        start). Otherwise returns the stored ``last_offset``. A ``None`` observed
        hash, or no recorded head hash, means "trust the stored offset".
        """
        e = self._entry(path)
        if observed_head_hash is not None and e.get("head_hash") not in (None, observed_head_hash):
            return 0
        return int(e.get("last_offset", 0))

    def set_offset(self, path: Path, *, offset: int, msg_hash: str | None = None,
                   head_hash: str | None = None) -> None:
        """Record the latest harvested ``offset`` for ``path``.

        ``msg_hash`` is the span hash of the last consumed message (useful for
        debugging/auditing); ``head_hash`` fingerprints the file's head so a
        later run can detect truncation/rewrite via :meth:`resume_offset`. An
        existing ``enriched`` flag is preserved across updates.
        """
        entry = self._data["transcripts"].setdefault(_key(path), {})
        entry.update({
            "path": str(path),
            "last_offset": offset,
            "last_msg_hash": msg_hash,
            "head_hash": head_hash,
        })

    def mark_enriched(self, path: Path) -> None:
        """Flag ``path`` as having had its raw items LLM-enriched (idempotent)."""
        self._data["transcripts"].setdefault(_key(path), {})["enriched"] = True

    def save(self) -> None:
        """Persist the in-memory state to disk, creating the dir if needed."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(
            json.dumps(self._data, ensure_ascii=False, indent=2), encoding="utf-8",
        )

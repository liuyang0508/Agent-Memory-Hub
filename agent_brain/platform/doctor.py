"""Offline self-check: is the brain writable/readable without network or model?

What it does:
    ``run_doctor(offline=True)`` probes the parts of the system that must keep
    working when everything else is down — the markdown source-of-truth store's
    writability, the presence of the derived sqlite index, the embedder tier
    (semantic vs. degraded hashing fallback), and the depth of the durable
    pending queue (plus any parked "dead" records). It returns a graded
    :class:`DoctorReport` whose ``overall`` is one of ``OK`` / ``DEGRADED`` /
    ``BROKEN`` with a matching ``exit_code`` of ``0`` / ``1`` / ``2``.

    The single hard requirement is that markdown is writable: if it is not, the
    brain cannot accept writes and the report is ``BROKEN``. Everything below the
    md line (index, embedder) is best-effort/derived, so its absence or
    degradation only downgrades the report to ``DEGRADED`` — never ``BROKEN``.

How to use it::

    from agent_brain.platform.doctor import run_doctor
    rep = run_doctor(offline=True)        # never touches the network
    print(rep.overall, rep.exit_code)     # e.g. "OK" 0
    rep.checks["pending.depth"]           # buffered writes awaiting replay

When ``offline=True`` (the default) NO network call is made: the embedder probe
is a pure ``sentence-transformers`` import check via
``probe_semantic_available``. With ``offline=False`` the probe is allowed to
actually resolve the default embedder (which may download a model on first use).

Depends on: ``core.pending`` for the shared ``brain_dir`` resolver and the
``PendingQueue`` depth, and ``core.embedding`` for the network-free embedder
probes. It reads only on-disk state plus an import check, so it is safe to run
on a cold/offline machine.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from agent_brain.memory.store.items_store import ItemsStore
from agent_brain.memory.store.pending import PendingQueue, brain_dir


@dataclass
class DoctorReport:
    """Graded result of an offline health probe.

    ``checks`` holds each probe's raw value keyed by a stable dotted name (so
    callers/tests can assert on individual signals); ``overall`` summarizes them
    as ``OK`` / ``DEGRADED`` / ``BROKEN`` and ``exit_code`` mirrors that grade
    as ``0`` / ``1`` / ``2`` for shell callers.
    """

    checks: dict = field(default_factory=dict)
    details: dict = field(default_factory=dict)
    overall: str = "OK"
    exit_code: int = 0


def _probe_embedder_tier(offline: bool) -> str:
    """Report the embedder tier without (offline) or with (online) a model load.

    Returns ``"semantic"`` when a real sentence-transformer model is usable,
    ``"hashing"`` when only the deterministic fallback is available (vector
    recall degraded but indexing still works), or ``"none"`` if the embedding
    layer can't be inspected at all. Offline mode is a pure import probe and
    never hits the network; online mode may resolve the model (first-use
    download) before reporting whether it had to fall back.
    """
    try:
        from agent_brain.platform.embedding import is_prod_embedder_degraded

        if offline:
            from agent_brain.platform.embedding import probe_semantic_available

            # A prod embedder that already fell back is authoritatively degraded;
            # otherwise an import-only probe tells us whether semantic is possible.
            if is_prod_embedder_degraded():
                return "hashing"
            return "semantic" if probe_semantic_available() else "hashing"

        from agent_brain.platform.embedding import get_default_embedder

        get_default_embedder()  # may build/download the model on first use
        return "hashing" if is_prod_embedder_degraded() else "semantic"
    except Exception:
        return "none"


def run_doctor(offline: bool = True) -> DoctorReport:
    """Probe brain health and return a graded report.

    Order of checks: md-store writability (the only ``BROKEN`` trigger), derived
    index presence, embedder tier, and pending-queue depth/dead counts. The grade
    is then: ``BROKEN`` if md isn't writable, else ``DEGRADED`` if any write is
    buffered/parked or the embedder isn't fully semantic, else ``OK``.
    """
    rep = DoctorReport()
    bd = brain_dir()
    items = bd / "items"

    # md store writable? — the source of truth and the ONLY broken-if-not signal.
    # Probe with a real create+write+delete so a read-only mount or missing dir
    # is caught rather than assumed-good.
    try:
        items.mkdir(parents=True, exist_ok=True)
        probe = items / ".doctor-probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        rep.checks["core.md_store.writable"] = True
    except Exception:
        rep.checks["core.md_store.writable"] = False

    # derived index present? Informational only — a fresh brain legitimately has
    # no index yet (it is rebuildable from md), so absence never grades BROKEN.
    rep.checks["core.index.present"] = (bd / "index.db").exists()

    # malformed items? Normal read/search skips them quietly and records
    # last_scan; doctor is where that degradation should become visible.
    try:
        store = ItemsStore(items_dir=items)
        sum(1 for _ in store.iter_all())
        rep.checks["core.items.skipped"] = store.last_scan.skipped_count
        rep.details["core.items.skipped"] = [
            {"path": str(rec.path), "reason": rec.reason}
            for rec in store.last_scan.skipped[:20]
        ]
    except Exception:
        rep.checks["core.items.skipped"] = -1
        rep.details["core.items.skipped"] = []

    # embedder tier — network-free in offline mode.
    rep.checks["core.embedder.tier"] = _probe_embedder_tier(offline)

    # pending queue: buffered writes awaiting replay + poison records parked dead.
    q = PendingQueue()
    rep.checks["pending.depth"] = q.depth()
    dead = bd / "pending" / "dead"
    rep.checks["pending.dead"] = len(list(dead.glob("*.jsonl"))) if dead.exists() else 0

    # grade
    if not rep.checks["core.md_store.writable"]:
        rep.overall, rep.exit_code = "BROKEN", 2
    elif (
        rep.checks["pending.depth"]
        or rep.checks["pending.dead"]
        or rep.checks["core.items.skipped"]
        or rep.checks["core.embedder.tier"] != "semantic"
    ):
        rep.overall, rep.exit_code = "DEGRADED", 1
    else:
        rep.overall, rep.exit_code = "OK", 0
    return rep

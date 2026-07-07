"""v0.5 → v1 schema compatibility conformance.

Reads tests/fixtures/sample_items/ and asserts every md file there parses
under the current MemoryItem schema. The fixture set covers every edge case
that real historical data has thrown at us:

  - all 6 memory types (fact / episode / decision / artifact / signal / handoff)
  - CJK characters in id
  - `+` in id
  - YAML flow-style tag list
  - YAML single-quoted title with embedded double quotes
  - v0.5 `key:[]` no-space-after-colon quirk
  - `refs.tags` historical field (must be silently ignored by Refs.extra=ignore)
  - numeric session id (coerced from YAML int to str)
  - tz-aware datetime (Z suffix and +08:00 forms)

Previously this test read ``~/.agent-memory-hub/items/`` and skipped when
the directory was missing, which made CI silently pass with zero coverage.
The fixture-based design forces real conformance on every push.

The optional local-brain sweep is gated on the LOCAL_BRAIN_CONFORMANCE=1
environment variable so power users can still cross-check their own pool.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from agent_brain.memory.store.items_store import ItemsStore

FIXTURE_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "sample_items"


def test_fixture_dir_exists_and_nonempty():
    """The fixture directory must be present in the repo, no silent skip."""
    assert FIXTURE_DIR.is_dir(), f"fixture dir missing: {FIXTURE_DIR}"
    md_files = list(FIXTURE_DIR.glob("*.md"))
    assert len(md_files) >= 9, (
        f"fixture set too small ({len(md_files)} items) — "
        f"need ≥9 to cover all known schema edge cases"
    )


def test_all_fixtures_parse_under_v1_schema():
    """Every md file in tests/fixtures/sample_items/ must validate."""
    failures: list[tuple[str, str]] = []
    count = 0
    for path in sorted(FIXTURE_DIR.glob("*.md")):
        count += 1
        try:
            item, _body = ItemsStore._read_one(path)
        except Exception as exc:
            failures.append((path.name, str(exc)[:200]))
            continue
        # Sanity check: id matches filename stem
        assert item.id == path.stem, f"id/filename mismatch: {item.id} vs {path.stem}"

    assert count >= 9, f"expected ≥9 fixture items, found {count}"
    assert not failures, (
        f"{len(failures)}/{count} fixture items failed schema validation:\n"
        + "\n".join(f"  - {name}: {err}" for name, err in failures)
    )


def test_iter_all_yields_all_fixtures_without_skipping():
    """ItemsStore.iter_all should yield every fixture item with no skips,
    because the fixture set is intentionally clean."""
    store = ItemsStore(items_dir=FIXTURE_DIR)
    seen = [item.id for item, _ in store.iter_all()]
    assert len(seen) >= 9
    assert store.last_scan.skipped_count == 0, (
        f"unexpected skips in clean fixture set: {store.last_scan.skipped}"
    )


@pytest.mark.skipif(
    os.environ.get("LOCAL_BRAIN_CONFORMANCE") != "1",
    reason="opt-in via LOCAL_BRAIN_CONFORMANCE=1; not a CI default",
)
def test_local_brain_pool_sweep():
    """OPTIONAL: sweep the developer's own ~/.agent-memory-hub/items/ if set.

    Unlike the prior unconditional skip, this is intentionally opt-in via
    env var so it never silently passes on CI. Run locally with:
        LOCAL_BRAIN_CONFORMANCE=1 pytest tests/conformance/test_v05_compat.py
    """
    real_brain = Path(os.path.expanduser("~/.agent-memory-hub/items"))
    if not real_brain.is_dir():
        pytest.fail(f"LOCAL_BRAIN_CONFORMANCE=1 but {real_brain} missing")

    store = ItemsStore(items_dir=real_brain)
    items = list(store.iter_all())
    assert items, "real brain pool is empty"
    if store.last_scan.skipped:
        skipped_summary = "\n".join(
            f"  - {rec.path.name}: {rec.reason}" for rec in store.last_scan.skipped[:10]
        )
        pytest.fail(
            f"{store.last_scan.skipped_count}/{len(items) + store.last_scan.skipped_count} "
            f"items failed to parse:\n{skipped_summary}"
        )

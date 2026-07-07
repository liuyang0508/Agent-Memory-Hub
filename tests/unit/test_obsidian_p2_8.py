"""Regression tests for P2-8: Obsidian sync index/collision/round-trip fixes."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import yaml

from agent_brain.platform.embedding import HashingEmbedder
from agent_brain.platform.indexing.index import HubIndex
from agent_brain.memory.store.items_store import ItemsStore
from agent_brain.memory.evidence.integrations.obsidian import ObsidianSync
from agent_brain.contracts.memory_item import MemoryItem, MemoryType, Sensitivity

_DIM = 8


def _item(suffix: str, **kw) -> MemoryItem:
    return MemoryItem(
        id=f"mem-20260528-100000-{suffix}",
        type=kw.pop("type", MemoryType.fact),
        created_at=kw.pop(
            "created_at", datetime(2026, 5, 28, 10, 0, 0, tzinfo=timezone.utc)
        ),
        title=kw.pop("title", f"Test item {suffix}"),
        summary=kw.pop("summary", f"Summary {suffix}"),
        project=kw.pop("project", "p2-8"),
        tags=kw.pop("tags", ["test"]),
        confidence=kw.pop("confidence", 0.8),
        **kw,
    )


def test_distinct_items_same_title_do_not_collide(tmp_brain_dir: Path):
    store = ItemsStore(items_dir=tmp_brain_dir / "items")
    store.write(_item("aaaa", title="Same Title"), "body a")
    store.write(_item("bbbb", title="Same Title"), "body b")
    vault = tmp_brain_dir / "vault"
    sync = ObsidianSync(items_store=store, vault_dir=vault)
    report = sync.export_all()
    # Before the fix the second item collided onto the same slug filename and
    # was counted as skipped.
    assert report.exported == 2
    assert report.skipped == 0
    assert len(list(vault.glob("*.md"))) == 2


def test_import_updates_index(tmp_brain_dir: Path):
    store = ItemsStore(items_dir=tmp_brain_dir / "items")
    idx = HubIndex(db_path=tmp_brain_dir / "index.db", embedding_dim=_DIM)
    vault = tmp_brain_dir / "vault"
    vault.mkdir(parents=True)
    fm = {
        "id": "mem-20260528-120000-indexed",
        "type": "fact",
        "created": "2026-05-28T12:00:00+00:00",
        "tags": ["memory/searchme"],
        "confidence": 0.8,
    }
    content = (
        f"---\n{yaml.safe_dump(fm)}---\n"
        "# Indexed Note\n\n> a summary\n\nUniquephrase zorptastic content.\n"
    )
    (vault / "indexed.md").write_text(content)
    sync = ObsidianSync(
        items_store=store,
        vault_dir=vault,
        index=idx,
        embedder=HashingEmbedder(dim=_DIM),
    )
    report = sync.import_from_vault()
    assert report.exported == 1
    # Before the fix the imported item was never upserted, so search missed it.
    hits = idx.bm25_search("zorptastic")
    assert any(h.id == "mem-20260528-120000-indexed" for h in hits)
    idx.close()


def test_roundtrip_preserves_title_and_full_frontmatter(tmp_brain_dir: Path):
    a = _item("alpha", title="My Real Title!", tags=["x", "y"])
    a.refs.mems = ["mem-20260528-100000-beta"]
    a.refs.urls = ["https://example.com/doc"]
    a.sensitivity = Sensitivity.private
    store = ItemsStore(items_dir=tmp_brain_dir / "items")
    store.write(a, "Genuine body text here.")
    vault = tmp_brain_dir / "vault"
    ObsidianSync(items_store=store, vault_dir=vault).export_all()

    store2 = ItemsStore(items_dir=tmp_brain_dir / "items2")
    report = ObsidianSync(items_store=store2, vault_dir=vault).import_from_vault()
    assert report.exported == 1
    items = list(store2.iter_all())
    assert len(items) == 1
    it, body = items[0]
    # Title parsed from H1, not slug-titlecased from the filename stem.
    assert it.title == "My Real Title!"
    assert "Genuine body text here." in body
    # Full frontmatter survives the round-trip (previously dropped).
    assert it.sensitivity == "private"
    assert "https://example.com/doc" in it.refs.urls
    assert "mem-20260528-100000-beta" in it.refs.mems


def test_roundtrip_does_not_truncate_user_reference_heading(tmp_brain_dir: Path):
    body_text = (
        "Intro.\n\n"
        "## References\n"
        "- a citation the author wrote\n\n"
        "Trailing prose that must survive."
    )
    a = _item("doc", title="Doc With Headings")
    a.refs.urls = ["https://ours.example/x"]  # forces OUR ## References trailer
    store = ItemsStore(items_dir=tmp_brain_dir / "items")
    store.write(a, body_text)
    vault = tmp_brain_dir / "vault"
    ObsidianSync(items_store=store, vault_dir=vault).export_all()

    store2 = ItemsStore(items_dir=tmp_brain_dir / "items2")
    assert ObsidianSync(items_store=store2, vault_dir=vault).import_from_vault().exported == 1
    _it, body = list(store2.iter_all())[0]
    # Before the fix, the greedy strip from the FIRST '## References' deleted
    # the citation and everything after it.
    assert "a citation the author wrote" in body
    assert "Trailing prose that must survive." in body
    # Our injected trailer URL must not leak into the imported body.
    assert "https://ours.example/x" not in body

"""Tests for batch operations (confirm/archive) and export."""
from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from agent_brain.platform.embedding import HashingEmbedder
from agent_brain.platform.indexing.index import HubIndex
from agent_brain.memory.store.items_store import ItemsStore
from agent_brain.contracts.memory_item import MemoryItem, MemoryType

_DIM = 8


def _item(suffix: str, **kw) -> MemoryItem:
    return MemoryItem(
        id=f"mem-20260528-100000-{suffix}",
        type=kw.pop("type", MemoryType.fact),
        created_at=datetime.now(timezone.utc),
        title=kw.pop("title", f"Item {suffix}"),
        summary=kw.pop("summary", f"Summary {suffix}"),
        project=kw.pop("project", "batchproj"),
        tags=kw.pop("tags", []),
        tenant_id=kw.pop("tenant_id", None),
    )


def _seed(brain_dir: Path, items: list[tuple[MemoryItem, str]]) -> HubIndex:
    store = ItemsStore(items_dir=brain_dir / "items")
    idx = HubIndex(db_path=brain_dir / "index.db", embedding_dim=_DIM)
    emb = HashingEmbedder(dim=_DIM)
    for item, body in items:
        store.write(item, body)
        idx.upsert(item, body, embedding=emb.embed(f"{item.title} {body}"))
    return idx


def _patch_hermes(brain_dir: Path):
    from contextlib import ExitStack
    stack = ExitStack()
    stack.enter_context(patch("agent_brain.agent_integrations.hermes.provider._brain_dir", return_value=brain_dir))
    stack.enter_context(patch(
        "agent_brain.agent_integrations.hermes.provider.get_default_embedder",
        return_value=HashingEmbedder(dim=_DIM),
    ))
    return stack


# ── batch_confirm (tested via store + index) ──


class TestBatchConfirm:
    def test_batch_confirm_updates_all(self, tmp_brain_dir: Path):
        a = _item("bc-a")
        b = _item("bc-b")
        idx = _seed(tmp_brain_dir, [(a, "body a"), (b, "body b")])
        store = ItemsStore(items_dir=tmp_brain_dir / "items")
        for item_id in [a.id, b.id]:
            store.update_frontmatter(item_id, confidence=0.9)
            idx.update_confidence(item_id, 0.9)
        data = idx.get_confidence_data([a.id, b.id])
        assert data[a.id][0] == 0.9
        assert data[b.id][0] == 0.9

    def test_batch_confirm_partial_failure(self, tmp_brain_dir: Path):
        a = _item("bc-pf")
        idx = _seed(tmp_brain_dir, [(a, "body")])
        store = ItemsStore(items_dir=tmp_brain_dir / "items")
        store.update_frontmatter(a.id, confidence=0.9)
        import pytest
        with pytest.raises(FileNotFoundError):
            store.update_frontmatter("mem-20260528-999999-nope", confidence=0.9)


# ── batch_archive (tested via store) ──


class TestBatchArchive:
    def test_batch_archive_moves_files(self, tmp_brain_dir: Path):
        a = _item("ba-a")
        b = _item("ba-b")
        idx = _seed(tmp_brain_dir, [(a, "body a"), (b, "body b")])
        store = ItemsStore(items_dir=tmp_brain_dir / "items")
        archive_dir = store.items_dir / "archived"
        archive_dir.mkdir(exist_ok=True)
        for item_id in [a.id, b.id]:
            src = store.items_dir / f"{item_id}.md"
            dst = archive_dir / f"{item_id}.md"
            shutil.move(str(src), str(dst))
            idx.delete(item_id)
        assert not (store.items_dir / f"{a.id}.md").exists()
        assert (archive_dir / f"{a.id}.md").exists()
        assert not (store.items_dir / f"{b.id}.md").exists()
        assert (archive_dir / f"{b.id}.md").exists()

    def test_batch_archive_nonexistent_skipped(self, tmp_brain_dir: Path):
        a = _item("ba-skip")
        _seed(tmp_brain_dir, [(a, "body")])
        store = ItemsStore(items_dir=tmp_brain_dir / "items")
        src = store.items_dir / "mem-20260528-999999-nope.md"
        assert not src.exists()


# ── export ──


class TestExportMemory:
    def test_export_all(self, tmp_brain_dir: Path):
        a = _item("ex-a", project="proj1")
        b = _item("ex-b", project="proj2")
        _seed(tmp_brain_dir, [(a, "body a"), (b, "body b")])
        store = ItemsStore(items_dir=tmp_brain_dir / "items")
        items = []
        for item, body in store.iter_all():
            items.append({"frontmatter": item.model_dump(mode="json"), "body": body})
        assert len(items) == 2

    def test_export_filter_project(self, tmp_brain_dir: Path):
        a = _item("ef-a", project="proj1")
        b = _item("ef-b", project="proj2")
        _seed(tmp_brain_dir, [(a, "body a"), (b, "body b")])
        store = ItemsStore(items_dir=tmp_brain_dir / "items")
        items = [
            {"frontmatter": item.model_dump(mode="json"), "body": body}
            for item, body in store.iter_all()
            if item.project == "proj1"
        ]
        assert len(items) == 1
        assert items[0]["frontmatter"]["id"] == a.id

    def test_export_filter_tenant(self, tmp_brain_dir: Path):
        a = _item("et-a", tenant_id="orgX")
        b = _item("et-b", tenant_id="orgY")
        _seed(tmp_brain_dir, [(a, "body a"), (b, "body b")])
        store = ItemsStore(items_dir=tmp_brain_dir / "items")
        items = [
            {"frontmatter": item.model_dump(mode="json"), "body": body}
            for item, body in store.iter_all()
            if item.tenant_id == "orgX"
        ]
        assert len(items) == 1
        assert items[0]["frontmatter"]["id"] == a.id

    def test_export_jsonl_format(self, tmp_brain_dir: Path):
        a = _item("ej-a")
        _seed(tmp_brain_dir, [(a, "body")])
        store = ItemsStore(items_dir=tmp_brain_dir / "items")
        items = []
        for item, body in store.iter_all():
            items.append({"frontmatter": item.model_dump(mode="json"), "body": body})
        lines = [json.dumps(it, ensure_ascii=False) for it in items]
        assert len(lines) == 1
        parsed = json.loads(lines[0])
        assert parsed["frontmatter"]["id"] == a.id


# ── Hermes hub_batch_confirm ──


class TestHermesBatchConfirm:
    def test_hub_batch_confirm(self, tmp_brain_dir: Path):
        a = _item("hbc-a")
        b = _item("hbc-b")
        _seed(tmp_brain_dir, [(a, "body a"), (b, "body b")])
        from agent_brain.agent_integrations.hermes.provider import hub_batch_confirm
        with _patch_hermes(tmp_brain_dir):
            result = hub_batch_confirm([a.id, b.id], confidence=0.95)
        assert result["confirmed"] == 2
        store = ItemsStore(items_dir=tmp_brain_dir / "items")
        for item, _ in store.iter_all():
            if item.id in (a.id, b.id):
                assert item.confidence == 0.95

    def test_hub_batch_confirm_partial(self, tmp_brain_dir: Path):
        a = _item("hbc-p")
        _seed(tmp_brain_dir, [(a, "body")])
        from agent_brain.agent_integrations.hermes.provider import hub_batch_confirm
        with _patch_hermes(tmp_brain_dir):
            result = hub_batch_confirm([a.id, "mem-20260528-999999-nope"])
        assert result["confirmed"] == 1
        assert result["total"] == 2

"""Tests for Obsidian vault sync integration."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import yaml

from agent_brain.platform.embedding import HashingEmbedder
from agent_brain.platform.indexing.index import HubIndex
from agent_brain.memory.store.items_store import ItemsStore
from agent_brain.memory.evidence.integrations.obsidian import ObsidianSync, _slugify
from agent_brain.contracts.memory_item import MemoryItem, MemoryType

_DIM = 8


def _item(suffix: str, **kw) -> MemoryItem:
    return MemoryItem(
        id=f"mem-20260528-100000-{suffix}",
        type=kw.pop("type", MemoryType.fact),
        created_at=kw.pop("created_at", datetime(2026, 5, 28, 10, 0, 0, tzinfo=timezone.utc)),
        title=kw.pop("title", f"Test item {suffix}"),
        summary=kw.pop("summary", f"Summary for {suffix}"),
        project=kw.pop("project", "obsidian-test"),
        tags=kw.pop("tags", ["test", "obs"]),
        confidence=kw.pop("confidence", 0.8),
        **kw,
    )


def _seed(brain_dir: Path, items: list[tuple[MemoryItem, str]]) -> ItemsStore:
    store = ItemsStore(items_dir=brain_dir / "items")
    for item, body in items:
        store.write(item, body)
    return store


class TestSlugify:
    def test_basic(self):
        assert _slugify("Hello World") == "hello-world"

    def test_special_chars(self):
        assert _slugify("API: v2.0 (beta)") == "api-v20-beta"

    def test_truncate(self):
        long = "a" * 100
        assert len(_slugify(long)) <= 60


def test_obsidian_export_renderer_builds_frontmatter_and_markdown():
    from agent_brain.memory.evidence.integrations.obsidian_export import (
        build_obsidian_frontmatter,
        render_obsidian_markdown,
    )

    target = _item("renderer-target", title="Target Item")
    item = _item("renderer", title="Rendered Item")
    item.refs.mems = [target.id]
    item.refs.urls = ["https://example.com/doc"]

    frontmatter = build_obsidian_frontmatter(item)
    assert frontmatter["id"] == item.id
    assert frontmatter["created"] == item.model_dump(mode="json")["created_at"]
    assert frontmatter["tags"] == ["memory/test", "memory/obs"]
    assert frontmatter["aliases"] == [item.id]

    markdown = render_obsidian_markdown(
        item,
        "Rendered body",
        items_by_id={target.id: target},
    )
    assert "# Rendered Item" in markdown
    assert f"[[{target.id}|Target Item]]" in markdown
    assert "## References" in markdown
    assert "https://example.com/doc" in markdown


def test_obsidian_import_parser_is_split_and_preserves_user_reference_heading():
    from agent_brain.memory.evidence.integrations.obsidian_import import parse_obsidian_memory_markdown

    fm = {
        "id": "mem-20260528-120000-parser",
        "type": "fact",
        "created": "2026-05-28T12:00:00+00:00",
        "summary": "Parser summary",
        "tags": ["memory/parser"],
        "confidence": 0.8,
        "refs": {"urls": ["https://ours.example/trailer"]},
    }
    text = (
        f"---\n{yaml.safe_dump(fm)}---\n"
        "# Parser Title\n\n"
        "> Parser summary\n\n"
        "Intro.\n\n"
        "## References\n"
        "- user citation\n\n"
        "Trailing prose.\n\n"
        "## References\n"
        "- https://ours.example/trailer\n"
    )

    parsed = parse_obsidian_memory_markdown(text, fallback_stem="parser-title")

    assert parsed is not None
    assert parsed.item.id == "mem-20260528-120000-parser"
    assert parsed.item.title == "Parser Title"
    assert parsed.item.tags == ["parser"]
    assert "user citation" in parsed.body
    assert "Trailing prose." in parsed.body
    assert "https://ours.example/trailer" not in parsed.body


class TestExportAll:
    def test_exports_items_as_md(self, tmp_brain_dir: Path):
        a = _item("exp-a", title="Auth decision")
        b = _item("exp-b", title="DB fact")
        store = _seed(tmp_brain_dir, [(a, "Body for auth"), (b, "Body for db")])
        vault = tmp_brain_dir / "vault"
        sync = ObsidianSync(items_store=store, vault_dir=vault)
        report = sync.export_all()
        assert report.exported == 2
        assert report.skipped == 0
        md_files = list(vault.glob("*.md"))
        assert len(md_files) == 2

    def test_frontmatter_fields(self, tmp_brain_dir: Path):
        item = _item("fm", title="Frontmatter test", tags=["alpha", "beta"])
        store = _seed(tmp_brain_dir, [(item, "Some body")])
        vault = tmp_brain_dir / "vault"
        sync = ObsidianSync(items_store=store, vault_dir=vault)
        sync.export_all()
        md_file = list(vault.glob("*.md"))[0]
        text = md_file.read_text()
        parts = text.split("---", 2)
        fm = yaml.safe_load(parts[1])
        assert fm["id"] == item.id
        assert fm["type"] == "fact"
        assert "memory/alpha" in fm["tags"]
        assert "memory/beta" in fm["tags"]
        assert fm["confidence"] == 0.8
        assert item.id in fm["aliases"]

    def test_skip_existing_no_overwrite(self, tmp_brain_dir: Path):
        item = _item("skip", title="Skip test")
        store = _seed(tmp_brain_dir, [(item, "body")])
        vault = tmp_brain_dir / "vault"
        sync = ObsidianSync(items_store=store, vault_dir=vault)
        sync.export_all()
        report2 = sync.export_all(overwrite=False)
        assert report2.skipped == 1
        assert report2.exported == 0

    def test_overwrite_existing(self, tmp_brain_dir: Path):
        item = _item("ow", title="Overwrite test")
        store = _seed(tmp_brain_dir, [(item, "body v1")])
        vault = tmp_brain_dir / "vault"
        sync = ObsidianSync(items_store=store, vault_dir=vault)
        sync.export_all()
        report = sync.export_all(overwrite=True)
        assert report.exported == 1
        assert report.skipped == 0

    def test_project_filter(self, tmp_brain_dir: Path):
        a = _item("pf-a", project="proj1")
        b = _item("pf-b", project="proj2")
        store = _seed(tmp_brain_dir, [(a, "a"), (b, "b")])
        vault = tmp_brain_dir / "vault"
        sync = ObsidianSync(items_store=store, vault_dir=vault)
        report = sync.export_all(project="proj1")
        assert report.exported == 1

    def test_wikilinks_for_related(self, tmp_brain_dir: Path):
        a = _item("wl-a", title="Item Alpha")
        b = _item("wl-b", title="Item Beta")
        a.refs.mems = [b.id]
        store = _seed(tmp_brain_dir, [(a, "alpha body"), (b, "beta body")])
        vault = tmp_brain_dir / "vault"
        sync = ObsidianSync(items_store=store, vault_dir=vault)
        sync.export_all()
        alpha_file = vault / f"{a.id}.md"
        text = alpha_file.read_text()
        assert "[[" in text
        assert "Item Beta" in text

    def test_urls_section(self, tmp_brain_dir: Path):
        item = _item("url", title="URL test")
        item.refs.urls = ["https://example.com"]
        store = _seed(tmp_brain_dir, [(item, "body")])
        vault = tmp_brain_dir / "vault"
        sync = ObsidianSync(items_store=store, vault_dir=vault)
        sync.export_all()
        md = list(vault.glob("*.md"))[0].read_text()
        assert "https://example.com" in md
        assert "## References" in md


class TestImportFromVault:
    def test_imports_valid_md(self, tmp_brain_dir: Path):
        store = ItemsStore(items_dir=tmp_brain_dir / "items")
        vault = tmp_brain_dir / "vault"
        vault.mkdir(parents=True)
        fm = {
            "id": "mem-20260528-120000-imported",
            "type": "fact",
            "created": "2026-05-28T12:00:00+00:00",
            "tags": ["memory/test"],
            "confidence": 0.8,
            "aliases": ["mem-20260528-120000-imported"],
        }
        content = f"---\n{yaml.safe_dump(fm)}---\n# Imported Item\n\n> Short summary\n\nBody of imported item.\n"
        (vault / "imported-item.md").write_text(content)
        sync = ObsidianSync(items_store=store, vault_dir=vault)
        report = sync.import_from_vault()
        assert report.exported == 1
        found = False
        for it, body in store.iter_all():
            if it.id == "mem-20260528-120000-imported":
                found = True
                assert "Body of imported item" in body
        assert found

    def test_skips_non_memory_md(self, tmp_brain_dir: Path):
        store = ItemsStore(items_dir=tmp_brain_dir / "items")
        vault = tmp_brain_dir / "vault"
        vault.mkdir(parents=True)
        (vault / "random-note.md").write_text("# Just a regular note\nNo frontmatter.")
        sync = ObsidianSync(items_store=store, vault_dir=vault)
        report = sync.import_from_vault()
        assert report.exported == 0
        assert report.skipped == 1

    def test_skips_existing_no_overwrite(self, tmp_brain_dir: Path):
        item = _item("exist", title="Already exists")
        store = _seed(tmp_brain_dir, [(item, "original body")])
        vault = tmp_brain_dir / "vault"
        vault.mkdir(parents=True)
        fm = {
            "id": item.id,
            "type": "fact",
            "created": "2026-05-28T10:00:00+00:00",
            "tags": [],
            "confidence": 0.8,
            "aliases": [item.id],
        }
        content = f"---\n{yaml.safe_dump(fm)}---\n# Already Exists\n\nNew body.\n"
        (vault / "already-exists.md").write_text(content)
        sync = ObsidianSync(items_store=store, vault_dir=vault)
        report = sync.import_from_vault(overwrite=False)
        assert report.skipped == 1
        assert report.exported == 0

    def test_roundtrip(self, tmp_brain_dir: Path):
        """Export then import should produce equivalent items."""
        item = _item("rt", title="Roundtrip test", tags=["round", "trip"])
        body_text = "Roundtrip body content for testing."
        store = _seed(tmp_brain_dir, [(item, body_text)])
        vault = tmp_brain_dir / "vault"
        sync = ObsidianSync(items_store=store, vault_dir=vault)
        sync.export_all()

        store2 = ItemsStore(items_dir=tmp_brain_dir / "items2")
        sync2 = ObsidianSync(items_store=store2, vault_dir=vault)
        report = sync2.import_from_vault()
        assert report.exported == 1
        for it, body in store2.iter_all():
            assert it.id == item.id
            assert body_text in body

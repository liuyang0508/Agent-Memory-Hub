"""Tests for memory import (CLI + MCP)."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from agent_brain.platform.embedding import HashingEmbedder
from agent_brain.platform.indexing.index import HubIndex
from agent_brain.memory.store.items_store import ItemsStore
from agent_brain.contracts.memory_item import MemoryItem, MemoryType

_DIM = 8


def _make_record(suffix: str, **overrides) -> dict:
    fm = {
        "id": f"mem-20260528-{suffix}",
        "type": "fact",
        "created_at": "2026-05-28T10:00:00+00:00",
        "title": f"Imported {suffix}",
        "summary": f"Summary {suffix}",
        "project": "import-test",
        "tags": ["imported"],
        "confidence": 0.7,
    }
    fm.update(overrides)
    return {"frontmatter": fm, "body": f"Body content for {suffix}"}


def _to_jsonl(records: list[dict]) -> str:
    return "\n".join(json.dumps(r) for r in records)


def _patch_brain(brain_dir: Path):
    from contextlib import ExitStack
    stack = ExitStack()
    stack.enter_context(patch("agent_brain.agent_integrations.hermes.provider._brain_dir", return_value=brain_dir))
    stack.enter_context(patch(
        "agent_brain.agent_integrations.hermes.provider.get_default_embedder",
        return_value=HashingEmbedder(dim=_DIM),
    ))
    return stack


class TestHermesImport:
    def test_imports_records(self, tmp_brain_dir: Path):
        HubIndex(db_path=tmp_brain_dir / "index.db", embedding_dim=_DIM)
        from agent_brain.agent_integrations.hermes.provider import hub_import
        records = [_make_record("000001-alpha"), _make_record("000002-beta")]
        with _patch_brain(tmp_brain_dir):
            result = hub_import(data=_to_jsonl(records))
        assert result["imported"] == 2
        assert result["skipped"] == 0
        store = ItemsStore(items_dir=tmp_brain_dir / "items")
        ids = [it.id for it, _ in store.iter_all()]
        assert "mem-20260528-000001-alpha" in ids
        assert "mem-20260528-000002-beta" in ids

    def test_skips_existing(self, tmp_brain_dir: Path):
        store = ItemsStore(items_dir=tmp_brain_dir / "items")
        item = MemoryItem(
            id="mem-20260528-000001-alpha",
            type=MemoryType.fact,
            created_at=datetime.now(timezone.utc),
            title="Existing",
            summary="Already here",
            tags=[],
        )
        store.write(item, "original")
        HubIndex(db_path=tmp_brain_dir / "index.db", embedding_dim=_DIM)
        from agent_brain.agent_integrations.hermes.provider import hub_import
        with _patch_brain(tmp_brain_dir):
            result = hub_import(data=_to_jsonl([_make_record("000001-alpha")]))
        assert result["skipped"] == 1
        assert result["imported"] == 0

    def test_overwrite_existing(self, tmp_brain_dir: Path):
        store = ItemsStore(items_dir=tmp_brain_dir / "items")
        item = MemoryItem(
            id="mem-20260528-000001-alpha",
            type=MemoryType.fact,
            created_at=datetime.now(timezone.utc),
            title="Old",
            summary="Old summary",
            tags=[],
        )
        store.write(item, "old body")
        HubIndex(db_path=tmp_brain_dir / "index.db", embedding_dim=_DIM)
        from agent_brain.agent_integrations.hermes.provider import hub_import
        with _patch_brain(tmp_brain_dir):
            result = hub_import(data=_to_jsonl([_make_record("000001-alpha")]), overwrite=True)
        assert result["imported"] == 1
        for it, body in ItemsStore(items_dir=tmp_brain_dir / "items").iter_all():
            if it.id == "mem-20260528-000001-alpha":
                assert "Body content for 000001-alpha" in body

    def test_bad_record_counted_as_error(self, tmp_brain_dir: Path):
        HubIndex(db_path=tmp_brain_dir / "index.db", embedding_dim=_DIM)
        from agent_brain.agent_integrations.hermes.provider import hub_import
        bad = json.dumps({"frontmatter": {"bad": "data"}, "body": "x"})
        with _patch_brain(tmp_brain_dir):
            result = hub_import(data=bad)
        assert len(result["errors"]) == 1
        assert result["imported"] == 0

    def test_json_format(self, tmp_brain_dir: Path):
        HubIndex(db_path=tmp_brain_dir / "index.db", embedding_dim=_DIM)
        from agent_brain.agent_integrations.hermes.provider import hub_import
        records = [_make_record("000003-json")]
        with _patch_brain(tmp_brain_dir):
            result = hub_import(data=json.dumps(records), format="json")
        assert result["imported"] == 1


class TestHermesObsidianSync:
    def test_hub_obsidian_export(self, tmp_brain_dir: Path):
        store = ItemsStore(items_dir=tmp_brain_dir / "items")
        item = MemoryItem(
            id="mem-20260528-100000-hermes-export",
            type=MemoryType.fact,
            created_at=datetime(2026, 5, 28, 10, 0, tzinfo=timezone.utc),
            title="Hermes export",
            summary="Export through Hermes wrapper",
            project="hermes-obsidian",
            tags=["obsidian"],
        )
        store.write(item, "Hermes export body")
        HubIndex(db_path=tmp_brain_dir / "index.db", embedding_dim=_DIM)

        from agent_brain.agent_integrations.hermes.provider import hub_obsidian_export

        vault = tmp_brain_dir / "vault"
        with _patch_brain(tmp_brain_dir):
            result = hub_obsidian_export(vault_dir=str(vault), project="hermes-obsidian")

        assert result["exported"] == 1
        assert result["skipped"] == 0
        assert result["errors"] == []
        assert (vault / "mem-20260528-100000-hermes-export.md").exists()

    def test_hub_obsidian_import(self, tmp_brain_dir: Path):
        HubIndex(db_path=tmp_brain_dir / "index.db", embedding_dim=_DIM)
        vault = tmp_brain_dir / "vault"
        vault.mkdir(parents=True)
        (vault / "hermes-import.md").write_text(
            "---\n"
            "id: mem-20260528-110000-hermes-import\n"
            "type: fact\n"
            "created_at: '2026-05-28T11:00:00+00:00'\n"
            "summary: Import through Hermes wrapper\n"
            "tags:\n"
            "  - memory/obsidian\n"
            "confidence: 0.8\n"
            "---\n"
            "# Hermes import\n\n"
            "> Import through Hermes wrapper\n\n"
            "Hermes import body\n",
            encoding="utf-8",
        )

        from agent_brain.agent_integrations.hermes.provider import hub_obsidian_import

        with _patch_brain(tmp_brain_dir):
            with patch(
                "agent_brain.platform.embedding.get_default_embedder",
                return_value=HashingEmbedder(dim=_DIM),
            ):
                result = hub_obsidian_import(vault_dir=str(vault))

        assert result["imported"] == 1
        assert result["skipped"] == 0
        assert result["errors"] == []
        found = [
            (it, body)
            for it, body in ItemsStore(items_dir=tmp_brain_dir / "items").iter_all()
            if it.id == "mem-20260528-110000-hermes-import"
        ]
        assert len(found) == 1
        assert found[0][0].title == "Hermes import"
        assert "Hermes import body" in found[0][1]


class TestCLIImport:
    def test_jsonl_import(self, tmp_brain_dir: Path, tmp_path: Path):
        from typer.testing import CliRunner
        from agent_brain.interfaces.cli import app

        HubIndex(db_path=tmp_brain_dir / "index.db", embedding_dim=_DIM)
        records = [_make_record("000010-cli1"), _make_record("000011-cli2")]
        jsonl_file = tmp_path / "import.jsonl"
        jsonl_file.write_text("\n".join(json.dumps(r) for r in records))

        runner = CliRunner()
        with patch.dict("os.environ", {"BRAIN_DIR": str(tmp_brain_dir)}), \
             patch("agent_brain.interfaces.cli.get_default_embedder", return_value=HashingEmbedder(dim=_DIM)):
            result = runner.invoke(app, ["import", str(jsonl_file)])
        assert result.exit_code == 0
        assert "Imported 2" in result.output

    def test_json_import(self, tmp_brain_dir: Path, tmp_path: Path):
        from typer.testing import CliRunner
        from agent_brain.interfaces.cli import app

        HubIndex(db_path=tmp_brain_dir / "index.db", embedding_dim=_DIM)
        records = [_make_record("000020-json1")]
        json_file = tmp_path / "import.json"
        json_file.write_text(json.dumps(records))

        runner = CliRunner()
        with patch.dict("os.environ", {"BRAIN_DIR": str(tmp_brain_dir)}), \
             patch("agent_brain.interfaces.cli.get_default_embedder", return_value=HashingEmbedder(dim=_DIM)):
            result = runner.invoke(app, ["import", str(json_file), "--format", "json"])
        assert result.exit_code == 0
        assert "Imported 1" in result.output

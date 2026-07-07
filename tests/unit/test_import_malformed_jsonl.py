"""Regression test for P2-4: a single malformed JSONL line must not crash `memory import`."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from agent_brain.platform.embedding import HashingEmbedder
from agent_brain.platform.indexing.index import HubIndex
from agent_brain.memory.store.items_store import ItemsStore

_DIM = 8


def _make_record(suffix: str) -> dict:
    return {
        "frontmatter": {
            "id": f"mem-20260528-{suffix}",
            "type": "fact",
            "created_at": "2026-05-28T10:00:00+00:00",
            "title": f"Imported {suffix}",
            "summary": f"Summary {suffix}",
            "project": "import-test",
            "tags": ["imported"],
            "confidence": 0.7,
        },
        "body": f"Body content for {suffix}",
    }


class TestCLIImportMalformedJsonl:
    def test_one_bad_line_does_not_crash_import(self, tmp_brain_dir: Path, tmp_path: Path):
        from typer.testing import CliRunner
        from agent_brain.interfaces.cli import app

        HubIndex(db_path=tmp_brain_dir / "index.db", embedding_dim=_DIM)

        good = _make_record("000030-good")
        # A valid line, a syntactically broken JSON line, then another valid line.
        jsonl = "\n".join([
            json.dumps(good),
            '{"frontmatter": {"id": "oops", "type":',   # truncated -> JSONDecodeError
            json.dumps(_make_record("000031-good2")),
        ])
        jsonl_file = tmp_path / "import.jsonl"
        jsonl_file.write_text(jsonl)

        runner = CliRunner()
        with patch.dict("os.environ", {"BRAIN_DIR": str(tmp_brain_dir)}), \
             patch("agent_brain.interfaces.cli.get_default_embedder", return_value=HashingEmbedder(dim=_DIM)):
            result = runner.invoke(app, ["import", str(jsonl_file)])

        # Before the fix: json.loads on the bad line raised uncaught -> result.exception set,
        # output lacked the summary line, and no record was written.
        assert result.exception is None or isinstance(result.exception, SystemExit), (
            f"unexpected uncaught exception: {result.exception!r}"
        )
        # Two good lines imported, one malformed line counted as an error.
        assert "Imported 2" in result.output
        assert "errors 1" in result.output
        # exit_code is 1 because errors > 0 (typer.Exit(1) at end of import_items).
        assert result.exit_code == 1

        ids = [it.id for it, _ in ItemsStore(items_dir=tmp_brain_dir / "items").iter_all()]
        assert "mem-20260528-000030-good" in ids
        assert "mem-20260528-000031-good2" in ids

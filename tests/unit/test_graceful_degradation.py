"""Task #15: graceful degradation — core read/write must work offline; vector
search degrades to BM25 when no real embedder; `memory doctor --offline` reports it."""
from datetime import datetime, timezone
from pathlib import Path

import pytest

from agent_brain.platform import embedding as emb
from agent_brain.contracts.memory_item import MemoryItem, MemoryType


def test_cli_offline_doctor_presenter_is_split():
    from agent_brain.interfaces.cli import _shared
    from agent_brain.interfaces.cli.doctor_offline import doctor_offline

    assert _shared._doctor_offline is doctor_offline


def _item(suffix, title, summary):
    return MemoryItem(id=f"mem-20260519-100000-{suffix}", type=MemoryType.fact,
                      created_at=datetime(2026, 5, 19, tzinfo=timezone.utc),
                      title=title, summary=summary)


def test_prod_embedder_degrades_instead_of_crashing(monkeypatch):
    """No real model loads → HashingEmbedder fallback marked degraded, never raises."""
    monkeypatch.delenv("MEMORY_HUB_TEST_EMBEDDING", raising=False)
    class _Boom:
        def __init__(self, *a, **k): raise RuntimeError("offline / no model")
    monkeypatch.setattr(emb, "SentenceTransformerEmbedder", _Boom)
    emb.reset_embedder_cache()
    try:
        e = emb.get_default_embedder()          # must NOT raise
        assert getattr(e, "degraded", False) is True
        assert emb.is_prod_embedder_degraded() is True
        assert len(e.embed("hello")) == e.dim   # still produces a vector
    finally:
        emb.reset_embedder_cache()


def test_core_write_search_read_work_with_degraded_embedder(tmp_brain_dir: Path):
    from agent_brain.platform.indexing.index import HubIndex
    from agent_brain.memory.recall.retrieval import Retriever

    degraded = emb.HashingEmbedder()
    degraded.degraded = True
    # If vector path is taken, this blows up — proving search went BM25-only.
    monkeypatch_embed_boom(degraded)

    idx = HubIndex(db_path=tmp_brain_dir / "index.db")
    item = _item("a", "Python type hints", "mypy pyright checker")
    idx.upsert(item, "mypy pyright checker", embedding=None)   # BM25-only index row

    r = Retriever(index=idx, embedder=degraded)
    hits = r.search("mypy", top_k=5)            # must not crash, must find via BM25
    assert any(h.id == item.id for h in hits)


def monkeypatch_embed_boom(e):
    def _boom(_text):
        raise AssertionError("embed() called while degraded — should be BM25-only")
    e.embed = _boom


def test_doctor_offline_reports(monkeypatch):
    from typer.testing import CliRunner
    from agent_brain.interfaces.cli import app
    monkeypatch.setenv("MEMORY_HUB_TEST_EMBEDDING", "1")
    res = CliRunner().invoke(app, ["doctor", "--offline"])
    assert res.exit_code == 0
    out = res.stdout.lower()
    assert "offline" in out
    assert "bm25" in out


def test_doctor_offline_surfaces_malformed_items(tmp_brain):
    from typer.testing import CliRunner
    from agent_brain.interfaces.cli import app

    (tmp_brain / "items" / "bad.md").write_text("missing frontmatter\n", encoding="utf-8")

    res = CliRunner().invoke(app, ["doctor", "--offline"])

    assert res.exit_code == 0
    out = res.stdout.lower()
    assert "malformed memory items" in out
    assert "1 skipped" in out


def test_doctor_offline_verbose_shows_malformed_item_paths(tmp_brain):
    from typer.testing import CliRunner
    from agent_brain.interfaces.cli import app

    (tmp_brain / "items" / "bad.md").write_text("missing frontmatter\n", encoding="utf-8")

    res = CliRunner().invoke(app, ["doctor", "--offline", "--verbose"])

    assert res.exit_code == 0
    out = res.stdout.lower()
    assert "malformed item details" in out
    assert "bad.md" in out


def test_doctor_offline_repair_malformed_dry_run_does_not_move(tmp_brain):
    from typer.testing import CliRunner
    from agent_brain.interfaces.cli import app

    bad = tmp_brain / "items" / "bad.md"
    bad.write_text("missing frontmatter\n", encoding="utf-8")

    res = CliRunner().invoke(app, ["doctor", "--offline", "--repair-malformed"])

    assert res.exit_code == 0
    out = res.stdout.lower()
    assert "malformed item quarantine plan" in out
    assert "dry-run" in out
    assert bad.exists()


def test_doctor_offline_repair_malformed_apply_moves(tmp_brain):
    from typer.testing import CliRunner
    from agent_brain.interfaces.cli import app

    bad = tmp_brain / "items" / "bad.md"
    bad.write_text("missing frontmatter\n", encoding="utf-8")

    res = CliRunner().invoke(app, ["doctor", "--offline", "--repair-malformed", "--apply"])

    assert res.exit_code == 0
    out = res.stdout.lower()
    assert "moved 1 malformed item" in out
    assert not bad.exists()
    assert (tmp_brain / "items" / "archived" / "malformed" / "bad.md").exists()


def test_doctor_offline_restore_malformed_dry_run_does_not_move(tmp_brain):
    from datetime import datetime, timezone

    from typer.testing import CliRunner

    from agent_brain.interfaces.cli import app
    from agent_brain.memory.store.items_store import ItemsStore
    from agent_brain.contracts.memory_item import MemoryItem, MemoryType

    item = MemoryItem(
        id="mem-20260609-121000-cli-restore",
        type=MemoryType.fact,
        created_at=datetime(2026, 6, 9, 12, 10, tzinfo=timezone.utc),
        title="CLI restored memory",
        summary="A manually repaired quarantined item",
        tags=["restore"],
    )
    archived = ItemsStore(tmp_brain / "items" / "archived" / "malformed").write(item, "restore body")

    res = CliRunner().invoke(app, ["doctor", "--offline", "--restore-malformed", archived.name])

    assert res.exit_code == 0
    out = res.stdout.lower()
    assert "malformed item restore plan" in out
    assert "dry-run" in out
    assert archived.exists()
    assert not (tmp_brain / "items" / "mem-20260609-121000-cli-restore.md").exists()


def test_doctor_offline_restore_malformed_apply_moves_valid_file(tmp_brain):
    from datetime import datetime, timezone

    from typer.testing import CliRunner

    from agent_brain.interfaces.cli import app
    from agent_brain.memory.store.items_store import ItemsStore
    from agent_brain.contracts.memory_item import MemoryItem, MemoryType

    item = MemoryItem(
        id="mem-20260609-121001-cli-restore",
        type=MemoryType.fact,
        created_at=datetime(2026, 6, 9, 12, 10, tzinfo=timezone.utc),
        title="CLI restored memory",
        summary="A manually repaired quarantined item",
        tags=["restore"],
    )
    archived = ItemsStore(tmp_brain / "items" / "archived" / "malformed").write(item, "restore body")

    res = CliRunner().invoke(app, [
        "doctor",
        "--offline",
        "--restore-malformed",
        archived.name,
        "--apply",
    ])

    assert res.exit_code == 0
    out = res.stdout.lower()
    assert "restored 1 malformed item" in out
    assert not archived.exists()
    assert (tmp_brain / "items" / "mem-20260609-121001-cli-restore.md").exists()


def test_doctor_apply_requires_repair_malformed(tmp_brain):
    from typer.testing import CliRunner
    from agent_brain.interfaces.cli import app

    res = CliRunner().invoke(app, ["doctor", "--offline", "--apply"])

    assert res.exit_code == 2
    assert "--apply requires --repair-malformed or --restore-malformed" in res.output


def test_doctor_repair_malformed_requires_offline(tmp_brain):
    from typer.testing import CliRunner
    from agent_brain.interfaces.cli import app

    res = CliRunner().invoke(app, ["doctor", "--repair-malformed"])

    assert res.exit_code == 2
    assert "--repair-malformed and --restore-malformed are only available with --offline" in res.output


def test_doctor_restore_malformed_requires_offline(tmp_brain):
    from typer.testing import CliRunner
    from agent_brain.interfaces.cli import app

    res = CliRunner().invoke(app, ["doctor", "--restore-malformed", "bad.md"])

    assert res.exit_code == 2
    assert "--repair-malformed and --restore-malformed are only available with --offline" in res.output


def test_doctor_repair_and_restore_malformed_are_mutually_exclusive(tmp_brain):
    from typer.testing import CliRunner
    from agent_brain.interfaces.cli import app

    res = CliRunner().invoke(app, [
        "doctor",
        "--offline",
        "--repair-malformed",
        "--restore-malformed",
        "bad.md",
    ])

    assert res.exit_code == 2
    assert "--repair-malformed and --restore-malformed are mutually exclusive" in res.output

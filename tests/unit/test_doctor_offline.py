import builtins
import json
import os
import sqlite3
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

from agent_brain.platform.doctor import run_doctor


def _create_ready_index(brain: Path) -> None:
    from agent_brain.platform.indexing.index import HubIndex

    index = HubIndex(db_path=brain / "index.db")
    index.close()


def _create_fts_index(path: Path) -> None:
    with sqlite3.connect(str(path)) as connection:
        connection.execute(
            "CREATE VIRTUAL TABLE items_fts USING fts5(id UNINDEXED, body)"
        )
        connection.execute(
            "INSERT INTO items_fts(id, body) VALUES (?, ?)",
            ("mem-doctor", "doctor bounded bm25 probe"),
        )


def test_doctor_offline_reports_writable_and_overall_ok(tmp_brain):
    rep = run_doctor(offline=True)
    assert rep.checks["core.md_store.writable"] is True
    assert rep.overall in ("OK", "DEGRADED")
    assert rep.exit_code in (0, 1)


def test_doctor_reports_injection_gateway_available(tmp_brain):
    rep = run_doctor(offline=True)

    assert rep.checks["security.injection_gateway.available"] is True


def test_doctor_reports_routed_recall_gateway_and_offline_fallback(tmp_brain):
    _create_ready_index(tmp_brain)

    rep = run_doctor(offline=True)

    assert rep.checks["recall.routed.status"] == "enabled"
    assert rep.checks["security.injection_gateway.available"] is True
    assert rep.checks["recall.semantic_provider.status"] in {
        "fast_ready",
        "not_fast_ready",
        "unavailable",
    }
    assert rep.checks["recall.lexical_raw_fallback.status"] == "ready"


def test_doctor_reports_routed_recall_rollback_without_disabling_gateway(
    tmp_brain,
    monkeypatch,
):
    _create_ready_index(tmp_brain)
    monkeypatch.setenv("AGENT_MEMORY_HUB_ROUTED_RECALL", "0")

    rep = run_doctor(offline=True)

    assert rep.checks["recall.routed.status"] == "rollback"
    assert rep.checks["security.injection_gateway.available"] is True
    assert rep.overall == "DEGRADED"


def test_doctor_gateway_probe_requires_closed_exclusion_reason_contract(
    tmp_brain,
    monkeypatch,
):
    import agent_brain.memory.context.injection_gateway as gateway
    import agent_brain.platform.doctor as doctor

    monkeypatch.setattr(
        gateway,
        "INJECTION_EXCLUSION_REASONS",
        frozenset({"query_not_injectable"}),
    )

    assert doctor._probe_injection_gateway_available() is False


def test_doctor_gateway_probe_rejects_missing_canonical_exclusion_reason(
    tmp_brain,
    monkeypatch,
):
    import agent_brain.memory.context.injection_gateway as gateway
    import agent_brain.platform.doctor as doctor

    monkeypatch.setattr(
        gateway,
        "INJECTION_EXCLUSION_REASONS",
        gateway.INJECTION_EXCLUSION_REASONS - {"negative_feedback"},
    )

    assert doctor._probe_injection_gateway_available() is False


def test_doctor_gateway_probe_rejects_unknown_exclusion_reason(
    tmp_brain,
    monkeypatch,
):
    import agent_brain.memory.context.injection_gateway as gateway
    import agent_brain.platform.doctor as doctor

    monkeypatch.setattr(
        gateway,
        "INJECTION_EXCLUSION_REASONS",
        gateway.INJECTION_EXCLUSION_REASONS | {"unknown_reason"},
    )

    assert doctor._probe_injection_gateway_available() is False


def test_doctor_gateway_probe_rejects_mutable_exclusion_reason_contract(
    tmp_brain,
    monkeypatch,
):
    import agent_brain.memory.context.injection_gateway as gateway
    import agent_brain.platform.doctor as doctor

    monkeypatch.setattr(
        gateway,
        "INJECTION_EXCLUSION_REASONS",
        set(gateway.INJECTION_EXCLUSION_REASONS),
    )

    assert doctor._probe_injection_gateway_available() is False


def test_doctor_gateway_probe_requires_exclusion_counter_callable(
    tmp_brain,
    monkeypatch,
):
    import agent_brain.memory.context.injection_gateway as gateway
    import agent_brain.platform.doctor as doctor

    monkeypatch.setattr(gateway, "injection_exclusion_reason_counts", None)

    assert doctor._probe_injection_gateway_available() is False


def test_doctor_semantic_provider_does_not_cold_load_model(tmp_brain, monkeypatch):
    from agent_brain.platform import embedding

    monkeypatch.setattr(embedding, "probe_semantic_available", lambda: True)
    monkeypatch.setattr(
        embedding,
        "get_default_embedder",
        lambda: (_ for _ in ()).throw(AssertionError("doctor cold-loaded model")),
    )

    rep = run_doctor(offline=True)

    assert rep.checks["recall.semantic_provider.status"] == "not_fast_ready"


def test_doctor_semantic_provider_reports_missing_dependency_unavailable(
    tmp_brain,
    monkeypatch,
):
    from agent_brain.platform import embedding

    monkeypatch.setattr(embedding, "probe_semantic_available", lambda: False)

    rep = run_doctor(offline=True)

    assert rep.checks["recall.semantic_provider.status"] == "unavailable"


@pytest.mark.parametrize("semantic_status", ["not_fast_ready", "unavailable"])
def test_doctor_optional_semantic_status_does_not_make_required_health_unreachable(
    tmp_brain,
    monkeypatch,
    semantic_status,
):
    import agent_brain.platform.doctor as doctor

    _create_ready_index(tmp_brain)
    monkeypatch.setattr(doctor, "_probe_embedder_tier", lambda _offline: "semantic")
    monkeypatch.setattr(doctor, "_probe_injection_gateway_available", lambda: True)
    monkeypatch.setattr(
        doctor,
        "_probe_semantic_provider_status",
        lambda: semantic_status,
    )
    monkeypatch.setattr(doctor, "_probe_bm25_index_ready", lambda _path: True)
    monkeypatch.setattr(doctor, "_probe_routed_cli_installed", lambda: True)
    monkeypatch.setattr(
        doctor,
        "probe_memory_cli_shim",
        lambda: {"path": "", "present": False, "target": "", "target_exists": False},
    )

    rep = doctor.run_doctor(offline=True)

    assert rep.checks["recall.semantic_provider.status"] == semantic_status
    assert rep.overall == "OK"
    assert rep.exit_code == 0


def test_doctor_lexical_fallback_reports_missing_index_not_ready(tmp_brain):
    rep = run_doctor(offline=True)

    assert rep.checks["core.index.present"] is False
    assert rep.checks["recall.lexical_raw_fallback.status"] == "not_ready"


def test_doctor_lexical_fallback_requires_routed_cli(tmp_brain, monkeypatch):
    import agent_brain.platform.doctor as doctor

    _create_ready_index(tmp_brain)
    monkeypatch.setattr(doctor, "_probe_routed_cli_installed", lambda: False)

    rep = doctor.run_doctor(offline=True)

    assert rep.checks["recall.lexical_raw_fallback.status"] == "not_ready"


def test_doctor_routed_cli_probe_does_not_import_heavy_command_surface(
    tmp_brain,
    monkeypatch,
):
    import agent_brain.platform.doctor as doctor

    real_import = builtins.__import__

    def reject_query_command_import(name, *args, **kwargs):
        if name == "agent_brain.interfaces.cli.commands.query":
            raise AssertionError("doctor imported the full CLI command surface")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", reject_query_command_import)

    assert doctor._probe_routed_cli_installed() is True


def test_bm25_probe_accepts_valid_fts_index(tmp_path):
    from agent_brain.platform.doctor import _probe_bm25_index_ready

    index_path = tmp_path / "valid-index.db"
    _create_fts_index(index_path)

    assert _probe_bm25_index_ready(index_path) is True


def test_bm25_probe_rejects_fake_fts_schema(tmp_path):
    from agent_brain.platform.doctor import _probe_bm25_index_ready

    index_path = tmp_path / "fake-index.db"
    with sqlite3.connect(str(index_path)) as connection:
        connection.execute("CREATE TABLE items_fts(id TEXT, body TEXT)")

    assert _probe_bm25_index_ready(index_path) is False


def test_bm25_probe_rejects_corrupt_index(tmp_path):
    from agent_brain.platform.doctor import _probe_bm25_index_ready

    index_path = tmp_path / "corrupt-index.db"
    index_path.write_bytes(b"not a sqlite database")

    assert _probe_bm25_index_ready(index_path) is False


def test_bm25_probe_is_readonly_without_sidecars(tmp_path):
    from agent_brain.platform.doctor import _probe_bm25_index_ready

    readonly_dir = tmp_path / "readonly"
    readonly_dir.mkdir()
    index_path = readonly_dir / "index ?#% spaced.db"
    _create_fts_index(index_path)
    before = index_path.read_bytes()
    readonly_dir.chmod(0o555)
    try:
        assert _probe_bm25_index_ready(index_path) is True
        assert index_path.read_bytes() == before
        assert not Path(f"{index_path}-wal").exists()
        assert not Path(f"{index_path}-shm").exists()
        assert not Path(f"{index_path}-journal").exists()
    finally:
        readonly_dir.chmod(0o755)


def test_bm25_probe_fails_fast_under_exclusive_delete_journal_lock(tmp_path):
    from agent_brain.platform.doctor import _probe_bm25_index_ready

    index_path = tmp_path / "locked-index.db"
    _create_fts_index(index_path)
    locker = sqlite3.connect(str(index_path))
    try:
        locker.execute("PRAGMA journal_mode=DELETE")
        locker.execute("BEGIN EXCLUSIVE")
        started = time.monotonic()

        assert _probe_bm25_index_ready(index_path) is False

        elapsed = time.monotonic() - started
        assert elapsed < 0.75
    finally:
        locker.rollback()
        locker.close()


def test_doctor_offline_renders_four_routed_recall_status_rows(
    tmp_brain,
    monkeypatch,
):
    from typer.testing import CliRunner

    from agent_brain.interfaces.cli import app

    _create_ready_index(tmp_brain)
    monkeypatch.setenv("COLUMNS", "220")

    result = CliRunner().invoke(app, ["doctor", "--offline"])

    assert result.exit_code == 0, result.output
    output = " ".join(result.stdout.lower().split())
    assert "routed recall" in output
    assert "prompt injection gateway" in output
    assert "semantic provider" in output
    assert "lexical raw fallback" in output


def test_doctor_pending_next_action_is_preview_only(tmp_brain, monkeypatch):
    from typer.testing import CliRunner

    from agent_brain.interfaces.cli import app
    from agent_brain.memory.store.pending import enqueue_write_record

    _create_ready_index(tmp_brain)
    enqueue_write_record({"op": "write", "item": {"title": "doctor pending"}})
    monkeypatch.setenv("COLUMNS", "240")

    result = CliRunner().invoke(app, ["doctor", "--offline"])

    assert result.exit_code in {0, 1}, result.output
    output = " ".join(result.stdout.split())
    assert "memory sync-pending --summary-only --format json" in output
    assert "--apply" not in output


def test_doctor_pending_row_reports_ready_review_blocker_and_oldest(
    tmp_brain,
    monkeypatch,
):
    from datetime import datetime, timedelta, timezone

    from typer.testing import CliRunner

    from agent_brain.interfaces.cli import app
    from agent_brain.memory.store.pending import enqueue_write_record

    _create_ready_index(tmp_brain)
    now = datetime.now(timezone.utc)
    enqueue_write_record({
        "v": 2,
        "op": "write",
        "origin": "hook",
        "record_id": "doctor-ready-record",
        "enqueued_at": now.isoformat(),
        "original_created_at": now.isoformat(),
        "item": {
            "type": "fact",
            "title": "ready doctor item",
            "summary": "ready doctor summary",
            "body": "ready doctor body",
        },
    })
    stale_time = now - timedelta(days=40)
    enqueue_write_record({
        "v": 2,
        "op": "write",
        "origin": "hook",
        "record_id": "doctor-review-record",
        "enqueued_at": stale_time.isoformat(),
        "original_created_at": stale_time.isoformat(),
        "item": {
            "type": "signal",
            "title": "review doctor item",
            "summary": "review doctor summary",
            "body": "review doctor body",
        },
    })
    (tmp_brain / "pending" / "broken.jsonl").write_text(
        "{not-json\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("COLUMNS", "260")

    result = CliRunner().invoke(app, ["doctor", "--offline"])

    assert result.exit_code in {0, 1}, result.output
    output = " ".join(result.stdout.split())
    assert "ready=1" in output
    assert "review=1" in output
    assert "blocker=1" in output
    assert "oldest=" in output
    assert "memory sync-pending --summary-only --format json" in output
    assert "--apply" not in output


def test_doctor_keeps_semantic_not_fast_ready_visible_when_lifecycle_passes(
    tmp_brain,
    monkeypatch,
):
    from typer.testing import CliRunner

    import agent_brain.platform.doctor as doctor
    from agent_brain.interfaces.cli import app

    _create_ready_index(tmp_brain)
    monkeypatch.setattr(
        doctor,
        "_probe_semantic_provider_status",
        lambda: "not_fast_ready",
    )
    monkeypatch.setenv("COLUMNS", "260")

    result = CliRunner().invoke(app, ["doctor", "--offline"])

    assert result.exit_code in {0, 1}, result.output
    output = " ".join(result.stdout.split())
    assert "semantic provider" in output
    assert "not_fast_ready" in output
    assert "lifecycle / pending governance" in output


def test_doctor_gateway_probe_fails_closed_on_real_import_exception(
    tmp_brain,
    monkeypatch,
):
    import agent_brain.platform.doctor as doctor

    real_import = builtins.__import__

    def fail_gateway_import(name, *args, **kwargs):
        if name == "agent_brain.memory.context.injection_gateway":
            raise ImportError("simulated unavailable gateway")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fail_gateway_import)

    assert doctor._probe_injection_gateway_available() is False


def test_doctor_gateway_probe_rejects_noncallable_api(tmp_brain, monkeypatch):
    import agent_brain.memory.context.injection_gateway as gateway
    import agent_brain.platform.doctor as doctor

    monkeypatch.setattr(gateway, "build_injection_context", None)

    assert doctor._probe_injection_gateway_available() is False


def test_doctor_offline_cold_import_never_uses_network_and_loads_gateway(tmp_brain):
    root = Path(__file__).resolve().parents[2]
    script = textwrap.dedent(
        """
        import json
        import socket

        calls = []

        class BlockedSocket(socket.socket):
            def __new__(cls, *args, **kwargs):
                calls.append("socket")
                raise AssertionError("offline doctor called socket.socket")

        def blocked(name):
            def fail(*args, **kwargs):
                calls.append(name)
                raise AssertionError(f"offline doctor called socket.{name}")
            return fail

        socket.socket = BlockedSocket
        socket.create_connection = blocked("create_connection")
        socket.getaddrinfo = blocked("getaddrinfo")

        from agent_brain.platform.doctor import run_doctor

        report = run_doctor(offline=True)
        print(json.dumps({
            "calls": calls,
            "gateway": report.checks["security.injection_gateway.available"],
        }))
        """
    )
    env = {
        **os.environ,
        "BRAIN_DIR": str(tmp_brain),
        "MEMORY_HUB_TEST_EMBEDDING": "1",
        "PYTHONPATH": str(root),
    }

    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload == {"calls": [], "gateway": True}


def test_doctor_degrades_when_injection_gateway_is_unavailable(
    tmp_brain,
    monkeypatch,
):
    import agent_brain.platform.doctor as doctor

    monkeypatch.setattr(
        doctor,
        "_probe_injection_gateway_available",
        lambda: False,
        raising=False,
    )

    rep = doctor.run_doctor(offline=True)

    assert rep.checks["security.injection_gateway.available"] is False
    assert rep.overall == "DEGRADED"
    assert rep.exit_code == 1


def test_doctor_keeps_md_store_failure_broken_when_gateway_is_unavailable(
    tmp_brain,
    monkeypatch,
):
    import agent_brain.platform.doctor as doctor

    original_write_text = Path.write_text

    def fail_doctor_probe(path: Path, *args, **kwargs):
        if path.name == ".doctor-probe":
            raise OSError("read-only store")
        return original_write_text(path, *args, **kwargs)

    monkeypatch.setattr(
        doctor,
        "_probe_injection_gateway_available",
        lambda: False,
        raising=False,
    )
    monkeypatch.setattr(Path, "write_text", fail_doctor_probe)

    rep = doctor.run_doctor(offline=True)

    assert rep.checks["core.md_store.writable"] is False
    assert rep.checks["security.injection_gateway.available"] is False
    assert rep.overall == "BROKEN"
    assert rep.exit_code == 2


def test_doctor_reports_pending_depth(tmp_brain):
    from agent_brain.memory.store.pending import enqueue_write_record
    enqueue_write_record({"op": "write", "item": {"title": "x", "summary": "s", "body": "b"}})
    rep = run_doctor(offline=True)
    assert rep.checks["pending.depth"] == 1


def test_doctor_reports_broken_memory_cli_shim(tmp_brain, tmp_path, monkeypatch):
    home = tmp_path / "home"
    shim = home / ".local" / "bin" / "memory"
    target = tmp_path / "deleted" / ".venv" / "bin" / "memory"
    shim.parent.mkdir(parents=True)
    shim.write_text(f'#!/bin/sh\nexec "{target}" "$@"\n', encoding="utf-8")
    shim.chmod(0o755)
    monkeypatch.setenv("HOME", str(home))

    rep = run_doctor(offline=True)

    assert rep.checks["cli.shim.present"] is True
    assert rep.checks["cli.shim.target_exists"] is False
    assert rep.details["cli.shim.target"] == str(target)
    assert rep.overall == "DEGRADED"


def test_doctor_reports_malformed_item_count(tmp_brain):
    bad = tmp_brain / "items" / "bad.md"
    bad.write_text("missing frontmatter\n", encoding="utf-8")

    rep = run_doctor(offline=True)

    assert rep.checks["core.items.skipped"] == 1
    assert rep.overall == "DEGRADED"
    assert rep.exit_code == 1


def test_doctor_reports_bounded_malformed_item_details(tmp_brain):
    bad = tmp_brain / "items" / "bad.md"
    bad.write_text("missing frontmatter\n", encoding="utf-8")

    rep = run_doctor(offline=True)

    assert rep.details["core.items.skipped"][0]["path"].endswith("bad.md")
    assert rep.details["core.items.skipped"][0]["reason"]


def test_malformed_repair_dry_run_does_not_move_file(tmp_brain):
    from agent_brain.memory.store.malformed_repair import quarantine_malformed_items

    bad = tmp_brain / "items" / "bad.md"
    bad.write_text("missing frontmatter\n", encoding="utf-8")

    report = quarantine_malformed_items(tmp_brain / "items", apply=False)

    assert report.found == 1
    assert report.moved == 0
    assert bad.exists()
    assert report.actions[0].destination.name == "bad.md"


def test_malformed_repair_apply_moves_file_and_records_reason(tmp_brain):
    from agent_brain.memory.store.malformed_repair import quarantine_malformed_items

    bad = tmp_brain / "items" / "bad.md"
    bad.write_text("missing frontmatter\n", encoding="utf-8")

    report = quarantine_malformed_items(tmp_brain / "items", apply=True)

    assert report.found == 1
    assert report.moved == 1
    assert not bad.exists()
    moved = tmp_brain / "items" / "archived" / "malformed" / "bad.md"
    assert moved.read_text(encoding="utf-8") == "missing frontmatter\n"
    assert (moved.with_suffix(moved.suffix + ".reason.txt")).exists()

    repaired = run_doctor(offline=True)
    assert repaired.checks["core.items.skipped"] == 0


def test_malformed_restore_dry_run_does_not_move_valid_file(tmp_brain):
    from datetime import datetime, timezone

    from agent_brain.memory.store.items_store import ItemsStore
    from agent_brain.memory.store.malformed_repair import restore_malformed_item
    from agent_brain.contracts.memory_item import MemoryItem, MemoryType

    item = MemoryItem(
        id="mem-20260609-120000-restored",
        type=MemoryType.fact,
        created_at=datetime(2026, 6, 9, 12, 0, tzinfo=timezone.utc),
        title="Restored memory",
        summary="A manually repaired quarantined item",
        tags=["restore"],
    )
    archive_store = ItemsStore(tmp_brain / "items" / "archived" / "malformed")
    archived = archive_store.write(item, "restored body")

    report = restore_malformed_item(tmp_brain / "items", archived.name, apply=False)

    assert report.found == 1
    assert report.restored == 0
    assert archived.exists()
    assert not (tmp_brain / "items" / "mem-20260609-120000-restored.md").exists()
    assert report.actions[0].valid is True


def test_malformed_restore_apply_moves_valid_file_back_to_active_items(tmp_brain):
    from datetime import datetime, timezone

    from agent_brain.memory.store.items_store import ItemsStore
    from agent_brain.memory.store.malformed_repair import restore_malformed_item
    from agent_brain.contracts.memory_item import MemoryItem, MemoryType

    item = MemoryItem(
        id="mem-20260609-120001-restored",
        type=MemoryType.fact,
        created_at=datetime(2026, 6, 9, 12, 1, tzinfo=timezone.utc),
        title="Restored memory",
        summary="A manually repaired quarantined item",
        tags=["restore"],
    )
    archive_store = ItemsStore(tmp_brain / "items" / "archived" / "malformed")
    archived = archive_store.write(item, "restored body")
    archived.with_suffix(archived.suffix + ".reason.txt").write_text("fixed manually\n", encoding="utf-8")

    report = restore_malformed_item(tmp_brain / "items", archived.name, apply=True)

    active = tmp_brain / "items" / "mem-20260609-120001-restored.md"
    assert report.found == 1
    assert report.restored == 1
    assert not archived.exists()
    assert active.exists()
    assert "restored body" in active.read_text(encoding="utf-8")
    assert not archived.with_suffix(archived.suffix + ".reason.txt").exists()


def test_malformed_restore_rejects_still_invalid_file(tmp_brain):
    from agent_brain.memory.store.malformed_repair import restore_malformed_item

    archive = tmp_brain / "items" / "archived" / "malformed" / "bad.md"
    archive.parent.mkdir(parents=True)
    archive.write_text("still missing frontmatter\n", encoding="utf-8")

    report = restore_malformed_item(tmp_brain / "items", archive.name, apply=True)

    assert report.found == 1
    assert report.restored == 0
    assert archive.exists()
    assert report.actions[0].valid is False
    assert "ValueError" in report.actions[0].reason


def test_malformed_restore_does_not_overwrite_active_item(tmp_brain):
    from datetime import datetime, timezone

    from agent_brain.memory.store.items_store import ItemsStore
    from agent_brain.memory.store.malformed_repair import restore_malformed_item
    from agent_brain.contracts.memory_item import MemoryItem, MemoryType

    item = MemoryItem(
        id="mem-20260609-120002-duplicate",
        type=MemoryType.fact,
        created_at=datetime(2026, 6, 9, 12, 2, tzinfo=timezone.utc),
        title="Duplicate memory",
        summary="A manually repaired quarantined item",
        tags=["restore"],
    )
    active_store = ItemsStore(tmp_brain / "items")
    active = active_store.write(item, "active body")
    archive_store = ItemsStore(tmp_brain / "items" / "archived" / "malformed")
    archived = archive_store.write(item, "archived body")

    report = restore_malformed_item(tmp_brain / "items", archived.name, apply=True)

    assert report.found == 1
    assert report.restored == 0
    assert archived.exists()
    assert "active item already exists" in report.actions[0].reason
    assert "active body" in active.read_text(encoding="utf-8")


def test_malformed_restore_rejects_path_traversal(tmp_brain):
    import pytest

    from agent_brain.memory.store.malformed_repair import restore_malformed_item

    with pytest.raises(ValueError, match="archived_name must stay under"):
        restore_malformed_item(tmp_brain / "items", "../bad.md")

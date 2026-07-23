"""WriteService funnel contract.

These tests pin the central invariant of the brain pool: the markdown append
(ItemsStore.write) is the ONLY thing that decides "written". Indexing/embedding
are best-effort — their failure degrades the result but never blocks the write —
and the audit gate fail-closes on critical/high findings unless explicitly waived.
"""

import logging
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from agent_brain.contracts.memory_item import MemoryItem, MemoryType, Refs, Source
from agent_brain.memory.store.items_store import ItemsStore
from agent_brain.memory.store.write_service import WriteService, WriteResult


def test_write_result_type_is_split_and_reexported():
    from agent_brain.memory.store import write_service
    from agent_brain.memory.store.write_types import WriteResult as SplitWriteResult

    assert write_service.WriteResult is SplitWriteResult
    result = SplitWriteResult(status="written", item_id="mem-20260519-100000-test")
    assert result.status == "written"
    assert result.degraded == []
    assert result.warnings == []


def _item(title="hello world", type=MemoryType.fact):
    from agent_brain.memory.store.items_store import make_item_id

    now = datetime.now(timezone.utc).astimezone()
    return MemoryItem(
        id=make_item_id(title, when=now), type=type, created_at=now, title=title, summary="s"
    )


def _tree_snapshot(root: Path) -> list[tuple[str, bytes | None]]:
    return [
        (
            str(path.relative_to(root)),
            None if path.is_dir() else path.read_bytes(),
        )
        for path in sorted(root.rglob("*"))
    ]


def test_write_succeeds_when_md_append_succeeds(tmp_brain):
    # tmp_brain fixture points ItemsStore at a temp dir (see conftest)
    svc = WriteService.for_brain(tmp_brain)
    res = svc.write(item=_item(), body="body text", allow_unsafe=True)
    assert isinstance(res, WriteResult)
    assert res.status == "written"
    assert res.item_id
    assert res.indexed is True


def test_write_still_written_when_indexing_fails(tmp_brain, monkeypatch):
    svc = WriteService.for_brain(tmp_brain)
    # Force the index/embedder layer to explode:
    monkeypatch.setattr(
        svc, "_index_item", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("embedder offline"))
    )
    res = svc.write(item=_item(), body="b", allow_unsafe=True)
    assert res.status == "written"  # md append is the only verdict
    assert res.indexed is False
    assert "index" in res.degraded


def test_reconcile_existing_repairs_source_ledger_and_index(tmp_brain):
    item = _item(title="reconcile interrupted write")
    body = "durable markdown survived the interrupted write"
    store = ItemsStore(tmp_brain / "items")
    path = store.write(item, body)
    svc = WriteService.for_brain(tmp_brain)

    res = svc.reconcile_existing(item=item, body=body)

    assert res.status == "written"
    assert res.item_id == item.id
    assert res.path == str(path)
    assert res.indexed is True
    assert res.degraded == []
    source = tmp_brain / "sources" / "writes" / f"{item.id}.json"
    assert json.loads(source.read_text(encoding="utf-8"))["body_sha256"]


def test_reconcile_existing_marks_index_dirty_without_losing_written_verdict(
    tmp_brain, monkeypatch
):
    item = _item(title="reconcile missing index")
    body = "body"
    store = ItemsStore(tmp_brain / "items")
    store.write(item, body)
    svc = WriteService.for_brain(tmp_brain)
    monkeypatch.setattr(
        svc,
        "_index_item",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("offline")),
    )

    res = svc.reconcile_existing(item=item, body=body)

    assert res.status == "written"
    assert res.indexed is False
    assert res.degraded == ["index"]
    assert item.id in (tmp_brain / ".index-dirty").read_text(encoding="utf-8")


def test_write_service_dirty_marker_uses_explicit_brain_not_environment(
    tmp_path,
    monkeypatch,
):
    requested = tmp_path / "requested"
    environment = tmp_path / "environment"
    monkeypatch.setenv("BRAIN_DIR", str(environment))
    store = ItemsStore(requested / "items")
    svc = WriteService(
        store,
        index=None,
        embedder=None,
        brain_dir=requested,
    )
    item = _item(title="explicit dirty marker brain")

    result = svc.write(item=item, body="body", allow_unsafe=True)

    assert result.degraded == ["index"]
    assert item.id in (requested / ".index-dirty").read_text(encoding="utf-8")
    assert not (environment / ".index-dirty").exists()


def test_audit_gate_blocks_critical(tmp_brain):
    svc = WriteService.for_brain(tmp_brain)
    # An item whose text trips a critical audit rule (a private key marker).
    private_key_marker = "-----BEGIN " + "RSA PRIVATE KEY-----"
    bad = _item(title=private_key_marker)
    res = svc.write(item=bad, body=private_key_marker, allow_unsafe=False)
    assert res.status == "blocked"
    assert res.findings


def test_allow_unsafe_write_logs_audit_bypass(tmp_brain, caplog):
    svc = WriteService.for_brain(tmp_brain)
    item = _item(title="unsafe bypass audit")

    with caplog.at_level(logging.WARNING, logger="agent_brain.memory.store.write_service"):
        res = svc.write(item=item, body="body", allow_unsafe=True)

    assert res.status == "written"
    assert item.id in caplog.text
    assert "allow_unsafe" in caplog.text


def test_structured_memory_quality_warnings_do_not_block_write(tmp_brain):
    svc = WriteService.for_brain(tmp_brain)
    item = _item(title="decision without sections", type=MemoryType.decision)

    res = svc.write(item=item, body="We picked SSE.", allow_unsafe=True)

    assert res.status == "written"
    assert (
        "decision body missing required sections: **决策**, **理由**, **改回去的代价**"
        in res.warnings
    )
    assert "decision item has no source refs" not in res.warnings


def test_write_service_attaches_write_input_evidence_sidecar(tmp_brain):
    svc = WriteService.for_brain(tmp_brain)
    item = _item(title="sourced by write input", type=MemoryType.fact)
    body = "**事实**\nManual smoke passed.\n**来源**\nCurrent write input.\n**有效期**\ncurrent"

    res = svc.write(item=item, body=body, allow_unsafe=True)

    stored, stored_body = svc._store.get(item.id)
    assert res.status == "written"
    assert stored_body.rstrip("\n") == body
    assert stored.refs.resources
    assert stored.refs.extractions
    resource_id = stored.refs.resources[0]
    extraction_id = stored.refs.extractions[0]
    resource_path = tmp_brain / "resources" / f"{resource_id}.json"
    extraction_path = tmp_brain / "extractions" / f"{extraction_id}.json"
    assert resource_path.exists()
    assert extraction_path.exists()
    resource = json.loads(resource_path.read_text(encoding="utf-8"))
    extraction = json.loads(extraction_path.read_text(encoding="utf-8"))
    created_at = datetime.fromisoformat(resource["created_at"]).astimezone(timezone.utc)
    assert resource_id[4:19] == f"{created_at:%Y%m%d-%H%M%S}"
    assert extraction_id[4:19] == f"{created_at:%Y%m%d-%H%M%S}"
    assert extraction["created_at"] == resource["created_at"]
    source_record = tmp_brain / "sources" / "writes" / f"{item.id}.json"
    assert source_record.exists()
    data = json.loads(source_record.read_text(encoding="utf-8"))
    assert data["item_id"] == item.id
    assert data["source_kind"] == "write_input"
    assert data["refs"]["resources"] == [resource_id]
    assert data["refs"]["extractions"] == [extraction_id]
    assert "fact item has no source refs" not in res.warnings


@pytest.mark.parametrize(
    ("case", "expected"),
    [
        ("count", "WRITE_EVIDENCE_FILE_COUNT_EXCEEDED"),
        ("size", "WRITE_EVIDENCE_FILE_TOO_LARGE"),
    ],
)
def test_write_evidence_caps_fail_before_any_durable_write(
    tmp_brain: Path,
    case: str,
    expected: str,
) -> None:
    svc = WriteService.for_brain(tmp_brain)
    if case == "count":
        refs = Refs(
            files=[str(tmp_brain / f"missing-{index}.txt") for index in range(65)]
        )
        rejected_path = refs.files[-1]
    else:
        oversized = tmp_brain / "oversized.bin"
        with oversized.open("wb") as handle:
            handle.truncate(64 * 1024 * 1024 + 1)
        refs = Refs(files=[str(oversized)])
        rejected_path = str(oversized)
    item = _item(title=f"reject evidence {case}").model_copy(
        update={"refs": refs}
    )
    before = _tree_snapshot(tmp_brain)

    with pytest.raises(RuntimeError) as caught:
        svc.write(item=item, body="bounded body", allow_unsafe=True)

    assert str(caught.value) == expected
    assert rejected_path not in str(caught.value)
    assert _tree_snapshot(tmp_brain) == before
    assert not list((tmp_brain / "items").glob("*.md"))
    assert not (tmp_brain / "resources").exists()
    assert not (tmp_brain / "extractions").exists()


def test_write_evidence_accepts_exact_file_count_cap(tmp_brain: Path) -> None:
    svc = WriteService.for_brain(tmp_brain)
    refs = Refs(
        files=[str(tmp_brain / f"missing-{index}.txt") for index in range(64)]
    )
    item = _item(title="exact evidence file count").model_copy(
        update={"refs": refs}
    )

    result = svc.write(item=item, body="bounded body", allow_unsafe=True)

    stored, _body = svc._store.get(item.id)
    assert result.status == "written"
    assert stored.refs.files == refs.files


def test_write_evidence_accepts_exact_file_size_cap(tmp_brain: Path) -> None:
    evidence = tmp_brain / "exactly-64mib.bin"
    with evidence.open("wb") as handle:
        handle.truncate(64 * 1024 * 1024)
    svc = WriteService.for_brain(tmp_brain)
    item = _item(title="exact evidence size").model_copy(
        update={"refs": Refs(files=[str(evidence)])}
    )

    result = svc.write(item=item, body="bounded body", allow_unsafe=True)

    stored, _body = svc._store.get(item.id)
    assert result.status == "written"
    assert len(stored.refs.resources) == 2
    assert len(stored.refs.extractions) == 1


def test_evidence_quality_warnings_do_not_block_write(tmp_brain):
    svc = WriteService.for_brain(tmp_brain)
    item = _item(title="image-derived fact", type=MemoryType.fact)

    res = svc.write(
        item=item,
        body="**事实**\n[Image #1] shows a warning.\n**来源**\nunknown\n**有效期**\ncurrent",
        allow_unsafe=True,
    )

    assert res.status == "written"
    assert "fact item has no source refs" in res.warnings
    assert (
        "body contains multimodal placeholder without resource/extraction refs: [Image #1]"
        in res.warnings
    )


def test_write_marks_unbounded_harvested_memory_as_review_candidate(tmp_brain):
    svc = WriteService.for_brain(tmp_brain)
    item = _item(title="maybe browser works now", type=MemoryType.episode).model_copy(
        update={"source": Source(kind="harvested", extractor="mechanical")}
    )

    res = svc.write(
        item=item, body="User said the browser issue might be fixed.", allow_unsafe=True
    )

    stored, _body = svc._store.get(item.id)
    assert res.status == "written"
    assert "needs-review" in stored.tags
    assert "unverified-boundary" in stored.tags
    assert stored.confidence <= 0.35
    assert (
        "memory item lacks explicit validity/source boundary; marked needs-review" in res.warnings
    )


def test_write_keeps_sourced_harvested_memory_in_normal_pool(tmp_brain):
    svc = WriteService.for_brain(tmp_brain)
    item = _item(title="sourced harvested workflow", type=MemoryType.episode).model_copy(
        update={
            "source": Source(kind="harvested", extractor="mechanical"),
            "refs": Refs(files=["docs/workflow.md"]),
        }
    )

    res = svc.write(item=item, body="Workflow backed by a file.", allow_unsafe=True)

    stored, _body = svc._store.get(item.id)
    assert res.status == "written"
    assert "needs-review" not in stored.tags
    assert "unverified-boundary" not in stored.tags


def test_write_service_enriches_provable_runtime_fields(tmp_brain, monkeypatch):
    monkeypatch.setenv("AGENT_MEMORY_HUB_CWD", str(tmp_brain))
    monkeypatch.setenv("AGENT_MEMORY_HUB_ADAPTER", "codex")
    svc = WriteService.for_brain(tmp_brain)
    item = _item(title="runtime field enrichment", type=MemoryType.fact)

    res = svc.write(item=item, body="body", allow_unsafe=True)

    stored, _body = svc._store.get(item.id)
    assert res.status == "written"
    assert stored.validity.observed_at == stored.created_at
    assert stored.retention.last_accessed == stored.created_at
    assert stored.retention.access_count == 0
    assert stored.validity.cwd == str(tmp_brain)
    assert stored.validity.adapter == "codex"
    assert stored.validity.os
    assert stored.source.transcript_id is None
    assert stored.source.span_hash is None

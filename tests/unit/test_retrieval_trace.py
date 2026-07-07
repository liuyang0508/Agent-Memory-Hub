from __future__ import annotations

from datetime import datetime
from pathlib import Path

from agent_brain.contracts.memory_item import MemoryItem, MemoryType
from agent_brain.platform.embedding import HashingEmbedder
from agent_brain.platform.indexing.index import HubIndex


def _index(tmp: Path) -> HubIndex:
    return HubIndex(db_path=tmp / "index.db", embedding_dim=8)


def _item(item_id: str, title: str, summary: str = "summary") -> MemoryItem:
    return MemoryItem(
        id=item_id,
        type=MemoryType.fact,
        created_at=datetime.now(),
        title=title,
        summary=summary,
    )


def test_search_explain_attaches_retrieval_trace(tmp_brain_dir: Path) -> None:
    from agent_brain.memory.recall.retrieval import Retriever

    idx = _index(tmp_brain_dir)
    emb = HashingEmbedder(dim=8)
    item = _item("mem-20260620-010000-trace-python", "Python trace", "python trace")
    idx.upsert(item, "python trace retrieval", embedding=emb.embed("python trace retrieval"))

    hit = Retriever(idx, emb, vector_weight=0, apply_decay=False, record_access=False).search(
        "python trace",
        top_k=1,
        explain=True,
    )[0]

    assert hit.trace is not None
    assert hit.trace.initial_bm25_rank == 1
    assert hit.trace.final_rank == 1
    assert "bm25" in hit.trace.signals
    assert "rrf" in hit.trace.compact()


def test_search_without_explain_leaves_trace_empty(tmp_brain_dir: Path) -> None:
    from agent_brain.memory.recall.retrieval import Retriever

    idx = _index(tmp_brain_dir)
    emb = HashingEmbedder(dim=8)
    item = _item("mem-20260620-010001-no-trace", "No trace", "no trace")
    idx.upsert(item, "no trace retrieval", embedding=emb.embed("no trace retrieval"))

    hit = Retriever(idx, emb, vector_weight=0, apply_decay=False, record_access=False).search(
        "no trace",
        top_k=1,
    )[0]

    assert hit.trace is None


def test_graph_expanded_neighbor_trace_records_added_stage(tmp_brain_dir: Path) -> None:
    from agent_brain.memory.recall.retrieval import Retriever

    idx = _index(tmp_brain_dir)
    emb = HashingEmbedder(dim=8)
    root = _item("mem-20260620-010002-root", "Root trace", "root trace")
    neighbor = _item("mem-20260620-010003-neighbor", "Neighbor trace", "neighbor trace")
    idx.upsert(root, "root graph seed", embedding=emb.embed("root graph seed"))
    idx.upsert(neighbor, "unrelated neighbor", embedding=emb.embed("unrelated neighbor"))
    idx.add_ref(root.id, neighbor.id, "refs")

    hits = Retriever(
        idx,
        emb,
        vector_weight=0,
        graph_expand=True,
        graph_depth=1,
        apply_decay=False,
        record_access=False,
    ).search("root graph seed", top_k=2, explain=True)

    by_id = {hit.id: hit for hit in hits}
    trace = by_id[neighbor.id].trace
    assert trace is not None
    assert any(stage.name == "graph_expand" and stage.effect == "added" for stage in trace.stages)
    assert "graph_expand:added" in trace.signals

"""P1-1: CJK BM25 must actually match.

The query side tokenizes CJK into single characters (_tokenize_mixed →
expand_query → '"第" OR "二" OR "大" OR "脑"'), but the unicode61 FTS indexer
stored a run of CJK as ONE token. So no CJK query ever matched on BM25 — half
the retrieval signal was dead for a Chinese-first product. The fix segments CJK
at index time so each char is its own token, matching the query side.
"""
from datetime import datetime, timezone
from pathlib import Path

from agent_brain.contracts.memory_item import MemoryItem, MemoryType


def _item(suffix: str, title: str, summary: str) -> tuple[MemoryItem, str]:
    item = MemoryItem(
        id=f"mem-20260519-100000-{suffix}",
        type=MemoryType.decision,
        created_at=datetime(2026, 5, 19, tzinfo=timezone.utc),
        title=title,
        summary=summary,
    )
    return item, summary


def test_cjk_bm25_finds_item_via_expanded_query(tmp_brain_dir: Path):
    from agent_brain.platform.indexing.index import HubIndex
    from agent_brain.memory.recall.retrieval import expand_query

    idx = HubIndex(db_path=tmp_brain_dir / "index.db")
    item, body = _item("cjk", "多智能体共享第二大脑", "跨 agent 的记忆中枢与检索")
    idx.upsert(item, body, embedding=None)

    hits = idx.bm25_search(expand_query("第二大脑"), top_k=10)
    assert hits, "CJK query returned no BM25 hits"
    assert hits[0].id == item.id


def test_mixed_cjk_ascii_still_matches_ascii(tmp_brain_dir: Path):
    from agent_brain.platform.indexing.index import HubIndex
    from agent_brain.memory.recall.retrieval import expand_query

    idx = HubIndex(db_path=tmp_brain_dir / "index.db")
    item, body = _item("mix", "用 Postgres 做向量检索", "pgvector embedding 方案")
    idx.upsert(item, body, embedding=None)

    assert idx.bm25_search(expand_query("pgvector"), top_k=10)
    assert idx.bm25_search(expand_query("向量检索"), top_k=10)


def test_extended_cjk_kana_and_hangul_match_bm25(tmp_brain_dir: Path):
    from agent_brain.platform.indexing.index import HubIndex
    from agent_brain.memory.recall.retrieval import expand_query

    idx = HubIndex(db_path=tmp_brain_dir / "index.db")
    item, body = _item(
        "cjk-wide",
        "かな ハングル 𠀀",
        "日本語かなと한국어한글以及扩展B汉字𠀀",
    )
    idx.upsert(item, body, embedding=None)

    assert idx.bm25_search(expand_query("かな"), top_k=10)
    assert idx.bm25_search(expand_query("한글"), top_k=10)
    assert idx.bm25_search(expand_query("𠀀"), top_k=10)

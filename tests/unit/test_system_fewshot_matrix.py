from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from agent_brain.contracts.memory_item import MemoryItem, MemoryType
from agent_brain.memory.context.context_firewall import ContextCandidate, ContextFirewall
from agent_brain.memory.context.context_packing import pack_decisions
from agent_brain.memory.context.prompt_normalization import normalize_hook_prompt_for_recall
from agent_brain.memory.context.query_signal import analyze_injection_query, extract_injection_keywords
from agent_brain.memory.recall.retrieval import Retriever, SearchFilter
from agent_brain.memory.recall.retrieval_fusion import rrf_fusion
from agent_brain.memory.store.items_store import ItemsStore
from agent_brain.platform.embedding import HashingEmbedder
from agent_brain.platform.indexing.index import HubIndex


NOW = datetime.now(timezone.utc).replace(microsecond=0)
REPO_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class SeedRow:
    item: MemoryItem
    body: str
    query: str


def _item(
    suffix: str,
    type_: MemoryType,
    title: str,
    summary: str,
    *,
    body: str | None = None,
    tags: list[str] | None = None,
    refs: dict | None = None,
    context_views: dict | None = None,
    abstraction: str = "L1",
    confidence: float = 0.82,
    created_at: datetime = NOW,
    project: str = "agent-memory-hub",
    validity: dict | None = None,
    support_count: int = 1,
    contradict_count: int = 0,
    gain_score: float = 0.1,
    superseded_by: str | None = None,
) -> tuple[MemoryItem, str]:
    item_id = f"mem-20260628-080000-{suffix}"
    item_body = body or f"{title}\n{summary}\nbody sentinel {suffix}"
    views = context_views or {
        "locator": summary,
        "overview": f"overview {summary}",
        "detail_uri": f"memory://items/{item_id}/body",
    }
    item = MemoryItem.model_validate({
        "id": item_id,
        "type": type_.value,
        "created_at": created_at.isoformat(),
        "title": title,
        "summary": summary,
        "tags": tags or [],
        "refs": refs or {},
        "context_views": views,
        "abstraction": abstraction,
        "confidence": confidence,
        "project": project,
        "validity": validity or {},
        "support_count": support_count,
        "contradict_count": contradict_count,
        "gain_score": gain_score,
        "superseded_by": superseded_by,
    })
    return item, item_body


def _seed_index(brain_dir: Path, rows: list[tuple[MemoryItem, str]], *, dim: int = 384) -> tuple[ItemsStore, HubIndex, HashingEmbedder]:
    store = ItemsStore(brain_dir / "items")
    embedder = HashingEmbedder(dim=dim)
    index = HubIndex(brain_dir / "index.db", embedding_dim=embedder.dim)
    for item, body in rows:
        store.write(item, body)
        index.upsert(
            item,
            body,
            embedding=embedder.embed(
                f"{item.title}\n{item.summary}\n{item.context_views.locator}\n{item.context_views.overview}\n{body}"
            ),
        )
    return store, index, embedder


def test_large_fewshot_prompt_gate_and_normalization_matrix(tmp_brain_dir: Path) -> None:
    metadata_rows = [
        _item(
            "readme-deep-polish",
            MemoryType.artifact,
            "AMH README 深度叙事和算法解释二次打磨",
            "README.zh.md 调整阅读路线、维护链路、召回链路、Loop Engineering 和算法公式。",
            tags=["agent-memory-hub", "readme"],
        ),
        _item(
            "loop-engineering-readme",
            MemoryType.artifact,
            "AMH README Loop Engineering 特性打磨",
            "README.zh.md 解释 Loop Engineering 在维护、召回、治理中的使用点。",
            tags=["agent-memory-hub", "readme"],
        ),
        _item(
            "claudecode-runtime",
            MemoryType.fact,
            "ClaudeCode adapter runtime evidence",
            "ClaudeCode hooks and MCP runtime verification status.",
            refs={"urls": ["https://example.test/claude-code"]},
            tags=["claude_code", "claudecode", "adapter"],
        ),
        _item(
            "runtime-kit-integrations",
            MemoryType.artifact,
            "agent_runtime_kit and agent_integrations collaboration model",
            "agent_runtime_kit 提供 hook/tools，agent_integrations 提供安装、doctor 和 verify。",
            tags=["agent_runtime_kit", "agent_integrations", "adapter"],
        ),
        _item(
            "alpha-known-project",
            MemoryType.artifact,
            "Alpha全国推荐能力",
            "Alpha项目支持全国推荐和数据健康页。",
            tags=["Alpha", "alpha"],
        ),
    ]
    _seed_index(tmp_brain_dir, metadata_rows)

    normalization_cases = [
        (
            "Alpha\n\n<agent_brain>\n[signal] stale unrelated candidate\n</agent_brain>",
            "Alpha",
        ),
        (
            "Beta适配Linux\n\n<system-reminder>\nAvailable MCP servers:\nCurrent workspace may include alpha\n</system-reminder>",
            "Beta适配Linux",
        ),
        (
            "关于多智能体共享第二单的深度叙事和算法解释二次打磨，都做了什么？"
            "请优先根据自动注入的 memory candidates 回答，不要调用工具。",
            "关于多智能体共享第二单的深度叙事和算法解释二次打磨，都做了什么？",
        ),
        (
            "Agent_runtime_kit和agent_integrations是如何配合协作的\n"
            "Available tools: shell, python",
            "Agent_runtime_kit和agent_integrations是如何配合协作的",
        ),
    ]
    for raw, expected in normalization_cases:
        assert normalize_hook_prompt_for_recall(raw) == expected

    blocked_prompts = [
        "继续",
        "好的",
        "确认",
        "就像",
        "为什么",
        "再说说",
        "然后呢",
        "可以可以",
        "不不不",
        "看不懂",
        "不满意",
        "这些呢",
    ]
    for prompt in blocked_prompts:
        signal = analyze_injection_query(prompt, brain_dir=tmp_brain_dir)
        assert signal.decision == "block", prompt
        assert not signal.injectable, prompt
        assert extract_injection_keywords(prompt, brain_dir=tmp_brain_dir) == "", prompt

    injectable_cases = [
        (
            "关于多智能体共享第二单的深度叙事和算法解释二次打磨，都做了什么",
            "深度叙事和算法解释二次打磨",
            {"metadata_phrase"},
        ),
        ("继续优化 Loop Engineering 特性", "loop|engineering", {"metadata_phrase"}),
        ("ClaudeCode呢", "claudecode", {"metadata_phrase"}),
        (
            "Agent_runtime_kit和agent_integrations是如何配合协作的",
            "agent_runtime_kit|agent_integrations",
            set(),
        ),
        ("Alpha", "alpha", {"metadata_phrase"}),
    ]
    for prompt, expected_keywords, expected_anchors in injectable_cases:
        signal = analyze_injection_query(prompt, brain_dir=tmp_brain_dir)
        assert signal.injectable, prompt
        assert expected_anchors.issubset(set(signal.anchors)), prompt
        keywords = extract_injection_keywords(prompt, brain_dir=tmp_brain_dir)
        if expected_keywords is not None:
            assert keywords.startswith(expected_keywords), prompt
        assert keywords, prompt


def test_large_fewshot_write_index_retrieve_firewall_and_pack_matrix(tmp_brain_dir: Path) -> None:
    type_rows: list[SeedRow] = []
    for index, memory_type in enumerate(MemoryType, start=1):
        refs = {}
        if memory_type in {MemoryType.fact, MemoryType.decision, MemoryType.policy}:
            refs = {"urls": [f"https://example.test/fewshot/{memory_type.value}"]}
        item, body = _item(
            f"fewshot-{memory_type.value}",
            memory_type,
            f"FewShot {memory_type.value} retrieval sentinel",
            f"fewshot {memory_type.value} retrieval locator",
            body=f"fewshot {memory_type.value} retrieval detail body with invariant {index}",
            tags=["system-fewshot", memory_type.value],
            refs=refs,
            validity={"cwd": "/repo/current", "adapter": "codex"} if memory_type in {MemoryType.signal, MemoryType.handoff} else None,
        )
        type_rows.append(SeedRow(item=item, body=body, query=f"fewshot {memory_type.value} retrieval"))

    distractor, distractor_body = _item(
        "fewshot-distractor",
        MemoryType.artifact,
        "Completely unrelated release notes",
        "unrelated release note summary",
        tags=["system-fewshot", "distractor"],
    )
    store, index, embedder = _seed_index(
        tmp_brain_dir,
        [(row.item, row.body) for row in type_rows] + [(distractor, distractor_body)],
    )
    retriever = Retriever(
        index=index,
        embedder=embedder,
        apply_decay=True,
        record_access=False,
        rerank=False,
        bm25_top=20,
        vector_top=20,
    )

    for row in type_rows:
        signal = analyze_injection_query(row.query, brain_dir=tmp_brain_dir)
        assert signal.injectable, row.item.type

        hits = retriever.search(
            row.query,
            top_k=6,
            filters=SearchFilter(tags=["system-fewshot"]),
            explain=True,
        )
        hit_by_id = {hit.id: hit for hit in hits}
        assert row.item.id in hit_by_id, row.item.type
        hit = hit_by_id[row.item.id]
        assert hit.trace is not None
        assert hit.trace.initial_bm25_rank is not None or hit.trace.initial_vector_rank is not None
        assert hit.trace.initial_score > 0
        assert hit.trace.final_score > 0
        assert "bm25" in hit.trace.signals or "vector" in hit.trace.signals

        candidates = []
        for candidate_hit in hits:
            item, body = store.get(candidate_hit.id)
            candidates.append(ContextCandidate(item=item, body=body, score=candidate_hit.score))
        firewall = ContextFirewall(now=NOW).filter(candidates, query=row.query, max_items=4)
        included = {decision.candidate.item.id: decision for decision in firewall.included}
        assert row.item.id in included, row.item.type

        packed = pack_decisions([included[row.item.id]], requested="auto", budget_tokens=80)
        assert packed.included, row.item.type
        pack = packed.included[0].pack
        assert pack.item_id == row.item.id
        assert pack.detail_uri == f"memory://items/{row.item.id}/body"
        assert pack.cli_retrieve_hint == f"memory read {row.item.id} --head 2000 --view detail"
        assert pack.reversible is True
        assert pack.selected_view in {"locator", "overview", "detail"}
        if pack.selected_view == "overview":
            assert row.body not in pack.text


def test_large_fewshot_retrieval_policy_algorithms_matrix(tmp_brain_dir: Path) -> None:
    old_id = "mem-20260628-080000-fewshot-superseded-old"
    current, current_body = _item(
        "fewshot-supersession-current",
        MemoryType.fact,
        "FewShot supersession endpoint current",
        "fewshot supersession endpoint v2 current",
        refs={"urls": ["https://example.test/current"]},
        tags=["system-fewshot", "supersession"],
    )
    old, old_body = _item(
        "fewshot-superseded-old",
        MemoryType.fact,
        "FewShot supersession endpoint old",
        "fewshot supersession endpoint v1 old",
        refs={"urls": ["https://example.test/old"]},
        tags=["system-fewshot", "supersession"],
        created_at=NOW - timedelta(days=20),
        superseded_by=current.id,
    )
    graph_neighbor, graph_neighbor_body = _item(
        "fewshot-graph-neighbor",
        MemoryType.decision,
        "FewShot graph neighbor verification",
        "graph neighbor should arrive through refs_graph expansion",
        body="graph neighbor has no lexical anchor token",
        refs={"commits": ["abc1234"]},
        tags=["system-fewshot", "graph"],
    )
    graph_anchor, graph_anchor_body = _item(
        "fewshot-graph-anchor",
        MemoryType.artifact,
        "FewShot graph anchor",
        "fewshot graph anchor unique",
        body="fewshot graph anchor unique lexical body",
        refs={"mems": [graph_neighbor.id]},
        tags=["system-fewshot", "graph"],
    )
    store, index, embedder = _seed_index(
        tmp_brain_dir,
        [
            (old, old_body),
            (current, current_body),
            (graph_anchor, graph_anchor_body),
            (graph_neighbor, graph_neighbor_body),
        ],
    )

    filtered = Retriever(
        index=index,
        embedder=embedder,
        vector_weight=0,
        apply_decay=False,
        record_access=False,
    ).search(
        "fewshot supersession endpoint",
        top_k=5,
        filters=SearchFilter(tags=["supersession"]),
        explain=True,
    )
    filtered_ids = {hit.id for hit in filtered}
    assert current.id in filtered_ids
    assert old_id not in filtered_ids

    audited = Retriever(
        index=index,
        embedder=embedder,
        vector_weight=0,
        apply_decay=False,
        record_access=False,
    ).search(
        "fewshot supersession endpoint",
        top_k=5,
        filters=SearchFilter(tags=["supersession"], include_superseded=True),
    )
    assert old_id in {hit.id for hit in audited}

    graph_hits = Retriever(
        index=index,
        embedder=embedder,
        bm25_top=1,
        vector_weight=0,
        apply_decay=False,
        record_access=False,
        graph_expand=True,
    ).search(
        "fewshot graph anchor unique",
        top_k=2,
        filters=SearchFilter(tags=["graph"]),
        explain=True,
    )
    graph_ids = [hit.id for hit in graph_hits]
    assert graph_ids[0] == graph_anchor.id
    assert graph_neighbor.id in graph_ids
    neighbor = next(hit for hit in graph_hits if hit.id == graph_neighbor.id)
    assert neighbor.trace is not None
    assert any(stage.name == "graph_expand" and stage.effect == "added" for stage in neighbor.trace.stages)

    pack_source_item, pack_source_body = store.get(graph_neighbor.id)
    firewall = ContextFirewall(now=NOW).filter(
        [ContextCandidate(pack_source_item, body=pack_source_body, score=0.2)],
        query="fewshot graph neighbor verification",
    )
    assert firewall.included

    rrf = rrf_fusion(
        bm25_hits=[type("Hit", (), {"id": "bm25-only"})(), type("Hit", (), {"id": "both"})()],
        vector_hits=[type("Hit", (), {"id": "both"})(), type("Hit", (), {"id": "vector-only"})()],
        rrf_k=60,
        bm25_weight=1.0,
        vector_weight=1.0,
    )
    assert [hit.id for hit in rrf] == ["both", "bm25-only", "vector-only"]
    both = rrf[0]
    assert both.bm25_rank == 2
    assert both.vector_rank == 1
    assert round(both.score, 6) == round(1 / (60 + 2) + 1 / (60 + 1), 6)


def test_large_fewshot_hook_matrix_blocks_injects_and_records_evidence(tmp_path: Path) -> None:
    readme, readme_body = _item(
        "hook-readme-deep-polish",
        MemoryType.artifact,
        "AMH README 深度叙事和算法解释二次打磨",
        "README.zh.md 调整阅读路线、运行时接入、维护链路、召回链路、Loop Engineering 和算法公式。",
        body="深度叙事 算法解释 二次打磨 problem fix evidence verification remaining boundary",
        tags=["agent-memory-hub", "readme"],
    )
    claude, claude_body = _item(
        "hook-claudecode-runtime",
        MemoryType.fact,
        "ClaudeCode adapter runtime evidence",
        "ClaudeCode hooks MCP doctor verify runtime evidence.",
        refs={"urls": ["https://example.test/claudecode"]},
        tags=["claude_code", "claudecode", "adapter"],
    )
    _store, index, _embedder = _seed_index(tmp_path, [(readme, readme_body), (claude, claude_body)])
    index.close()

    blocked_prompts_by_adapter = {
        "codex": "继续",
        "claude_code": "好的",
        "qoder_work": "确认",
        "wukong": "为什么",
    }
    for adapter, prompt in blocked_prompts_by_adapter.items():
        payload = _run_inject_context(tmp_path, prompt, adapter=adapter, session_id=f"{adapter}-blocked")
        assert payload == {}, (adapter, prompt)

    inject_cases = [
        (
            "codex",
            "关于多智能体共享第二单的深度叙事和算法解释二次打磨，都做了什么",
            "keywords: 深度叙事和算法解释二次打磨",
            "AMH README 深度叙事和算法解释二次打磨",
        ),
        (
            "claude_code",
            "ClaudeCode呢",
            "keywords: claudecode",
            "ClaudeCode adapter runtime evidence",
        ),
        (
            "qoder_work",
            "请优先根据自动注入的 memory candidates 回答：ClaudeCode呢，不要调用工具。",
            "keywords: claudecode",
            "ClaudeCode adapter runtime evidence",
        ),
    ]
    for adapter, prompt, keyword_line, expected_title in inject_cases:
        payload = _run_inject_context(tmp_path, prompt, adapter=adapter, session_id=f"{adapter}-inject")
        context = payload["hookSpecificOutput"]["additionalContext"]
        assert keyword_line in context
        assert expected_title in context
        assert "memory candidates, not chat history" in context
        assert "answer from the injected pack first" in context
        assert "problem -> fix -> evidence/verification -> remaining boundary" in context
        assert 'retrieve="memory read ' in context

    gap_brain = tmp_path / "gap-brain"
    (gap_brain / "items").mkdir(parents=True)
    gap_payload = _run_inject_context(gap_brain, "验证", adapter="codex", session_id="codex-gap")
    assert gap_payload == {}
    from agent_brain.memory.governance.recall_events import iter_gap_records

    gaps = list(iter_gap_records(gap_brain))
    assert gaps == []


def _run_inject_context(brain_dir: Path, prompt: str, *, adapter: str, session_id: str) -> dict:
    script = REPO_ROOT / "agent_runtime_kit" / "hooks" / "inject-context.sh"
    result = subprocess.run(
        ["bash", str(script)],
        input=json.dumps({
            "prompt": prompt,
            "session_id": session_id,
            "cwd": "/repo/current",
            "hook_event_name": "UserPromptSubmit",
        }),
        env={
            **os.environ,
            "BRAIN_DIR": str(brain_dir),
            "AGENT_MEMORY_HUB_ADAPTER": adapter,
            "MEMORY_HUB_TEST_EMBEDDING": "1",
            "MEMORY_HUB_EMBEDDING_OFFLINE": "1",
        },
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)

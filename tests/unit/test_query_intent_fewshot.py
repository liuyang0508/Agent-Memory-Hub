from __future__ import annotations

import json
import os
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest

from agent_brain.contracts.memory_item import MemoryItem, MemoryType
from agent_brain.memory.context.query_signal import analyze_injection_query, extract_injection_keywords
from agent_brain.memory.store.items_store import ItemsStore


def test_query_intent_fewshot_cases(fixtures_dir: Path, tmp_brain_dir: Path) -> None:
    cases_path = fixtures_dir / "query_intent" / "fewshot_cases.json"
    cases = json.loads(cases_path.read_text(encoding="utf-8"))

    for case in cases:
        case_brain = tmp_brain_dir / case["prompt"].replace("/", "_")
        store = ItemsStore(case_brain / "items")
        for seed in case.get("seed_items", []):
            item = MemoryItem(
                id=seed["id"],
                type=MemoryType(seed["type"]),
                created_at=datetime.now(timezone.utc),
                title=seed["title"],
                summary=seed["summary"],
                tags=seed.get("tags", []),
            )
            store.write(item, seed["summary"])

        signal = analyze_injection_query(case["prompt"], brain_dir=case_brain)
        keywords = extract_injection_keywords(case["prompt"], brain_dir=case_brain)

        assert signal.decision == case["expected_decision"], case["description"]
        if "expected_keywords" in case:
            assert keywords == case["expected_keywords"], case["description"]
        if "expected_keywords_prefix" in case:
            assert keywords.startswith(case["expected_keywords_prefix"]), case["description"]
        for anchor in case.get("expected_anchors", []):
            assert anchor in signal.anchors, case["description"]


def test_query_intent_real_prompt_regression_cases(fixtures_dir: Path, tmp_brain_dir: Path) -> None:
    from agent_brain.memory.context.query_signal import diagnose_injection_query

    cases_path = fixtures_dir / "query_intent" / "real_prompt_cases.json"
    cases = json.loads(cases_path.read_text(encoding="utf-8"))

    for index, case in enumerate(cases):
        case_brain = tmp_brain_dir / f"real-prompt-{index}"
        store = ItemsStore(case_brain / "items")
        for seed in case.get("seed_items", []):
            item = MemoryItem(
                id=seed["id"],
                type=MemoryType(seed["type"]),
                created_at=datetime.now(timezone.utc),
                title=seed["title"],
                summary=seed["summary"],
                tags=seed.get("tags", []),
            )
            store.write(item, seed["summary"])

        diagnostic = diagnose_injection_query(case["prompt"], brain_dir=case_brain).to_dict()

        assert diagnostic["decision"] == case["expected_decision"], case["description"]
        if "expected_reason" in case:
            assert diagnostic["reason"] == case["expected_reason"], case["description"]
        if "expected_keywords" in case:
            assert diagnostic["keywords"] == case["expected_keywords"], case["description"]
        for anchor in case.get("expected_anchors", []):
            assert anchor in diagnostic["anchors"], case["description"]
        for weak_noise in case.get("expected_weak_noise", []):
            assert weak_noise in diagnostic["weak_noise"], case["description"]
        for trace in case.get("expected_trace_contains", []):
            assert trace in diagnostic["trace"], case["description"]


def test_query_signal_diagnose_json_cli_reports_real_prompt_trace(tmp_brain_dir: Path) -> None:
    item = MemoryItem(
        id="mem-20260704-220000-hybrid-amh-method-preset",
        type=MemoryType.decision,
        created_at=datetime.now(timezone.utc),
        title="AMH hybrid_amh.yaml method preset comparison",
        summary="hybrid_amh.yaml method preset is compared with external benchmark presets.",
        tags=["hybrid_amh.yaml", "method", "preset"],
    )
    ItemsStore(tmp_brain_dir / "items").write(item, "method preset comparison body")

    prompt = (
        "AMH 已作为本仓库追加的 hybrid_amh.yaml method preset 这句话我就没有看懂\n\n"
        "到底是不是跟其他竞品基于统一标准"
    )
    result = subprocess.run(
        [
            "python",
            "-m",
            "agent_brain.memory.context.query_signal",
            "--brain-dir",
            str(tmp_brain_dir),
            "--diagnose-json",
            prompt,
        ],
        capture_output=True,
        text=True,
        check=True,
    )

    payload = json.loads(result.stdout)
    assert payload["keywords"] == "hybrid_amh.yaml|method|preset|其他竞品|统一标准"
    assert payload["weak_noise"] == ["到底是不是跟其他竞品基于统一标准"]
    assert "cjk_question_focus" in payload["anchors"]


@pytest.mark.skipif(
    os.environ.get("LOCAL_BRAIN_QUERY_INTENT") != "1",
    reason="set LOCAL_BRAIN_QUERY_INTENT=1 to run read-only smoke against ~/.agent-memory-hub",
)
def test_local_brain_query_intent_smoke() -> None:
    brain_dir = Path.home() / ".agent-memory-hub"
    if not (brain_dir / "items").exists():
        pytest.skip(f"local brain items dir missing: {brain_dir / 'items'}")

    rich_prompt = "关于多智能体共享第二大脑的深度叙事和算法解释二次打磨，都做了什么"
    rich_signal = analyze_injection_query(rich_prompt, brain_dir=brain_dir)
    assert rich_signal.injectable
    assert extract_injection_keywords(rich_prompt, brain_dir=brain_dir)

    weak_signal = analyze_injection_query("继续", brain_dir=brain_dir)
    assert weak_signal.decision == "block"
    assert not weak_signal.injectable

    domain_prompt = "为什么陌生短句没有进入后处理"
    domain_signal = analyze_injection_query(domain_prompt, brain_dir=brain_dir)
    assert not domain_signal.injectable
    assert domain_signal.decision == "block"


@pytest.mark.skipif(
    os.environ.get("LOCAL_BRAIN_QUERY_INTENT") != "1",
    reason="set LOCAL_BRAIN_QUERY_INTENT=1 to replay hook against copied real memory items",
)
def test_local_brain_readme_deep_polish_hook_replay(tmp_path: Path) -> None:
    from agent_brain.platform.embedding import HashingEmbedder
    from agent_brain.platform.indexing.index import HubIndex

    source_items = Path.home() / ".agent-memory-hub" / "items"
    if not source_items.exists():
        pytest.skip(f"local brain items dir missing: {source_items}")
    matches = [
        *source_items.glob("mem-20260628-021045-*渐进叙事*.md"),
        *source_items.glob("mem-20260628-023227-*深度叙事*.md"),
    ]
    if len(matches) < 2:
        pytest.skip("local README polish memory items are not present")

    brain_dir = tmp_path / "brain"
    copied_items = brain_dir / "items"
    copied_items.mkdir(parents=True)
    for path in matches:
        shutil.copy2(path, copied_items / path.name)

    store = ItemsStore(copied_items)
    embedder = HashingEmbedder()
    idx = HubIndex(brain_dir / "index.db", embedding_dim=embedder.dim)
    for item, body in store.iter_all():
        idx.upsert(item, body, embedding=embedder.embed(f"{item.title}\n{item.summary}\n{body}"))
    idx.close()

    script = Path(__file__).resolve().parents[2] / "agent_runtime_kit" / "hooks" / "inject-context.sh"
    result = subprocess.run(
        ["bash", str(script)],
        input=json.dumps({
            "prompt": "关于多智能体共享第二单的深度叙事和算法解释二次打磨，都做了什么",
            "session_id": "local-readme-hook-replay",
            "cwd": "<repo>",
            "hook_event_name": "UserPromptSubmit",
        }),
        env={
            **os.environ,
            "BRAIN_DIR": str(brain_dir),
            "AGENT_MEMORY_HUB_ADAPTER": "codex",
            "MEMORY_HUB_TEST_EMBEDDING": "1",
            "MEMORY_HUB_EMBEDDING_OFFLINE": "1",
        },
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    context = payload["hookSpecificOutput"]["additionalContext"]
    assert "keywords: 深度叙事和算法解释二次打磨" in context
    assert "AMH README 深度叙事和算法解释二次打磨" in context

from __future__ import annotations

from datetime import datetime, timezone


def test_prompt_frame_classifies_structural_risk_without_prompt_enumeration(tmp_path) -> None:
    from agent_brain.contracts.memory_item import MemoryItem, MemoryType
    from agent_brain.memory.context.prompt_frame import analyze_prompt_frame
    from agent_brain.memory.store.items_store import ItemsStore

    store = ItemsStore(tmp_path / "items")
    store.write(
        MemoryItem(
            id="mem-20260703-010531-agent-memory-metrics",
            type=MemoryType.artifact,
            created_at=datetime.now(timezone.utc),
            title="AMH agent-memory metrics evaluation report",
            summary="Evaluate agent memory metrics and benchmark readiness.",
            tags=["agent", "memory", "metrics"],
        ),
        "agent memory metrics body",
    )

    control = analyze_prompt_frame("继续", brain_dir=tmp_path)
    assert control.intent_kind == "control"
    assert control.retrieval_mode == "block"
    assert control.injection_policy == "never"
    assert "weak_control" in control.risk_flags

    status = analyze_prompt_frame("17 passed, 2 skipped", brain_dir=tmp_path)
    assert status.intent_kind == "status_output"
    assert status.retrieval_mode == "block"
    assert status.injection_policy == "never"
    assert "status_only" in status.risk_flags

    singleton = analyze_prompt_frame("多Agent", brain_dir=tmp_path)
    assert singleton.intent_kind == "unknown"
    assert singleton.retrieval_mode == "block"
    assert singleton.injection_policy == "never"
    assert "generic_singleton" in singleton.risk_flags

    topic = analyze_prompt_frame("多Agent共享第二大脑", brain_dir=tmp_path)
    assert topic.intent_kind == "task_question"
    assert topic.retrieval_mode == "candidate_search"
    assert topic.injection_policy == "needs_answerability"
    assert topic.topic_anchors == ("多agent共享第二大脑",)
    assert "generic_singleton" not in topic.risk_flags


def test_prompt_frame_keeps_file_and_metadata_scope_separate(tmp_path) -> None:
    from agent_brain.memory.context.prompt_frame import analyze_prompt_frame

    frame = analyze_prompt_frame("优化一下 query_signal.py", brain_dir=tmp_path)

    assert frame.retrieval_mode == "candidate_search"
    assert frame.injection_policy == "needs_answerability"
    assert frame.scope_anchors == ("query_signal.py",)
    assert frame.topic_anchors == ()

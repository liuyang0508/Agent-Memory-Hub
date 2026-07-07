from __future__ import annotations


def test_summary_rewrite_prefers_chinese_list_boundary() -> None:
    from agent_brain.memory.governance.summary_rewrite import preview_summary_rewrite

    summary = "能力一、能力二、能力三、能力四、能力五、能力六、能力七"

    preview = preview_summary_rewrite(summary, target_length=18)

    assert preview.candidate_summary.endswith("、...")
    assert preview.candidate_length <= 18

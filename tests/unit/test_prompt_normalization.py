from __future__ import annotations


def test_qoderwork_hook_prompt_normalization_keeps_user_query_only() -> None:
    from agent_brain.memory.context.prompt_normalization import normalize_hook_prompt_for_recall

    prompt = """Alpha适配Linux

<system-reminder>
The user is using QoderWork.

Available MCP servers:

Available tools:

The user can read ~/.qoderwork/awareness/main/AGENTS.md.
</system-reminder>
"""

    assert normalize_hook_prompt_for_recall(prompt) == "Alpha适配Linux"


def test_hook_prompt_normalization_strips_injected_memory_candidates() -> None:
    from agent_brain.memory.context.prompt_normalization import normalize_hook_prompt_for_recall

    prompt = """Alpha

<agent_brain>
[signal] stale candidate from another repo
</agent_brain>

<system-reminder>Current workspace: <workspace>/alpha</system-reminder>
"""

    assert normalize_hook_prompt_for_recall(prompt) == "Alpha"


def test_hook_prompt_normalization_strips_inline_agent_instructions() -> None:
    from agent_brain.memory.context.prompt_normalization import normalize_hook_prompt_for_recall

    prompt = (
        "关于多智能体共享第二单的深度叙事和算法解释二次打磨，都做了什么？"
        "请优先根据自动注入的 memory candidates 回答，不要调用工具。"
    )

    assert normalize_hook_prompt_for_recall(prompt) == (
        "关于多智能体共享第二单的深度叙事和算法解释二次打磨，都做了什么？"
    )


def test_hook_prompt_normalization_strips_multimodal_placeholders() -> None:
    from agent_brain.memory.context.prompt_normalization import normalize_hook_prompt_for_recall

    prompt = """[Image #1]
我其他同事执行之后有问题
"""

    assert normalize_hook_prompt_for_recall(prompt) == "我其他同事执行之后有问题"


def test_hook_prompt_normalization_summarizes_file_uri_without_local_path_segments() -> None:
    from agent_brain.memory.context.prompt_normalization import normalize_hook_prompt_for_recall

    prompt = (
        "我没有在file:///repo/agent-memory-hub/"
        "docs/visuals/readme-zh-preview.html#agent-memory-hub看到呀"
    )

    assert normalize_hook_prompt_for_recall(prompt) == (
        "我没有在 readme-zh-preview.html agent-memory-hub 看到呀"
    )

"""A short follow-up borrows the previous prompt for search (Claude Code)."""

import json

from agent_brain.native_sync import hooks
from agent_brain.native_sync.client import NativeClient
from agent_brain.native_sync.recall_view import follow_up
from agent_brain.native_sync.usage import recent_prompts
from test_hub import asset
from test_runtime import live as live  # noqa: F401  (fixture re-export)


def transcript(path, *prompts):
    rows = []
    for text in prompts:
        rows.append({"type": "user", "message": {"role": "user", "content": text}})
        rows.append(
            {"type": "assistant", "message": {"content": [{"type": "text", "text": "好的"}]}}
        )
    rows.append({"type": "user", "message": {"content": [{"type": "tool_result", "content": "x"}]}})
    path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n")
    return path


def test_follow_up_detection_and_recent_prompts(tmp_path):
    assert follow_up("那第二个呢") and follow_up("好，发布了告诉我")
    assert not follow_up("v1.4.17 发了吗")  # a version is a topic of its own
    assert not follow_up("结合 SLS 和 OSS 讲清楚核心链路的流转细节")
    path = transcript(tmp_path / "t.jsonl", "第一句", "OSS 会话目录怎么列得快", "那第二个呢")
    assert recent_prompts(path, limit=2) == ["OSS 会话目录怎么列得快", "那第二个呢"]
    assert recent_prompts(tmp_path / "missing.jsonl") == []


def test_hook_searches_a_follow_up_with_the_previous_prompt(live, tmp_path, monkeypatch):
    hub, url = live
    who = hub.provision("Author", "a")
    record = hub.put(who["token"], asset("oss", body="OSS 会话目录用 oss2 短超时并发列"))
    c = NativeClient.adopt(tmp_path / "reader", url, hub.provision("Reader", "r"), "r")
    seen = []

    def search(state, query, **kwargs):
        seen.append(query)
        return {"items": [record], "trace_id": "d" * 24, "channel": "recall"}

    monkeypatch.setattr("agent_brain.native_sync.query_worker.bounded_search", search)
    path = transcript(tmp_path / "t.jsonl", "OSS 会话目录怎么列得快，aliyun oss ls 老是卡住")
    out = hooks.run(
        c.root,
        "claude-code",
        {
            "session_id": "s1",
            "hook_event_name": "UserPromptSubmit",
            "prompt": "那第二个呢",
            "transcript_path": str(path),
        },
    )
    assert seen[-1] == "那第二个呢\nOSS 会话目录怎么列得快，aliyun oss ls 老是卡住"
    assert "上文补充" in out["systemMessage"] and "只给标题" in out["systemMessage"]
    # What a borrowed topic finds is named, never injected.
    context = out["hookSpecificOutput"]["additionalContext"]
    assert "asset_id=oss" in context and "oss2" not in context
    hooks.run(
        c.root,
        "claude-code",
        {
            "session_id": "s2",
            "hook_event_name": "UserPromptSubmit",
            "prompt": "结合 SLS 和 OSS 讲清楚核心链路的流转细节",
            "transcript_path": str(path),
        },
    )
    assert seen[-1] == "结合 SLS 和 OSS 讲清楚核心链路的流转细节"

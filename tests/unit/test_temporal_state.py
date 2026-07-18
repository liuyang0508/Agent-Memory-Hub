from __future__ import annotations

from datetime import datetime, timedelta, timezone

from agent_brain.contracts.memory_item import MemoryItem


NOW = datetime(2026, 6, 12, 1, 0, tzinfo=timezone.utc)


def _item(
    type_: str,
    title: str,
    summary: str,
    *,
    days_ago: int = 0,
    tags: list[str] | None = None,
    validity: dict | None = None,
) -> MemoryItem:
    return MemoryItem.model_validate({
        "id": f"mem-20260612-010000-{title.lower().replace(' ', '-')[:24]}",
        "type": type_,
        "created_at": (NOW - timedelta(days=days_ago)).isoformat(),
        "title": title,
        "summary": summary,
        "tags": tags or [],
        "validity": validity or {},
    })


def test_old_negative_state_requires_revalidation() -> None:
    from agent_brain.memory.governance.temporal_state import TemporalStateGate

    item = _item(
        "signal",
        "Ghostty Operation not permitted",
        "历史上 Ghostty 无权限读取 Desktop，浏览器受限",
        days_ago=5,
        tags=["permission", "runtime"],
    )

    signal = TemporalStateGate(now=NOW).evaluate(item, "")

    assert signal.status == "stale"
    assert signal.category == "negative_state"
    assert "negative_state_ttl_expired" in signal.reasons


def test_recent_negative_state_is_current() -> None:
    from agent_brain.memory.governance.temporal_state import TemporalStateGate

    item = _item(
        "signal",
        "Ghostty Operation not permitted",
        "刚观察到权限失败",
        days_ago=0,
        tags=["permission"],
    )

    signal = TemporalStateGate(now=NOW).evaluate(item, "")

    assert signal.category == "negative_state"
    assert signal.status == "current"


def test_old_positive_state_requires_revalidation() -> None:
    from agent_brain.memory.governance.temporal_state import TemporalStateGate

    item = _item(
        "episode",
        "Browser fixed",
        "浏览器权限已经修复并且可用",
        days_ago=4,
        tags=["browser", "runtime"],
    )

    signal = TemporalStateGate(now=NOW).evaluate(item, "")

    assert signal.status == "stale"
    assert signal.category == "positive_state"
    assert "positive_state_ttl_expired" in signal.reasons


def test_stable_decision_is_not_temporal_state() -> None:
    from agent_brain.memory.governance.temporal_state import TemporalStateGate

    item = _item(
        "decision",
        "Use SSE",
        "选择 SSE 而不是 WebSocket",
        days_ago=90,
        tags=["architecture"],
    )

    signal = TemporalStateGate(now=NOW).evaluate(item, "")

    assert signal.category == "stable"
    assert signal.status == "current"
    assert signal.reasons == ()


def test_restoring_saved_decision_for_current_task_is_stable() -> None:
    from agent_brain.memory.governance.temporal_state import TemporalStateGate

    item = _item(
        "decision",
        "已保存决策恢复",
        "重新找回早先确认的技术决定和实施路线",
        days_ago=90,
        tags=["决策", "技术路线"],
    )

    signal = TemporalStateGate(now=NOW).evaluate(
        item,
        "召回层应把相关的历史决策提供给当前任务。",
    )

    assert signal.category == "stable"
    assert signal.status == "current"
    assert signal.reasons == ()


def test_browser_proxy_timeout_fact_is_stable_without_runtime_state_marker() -> None:
    from agent_brain.memory.governance.temporal_state import TemporalStateGate

    item = _item(
        "fact",
        "Browser proxy deadline",
        "The browser integration proxy has a thirty second request timeout",
        days_ago=90,
        tags=["browser", "proxy", "timeout"],
    )

    signal = TemporalStateGate(now=NOW).evaluate(
        item,
        "Requests through the browser bridge stop after 30 seconds.",
    )

    assert signal.category == "stable"
    assert signal.status == "current"
    assert signal.reasons == ()


def test_artifact_guide_with_install_and_test_terms_is_stable() -> None:
    from agent_brain.memory.governance.temporal_state import TemporalStateGate

    item = _item(
        "artifact",
        "Wukong Linux adaptation guide",
        "生成留档指南，覆盖 Linux 安装、回归测试、已修复能力和可用排障命令",
        days_ago=0,
        tags=["wukong", "linux", "AppImage"],
        validity={"os": "darwin", "adapter": "codex"},
    )

    signal = TemporalStateGate(now=NOW).evaluate(
        item,
        "正文包含 install.sh、pytest passed、fixed、available 等词，但这是文档产物，不是运行时状态。",
        current_scope={"os": "linux", "adapter": "qoder_work"},
    )

    assert signal.category == "stable"
    assert signal.status == "current"
    assert signal.reasons == ()


def test_stable_error_code_fact_is_not_temporal_state_without_state_anchor() -> None:
    from agent_brain.memory.governance.temporal_state import TemporalStateGate

    item = _item(
        "fact",
        "HTTP error handling",
        "HTTP 424 error code maps to quota handling policy",
        days_ago=30,
        tags=["api"],
    )

    signal = TemporalStateGate(now=NOW).evaluate(item, "")

    assert signal.category == "stable"
    assert signal.status == "current"


def test_generic_test_tag_alone_is_not_temporal_state() -> None:
    from agent_brain.memory.governance.temporal_state import TemporalStateGate

    item = _item(
        "fact",
        "Python coding item",
        "General coding fixture",
        days_ago=30,
        tags=["test"],
    )

    signal = TemporalStateGate(now=NOW).evaluate(item, "python coding")

    assert signal.category == "stable"
    assert signal.status == "current"


def test_state_scope_mismatch_requires_revalidation() -> None:
    from agent_brain.memory.governance.temporal_state import TemporalStateGate

    item = _item(
        "signal",
        "Browser currently limited",
        "浏览器在另一个仓库里不可用",
        days_ago=0,
        tags=["browser", "runtime"],
        validity={"cwd": "/repo/other", "adapter": "codex"},
    )

    signal = TemporalStateGate(now=NOW).evaluate(
        item,
        "",
        current_scope={"cwd": "/repo/current", "adapter": "codex"},
    )

    assert signal.category == "negative_state"
    assert signal.status == "scope_mismatch"
    assert "scope_mismatch:cwd" in signal.reasons


def test_stable_fact_scope_mismatch_is_not_temporal_mismatch() -> None:
    from agent_brain.memory.governance.temporal_state import TemporalStateGate

    item = _item(
        "fact",
        "HTTP 424 meaning",
        "HTTP 424 maps to quota policy",
        days_ago=30,
        tags=["api"],
        validity={"cwd": "/repo/other"},
    )

    signal = TemporalStateGate(now=NOW).evaluate(
        item,
        "",
        current_scope={"cwd": "/repo/current"},
    )

    assert signal.category == "stable"
    assert signal.status == "current"

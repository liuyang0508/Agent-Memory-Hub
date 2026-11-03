"""Real native index + detail layouts must deliver facts before navigation under budget."""

from uuid import uuid4

from agent_brain.native_sync.hub import Hub
from agent_brain.native_sync.recall import pack, coverage_key
from test_hub import enroll, asset


def records(tmp_path):
    hub = Hub(tmp_path / "hub.sqlite")
    member = enroll(hub, name="验收生产成员-CC")
    project = "星桥灰度验收-85b882"
    session_id = str(uuid4())
    index = hub.put(
        member["token"],
        asset(
            "asset_" + "1" * 32 + "__p000000",
            product="claude-code",
            project=project,
            path="MEMORY.md",
            body=f"- [Release probe](release_probe.md) — {project} 的灰度回滚核验口令\n",
        ),
    )
    detail = hub.put(
        member["token"],
        asset(
            "asset_" + "2" * 32 + "__p000000",
            product="claude-code",
            project=project,
            path="release_probe.md",
            body=(
                f"---\nname: release-probe\ndescription: 项目「{project}」的灰度回滚核验口令\n"
                "metadata:\n  node_type: memory\n  type: project\n"
                f"  originSessionId: {session_id}\n"
                "  modified: 2026-01-01T00:00:00.000Z\n---\n\n"
                f"项目「{project}」的灰度回滚核验口令是 NATIVE_NAVIGATION_CANARY。\n"
            ),
        ),
    )
    for record in (index, detail):
        record["local_detail_path"] = (
            "/tmp/native-tests/profiles/synthetic-runtime/desktop-state/recall-cache/"
            + "a" * 64
            + ".md"
        )
    return index, detail


def test_actual_claude_index_does_not_displace_answer_in_detail(tmp_path):
    index, detail = records(tmp_path)
    body, delivered = pack(
        [index, detail],
        covered=set(),
        max_bytes=1450,
        query="星桥灰度验收-85b882 灰度回滚核验口令是什么",
    )
    assert "NATIVE_NAVIGATION_CANARY" in body
    assert "验收生产成员-CC" in body
    assert coverage_key(detail) in delivered
    assert len(body.encode()) <= 1450


def test_navigation_links_cannot_reorder_another_members_source(tmp_path):
    index, detail = records(tmp_path)
    detail["source_device_id"] = "another-device"
    body, delivered = pack([index, detail], covered=set(), max_items=1)
    assert coverage_key(index) in delivered
    assert "NATIVE_NAVIGATION_CANARY" not in body


def test_fact_bearing_memory_file_is_not_treated_as_navigation_only(tmp_path):
    index, detail = records(tmp_path)
    index["body"] = "Important fact lives here.\n" + index["body"]
    body, delivered = pack([index, detail], covered=set(), max_items=1)
    assert "Important fact lives here." in body
    assert coverage_key(index) in delivered


def test_navigation_cycle_remains_bounded_and_does_not_duplicate(tmp_path):
    index, detail = records(tmp_path)
    index["body"] = "- [Next](sub/MEMORY.md)\n"
    detail["path"] = "sub/MEMORY.md"
    detail["body"] = "- [Back](../MEMORY.md)\n"
    body, delivered = pack([index, detail], covered=set(), max_bytes=4000)
    assert len(delivered) == len(set(delivered)) == 2


def test_link_order_does_not_override_retrieval_rank(tmp_path):
    index, detail = records(tmp_path)
    weaker = {
        **detail,
        "asset_id": "weak",
        "path": "weak.md",
        "body": "Less relevant detail.",
        "body_hash": "weak-hash",
    }
    index["body"] = "- [Weak](weak.md)\n- [Relevant](release_probe.md)\n"
    body, delivered = pack([index, detail, weaker], covered=set(), max_items=1)
    assert "NATIVE_NAVIGATION_CANARY" in body
    assert coverage_key(detail) in delivered

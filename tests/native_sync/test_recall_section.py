"""1.4.22: a long memory reaches the prompt as the section the prompt matched."""

from fastapi.testclient import TestClient

from agent_brain.native_sync.api import create_app
from agent_brain.native_sync.hub import Hub
from agent_brain.native_sync.recall import coverage_key, pack
from agent_brain.native_sync.recall_eval import relevant
from agent_brain.native_sync.relevance import section_span
from test_hub import asset
from test_learned_gate import TopicBackend, sim_only

APPROVAL = "# 审批\n" + "审批需要两位评审和一份变更清单。\n" * 60
RELEASE = "# 发布\n" + "发布列车每周四出发，错过就等下周。\n" * 60
AGGREGATE = APPROVAL + "\n" + RELEASE


class WindowBackend(TopicBackend):
    """Windows cut at fixed offsets, so a topic's window is known."""

    window_tokens = 200
    stride_tokens = 200

    def split_windows(self, text):
        return [
            {"text": text[i : i + 200], "start": i, "end": min(len(text), i + 200)}
            for i in range(0, len(text), 200)
        ]


def hub_with(tmp_path, *assets):
    hub = Hub(tmp_path / "hub.sqlite", semantic_backend=WindowBackend(), hook_gate=sim_only(0.5))
    token = hub.provision("one", "device")["token"]
    for item in assets:
        hub.put(token, item)
    hub.rebuild_semantic(token, kinds=["memory", "rule"])
    return hub, token


def test_section_stops_at_headings_and_keeps_the_window_when_capped():
    text = "# 甲\n" + "甲" * 100 + "\n# 乙\n" + "乙" * 100 + "\n## 丙\n" + "丙" * 100
    start = text.index("乙" * 5)
    low, high = section_span(text, start, start + 10)
    assert (
        text[low:high].startswith("# 乙")
        and "甲" not in text[low:high]
        and "丙" not in text[low:high]
    )
    plain = "字" * 5000
    low, high = section_span(plain, 3000, 3200, limit=600)
    assert high - low == 600 and low <= 3000 and high >= 3200
    # A window running past the cap still starts inside the text.
    assert section_span(plain, 0, 2000, limit=600) == (0, 600)


def test_recall_returns_the_matched_section_and_keeps_the_source_version(tmp_path):
    hub, token = hub_with(tmp_path, asset("big", title="聚合笔记", body=AGGREGATE))
    item = next(i for i in hub.recall(token, "发布")["items"] if i["asset_id"] == "big")
    assert "发布列车" in item["body"] and "审批需要" not in item["body"]
    assert item["body"].startswith("《发布》\n")
    assert item["section"]["chars"] == len(AGGREGATE)
    # The full source stays addressable: search and read are untouched.
    full = hub.retrieve(token, "发布", kinds=["memory"], channel="tool")["items"][0]
    assert "审批需要" in full["body"] and "section" not in full
    assert item["body_hash"] == full["body_hash"]


def test_short_memory_is_passed_through_whole(tmp_path):
    body = "发布列车每周四出发。"
    hub, token = hub_with(tmp_path, asset("small", title="发布", body=body))
    item = hub.recall(token, "发布")["items"][0]
    assert item["body"] == body and "section" not in item


def test_api_carries_the_section_marker(tmp_path):
    hub, token = hub_with(tmp_path, asset("big", title="聚合笔记", body=AGGREGATE))
    client = TestClient(create_app(hub))
    response = client.post(
        "/native/v1/recall",
        json={"query": "发布"},
        headers={"authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    item = next(i for i in response.json()["items"] if i["asset_id"] == "big")
    assert item["section"]["chars"] == len(AGGREGATE) and "审批需要" not in item["body"]


def record(body, section=None):
    row = {
        "asset_id": "big",
        "revision": 1,
        "body_hash": "h",
        "project": "",
        "kind": "memory",
        "product": "codex",
        "producer_name": "同事",
        "producer_member_id": "m",
        "body": body,
    }
    return {**row, "section": section} if section else row


def test_section_never_marks_the_whole_version_as_read():
    section = {"char_start": 0, "char_end": 9, "chars": 9000}
    _, first = pack([record("第一节内容", section)], covered=set())
    assert first and all(key != coverage_key(record("x")) for key in first)
    # Another section of the same memory can still arrive later in the session.
    text, second = pack([record("第二节内容", section)], covered=set(first))
    assert "第二节内容" in text and second != first
    # The same section is not repeated.
    assert pack([record("第一节内容", section)], covered=set(first)) == ("", [])
    # A memory delivered whole still counts as read.
    assert pack([record("完整内容")], covered=set())[1] == [coverage_key(record("x"))]


def test_gold_can_name_an_asset_directly():
    assert relevant({"asset_id": "asset_abc__p000003"}, ["asset_abc__p000003"])
    assert not relevant({"asset_id": "asset_abc__p000003"}, ["asset_abc__p000002"])


def test_capped_section_opens_on_a_whole_line():
    text = "第一行内容\n" * 400 + "命中这里的内容\n" + "后面的内容\n" * 400
    start = text.index("命中")
    low, high = section_span(text, start, start + 7, limit=300)
    assert low == 0 or text[low - 1] == "\n"
    assert low <= start and high >= start + 7 and high - low <= 300
    # A long line with nowhere near to break keeps the window in view.
    plain = "字" * 5000
    low, _ = section_span(plain, 3000, 3200, limit=600)
    assert 2800 <= low <= 3000


def test_pack_keeps_a_sections_title_through_the_cut():
    body = (
        "《聚合笔记》\n"
        + "无关的前文内容。\n" * 200
        + "发布列车每周四出发。\n"
        + "无关的后文内容。\n" * 200
    )
    section = {"char_start": 0, "char_end": len(body), "chars": 90000}
    text, keys = pack([record(body, section)], covered=set(), query="发布列车", per_item_bytes=400)
    assert "《聚合笔记》" in text and "发布列车" in text
    assert keys and ":excerpt:" in keys[0]
    # The anchor's line is not cut at its front.
    assert "\n发布列车每周四出发" in text or "无关的前文内容。\n" in text

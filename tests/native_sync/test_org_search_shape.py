"""1.4.28: a search lists enough short candidates, and a cited file can be opened."""

import hashlib
import json

import pytest
from fastapi.testclient import TestClient

from agent_brain.native_sync.api import create_app
from agent_brain.native_sync.hub import Hub
from agent_brain.native_sync.org_tools import (
    SEARCH_EXCERPT_CHARS,
    OrganizationTools,
    _cited_path,
    _focused,
)
from agent_brain.native_sync.retrieval_view import bounded_retrieval
from test_hub import asset
from test_org_mcp import hub  # noqa: F401  (fixture re-export)


def test_a_short_excerpt_opens_where_the_query_words_are():
    text = (
        "前言部分没有关系。\n" * 40
        + "升级要用 upgrade.sh，不要用 deploy.sh。\n"
        + "结尾也没有关系。\n" * 40
    )
    start, focused = _focused(text, "deploy.sh 升级")
    assert len(focused) <= SEARCH_EXCERPT_CHARS
    assert focused.startswith("升级要用 upgrade.sh")
    assert text[start : start + len(focused)] == focused


def test_a_short_excerpt_is_left_alone():
    assert _focused("很短", "任何") == (0, "很短")


def test_a_cited_path_is_recognized_in_or_out_of_a_sentence():
    path = "rollout_summaries/2026-10-24T05-36-42-wmwE-order_callback.md"
    assert _cited_path(path) == path
    assert _cited_path(f"打开 `{path}` 看根因") == path
    assert _cited_path("读一下 MEMORY.md") is None
    assert _cited_path("订单验签 根因") is None


def test_eight_candidates_reach_the_model_within_the_response_budget(hub):  # noqa: F811
    candidate = hub.candidate()
    long = "组织证据" * 100
    items = []
    for n in range(8):
        row = dict(
            candidate,
            asset_id=f"asset_{n}",
            document_id=f"document-{n}",
            excerpt=long,
            excerpt_truncated=True,
        )
        row.update(excerpt_char_start=0, excerpt_char_end=len(long))
        items.append(row)
    hub.override = {"items": items}
    result = OrganizationTools(hub.state).search("组织证据")
    assert result["ok"], result
    assert len(result["items"]) == 8 and result["omitted_items"] == 0
    assert len(json.dumps(result, ensure_ascii=False).encode()) <= 12000
    row = result["items"][0]
    assert len(row["excerpt"]) <= SEARCH_EXCERPT_CHARS and row["excerpt_truncated"]
    assert row["excerpt_char_end"] - row["excerpt_char_start"] == len(row["excerpt"])
    assert not {"tenant_id", "source_device_id", "uploader_member_id"} & row["source"].keys()
    assert "document_id" not in row, "the model read this id in place of asset_id"


def test_one_document_cannot_fill_the_list(hub):  # noqa: F811
    base = hub.candidate()
    handbook = [dict(base, asset_id=f"asset_h{n}", document_id="handbook") for n in range(6)]
    other = [dict(base, asset_id=f"asset_o{n}", document_id=f"other{n}") for n in range(3)]
    hub.override = {"items": handbook + other}
    rows = OrganizationTools(hub.state).search("question")["items"]
    assert [row["asset_id"] for row in rows] == [
        "asset_h0",
        "asset_h1",
        "asset_o0",
        "asset_o1",
        "asset_o2",
    ]


def test_oversized_limits_are_clamped_rather_than_refused(hub):  # noqa: F811
    tools = OrganizationTools(hub.state)
    found = tools.search("question", limit=50)
    assert found["ok"], found
    page = tools.read("asset_a", 2, found["research_id"], max_bytes=60000)
    assert page["ok"], page
    assert hub.calls[-1][1]["max_bytes"] == 12000


def test_a_read_still_verifies_the_full_source_after_a_slim_row(hub):  # noqa: F811
    tools = OrganizationTools(hub.state)
    found = tools.search("question")
    assert "tenant_id" not in found["items"][0]["source"]
    page = tools.read("asset_a", 2, found["research_id"], max_bytes=100)
    assert page["ok"], page


def test_search_asks_for_evidence_and_a_wide_response_by_default(hub):  # noqa: F811
    OrganizationTools(hub.state).search("question")
    sent = hub.calls[-1][1]
    assert sent["include_evidence"] is True and sent["wide"] is True and sent["limit"] == 20


def test_an_older_hub_rejecting_the_wide_field_still_answers(hub):  # noqa: F811
    real = hub.response

    def older(path, payload):
        if path == "retrieve" and "wide" in payload:
            return 422, {"detail": "extra field"}
        return real(path, payload)

    hub.response = older
    tools = OrganizationTools(hub.state)
    first = tools.search("question")
    assert first["ok"], first
    assert "asking" in hub.calls[-1][1] and "wide" not in hub.calls[-1][1]
    tools.search("another question")
    assert "wide" not in hub.calls[-1][1]


def test_a_read_finds_an_id_named_outside_this_research(hub):  # noqa: F811
    tools = OrganizationTools(hub.state)
    candidate = hub.candidate()
    other = dict(candidate, asset_id="asset_b")
    hub.override = {"items": [other]}
    rid = tools.search("question")["research_id"]
    hub.override = None
    result = tools.read("asset_a", 2, rid, max_bytes=100)
    assert result["ok"], result
    assert hub.calls[-2][1]["query"] == "asset_a"


def test_a_page_names_the_files_it_points_to(hub):  # noqa: F811
    hub.body = (
        "摘要。详情见 rollout_summaries/2026-10-24-a.md 和 rollout_summaries/2026-10-25-b.md。"
        "\n再次提到 rollout_summaries/2026-10-24-a.md；本地路径 /tmp/x/.codex/c/d.md 不算。"
    )
    tools = OrganizationTools(hub.state)
    rid = tools.search("问题")["research_id"]
    page = tools.read("asset_a", 2, rid)
    assert page["ok"], page
    assert page["cited_files"] == [
        "rollout_summaries/2026-10-24-a.md",
        "rollout_summaries/2026-10-25-b.md",
    ]
    assert "search_org_memory" in page["cited_files_note"]


def test_a_page_naming_nothing_adds_nothing(hub):  # noqa: F811
    tools = OrganizationTools(hub.state)
    rid = tools.search("问题")["research_id"]
    page = tools.read("asset_a", 2, rid)
    assert page["ok"] and "cited_files" not in page


def test_a_malformed_id_does_not_reach_the_hub_as_a_search(hub):  # noqa: F811
    tools = OrganizationTools(hub.state)
    rid = tools.search("question")["research_id"]
    before = len(hub.calls)
    assert tools.read("unknown", 2, rid)["code"] == "unknown_locator"
    assert len(hub.calls) == before


@pytest.fixture
def store(tmp_path):
    hub_ = Hub(tmp_path / "hub.sqlite")
    who = hub_.provision("Author", "Source", tenant="org")
    return hub_, who


def stored(hub_, who, parts, path):
    """Put a document the way a client does: every part names the whole document's hash."""
    document = parts[0][0].split("__")[0]
    whole = hashlib.sha256("".join(body for _, body in parts).encode()).hexdigest()
    for index, (asset_id, body) in enumerate(parts):
        hub_.put(
            who["token"],
            asset(
                asset_id,
                body,
                path=path,
                product="codex",
                document_id=document,
                document_hash=whole,
                part_index=index,
                part_count=len(parts),
            ),
        )


PATH = "rollout_summaries/2026-10-24T05-36-42-wmwE-order.md"


def test_the_hub_opens_a_cited_file_at_its_first_part(store):
    hub_, who = store
    stored(hub_, who, [("asset_x__p000000", f"根因见 {PATH}，这里只是指针。")], "MEMORY.md")
    stored(
        hub_,
        who,
        [("asset_y__p000000", "根因是签名密钥版本不一致"), ("asset_y__p000001", "后半部分")],
        PATH,
    )
    result = hub_.retrieve(who["token"], PATH, include_evidence=True, kinds=["memory"])
    assert result["items"][0]["asset_id"] == "asset_y__p000000"


def test_a_path_does_not_open_a_summary_when_evidence_was_not_asked_for(store):
    hub_, who = store
    stored(hub_, who, [("asset_y__p000000", "根因内容")], PATH)
    result = hub_.retrieve(who["token"], PATH, include_evidence=False, kinds=["memory"])
    assert "asset_y__p000000" not in [item["asset_id"] for item in result["items"]]


def test_an_older_client_keeps_its_response_limit():
    long = "证" * 400
    items = [
        {
            "asset_id": f"asset_{n}",
            "revision": 1,
            "body": long,
            "body_hash": "h",
            "kind": "memory",
            "product": "codex",
            "path": f"p{n}.md",
            "project": "",
            "title": "t",
            "producer_name": "A",
            "tenant_id": "org",
            "source_device_id": "d",
            "source_store": "s",
            "observed_at": 1.0,
        }
        for n in range(20)
    ]
    result = {"items": items, "retrieval": {"mode": "lexical"}}
    narrow = bounded_retrieval(result)
    wide = bounded_retrieval(result, wide=True)
    assert len(json.dumps(narrow, ensure_ascii=False).encode()) <= 12000
    assert len(json.dumps(wide, ensure_ascii=False).encode()) <= 48000
    assert len(wide["items"]) > len(narrow["items"])


def test_the_api_takes_wide_without_passing_it_to_the_search(store):
    hub_, who = store
    stored(hub_, who, [("asset_y__p000000", "回调验签失败")], "topic.md")
    client = TestClient(create_app(hub_))
    response = client.post(
        "/native/v1/retrieve",
        json={"query": "回调验签", "wide": True},
        headers={"Authorization": "Bearer " + who["token"]},
    )
    assert response.status_code == 200, response.text
    assert response.json()["items"]

"""Synthetic HTTP + real stdio MCP protocol; no enrolled user accounts or native scans."""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from agent_brain.native_sync import org_tools
from agent_brain.native_sync.org_tools import OrganizationTools
from agent_brain.native_sync.transport import HubHTTPError, Transport

TOKEN = "synthetic-isolated-device-credential-DO-NOT-RETURN"


class SyntheticHub:
    def __init__(self):
        self.body = "organization evidence " * 1000
        self.calls = []
        self.error = None
        self.override = None

    @property
    def version(self):
        return {
            "document_id": "document-a",
            "document_hash": hashlib.sha256(self.body.encode()).hexdigest(),
            "anchor_revision": 2,
        }

    def node(self, revision=2):
        return {
            "asset_id": "asset_a",
            "revision": revision,
            "source": {
                "producer_name": "synthetic-member",
                "kind": "memory",
                "path": "topic.md",
                "role": "topic_memory",
            },
            "version": {**self.version, "anchor_revision": revision},
            "current": revision == 2,
            "readable": revision == 2,
        }

    def candidate(self):
        return {
            "asset_id": "asset_a",
            "revision": 2,
            **{k: v for k, v in self.version.items() if k != "anchor_revision"},
            "role": "topic_memory",
            "source": self.node()["source"],
            "excerpt": self.body[:100],
            "excerpt_truncated": True,
            "excerpt_source_field": "body",
            "excerpt_char_start": 0,
            "excerpt_char_end": 100,
            "version_verified": True,
            "answer_status": "candidates_only",
            "private_server_state": "never forward unknown fields",
        }

    def response(self, path, payload):
        if self.error:
            return self.error
        if self.override is not None:
            return 200, self.override
        if path == "retrieve":
            return 200, {
                "items": [self.candidate()],
                "retrieval": {"mode": "hybrid", "degraded": False},
                "truncated": False,
                "omitted_items": 0,
            }
        if path == "read":
            raw = self.body.encode()
            cursor, size = payload["cursor"], payload["max_bytes"]
            end = min(len(raw), cursor + size)
            while end < len(raw) and raw[end] & 0xC0 == 0x80:
                end -= 1
            return 200, {
                "asset_id": "asset_a",
                "revision": 2,
                "text": raw[cursor:end].decode(),
                "source": self.node()["source"],
                "version": self.version,
                "cursor": cursor,
                "next_cursor": None if end == len(raw) else end,
                "total_bytes": len(raw),
                "complete": end == len(raw),
                "truncated": end != len(raw),
            }
        if path == "related":
            return 200, {
                "root": self.node(),
                "items": [self.node(1)],
                "edges": [
                    {
                        "from": {"asset_id": "asset_a", "revision": 2},
                        "to": {"asset_id": "asset_a", "revision": 1},
                        "type": "previous_version",
                        "evidence": {
                            "asset_id": "asset_a",
                            "revision": 2,
                            "field": "changes",
                            "previous_revision": 1,
                        },
                    }
                ],
                "truncated": False,
            }
        return 404, {"detail": "unexpected endpoint"}


@pytest.fixture
def hub(tmp_path):
    fixture = SyntheticHub()

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            fixture.calls.append((self.path, payload, self.headers.get("Authorization")))
            status, value = fixture.response(self.path.rsplit("/", 1)[-1], payload)
            body = json.dumps(value).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    fixture.url = f"http://127.0.0.1:{server.server_port}"
    fixture.state = tmp_path / "profile"
    fixture.state.mkdir()
    fixture.config = {
        "hub": fixture.url,
        "identity": {"tenant_id": "org", "member_id": "member", "token": TOKEN},
    }
    (fixture.state / "config.json").write_text(json.dumps(fixture.config))
    yield fixture
    server.shutdown()
    server.server_close()
    thread.join()


def test_real_stdio_initialize_list_search_read_related_and_unpaired(tmp_path, hub):
    async def scenario():
        state = tmp_path / "not-yet-paired"
        params = StdioServerParameters(
            command=sys.executable,
            args=["-m", "agent_brain.native_sync.org_mcp", "--state", str(state)],
            env={"PYTHONPATH": str(Path(__file__).resolve().parents[2])},
        )
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                hello = await session.initialize()
                assert "untrusted" in hello.instructions
                tools = (await session.list_tools()).tools
                assert {t.name for t in tools} == {
                    "search_org_memory",
                    "read_org_memory",
                    "related_org_memory",
                    "memory_feedback",
                }
                research = [t for t in tools if t.name != "memory_feedback"]
                assert all(t.annotations.readOnlyHint for t in research)
                assert all(
                    not {"tenant", "tenant_id", "hub", "token"} & t.inputSchema["properties"].keys()
                    for t in tools
                )
                unpaired = await session.call_tool("search_org_memory", {"query": "evidence"})
                assert unpaired.structuredContent["code"] == "not_connected"
                assert not state.exists()
                state.mkdir()
                (state / "config.json").write_text(json.dumps(hub.config))
                result = await session.call_tool("search_org_memory", {"query": "evidence"})
                searched = result.structuredContent
                assert searched["ok"] and searched["answer_status"] == "candidates_only"
                rid = searched["research_id"]
                page = (
                    await session.call_tool(
                        "read_org_memory",
                        {
                            "asset_id": "asset_a",
                            "revision": 2,
                            "research_id": rid,
                            "max_bytes": 100,
                        },
                    )
                ).structuredContent
                assert page["text"] == hub.body[:100] and page["next_cursor"] == 100
                related = (
                    await session.call_tool(
                        "related_org_memory",
                        {"asset_id": "asset_a", "revision": 2, "research_id": rid},
                    )
                ).structuredContent
                assert related["items"][0]["readable"] is False
                assert TOKEN not in json.dumps([searched, page, related])
                assert set(p.name for p in state.iterdir()) == {"config.json"}
        assert all(call[2] == "Bearer " + TOKEN for call in hub.calls)

    asyncio.run(scenario())


def test_empty_policy_works_but_mismatch_and_pause_never_send_requests(hub, tmp_path):
    policy = tmp_path / "public-policy.json"
    policy.write_text("{}")
    tools = OrganizationTools(hub.state, policy)
    assert tools.search("question")["ok"]
    before = len(hub.calls)
    policy.write_text(json.dumps({"hub": hub.url, "member_id": "wrong-member", "tenant_id": "org"}))
    assert tools.search("other question")["code"] == "policy_mismatch"
    (hub.state / "paused").touch()
    assert tools.search("question")["code"] == "paused"
    assert len(hub.calls) == before


def test_three_searches_duplicate_and_ttl_have_no_extra_network(hub, monkeypatch):
    tools = OrganizationTools(hub.state)
    first = tools.search("one")
    rid = first["research_id"]
    assert tools.search("one", research_id=rid)["code"] == "duplicate_request"
    assert tools.search("two", research_id=rid)["ok"]
    assert tools.search("three", research_id=rid)["ok"]
    assert tools.search("four", research_id=rid)["code"] == "budget_exhausted"
    assert len(hub.calls) == 3
    tools.researches[rid].created -= 601
    assert tools.read("asset_a", 2, rid)["code"] == "research_expired"
    assert len(hub.calls) == 3


def test_byte_budget_tightens_read_page_and_never_stores_bodies(hub):
    hub.body = "A" * 100000
    tools = OrganizationTools(hub.state)
    searched = tools.search("one")
    rid = searched["research_id"]
    cursor = 0
    pages = []
    for _ in range(3):
        result = tools.read("asset_a", 2, rid, cursor=cursor)
        assert result["ok"], result
        pages.append(result)
        cursor = result["next_cursor"]
    assert (
        sum(len(page["text"].encode()) for page in pages)
        + len(searched["items"][0]["excerpt"].encode())
        == 32000
    )
    assert hub.calls[-1][1]["max_bytes"] == 7900
    assert tools.read("asset_a", 2, rid, cursor=cursor)["code"] == "budget_exhausted"
    state = repr(tools.researches)
    assert "A" * 100 not in state and TOKEN not in state
    assert set(p.name for p in hub.state.iterdir()) == {"config.json"}


def test_twelve_call_limit_and_duplicate_page(hub):
    tools = OrganizationTools(hub.state)
    rid = tools.search("question")["research_id"]
    assert tools.read("asset_a", 2, rid, max_bytes=100)["ok"]
    assert tools.read("asset_a", 2, rid, max_bytes=200)["code"] == "duplicate_request"
    for _ in range(9):
        tools.read("unknown", 2, rid)
    assert tools.read("asset_a", 2, rid, cursor=100)["code"] == "budget_exhausted"
    assert len(hub.calls) == 2


def test_stale_version_safe_error_and_revocation_close_research(hub):
    tools = OrganizationTools(hub.state)
    rid = tools.search("question")["research_id"]
    hub.error = (409, {"code": "stale_version", "detail": TOKEN + " SQL credentials"})
    stale = tools.read("asset_a", 2, rid)
    assert stale["code"] == "stale_version" and stale["http_status"] == 409
    assert "Search again" in stale["message"] and TOKEN not in json.dumps(stale)
    hub.error = (401, {"detail": TOKEN})
    denied = tools.related("asset_a", 2, rid)
    assert denied["code"] == "access_rejected"
    calls = len(hub.calls)
    hub.error = None
    assert tools.search("another", research_id=rid)["code"] == "access_rejected"
    assert len(hub.calls) == calls


def test_transport_structured_valueerror_never_returns_arbitrary_server_text(hub):
    hub.error = (409, {"code": "stale_version", "detail": TOKEN})
    with pytest.raises(ValueError) as caught:
        Transport(hub.url, TOKEN).call("read", {})
    assert isinstance(caught.value, HubHTTPError)
    assert caught.value.http_status == 409 and caught.value.code == "stale_version"
    assert TOKEN not in str(caught.value)
    hub.error = (422, {"code": TOKEN, "detail": TOKEN})
    with pytest.raises(HubHTTPError) as caught:
        Transport(hub.url, TOKEN).call("read", {})
    assert caught.value.code == "request_rejected"
    assert TOKEN not in str(caught.value)


def test_invalid_server_envelope_and_credential_echo_are_rejected(hub):
    tools = OrganizationTools(hub.state)
    hub.override = {"ok": False, "code": TOKEN, "detail": TOKEN}
    assert tools.search("bad-envelope")["code"] == "invalid_response"
    row = hub.candidate()
    row["excerpt"] = TOKEN
    row["excerpt_char_end"] = len(TOKEN)
    hub.override = {"items": [row]}
    echoed = tools.search("token-echo")
    assert echoed["code"] == "invalid_response"
    assert TOKEN not in json.dumps(echoed)


def test_locator_auth_version_mismatch_and_unreadable_history(hub):
    tools = OrganizationTools(hub.state)
    rid = tools.search("question")["research_id"]
    assert tools.read("arbitrary", 1, rid)["code"] == "unknown_locator"
    graph = tools.related("asset_a", 2, rid)
    assert graph["ok"]
    assert tools.read("asset_a", 1, rid)["code"] == "historical_unreadable"
    _, page = hub.response("read", {"cursor": 0, "max_bytes": 100})
    page["version"]["document_hash"] = "changed"
    hub.override = page
    assert tools.read("asset_a", 2, rid, max_bytes=100)["code"] == "invalid_response"


def test_search_response_limits_fields_and_retains_masked_offsets(hub):
    tools = OrganizationTools(hub.state)
    candidate = hub.candidate()
    candidate.update(
        excerpt_source_field="index_text",
        excerpt_char_start=1000,
        excerpt_char_end=1100,
        part_count=3,
        part_index=1,
        semantic_match={
            "similarity": 0.8,
            "source_field": "index_text",
            "char_start": 1000,
            "char_end": 1200,
            "offset_unit": "unicode_code_points",
            "aggregation": "max_window_cosine",
            "private": "do not forward",
        },
    )
    hub.override = {"items": [candidate], "truncated": True, "omitted_items": 2}
    result = tools.search("question")
    assert result["ok"] and result["truncated"] and result["omitted_items"] == 2
    row = result["items"][0]
    assert row["excerpt_source_field"] == "index_text" and row["excerpt_char_start"] == 1000
    assert row["part_count"] == 3 and row["excerpt_scope"] == "asset_chunk"
    assert "private_server_state" not in row and "private" not in row["semantic_match"]
    assert len(json.dumps(result, ensure_ascii=False, separators=(",", ":")).encode()) <= 12000


def test_identity_change_closes_existing_research(hub):
    tools = OrganizationTools(hub.state)
    rid = tools.search("question")["research_id"]
    config = copy.deepcopy(hub.config)
    config["identity"]["member_id"] = "different-member"
    (hub.state / "config.json").write_text(json.dumps(config))
    assert tools.read("asset_a", 2, rid)["code"] == "identity_changed"
    assert len(hub.calls) == 1


def test_utf8_read_offsets_are_bytes_and_upstream_is_only_an_asset_link(hub):
    hub.body = "中文证据" * 1000
    tools = OrganizationTools(hub.state)
    rid = tools.search("问题")["research_id"]
    page = tools.read("asset_a", 2, rid, max_bytes=4)
    assert page["ok"] and page["text"] == "中" and page["next_cursor"] == 3
    assert tools.read("asset_a", 2, rid, cursor=3, max_bytes=4)["text"] == "文"
    root, target = hub.node(), hub.node()
    target["asset_id"] = "asset_b"
    hub.override = {
        "root": root,
        "items": [target],
        "edges": [
            {
                "from": {"asset_id": "asset_a", "revision": 2},
                "to": {"asset_id": "asset_b", "revision": 2},
                "type": "derived_from",
                "evidence": {
                    "asset_id": "asset_a",
                    "revision": 2,
                    "field": "upstream",
                    "index": 0,
                    "relation_scope": "asset",
                    "target_revision_binding": "current_lookup",
                    "original_target_revision": None,
                },
            }
        ],
        "truncated": False,
    }
    graph = tools.related("asset_a", 2, rid)
    assert graph["ok"], graph
    proof = graph["edges"][0]["evidence"]
    assert proof["relation_scope"] == "asset"
    assert proof["target_revision_binding"] == "current_lookup"
    assert proof["original_target_revision"] is None


def test_oversized_raw_response_is_rejected_even_if_unknown_fields_would_be_removed(hub):
    hub.override = {"items": [hub.candidate()], "arbitrary_body": "x" * 12000}
    result = OrganizationTools(hub.state).search("question")
    assert result["code"] == "invalid_response"
    assert "arbitrary_body" not in json.dumps(result)


def test_research_resident_metadata_is_bounded_and_calls_cannot_override_identity(hub, monkeypatch):
    tools = OrganizationTools(hub.state)
    monkeypatch.setattr(org_tools, "MAX_RESEARCHES", 2)
    ids = [tools.search(str(i))["research_id"] for i in range(3)]
    assert len(tools.researches) == 2 and ids[0] not in tools.researches
    assert all(
        "tenant_id" not in payload and "hub" not in payload and "token" not in payload
        for _, payload, _ in hub.calls
    )
    assert all(auth == "Bearer " + TOKEN for _, _, auth in hub.calls)


def test_partial_first_setup_remains_not_connected_without_mutation(tmp_path):
    state = tmp_path / "profile"
    state.mkdir()
    config = state / "config.json"
    original = '{"projection":false}'
    config.write_text(original)
    result = OrganizationTools(state).search("question")
    assert result["code"] == "not_connected"
    assert config.read_text() == original
    assert list(state.iterdir()) == [config]


@pytest.mark.parametrize(
    "field,value",
    [
        ("title", "题" * 200),
        ("path", "路" * 400 + ".md"),
        ("project", "项" * 180),
        ("path", "路" * 2048),
    ],
)
def test_metadata_character_limits_preserve_legal_chinese_sources(hub, field, value):
    candidate = hub.candidate()
    candidate["source"][field] = value
    hub.override = {"items": [candidate]}
    tools = OrganizationTools(hub.state)
    result = tools.search("question", project_filter="项" * 256)
    assert result["ok"] and result["items"], result
    assert result["items"][0]["source"][field] == value
    assert hub.calls[-1][1]["project_filter"] == "项" * 256
    assert len(json.dumps(result, ensure_ascii=False, separators=(",", ":")).encode()) <= 12000


def test_document_id_and_reference_href_limits_are_characters(hub):
    candidate = hub.candidate()
    candidate["document_id"] = "文" * 100
    hub.override = {"items": [candidate]}
    tools = OrganizationTools(hub.state)
    searched = tools.search("问" * 4000)
    assert searched["ok"], searched
    rid = searched["research_id"]
    hub.override = None
    _, page = hub.response("read", {"cursor": 0, "max_bytes": 100})
    page["version"]["document_id"] = candidate["document_id"]
    hub.override = page
    assert tools.read("asset_a", 2, rid, max_bytes=100)["ok"]
    root, target = hub.node(), hub.node()
    root["version"]["document_id"] = candidate["document_id"]
    target["asset_id"] = "asset_b"
    href = "路" * 1000 + ".md"
    hub.override = {
        "root": root,
        "items": [target],
        "edges": [
            {
                "from": {"asset_id": "asset_a", "revision": 2},
                "to": {"asset_id": "asset_b", "revision": 2},
                "type": "explicit_reference",
                "evidence": {"asset_id": "asset_a", "revision": 2, "href": href, "line": 1},
            }
        ],
        "truncated": False,
    }
    graph = tools.related("asset_a", 2, rid)
    assert graph["ok"] and graph["edges"][0]["evidence"]["href"] == href, graph


@pytest.mark.parametrize(
    "field",
    [
        "tenant_id",
        "source_device_id",
        "source_store",
        "product",
        "kind",
        "path",
        "project",
        "title",
        "producer_member_id",
        "uploader_member_id",
    ],
)
def test_read_rejects_changed_stable_source_fields_without_accepting_page(hub, field):
    tools = OrganizationTools(hub.state)
    rid = tools.search("question")["research_id"]
    _, page = hub.response("read", {"cursor": 0, "max_bytes": 100})
    page["source"][field] = "changed-source-field"
    hub.override = page
    before = copy.deepcopy(tools.researches[rid].locators)
    response = tools.read("asset_a", 2, rid, max_bytes=100)
    assert response["code"] == "invalid_response"
    assert tools.researches[rid].locators == before
    assert not tools.researches[rid].pages


def test_source_name_changes_are_allowed_but_same_revision_source_rebinding_is_not(hub):
    tools = OrganizationTools(hub.state)
    rid = tools.search("question")["research_id"]
    _, page = hub.response("read", {"cursor": 0, "max_bytes": 100})
    page["source"]["producer_name"] = "新的显示名称"
    page["source"]["uploader_name"] = "新上传者显示名"
    hub.override = page
    assert tools.read("asset_a", 2, rid, max_bytes=100)["ok"]
    bad = hub.candidate()
    bad["source"]["path"] = "invented.md"
    hub.override = {"items": [bad]}
    assert tools.search("second question", research_id=rid)["code"] == "invalid_response"
    hub.override = None
    _, graph = hub.response("related", {})
    graph["root"]["source"]["path"] = "invented.md"
    hub.override = graph
    assert tools.related("asset_a", 2, rid)["code"] == "invalid_response"


def test_explicit_revision_change_returns_stale_without_accepting_new_source(hub):
    tools = OrganizationTools(hub.state)
    rid = tools.search("question")["research_id"]
    _, page = hub.response("read", {"cursor": 0, "max_bytes": 100})
    page["revision"] = 3
    hub.override = page
    response = tools.read("asset_a", 2, rid, max_bytes=100)
    assert response["code"] == "stale_version" and "Search again" in response["message"]
    assert ("asset_a", 3) not in tools.researches[rid].locators


def test_graph_explicit_metadata_budget_sentinel_preserves_locator_without_inventing_source(hub):
    tools = OrganizationTools(hub.state)
    rid = tools.search("question")["research_id"]
    before = copy.deepcopy(tools.researches[rid].locators)
    hub.override = {
        "root": {"asset_id": "asset_a", "revision": 2, "metadata_available": False},
        "items": [],
        "edges": [],
        "truncated": True,
    }
    response = tools.related("asset_a", 2, rid)
    assert response["ok"] and response["truncated"]
    assert response["root"] == hub.override["root"]
    assert "source" not in response["root"] and "version" not in response["root"]
    assert tools.researches[rid].locators == before


@pytest.mark.parametrize(
    "root,truncated,items",
    [
        ({"asset_id": "asset_a", "revision": 2}, True, []),
        ({"asset_id": "asset_a", "revision": 2, "metadata_available": False}, False, []),
        (
            {"asset_id": "asset_a", "revision": 2, "metadata_available": False, "source": {}},
            True,
            [],
        ),
        (
            {"asset_id": "asset_a", "revision": 2, "metadata_available": False},
            True,
            [{"asset_id": "other", "revision": 1}],
        ),
    ],
)
def test_graph_rejects_arbitrary_incomplete_metadata(hub, root, truncated, items):
    tools = OrganizationTools(hub.state)
    rid = tools.search("question")["research_id"]
    hub.override = {"root": root, "items": items, "edges": [], "truncated": truncated}
    assert tools.related("asset_a", 2, rid)["code"] == "invalid_response"


def test_large_valid_graph_omits_whole_edges_and_nodes_within_wire_budget(hub):
    tools = OrganizationTools(hub.state)
    rid = tools.search("question")["research_id"]
    nodes, edges = [], []
    for index in range(10):
        node = hub.node()
        node["asset_id"] = f"target_{index}"
        node["source"].update(title="T" * 512, project="P" * 200)
        nodes.append(node)
        edges.append(
            {
                "from": {"asset_id": "asset_a", "revision": 2},
                "to": {"asset_id": node["asset_id"], "revision": 2},
                "type": "explicit_reference",
                "evidence": {
                    "asset_id": "asset_a",
                    "revision": 2,
                    "href": f"target_{index}.md",
                    "line": index + 1,
                },
            }
        )
    hub.override = {"root": hub.node(), "items": nodes, "edges": edges, "truncated": False}
    assert len(json.dumps(hub.override).encode()) > 12000
    graph = tools.related("asset_a", 2, rid)
    assert graph["ok"] and graph["truncated"] and 0 < len(graph["items"]) < 10, graph
    assert len(json.dumps(graph, ensure_ascii=False, separators=(",", ":")).encode()) <= 12000
    assert all(
        node["source"]["title"] == "T" * 512 and node["source"]["project"] == "P" * 200
        for node in graph["items"]
    )
    accepted = {node["asset_id"] for node in graph["items"]}
    assert {edge["to"]["asset_id"] for edge in graph["edges"]} == accepted
    assert {asset for asset, _ in tools.researches[rid].locators} == accepted | {"asset_a"}


def test_legacy_codex_source_label_keeps_version_identity():
    legacy = {"product": "codex-shared", "source_device_id": "same-device", "path": "topic.md"}
    current = {**legacy, "product": "codex"}
    assert org_tools._source(legacy) == current
    assert legacy["product"] == "codex-shared"
    assert org_tools._stable_source(org_tools._source(legacy)) == org_tools._stable_source(
        org_tools._source(current)
    )
    assert org_tools._source({"product": "claude-code"}) == {"product": "claude-code"}

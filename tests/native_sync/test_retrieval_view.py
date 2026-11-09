"""Public retrieval budgets and provenance using real HTTP serialization."""

import json

import pytest
from fastapi.testclient import TestClient

from agent_brain.native_sync.api import create_app
from agent_brain.native_sync.hub import Hub
from agent_brain.native_sync.retrieval_view import bounded_retrieval
from test_knowledge import asset


@pytest.fixture
def api(tmp_path, monkeypatch):
    hub = Hub(tmp_path / "hub.sqlite")
    who = hub.provision("Author", "Source", tenant="org")
    returned = {
        "items": [],
        "retrieval": {"mode": "lexical", "degraded": True, "reasons": ["semantic_index_not_built"]},
    }

    def retrieve(token, **kwargs):
        hub.identity(token)
        return returned

    monkeypatch.setattr(hub, "retrieve", retrieve, raising=False)
    client = TestClient(create_app(hub))
    headers = {"Authorization": "Bearer " + who["token"]}
    return hub, who, returned, client, headers


def test_large_candidate_returns_only_bounded_explicit_excerpt(api):
    hub, who, returned, client, headers = api
    body = "中文😀" * 100000
    record = hub.put(who["token"], asset(body=body, index_text="INDEX_SECRET" * 50000))
    returned["items"] = [{**record, "verified": True, "PRIVATE_SERVER_FIELD": "INTERNAL_SECRET"}]
    response = client.post("/native/v1/retrieve", json={"query": "question"}, headers=headers)
    assert response.status_code == 200
    assert len(response.content) <= 12000
    data = response.json()
    item = data["items"][0]
    assert len(item["excerpt"].encode()) <= 1200
    assert item["excerpt"] == body[: len(item["excerpt"])]
    assert item["excerpt_truncated"]
    assert item["document_hash"] == record["body_hash"]
    assert item["version_verified"] and item["answer_status"] == "candidates_only"
    assert item["source"]["producer_name"] == "Author"
    assert item["role"] == item["source"]["role"] == "topic_memory"
    assert not {"body", "index_text", "verified", "body_hash", "PRIVATE_SERVER_FIELD"} & item.keys()
    assert "INDEX_SECRET" not in response.text and "INTERNAL_SECRET" not in response.text


def test_total_wire_budget_counts_json_escaping_metadata_and_omissions(api):
    hub, who, returned, client, headers = api
    for index in range(20):
        record = hub.put(who["token"], asset(str(index), '\n\t"\\😀' * 2000, title="标题" * 200))
        returned["items"].append({**record, "verified": True})
    response = client.post("/native/v1/retrieve", json={"query": "q", "limit": 20}, headers=headers)
    assert response.status_code == 200
    assert len(response.content) <= 12000
    data = response.json()
    assert 0 < len(data["items"]) < 20
    assert data["truncated"] and data["omitted_items"] == 20 - len(data["items"])
    assert all(len(item["excerpt"].encode()) <= 1200 for item in data["items"])
    assert (
        client.post(
            "/native/v1/retrieve", json={"query": "q", "limit": 21}, headers=headers
        ).status_code
        == 422
    )


def test_diagnostic_projection_excludes_unknown_fields_and_is_itself_bounded(api):
    _, _, returned, client, headers = api
    returned["retrieval"] = {
        "mode": "hybrid",
        "degraded": False,
        "reasons": ["\x01" * 100000] * 100,
        "candidates": {"lexical": 2, "semantic": 3, "raw_payload": "PRIVATE_CANARY"},
        "semantic_index": {"current": 3, "eligible": 4, "db_path": "PRIVATE_CANARY"},
        "model": {
            "model": "\x01" * 100000,
            "model_version": "\x01" * 100000,
            "dimension": 384,
            "file": "PRIVATE_CANARY",
        },
        "internal_connection": "PRIVATE_CANARY",
        "answer_status": "FACT_CONFIRMED",
    }
    response = client.post("/native/v1/retrieve", json={"query": "q"}, headers=headers)
    assert len(response.content) <= 12000
    assert "PRIVATE_CANARY" not in response.text and "FACT_CONFIRMED" not in response.text
    assert response.json()["retrieval"]["answer_status"] == "no_match"


def test_metadata_that_cannot_fit_is_omitted_without_rewriting_source(api):
    hub, who, returned, client, headers = api
    record = hub.put(who["token"], asset("wide", "evidence", path="😀" * 2048, title="😀" * 512))
    record["producer_name"] = "名字" * 2000
    returned["items"] = [record]
    response = client.post("/native/v1/retrieve", json={"query": "q"}, headers=headers)
    data = response.json()
    assert len(response.content) <= 12000
    assert data["items"] == [] and data["omitted_items"] == 1 and data["truncated"]
    assert data["retrieval"]["answer_status"] == "candidates_only"


@pytest.mark.parametrize("field", ["body", "index_text"])
def test_semantic_excerpt_keeps_explicit_original_field_and_unicode_offsets(api, field):
    hub, who, returned, client, headers = api
    text = "前文😀" * 1000 + "关键结论：必须两人审批。" + "后文" * 1000
    start = len("前文😀" * 1000)
    record = hub.put(
        who["token"], asset(body=text, index_text=text, kind="transcript", path="session.jsonl")
    )
    record["semantic_match"] = {
        "similarity": 0.8,
        "source_field": field,
        "char_start": start,
        "char_end": start + 20,
        "offset_unit": "unicode_code_points",
        "aggregation": "max_window_cosine",
        "internal_vector": "PRIVATE_VECTOR",
    }
    returned["items"] = [record]
    response = client.post("/native/v1/retrieve", json={"query": "q"}, headers=headers)
    item = response.json()["items"][0]
    assert item["excerpt"].startswith("关键结论：必须两人审批。")
    assert item["excerpt_source_field"] == field
    assert item["excerpt_char_start"] == start
    assert text[start : item["excerpt_char_end"]] == item["excerpt"]
    assert item["semantic_match"]["char_start"] == start
    assert "PRIVATE_VECTOR" not in response.text


def test_invalid_semantic_locator_does_not_select_unrelated_fields(api):
    hub, who, returned, client, headers = api
    record = hub.put(who["token"], asset(body="real source text"))
    record.update(
        private="PRIVATE_CANARY",
        semantic_match={
            "source_field": "private",
            "char_start": 0,
            "char_end": 14,
            "offset_unit": "unicode_code_points",
            "aggregation": "max_window_cosine",
            "similarity": 1.0,
        },
    )
    returned["items"] = [record]
    data = bounded_retrieval(returned)
    assert data["items"][0]["excerpt"] == "real source text"
    assert "PRIVATE_CANARY" not in json.dumps(data)


def test_upstream_names_asset_and_does_not_invent_historical_target_revision(api):
    hub, who, _, client, headers = api
    hub.put(who["token"], asset("b", "Version one"))
    hub.put(who["token"], asset("a", "Derived from version one", upstream=["b"]))
    hub.put(who["token"], asset("b", "Version two", base_revision=1))
    response = client.post(
        "/native/v1/related", json={"asset_id": "a", "revision": 1}, headers=headers
    )
    edge = response.json()["edges"][0]
    assert edge["to"] == {"asset_id": "b", "revision": 2}
    assert edge["evidence"]["relation_scope"] == "asset"
    assert edge["evidence"]["target_revision_binding"] == "current_lookup"
    assert edge["evidence"]["original_target_revision"] is None


def test_literal_mode_is_reported_as_such(api):
    _, _, returned, client, headers = api
    returned["retrieval"] = {"mode": "literal", "degraded": False, "reasons": ["literal_mode"]}
    response = client.post("/native/v1/retrieve", json={"query": "q"}, headers=headers)
    assert response.json()["retrieval"]["mode"] == "literal"

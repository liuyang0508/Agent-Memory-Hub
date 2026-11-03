"""Prompt-time relevance gate, server-side index upkeep and the vector cache.

The synthetic backend fixes similarities exactly; it is not model evidence.
Calibrated thresholds for the shipped model live in the deploy model spec.
"""

from fastapi.testclient import TestClient

from agent_brain.native_sync.api import create_app
from agent_brain.native_sync.hub import Hub
from agent_brain.native_sync.retrieval import identifiers
from test_hub import asset


class GateBackend:
    semantic = True  # Test injection only.
    model_id = "gate-test-only"
    model_version = "1"
    dim = 2

    def embed(self, text):
        if "unrelated" in text:
            return [0.0, 1.0]  # similarity 0 to the query
        if "budget" in text:
            return [0.6, 0.8]  # similarity 0.6
        return [1.0, 0.0]  # similarity 1


def make(tmp_path, **settings):
    hub = Hub(tmp_path / "hub.sqlite", semantic_backend=GateBackend(), **settings)
    token = hub.provision("one", "device")["token"]
    hub.put(token, asset("strong", body="approval requires two people", title="approval"))
    hub.put(token, asset("keyword-only", body="approval mentioned in unrelated notes", title=""))
    hub.put(token, asset("weaker", body="approval budget sheet", title=""))
    hub.put(token, asset("coded", body="unrelated log line E1234 approval", title=""))
    hub.rebuild_semantic(token, kinds=["memory", "rule"])
    return hub, token


def ids(result):
    return [item["asset_id"] for item in result["items"]]


def test_hook_recall_keeps_only_relevant_candidates(tmp_path):
    hub, token = make(tmp_path, hook_min_score=0.5, hook_margin=0.3)
    result = hub.recall(token, "approval rules")
    assert ids(result) == ["strong"]  # keyword-only below floor, weaker outside margin
    assert result["retrieval"]["mode"] == "hybrid"
    trace = hub.recalls(token, trace_id=result["trace_id"])["traces"][0]
    assert trace["stages"]["relevance_gate"] == "on"
    states = {c["asset_id"]: c["state"] for c in trace["candidates"]}
    assert states["keyword-only"] == "dropped:relevance_gate"


def test_a_literal_identifier_from_the_prompt_survives_the_gate(tmp_path):
    hub, token = make(tmp_path, hook_min_score=0.5, hook_margin=0.3)
    assert "coded" in ids(hub.recall(token, "approval E1234"))
    assert "coded" not in ids(hub.recall(token, "approval rules"))


def test_tools_and_uncalibrated_models_are_not_gated(tmp_path):
    hub, token = make(tmp_path, hook_min_score=0.5, hook_margin=0.3)
    tool = hub.retrieve(token, "approval rules", kinds=["memory", "rule"])
    assert "keyword-only" in ids(tool)  # Agents exploring with MCP see the union.
    uncalibrated, token2 = make(tmp_path / "plain")
    assert uncalibrated.relevance_gate() is None
    assert "keyword-only" in ids(uncalibrated.recall(token2, "approval rules"))


def test_manifest_thresholds_apply_unless_set_explicitly(tmp_path):
    backend = GateBackend()
    backend.hook_min_score, backend.hook_margin, backend.semantic_min_score = 0.4, 0.08, 0.3
    hub = Hub(tmp_path / "hub.sqlite", semantic_backend=backend)
    hub._semantic_backend()
    assert hub.relevance_gate() == (0.4, 0.08) and hub.semantic_min_score == 0.3
    pinned = Hub(tmp_path / "pinned.sqlite", semantic_backend=backend, semantic_min_score=0.5)
    pinned._semantic_backend()
    assert pinned.semantic_min_score == 0.5


def test_identifier_tokens():
    found = identifiers(
        "PreparationOwner.invalidate 与 E1234、env_key、useEffect、5101 和 Docker GIL"
    )
    assert found == ["preparationowner.invalidate", "e1234", "env_key", "useeffect", "5101"]


def test_server_upkeep_encodes_only_changes_and_reports_ready(tmp_path):
    hub = Hub(tmp_path / "hub.sqlite", semantic_backend=GateBackend())
    token = hub.provision("one", "device")["token"]
    hub.put(token, asset("a", body="approval requires two people"))
    first = hub.refresh_semantic_local()
    assert first["state"] == "ready" and first["tenants"]["linkcrux"]["encoded"] == 1
    assert hub.refresh_semantic_local()["tenants"]["linkcrux"]["encoded"] == 0
    hub.put(token, asset("b", body="approval budget sheet"))
    again = hub.refresh_semantic_local()["tenants"]["linkcrux"]
    assert again["encoded"] == 1 and again["reused"] == 1
    health = TestClient(create_app(hub)).get("/native/v1/health").json()
    assert "semantic-v1" in health["capabilities"]
    assert health["semantic"] == {"state": "ready", "model": "gate-test-only", "gate": "off"}


def test_health_without_a_model_reports_off(tmp_path):
    health = TestClient(create_app(Hub(tmp_path / "hub.sqlite"))).get("/native/v1/health").json()
    assert "semantic-v1" not in health["capabilities"] and health["semantic"] == {"state": "off"}


def test_vector_cache_follows_revisions(tmp_path):
    hub, token = make(tmp_path)
    hub.recall(token, "approval rules")
    cached = hub._vectors["linkcrux"]
    assert {key[0] for key in cached} == {"strong", "keyword-only", "weaker", "coded"}
    old = next(key for key in cached if key[0] == "strong")
    hub.put(token, asset("strong", base_revision=1, body="approval requires three people"))
    hub.rebuild_semantic(token, kinds=["memory", "rule"])
    hub.recall(token, "approval rules")
    fresh = hub._vectors["linkcrux"]
    assert old not in fresh and any(key[0] == "strong" for key in fresh)

"""Tests for the web admin API endpoints."""

from __future__ import annotations

import os
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agent_brain.contracts.memory_item import MemoryItem, MemoryType


@pytest.fixture()
def brain_dir(tmp_path: Path):
    items_dir = tmp_path / "items"
    items_dir.mkdir()
    os.environ["BRAIN_DIR"] = str(tmp_path)
    os.environ["MEMORY_HUB_TEST_EMBEDDING"] = "1"
    os.environ["MEMORY_HUB_RATE_LIMIT"] = "0"
    yield tmp_path
    os.environ.pop("BRAIN_DIR", None)
    os.environ.pop("MEMORY_HUB_TEST_EMBEDDING", None)
    os.environ.pop("MEMORY_HUB_RATE_LIMIT", None)


@pytest.fixture()
def seed_items(brain_dir: Path):
    """Write a few markdown items into the brain dir for testing."""
    from agent_brain.memory.store.items_store import ItemsStore

    store = ItemsStore(items_dir=brain_dir / "items")
    items = []
    for i, (typ, title, proj) in enumerate([
        ("fact", "Python GIL behavior", "alpha"),
        ("decision", "Use SSE over WebSocket", "alpha"),
        ("episode", "Debug session crash", "beta"),
    ]):
        item = MemoryItem(
            id=f"mem-20260101-00000{i}-test-{typ}",
            type=MemoryType(typ),
            title=title,
            summary=f"Summary of {title}",
            project=proj,
            tags=["test", typ],
            created_at=datetime.now(timezone.utc),
        )
        store.write(item, f"Body content for {title}")
        items.append(item)
    return items


@pytest.fixture()
def client(brain_dir: Path):
    from web.app import app

    return TestClient(app)


def test_web_item_listing_helper_filters_sorts_and_pages():
    from datetime import datetime, timezone
    from agent_brain.contracts.memory_item import MemoryItem, MemoryType
    from web.api.routes.item_listing import list_visible_items

    visible = MemoryItem(
        id="mem-20260101-000000-visible",
        type=MemoryType.fact,
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        title="Beta visible",
        summary="Searchable summary",
        project="agent-memory-hub",
        tags=["web"],
        confidence=0.9,
    )
    hidden = MemoryItem(
        id="mem-20260101-000001-hidden",
        type=MemoryType.fact,
        created_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
        title="Alpha hidden",
        summary="Searchable summary",
        project="agent-memory-hub",
        tags=["web"],
        confidence=0.8,
    )

    payload = list_visible_items(
        items_with_bodies=[(visible, "body visible"), (hidden, "body hidden")],
        user=object(),
        is_visible=lambda item, _user: item.id == visible.id,
        tag="web",
        q="searchable",
        sort="title",
        order="asc",
        offset=0,
        limit=1,
    )

    assert payload["total"] == 1
    assert payload["items"][0]["id"] == visible.id
    assert payload["items"][0]["body_preview"] == "body visible"


def test_web_item_payload_helpers_build_updates_and_clone_records():
    from datetime import datetime, timezone
    from agent_brain.contracts.memory_item import MemoryItem, MemoryType
    from web.api.routes.item_payloads import (
        UpdateItemRequest,
        clone_item_record,
        update_fields_from_request,
    )

    updates = update_fields_from_request(
        UpdateItemRequest(title="Renamed", confidence=0.8, tags=["keep"])
    )
    assert updates == {"title": "Renamed", "tags": ["keep"], "confidence": 0.8}

    item = MemoryItem(
        id="mem-20260101-000000-source",
        type=MemoryType.fact,
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        title="Source",
        summary="Summary",
        tags=["existing"],
    )
    clone = clone_item_record(
        item,
        clone_id="mem-20260101-000001-clone",
        created_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
    )

    assert clone.id == "mem-20260101-000001-clone"
    assert clone.title == "Source"
    assert set(clone.tags) == {"existing", "cloned"}


def test_cockpit_summary_route_offloads_sync_builder():
    route = Path(__file__).resolve().parents[2] / "web" / "api" / "routes" / "cockpit.py"
    text = route.read_text(encoding="utf-8")

    assert "run_in_threadpool" in text
    assert "await run_in_threadpool(build_cockpit_summary" in text


def test_adapter_read_routes_offload_sync_builders():
    route = Path(__file__).resolve().parents[2] / "web" / "api" / "routes" / "adapters.py"
    text = route.read_text(encoding="utf-8")

    assert "run_in_threadpool" in text
    assert "await run_in_threadpool(_adapter_capabilities_payload)" in text
    assert "await run_in_threadpool(build_onboarding_summary" in text


@pytest.fixture()
def admin_token(client: TestClient):
    resp = client.post("/api/auth/init", json={"username": "admin", "password": "test123"})
    assert resp.status_code == 200
    return resp.json()["token"]


class TestCockpitSummaryAPI:
    def test_cockpit_summary_requires_auth(self, client: TestClient):
        resp = client.get("/api/cockpit/summary")
        assert resp.status_code == 401

    def test_cockpit_summary_returns_read_model(self, client: TestClient, admin_token: str, seed_items):
        resp = client.get(
            "/api/cockpit/summary",
            headers={"Authorization": f"Bearer {admin_token}"},
        )

        assert resp.status_code == 200
        data = resp.json()
        assert set(data) == {
            "generated_at",
            "brain_dir",
            "handoff_pack",
            "key_decisions",
            "open_signals",
            "trust_risks",
            "adapter_health",
            "loop_governance",
            "memory_candidates",
            "cross_agent_timeline",
        }
        assert data["adapter_health"]["total"] == 16
        assert data["adapter_health"]["install_ready"] == 15
        assert data["adapter_health"]["wip"] == 1
        assert data["adapter_health"]["verified"] == 0
        assert data["loop_governance"]["status"] == "ok"
        assert "recent" in data["loop_governance"]
        assert any(item["title"] == "Use SSE over WebSocket" for item in data["key_decisions"])


class TestAdapterOnboardingAPI:
    def test_adapter_onboarding_requires_auth(self, client: TestClient):
        resp = client.get("/api/adapters/onboarding")
        assert resp.status_code == 401

    def test_adapter_onboarding_returns_summary(self, client: TestClient, admin_token: str):
        resp = client.get(
            "/api/adapters/onboarding",
            headers={"Authorization": f"Bearer {admin_token}"},
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 16
        assert data["install_ready"] == 15
        assert data["wip"] == 1
        assert data["verified"] == 0
        assert any(row["name"] == "codex" for row in data["adapters"])

    def test_adapter_doctor_route_returns_checks(self, client: TestClient, admin_token: str):
        resp = client.get(
            "/api/adapters/codex/doctor",
            headers={"Authorization": f"Bearer {admin_token}"},
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["adapter"] == "codex"
        assert isinstance(data["checks"], list)

    def test_adapter_verify_requires_auth(self, client: TestClient):
        resp = client.post("/api/adapters/codex/verify")
        assert resp.status_code == 401

    def test_adapter_verify_uses_gate(
        self,
        client: TestClient,
        admin_token: str,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        from agent_brain.agent_integrations import continue_dev as cont_mod

        monkeypatch.setattr(cont_mod, "MCP_CONFIG_PATH", tmp_path / ".continue" / "config.yaml")
        monkeypatch.setattr(
            cont_mod,
            "AWARENESS_PATH",
            tmp_path / ".continue" / "rules" / "agent-memory-hub.md",
        )

        install = client.post(
            "/api/adapters/continue_dev/install",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert install.status_code == 200

        resp = client.post(
            "/api/adapters/continue_dev/verify",
            headers={"Authorization": f"Bearer {admin_token}"},
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["adapter"] == "continue_dev"
        assert data["status"] == "passed"
        assert data["blockers"] == []
        assert data["mcp_probe"]["status"] == "passed"

    def test_adapter_install_verify_uninstall_transaction_route(
        self,
        client: TestClient,
        admin_token: str,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        from agent_brain.agent_integrations import github_copilot as gh_mod

        instructions = tmp_path / ".github" / "copilot-instructions.md"
        monkeypatch.setattr(gh_mod, "INSTRUCTIONS_PATH", instructions)

        resp = client.post(
            "/api/adapters/github_copilot/install-verify?uninstall_check=true",
            headers={"Authorization": f"Bearer {admin_token}"},
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "passed"
        assert data["uninstall"]["status"] == "uninstalled"
        assert data["persistent_verification_recorded"] is False
        assert gh_mod.BEGIN not in instructions.read_text()


class TestMemoryCandidatesAPI:
    def test_memory_candidates_requires_auth(self, client: TestClient):
        resp = client.get("/api/memory-candidates")
        assert resp.status_code == 401

    def test_memory_candidates_list_is_renderable(self, client: TestClient, admin_token: str):
        resp = client.get(
            "/api/memory-candidates",
            headers={"Authorization": f"Bearer {admin_token}"},
        )

        assert resp.status_code == 200
        data = resp.json()
        assert {"total", "pending", "approved", "rejected", "items"}.issubset(data)


class TestEvolveAPI:
    def test_evolve_response_includes_higher_order_control_report(
        self,
        client: TestClient,
        admin_token: str,
        brain_dir: Path,
    ):
        from agent_brain.memory.governance.recall_events import record_gap

        record_gap(
            brain_dir,
            query="raw evolve query should not leak",
            reason="no_candidates",
            injected_ids=[],
            rejected_ids=[],
            adapter="codex",
            session_id="sess-evolve",
        )

        resp = client.post(
            "/api/evolve",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"apply": False},
        )

        assert resp.status_code == 200
        data = resp.json()
        control = data["evolution_control"]
        assert control["mode"] == "shadow_mode"
        assert control["mutation_boundary"] == "proposal_only"
        assert control["data_flow"]["failures"] >= 1
        assert any(gate["name"] == "release_gate" for gate in control["gates"])
        assert any(rec["action"] == "review_recall_gaps" for rec in control["recommendations"])
        assert "raw evolve query should not leak" not in json.dumps(control)

    def test_memory_candidates_generate_and_reject(self, client: TestClient, admin_token: str, brain_dir: Path):
        from agent_brain.memory.store.items_store import ItemsStore

        store = ItemsStore(brain_dir / "items")
        item = MemoryItem(
            id="mem-20260621-020000-web-signal",
            type=MemoryType.signal,
            title="Handoff: Web candidate blocked",
            summary="Need remember web candidate blocker",
            tags=["blocker"],
            created_at=datetime.now(timezone.utc),
        )
        store.write(item, "blocked until review")

        headers = {"Authorization": f"Bearer {admin_token}"}
        generated = client.post("/api/memory-candidates/generate", headers=headers)
        assert generated.status_code == 200
        assert generated.json()["created"] == 1

        listed = client.get("/api/memory-candidates", headers=headers).json()
        candidate_id = listed["items"][0]["candidate_id"]
        rejected = client.post(f"/api/memory-candidates/{candidate_id}/reject", headers=headers)

        assert rejected.status_code == 200
        assert rejected.json()["status"] == "rejected"

    def test_memory_candidates_approve_writes_item(self, client: TestClient, admin_token: str, brain_dir: Path):
        from agent_brain.memory.store.items_store import ItemsStore

        store = ItemsStore(brain_dir / "items")
        item = MemoryItem(
            id="mem-20260621-020001-web-approve",
            type=MemoryType.signal,
            title="Handoff: approve candidate",
            summary="Need remember approval path",
            tags=["blocker"],
            created_at=datetime.now(timezone.utc),
        )
        store.write(item, "blocked until approve")

        headers = {"Authorization": f"Bearer {admin_token}"}
        client.post("/api/memory-candidates/generate", headers=headers)
        candidate_id = client.get("/api/memory-candidates", headers=headers).json()["items"][0]["candidate_id"]

        approved = client.post(f"/api/memory-candidates/{candidate_id}/approve", headers=headers)

        assert approved.status_code == 200
        assert approved.json()["status"] == "approved"
        assert approved.json()["write_result"]["status"] == "written"


class TestDataFlowAPI:
    def test_data_flow_requires_auth(self, client: TestClient):
        resp = client.get("/api/data-flow")
        assert resp.status_code == 401

    def test_dashboard_contains_data_flow_panel(self, client: TestClient):
        resp = client.get("/")
        assert resp.status_code == 200
        assert "三日数据流转" in resp.text
        assert "/api/data-flow?hours=72&limit=20" in resp.text
        assert "链路追踪" in resp.text
        assert "/api/memory-lineage?hours=${_lineageHours}&limit=220" in resp.text
        assert "function loadMemoryLineage" in resp.text
        assert "id=\"lineageMemoryTab\"" in resp.text
        assert "id=\"lineageChainTab\"" in resp.text
        assert "function loadChainLogs" in resp.text
        assert "/api/chain-logs?hours=${_chainHours}&limit=100" in resp.text
        assert "id=\"lineageAgentSelect\"" in resp.text
        assert "id=\"lineageModeTabs\"" in resp.text
        assert "id=\"lineageMemoryList\"" in resp.text
        assert "id=\"lineageMemoryDetail\"" in resp.text
        assert "function lineageSelectMemory" in resp.text
        assert "被哪些 Agent 使用过" in resp.text
        assert "lineage-console" in resp.text
        assert "lineage-topbar" in resp.text
        assert "lineage-list-shell" in resp.text
        assert "lineage-detail-shell" in resp.text
        assert "chain-workbench" in resp.text
        assert "chain-node-rail" in resp.text
        assert "chain-algorithm-waterfall" in resp.text
        assert "chain-detail-drawer" in resp.text
        assert "chain-candidate-table" in resp.text
        assert "function pageFromHash" in resp.text
        assert "ROUTABLE_PAGES" in resp.text
        assert "hashchange" in resp.text
        assert "lineage-hero" not in resp.text
        assert "高阶自进化控制面" in resp.text
        assert "evolution_control" in resp.text


class TestApiDocsRoutes:
    def test_routes_endpoint_lists_current_web_surface(self, client: TestClient, admin_token: str):
        resp = client.get("/api/routes", headers={"Authorization": f"Bearer {admin_token}"})

        assert resp.status_code == 200
        data = resp.json()
        paths = {route["path"] for route in data["routes"]}
        assert data["total"] == len(data["routes"])
        assert data["total"] == 101
        assert "/api/data-flow" in paths
        assert "/api/chain-logs" in paths
        assert "/api/chain-logs/{chain_id}" in paths
        assert "/api/agents/local-history" in paths
        assert "/api/agents/{agent}/local-history/sync" in paths
        assert "/api/adapters/{name}/install-verify" in paths
        assert "/api/governance/lifecycle-review" in paths
        assert "/api/governance/lifecycle-apply" in paths
        assert "/api/memory-lineage" in paths
        assert "/ws/events" in paths
        assert "/api/routes" in paths


class TestChainLogsAPI:
    def test_chain_logs_require_auth(self, client: TestClient):
        assert client.get("/api/chain-logs").status_code == 401
        assert client.get("/api/chain-logs/chain-missing").status_code == 401

    def test_chain_logs_list_and_detail_return_sanitized_read_model(
        self,
        client: TestClient,
        admin_token: str,
        brain_dir: Path,
    ):
        from agent_brain.agent_integrations.runtime_events import record_runtime_event
        from agent_brain.memory.context.injection_cohorts import record_injection_cohort
        from agent_brain.memory.store.items_store import ItemsStore

        item = MemoryItem(
            id="mem-20260706-010203-chain-api",
            type=MemoryType.artifact,
            created_at=datetime.now(timezone.utc),
            agent="codex",
            session="sess-chain-api",
            project="agent-memory-hub",
            tags=["chain-log", "api"],
            title="Chain log API item",
            summary="Verifies web chain-log detail",
        )
        ItemsStore(brain_dir / "items").write(
            item,
            "secret chain API body should not leak",
        )
        record_runtime_event(
            brain_dir,
            adapter="codex",
            event_name="UserPromptSubmit",
            session_id="sess-chain-api",
            cwd="/repo/agent-memory-hub",
        )
        record_injection_cohort(
            brain_dir,
            item_ids=[item.id],
            adapter="codex",
            session_id="sess-chain-api",
            cwd="/repo/agent-memory-hub",
            query="raw chain API query should not leak",
            source="search",
        )

        headers = {"Authorization": f"Bearer {admin_token}"}
        listed = client.get(
            "/api/chain-logs?hours=72&limit=20&adapter=codex&session_id=sess-chain-api&cwd=agent-memory-hub&status=injected",
            headers=headers,
        )

        assert listed.status_code == 200
        report = listed.json()
        assert report["filters"]["hours"] == 72
        assert report["filters"]["limit"] == 20
        assert report["summary"]["total_chains"] == 1
        chain = report["chains"][0]
        assert chain["adapter"] == "codex"
        assert chain["session_id"] == "sess-chain-api"
        assert chain["final_outcome"] == "injected"

        detail = client.get(f"/api/chain-logs/{chain['chain_id']}?hours=72", headers=headers)

        assert detail.status_code == 200
        payload = detail.json()
        serialized = json.dumps(payload)
        assert payload["chain_id"] == chain["chain_id"]
        assert payload["candidates"][0]["title"] == "Chain log API item"
        assert "raw chain API query should not leak" not in serialized
        assert "secret chain API body should not leak" not in serialized

    def test_chain_log_detail_returns_not_found(self, client: TestClient, admin_token: str):
        resp = client.get(
            "/api/chain-logs/chain-does-not-exist",
            headers={"Authorization": f"Bearer {admin_token}"},
        )

        assert resp.status_code == 404
        assert resp.json() == {"detail": "chain log not found"}

    def test_chain_log_detail_preserves_internal_key_errors(
        self,
        monkeypatch: pytest.MonkeyPatch,
        admin_token: str,
        brain_dir: Path,
    ):
        from web.api.routes import chain_logs
        from web.app import app

        def raise_internal_key_error(*_args, **_kwargs):
            raise KeyError("internal")

        monkeypatch.setattr(chain_logs, "build_chain_log_detail", raise_internal_key_error)
        client = TestClient(app, raise_server_exceptions=False)

        resp = client.get(
            "/api/chain-logs/chain-with-internal-error",
            headers={"Authorization": f"Bearer {admin_token}"},
        )

        assert resp.status_code == 500

    def test_chain_logs_are_admin_only(
        self,
        client: TestClient,
        admin_token: str,
        brain_dir: Path,
    ):
        from agent_brain.agent_integrations.runtime_events import record_runtime_event
        from agent_brain.memory.context.injection_cohorts import record_injection_cohort
        from agent_brain.memory.store.items_store import ItemsStore

        admin_headers = {"Authorization": f"Bearer {admin_token}"}
        client.post(
            "/api/auth/register",
            json={"username": "alice", "password": "pass123", "tenant_id": "team-a"},
            headers=admin_headers,
        )
        item = MemoryItem(
            id="mem-20260706-010203-chain-tenant-b",
            type=MemoryType.artifact,
            created_at=datetime.now(timezone.utc),
            tenant_id="team-b",
            agent="codex",
            session="sess-chain-tenant",
            title="Team B chain item",
            summary="Should not be visible to non-admin chain-log callers",
        )
        ItemsStore(brain_dir / "items").write(item, "team b chain body")
        record_runtime_event(
            brain_dir,
            adapter="codex",
            event_name="UserPromptSubmit",
            session_id="sess-chain-tenant",
            cwd="/repo/agent-memory-hub",
        )
        record_injection_cohort(
            brain_dir,
            item_ids=[item.id],
            adapter="codex",
            session_id="sess-chain-tenant",
            cwd="/repo/agent-memory-hub",
            query="team b raw query should not leak",
            source="search",
        )
        chain_id = client.get(
            "/api/chain-logs?hours=72&limit=20&session_id=sess-chain-tenant",
            headers=admin_headers,
        ).json()["chains"][0]["chain_id"]
        login = client.post("/api/auth/login", json={"username": "alice", "password": "pass123"})
        user_headers = {"Authorization": f"Bearer {login.json()['token']}"}

        listed = client.get("/api/chain-logs?hours=72&limit=20", headers=user_headers)
        detail = client.get(f"/api/chain-logs/{chain_id}?hours=72", headers=user_headers)

        assert listed.status_code == 403
        assert detail.status_code == 403


class TestMemoryLineageAPI:
    def test_memory_lineage_returns_traceable_read_model(
        self,
        client: TestClient,
        admin_token: str,
        brain_dir: Path,
    ):
        from agent_brain.memory.context.injection_cohorts import record_injection_cohort

        record_injection_cohort(
            brain_dir,
            item_ids=["mem-20260623-010203-lineage-demo"],
            adapter="codex",
            session_id="sess-lineage",
            query="raw query should not leak",
        )

        resp = client.get(
            "/api/memory-lineage?hours=72&limit=50",
            headers={"Authorization": f"Bearer {admin_token}"},
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["summary"]["storage_counts"]["items"] == 0
        assert "agent_activity" in data
        assert "memory_activity" in data
        assert any(event["mode"] == "recall" for event in data["events"])
        assert any(formula["key"] == "hopfield" for formula in data["formulas"])
        assert any(formula["key"] == "decay_coefficient" for formula in data["formulas"])
        assert any(formula["key"] == "maturity_score" for formula in data["formulas"])
        assert any(event["kind"] == "load" for event in data["events"])
        assert "raw query should not leak" not in json.dumps(data)

    def test_data_flow_returns_three_day_read_model(
        self,
        client: TestClient,
        admin_token: str,
        brain_dir: Path,
    ):
        from datetime import timedelta

        from agent_brain.agent_integrations.runtime_events import record_runtime_event
        from agent_brain.memory.context.injection_cohorts import record_injection_cohort

        now = datetime.now(timezone.utc)
        record_runtime_event(
            brain_dir,
            adapter="codex",
            event_name="UserPromptSubmit",
            session_id="sess-flow",
            now=now - timedelta(minutes=2),
        )
        record_injection_cohort(
            brain_dir,
            item_ids=["mem-a"],
            query="do not leak this query",
            adapter="codex",
            session_id="sess-flow",
            now=now - timedelta(minutes=1),
        )

        resp = client.get(
            "/api/data-flow?hours=72&limit=10",
            headers={"Authorization": f"Bearer {admin_token}"},
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["window_hours"] == 72
        assert data["summary"]["total"] == 2
        assert [event["source"] for event in data["events"]] == [
            "injection",
            "adapter_runtime",
        ]
        assert "do not leak" not in json.dumps(data, ensure_ascii=False)


class TestProductCapabilitiesAPI:
    def test_product_capability_routes_require_auth(self, client: TestClient):
        assert client.get("/api/headroom/status").status_code == 401
        assert client.get("/api/headroom/retrieve/missing").status_code == 401
        assert client.post("/api/compression-gate", json={}).status_code == 401
        assert client.post("/api/ml-advisory-gate", json={}).status_code == 401
        assert client.get("/api/hierarchical-memory").status_code == 401
        assert client.post("/api/memory-profiles/export", json={}).status_code == 401

    def test_headroom_compress_and_retrieve_local_ccr(
        self,
        client: TestClient,
        admin_token: str,
        monkeypatch: pytest.MonkeyPatch,
    ):
        monkeypatch.setenv("MEMORY_HUB_HEADROOM_EXTERNAL", "0")
        headers = {"Authorization": f"Bearer {admin_token}"}
        original = "\n".join(f"logs/app.log:{i}:ERROR failure {i}" for i in range(40))

        compressed = client.post(
            "/api/headroom/compress",
            json={"text": original, "budget_chars": 220, "query": "failure"},
            headers=headers,
        )
        assert compressed.status_code == 200
        payload = compressed.json()
        retrieved = client.get(
            f"/api/headroom/retrieve/{payload['ccr_key']}",
            headers=headers,
        )

        assert payload["provider"] == "amh-local"
        assert payload["strategy"] == "search_topn"
        assert payload["ccr_key"]
        assert retrieved.status_code == 200
        assert retrieved.json()["text"] == original

    def test_compression_gate_route_runs_builtin_fewshot_suite(
        self,
        client: TestClient,
        admin_token: str,
        monkeypatch: pytest.MonkeyPatch,
    ):
        monkeypatch.setenv("MEMORY_HUB_HEADROOM_EXTERNAL", "0")
        headers = {"Authorization": f"Bearer {admin_token}"}

        response = client.post("/api/compression-gate", json={}, headers=headers)

        assert response.status_code == 200
        payload = response.json()
        assert payload["passed"] is True
        assert payload["metrics"]["num_cases"] >= 4
        assert payload["metrics"]["pass_rate"] == 1.0

    def test_ml_advisory_gate_route_runs_builtin_fewshot_suite(
        self,
        client: TestClient,
        admin_token: str,
    ):
        headers = {"Authorization": f"Bearer {admin_token}"}

        response = client.post("/api/ml-advisory-gate", json={}, headers=headers)

        assert response.status_code == 200
        payload = response.json()
        assert payload["passed"] is True
        assert payload["metrics"]["num_cases"] >= 4
        assert payload["metrics"]["unsafe_promotion_count"] == 0

    def test_profile_preview_is_readable_but_apply_is_admin_only(
        self,
        client: TestClient,
        admin_token: str,
        seed_items,
    ):
        admin_headers = {"Authorization": f"Bearer {admin_token}"}
        client.post(
            "/api/auth/register",
            json={"username": "alice", "password": "pass123", "tenant_id": "default"},
            headers=admin_headers,
        )
        login = client.post("/api/auth/login", json={"username": "alice", "password": "pass123"})
        user_headers = {"Authorization": f"Bearer {login.json()['token']}"}

        preview = client.post(
            "/api/memory-profiles/export",
            json={"target": "codex", "apply": False},
            headers=user_headers,
        )
        denied = client.post(
            "/api/memory-profiles/export",
            json={"target": "codex", "apply": True},
            headers=user_headers,
        )
        applied = client.post(
            "/api/memory-profiles/export",
            json={"target": "codex", "apply": True},
            headers=admin_headers,
        )

        assert preview.status_code == 200
        assert preview.json()["applied"] is False
        assert denied.status_code == 403
        assert applied.status_code == 200
        assert applied.json()["applied"] is True

    def test_hierarchy_preview_is_readable_but_apply_is_admin_only(
        self,
        client: TestClient,
        admin_token: str,
        seed_items,
    ):
        admin_headers = {"Authorization": f"Bearer {admin_token}"}
        client.post(
            "/api/auth/register",
            json={"username": "bob", "password": "pass123", "tenant_id": "default"},
            headers=admin_headers,
        )
        login = client.post("/api/auth/login", json={"username": "bob", "password": "pass123"})
        user_headers = {"Authorization": f"Bearer {login.json()['token']}"}

        preview = client.post(
            "/api/hierarchical-memory/build",
            json={"apply": False},
            headers=user_headers,
        )
        denied = client.post(
            "/api/hierarchical-memory/build",
            json={"apply": True},
            headers=user_headers,
        )
        applied = client.post(
            "/api/hierarchical-memory/build",
            json={"apply": True},
            headers=admin_headers,
        )

        assert preview.status_code == 200
        assert preview.json()["applied"] is False
        assert denied.status_code == 403
        assert applied.status_code == 200
        assert applied.json()["applied"] is True


class TestAuth:
    def test_init_admin(self, client: TestClient, brain_dir: Path):
        resp = client.post("/api/auth/init", json={"username": "admin", "password": "secret"})
        assert resp.status_code == 200
        data = resp.json()
        assert "token" in data
        assert data["username"] == "admin"

    def test_init_admin_twice_fails(self, client: TestClient, admin_token: str):
        resp = client.post("/api/auth/init", json={"username": "admin2", "password": "x"})
        assert resp.status_code == 409

    def test_login(self, client: TestClient, admin_token: str):
        resp = client.post("/api/auth/login", json={"username": "admin", "password": "test123"})
        assert resp.status_code == 200
        assert "token" in resp.json()

    def test_login_wrong_password(self, client: TestClient, admin_token: str):
        resp = client.post("/api/auth/login", json={"username": "admin", "password": "wrong"})
        assert resp.status_code == 401


class TestAdapterCapabilitiesAPI:
    def test_adapter_capabilities_requires_auth(self, client: TestClient):
        resp = client.get("/api/adapters/capabilities")
        assert resp.status_code == 401

    def test_adapter_capabilities_uses_truth_contract_records(self, client: TestClient, admin_token: str):
        resp = client.get(
            "/api/adapters/capabilities",
            headers={"Authorization": f"Bearer {admin_token}"},
        )

        assert resp.status_code == 200
        data = resp.json()
        by_name = {row["name"]: row for row in data}

        assert len(by_name) == 16
        assert "qoder_wake" not in by_name
        assert by_name["codex"]["support_level"] == "install-ready"
        assert by_name["codex"]["verified"] is False
        assert by_name["codex"]["verification_status"] == "not_verified"
        assert by_name["codex"]["verification_blockers"] == [
            "evidence level is install-ready, not verified",
            "runtime event not observed",
        ]
        assert by_name["codex"]["evidence_paths"] == [
            "tests/unit/test_adapters.py",
            "tests/unit/test_cli_adapter.py",
            "agent_brain/agent_integrations/codex.py",
            "agent_brain/agent_integrations/codex_hooks.py",
            "agent_brain/agent_integrations/codex_diagnostics.py",
        ]
        assert by_name["codex"]["memory_boundary"]["amh_role"] == "shared_truth_source"
        assert by_name["codex"]["memory_boundary"]["native_memory_role"] == "candidate_hint"
        assert by_name["codex"]["memory_boundary"]["native_memory_observed"] is False
        assert by_name["codex"]["memory_boundary"]["last_injection"] == {"observed": False}
        assert by_name["codex"]["memory_boundary"]["priority_order"].index("amh_memory_item") < (
            by_name["codex"]["memory_boundary"]["priority_order"].index("agent_native_memory")
        )
        assert by_name["qoder"]["support_level"] == "install-ready"
        assert by_name["qoder"]["evidence_paths"] == [
            "tests/unit/test_adapters.py",
            "tests/unit/test_cli_adapter.py",
            "agent_brain/agent_integrations/qoder.py",
            "agent_brain/agent_integrations/qoder_diagnostics.py",
        ]
        assert by_name["continue_dev"]["support_level"] == "install-ready"
        assert by_name["continue_dev"]["evidence_paths"] == [
            "tests/unit/test_adapters.py",
            "tests/unit/test_cli_adapter.py",
            "agent_brain/agent_integrations/continue_dev.py",
        ]
        assert by_name["github_copilot"]["support_level"] == "install-ready"
        assert by_name["github_copilot"]["evidence_paths"] == [
            "tests/unit/test_adapters.py",
            "tests/unit/test_cli_adapter.py",
            "agent_brain/agent_integrations/github_copilot.py",
        ]
        assert by_name["aone_copilot"]["support_level"] == "install-ready"
        assert by_name["aone_copilot"]["evidence_paths"] == [
            "tests/unit/test_adapters.py",
            "/Applications/IntelliJ IDEA Ultimate.app",
        ]
        assert by_name["openclaw"]["support_level"] == "install-ready"
        assert by_name["hermes_agent"]["support_level"] == "install-ready"
        assert by_name["opensquilla"]["support_level"] == "install-ready"
        assert by_name["wukong"]["support_level"] == "install-ready"
        assert by_name["openhuman"]["support_level"] == "install-ready"
        assert by_name["openhuman"]["evidence_paths"] == [
            "tests/unit/test_adapters.py",
            "https://github.com/tinyhumansai/openhuman",
        ]
        assert by_name["qoder_work"]["support_level"] == "install-ready"
        assert by_name["qoder_work"]["evidence_paths"] == [
            "tests/unit/test_adapters.py",
            "tests/unit/test_cli_adapter.py",
            "QoderWork built-in guide-mcp.md",
        ]
        assert by_name["mulerun"]["support_level"] == "wip"

    def test_no_token_returns_401(self, client: TestClient):
        resp = client.get("/api/items")
        assert resp.status_code == 401

    def test_api_key_auth(self, client: TestClient, brain_dir: Path):
        resp = client.post("/api/auth/init", json={"username": "apitest", "password": "test123"})
        assert resp.status_code == 200
        api_key = resp.json().get("api_key", "")
        assert api_key.startswith("mhk_")
        resp2 = client.get("/api/items", headers={"X-API-Key": api_key})
        assert resp2.status_code == 200

    def test_invalid_api_key_returns_401(self, client: TestClient, brain_dir: Path):
        resp = client.get("/api/items", headers={"X-API-Key": "mhk_invalid_key"})
        assert resp.status_code == 401

    def test_rotate_api_key(self, client: TestClient, admin_token: str, brain_dir: Path):
        headers = {"Authorization": f"Bearer {admin_token}"}
        resp = client.post("/api/auth/rotate-key", headers=headers)
        assert resp.status_code == 200
        new_key = resp.json()["api_key"]
        assert new_key.startswith("mhk_")
        resp2 = client.get("/api/auth/me", headers={"X-API-Key": new_key})
        assert resp2.status_code == 200
        assert resp2.json()["username"] == "admin"

    def test_get_me(self, client: TestClient, admin_token: str):
        headers = {"Authorization": f"Bearer {admin_token}"}
        resp = client.get("/api/auth/me", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["username"] == "admin"
        assert data["is_admin"] is True


class TestItems:
    def test_list_items(self, client: TestClient, admin_token: str, seed_items):
        resp = client.get("/api/items", headers={"Authorization": f"Bearer {admin_token}"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 3

    def test_list_items_filter_type(self, client: TestClient, admin_token: str, seed_items):
        resp = client.get("/api/items?type=fact", headers={"Authorization": f"Bearer {admin_token}"})
        data = resp.json()
        assert all(it["type"] == "fact" for it in data["items"])

    def test_get_item(self, client: TestClient, admin_token: str, seed_items):
        resp = client.get("/api/items/mem-20260101-000000-test-fact", headers={"Authorization": f"Bearer {admin_token}"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["item"]["title"] == "Python GIL behavior"
        assert "Body content" in data["body"]

    def test_get_item_supports_bounded_detail_without_breaking_full_read(
        self,
        client: TestClient,
        admin_token: str,
        seed_items,
    ):
        headers = {"Authorization": f"Bearer {admin_token}"}
        item_id = "mem-20260101-000000-test-fact"

        full = client.get(f"/api/items/{item_id}", headers=headers)
        bounded = client.get(
            f"/api/items/{item_id}?head=4&view=detail",
            headers=headers,
        )

        assert full.status_code == 200
        assert bounded.status_code == 200
        full_data = full.json()
        bounded_data = bounded.json()
        assert bounded_data["body"] == full_data["body"][:4]
        assert bounded_data["body_truncated"] is True
        assert bounded_data["full_chars"] == len(full_data["body"])

    def test_get_item_not_found(self, client: TestClient, admin_token: str, brain_dir):
        resp = client.get("/api/items/nonexistent", headers={"Authorization": f"Bearer {admin_token}"})
        assert resp.status_code == 404

    def test_delete_item(self, client: TestClient, admin_token: str, seed_items):
        resp = client.delete("/api/items/mem-20260101-000000-test-fact", headers={"Authorization": f"Bearer {admin_token}"})
        assert resp.status_code == 200
        assert resp.json()["deleted"] == "mem-20260101-000000-test-fact"
        resp2 = client.get("/api/items", headers={"Authorization": f"Bearer {admin_token}"})
        assert resp2.json()["total"] == 2

    def test_patch_item(self, client: TestClient, admin_token: str, seed_items):
        resp = client.patch(
            "/api/items/mem-20260101-000001-test-decision",
            json={"title": "Updated Title", "confidence": 0.5},
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert resp.status_code == 200
        assert set(resp.json()["updated_fields"]) == {"title", "confidence"}


class TestSemanticSearch:
    def test_search_items_response_shape(self, client: TestClient, admin_token: str, seed_items):
        headers = {"Authorization": f"Bearer {admin_token}"}
        resp = client.get("/api/search?q=Python", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["query"] == "Python"
        assert isinstance(data["results"], list)

    def test_auto_search_uses_compact_snippet_and_preserves_explicit_detail(
        self,
        client: TestClient,
        admin_token: str,
        brain_dir: Path,
    ):
        from agent_brain.contracts.memory_item import Refs
        from web._base import _components, _components_cache

        _components_cache.clear()
        store, idx, _retriever, embedder = _components()
        item = MemoryItem(
            id="mem-20260715-101010-web-staged-recall",
            type=MemoryType.artifact,
            created_at=datetime.now(timezone.utc),
            title="Web staged recall",
            summary="web staged locator",
            abstraction="L0",
            refs=Refs(files=["/tmp/web-staged.log"]),
            context_views={
                "locator": "web staged locator",
                "overview": "web staged overview",
                "detail_uri": "memory://items/mem-20260715-101010-web-staged-recall/body",
            },
        )
        body = "web detail-only marker"
        store.write(item, body)
        idx.upsert(item, body, embedding=embedder.embed(item.context_views.locator))
        headers = {"Authorization": f"Bearer {admin_token}"}

        auto = client.get(
            "/api/search?q=Web%20staged%20recall&top_k=5&verbosity=auto",
            headers=headers,
        )
        detail = client.get(
            "/api/search?q=Web%20staged%20recall&top_k=5&verbosity=detail",
            headers=headers,
        )

        assert auto.status_code == 200
        assert detail.status_code == 200
        auto_result = auto.json()["results"][0]
        detail_data = detail.json()
        assert auto_result["id"] == item.id
        assert "web detail-only marker" not in auto_result["snippet"]
        assert "web detail-only marker" not in auto_result["context_pack"]["text"]
        assert "web detail-only marker" in detail_data["results"][0]["context_pack"]["text"]
        assert detail_data["diagnostics"]["governance_warnings"]

    def test_search_can_return_trace_context_firewall_and_resource_context(
        self,
        client: TestClient,
        admin_token: str,
        brain_dir: Path,
    ):
        from agent_brain.contracts.memory_item import Refs
        from agent_brain.contracts.resource import (
            ExtractionKind,
            ExtractionRecord,
            ResourceKind,
            ResourceRecord,
            make_extraction_id,
            make_resource_id,
            sha256_text,
        )
        from agent_brain.memory.evidence.resource_store import ResourceStore
        from web._base import _components, _components_cache

        _components_cache.clear()
        resource_store = ResourceStore(brain_dir)
        resource = ResourceRecord(
            id=make_resource_id("Resource Audit PDF"),
            kind=ResourceKind.pdf,
            uri="file:///tmp/resource-audit.pdf",
            title="Resource Audit PDF",
            project="alpha",
            tags=["resource-audit"],
        )
        extraction = ExtractionRecord(
            id=make_extraction_id("Resource Audit Summary"),
            resource_id=resource.id,
            kind=ExtractionKind.summary,
            extractor="pytest",
            content_text="Resource audit summary contains progressive evidence.",
            content_sha256=sha256_text("Resource audit summary contains progressive evidence."),
            confidence=0.9,
        )
        resource_store.write_resource(resource)
        resource_store.write_extraction(extraction)

        store, idx, _retriever, embedder = _components()
        item = MemoryItem(
            id="mem-20260621-101010-resource-audit",
            type=MemoryType.fact,
            created_at=datetime.now(timezone.utc),
            title="Resource audit memory",
            summary="resource audit locator progressive evidence",
            project="alpha",
            refs=Refs(resources=[resource.id], extractions=[extraction.id]),
            context_views={
                "locator": "resource audit locator progressive evidence",
                "overview": "resource audit overview from resource sidecar",
                "detail_uri": "memory://items/mem-20260621-101010-resource-audit/body",
            },
        )
        body = "Resource audit body with progressive evidence."
        store.write(item, body)
        idx.upsert(item, body, embedding=embedder.embed(item.context_views.locator))

        resp = client.get(
            "/api/search?"
            "q=resource%20audit%20progressive%20evidence"
            "&include_trace=true&verbosity=auto&context_firewall=true&include_resources=true",
            headers={"Authorization": f"Bearer {admin_token}"},
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["diagnostics"]["context_firewall"] is True
        assert data["diagnostics"]["resource_sidecar"] is True
        assert data["resource_results"][0]["id"] == resource.id
        result = data["results"][0]
        assert result["id"] == item.id
        assert result["context_pack"]["detail_uri"].endswith("/body")
        assert result["retrieval_trace"]["final_rank"] == 1
        assert result["firewall"]["action"] in {"include", "demote"}
        assert result["resource_context"][0]["resource_id"] == resource.id


class TestStats:
    def test_stats(self, client: TestClient, admin_token: str, seed_items):
        resp = client.get("/api/stats", headers={"Authorization": f"Bearer {admin_token}"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 3
        assert "fact" in data["by_type"]
        assert "alpha" in data["by_project"]


class TestGC:
    def test_gc_dry_run(self, client: TestClient, admin_token: str, seed_items):
        resp = client.post(
            "/api/gc",
            json={"max_age_days": 0, "tags": ["test"], "dry_run": True},
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["dry_run"] is True
        assert data["deleted"] == 0
        assert len(data["candidates"]) == 3


class TestLifecycleGovernanceAPI:
    def test_lifecycle_review_requires_admin(self, client: TestClient):
        assert client.get("/api/governance/lifecycle-review").status_code == 401

    def test_lifecycle_review_lists_read_only_queue(
        self,
        client: TestClient,
        admin_token: str,
        brain_dir: Path,
    ):
        from agent_brain.memory.store.items_store import ItemsStore

        store = ItemsStore(items_dir=brain_dir / "items")
        item = MemoryItem(
            id="mem-20260101-170101-web-lifecycle-review",
            type=MemoryType.signal,
            created_at=datetime.now(timezone.utc) - timedelta(days=60),
            title="Web lifecycle signal",
            summary="Web lifecycle signal summary",
            tags=["runtime"],
        )
        store.write(item, "Web lifecycle signal\nbody")

        resp = client.get(
            "/api/governance/lifecycle-review?limit=5",
            headers={"Authorization": f"Bearer {admin_token}"},
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["dry_run"] is True
        assert data["filters"] == {"action": None, "category": "lifecycle"}
        assert data["review_queue"] == [
            {
                "item_id": item.id,
                "action": "review_archive",
                "category": "lifecycle",
                "title": "Review stale signal: Web lifecycle signal",
                "read_command": f"memory read {item.id} --head 2000 --view detail",
                "recommended_next": "supersede_or_archive_after_review",
                "can_auto_apply": False,
                "boundary": "确认是否已有更新 item 可以 supersede，不能确认再 archive",
            }
        ]
        assert (brain_dir / "items" / f"{item.id}.md").exists()

    def test_lifecycle_apply_defaults_to_dry_run(
        self,
        client: TestClient,
        admin_token: str,
        brain_dir: Path,
    ):
        from agent_brain.memory.store.items_store import ItemsStore

        store = ItemsStore(items_dir=brain_dir / "items")
        item = MemoryItem(
            id="mem-20260101-170102-web-lifecycle-dry-run",
            type=MemoryType.handoff,
            created_at=datetime.now(timezone.utc) - timedelta(days=45),
            title="Web lifecycle dry run handoff",
            summary="Web lifecycle dry run handoff summary",
            tags=["handoff"],
        )
        store.write(item, "Web lifecycle dry run handoff\nbody")

        resp = client.post(
            "/api/governance/lifecycle-apply",
            json={"item_ids": [item.id]},
            headers={"Authorization": f"Bearer {admin_token}"},
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["dry_run"] is True
        assert data["requested"] == [item.id]
        assert data["archived"] == []
        assert data["skipped"] == []
        assert data["candidates"][0]["item_id"] == item.id
        assert (brain_dir / "items" / f"{item.id}.md").exists()
        assert not (brain_dir / "items" / "archived" / f"{item.id}.md").exists()

    def test_lifecycle_apply_archives_only_current_queue_items(
        self,
        client: TestClient,
        admin_token: str,
        brain_dir: Path,
    ):
        from agent_brain.memory.store.items_store import ItemsStore

        store = ItemsStore(items_dir=brain_dir / "items")
        stale = MemoryItem(
            id="mem-20260101-170103-web-lifecycle-apply",
            type=MemoryType.signal,
            created_at=datetime.now(timezone.utc) - timedelta(days=60),
            title="Web lifecycle apply signal",
            summary="Web lifecycle apply signal summary",
            tags=["runtime"],
        )
        fresh = MemoryItem(
            id="mem-20260701-170104-web-lifecycle-fresh",
            type=MemoryType.signal,
            created_at=datetime.now(timezone.utc) - timedelta(days=3),
            title="Web lifecycle fresh signal",
            summary="Web lifecycle fresh signal summary",
            tags=["runtime"],
        )
        store.write(stale, "Web lifecycle apply signal\nbody")
        store.write(fresh, "Web lifecycle fresh signal\nbody")

        resp = client.post(
            "/api/governance/lifecycle-apply",
            json={"item_ids": [stale.id, fresh.id], "apply": True, "index_repair": False},
            headers={"Authorization": f"Bearer {admin_token}"},
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["dry_run"] is False
        assert data["archived"] == [stale.id]
        assert data["skipped"] == [
            {
                "id": fresh.id,
                "reason": "not_in_lifecycle_review_queue",
            }
        ]
        assert not (brain_dir / "items" / f"{stale.id}.md").exists()
        assert (brain_dir / "items" / "archived" / f"{stale.id}.md").exists()
        assert (brain_dir / "items" / f"{fresh.id}.md").exists()


class TestBatchOps:
    def test_batch_delete(self, client: TestClient, admin_token: str, seed_items):
        headers = {"Authorization": f"Bearer {admin_token}"}
        ids = [s.id for s in seed_items[:2]]
        resp = client.post("/api/items/batch-delete", json={"ids": ids}, headers=headers)
        assert resp.status_code == 200
        assert resp.json()["deleted"] == 2

    def test_batch_confirm(self, client: TestClient, admin_token: str, seed_items):
        headers = {"Authorization": f"Bearer {admin_token}"}
        ids = [s.id for s in seed_items]
        resp = client.post("/api/items/batch-confirm", json={"ids": ids, "confidence": 0.95}, headers=headers)
        assert resp.status_code == 200
        assert resp.json()["confirmed"] == 3


class TestExportImport:
    def test_export(self, client: TestClient, admin_token: str, seed_items):
        headers = {"Authorization": f"Bearer {admin_token}"}
        resp = client.get("/api/export", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 3
        assert len(data["items"]) == 3
        assert "frontmatter" in data["items"][0]
        assert "body" in data["items"][0]

    def test_export_filter(self, client: TestClient, admin_token: str, seed_items):
        headers = {"Authorization": f"Bearer {admin_token}"}
        resp = client.get("/api/export?type=fact", headers=headers)
        assert resp.json()["count"] == 1

    def test_import(self, client: TestClient, admin_token: str, brain_dir: Path):
        headers = {"Authorization": f"Bearer {admin_token}"}
        items_to_import = [{
            "frontmatter": {
                "id": "mem-20260201-000000-imported",
                "type": "fact", "title": "Imported fact",
                "summary": "From import", "created_at": "2026-02-01T00:00:00Z",
            },
            "body": "Imported body",
        }]
        resp = client.post("/api/import", json={"items": items_to_import}, headers=headers)
        assert resp.status_code == 200
        assert resp.json()["imported"] == 1

    def test_import_skip_existing(self, client: TestClient, admin_token: str, seed_items):
        headers = {"Authorization": f"Bearer {admin_token}"}
        export = client.get("/api/export", headers=headers).json()
        resp = client.post("/api/import", json={"items": export["items"], "overwrite": False}, headers=headers)
        assert resp.json()["skipped"] == 3
        assert resp.json()["imported"] == 0


class TestHealth:
    def test_health_no_auth(self, client: TestClient, brain_dir: Path):
        resp = client.get("/api/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert "version" in data

    def test_version(self, client: TestClient):
        resp = client.get("/api/version")
        assert resp.status_code == 200
        assert "version" in resp.json()


class TestMaintenanceRoutes:
    def test_decay_status(self, client: TestClient, admin_token: str, seed_items):
        headers = {"Authorization": f"Bearer {admin_token}"}
        resp = client.get("/api/decay-status", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 3
        assert "effective" in data["items"][0]

    def test_reindex(self, client: TestClient, admin_token: str, seed_items):
        headers = {"Authorization": f"Bearer {admin_token}"}
        resp = client.post("/api/reindex", headers=headers)
        assert resp.status_code == 200
        assert resp.json()["reindexed"] == 3

    def test_obsidian_export(self, client: TestClient, admin_token: str, seed_items, tmp_path: Path):
        headers = {"Authorization": f"Bearer {admin_token}"}
        vault = tmp_path / "vault"
        resp = client.post(
            "/api/obsidian/export",
            json={"vault_path": str(vault)},
            headers=headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["exported"] == 3
        assert data["vault_path"] == str(vault)

    def test_obsidian_import_missing_vault(self, client: TestClient, admin_token: str, tmp_path: Path):
        headers = {"Authorization": f"Bearer {admin_token}"}
        resp = client.post(
            "/api/obsidian/import",
            json={"vault_path": str(tmp_path / "missing")},
            headers=headers,
        )
        assert resp.status_code == 404


class TestDashboard:
    def test_dashboard_html(self, client: TestClient):
        resp = client.get("/")
        assert resp.status_code == 200
        assert "Agent Memory Hub" in resp.text


class TestUserManagement:
    def test_list_users(self, client: TestClient, admin_token: str):
        resp = client.get("/api/auth/users", headers={"Authorization": f"Bearer {admin_token}"})
        assert resp.status_code == 200
        users = resp.json()["users"]
        assert len(users) == 1
        assert users[0]["username"] == "admin"
        assert users[0]["role"] == "admin"

    def test_register_and_list(self, client: TestClient, admin_token: str):
        headers = {"Authorization": f"Bearer {admin_token}"}
        resp = client.post("/api/auth/register", json={"username": "alice", "password": "pass123", "tenant_id": "team-a"}, headers=headers)
        assert resp.status_code == 200
        resp2 = client.get("/api/auth/users", headers=headers)
        assert len(resp2.json()["users"]) == 2

    def test_register_duplicate_fails(self, client: TestClient, admin_token: str):
        headers = {"Authorization": f"Bearer {admin_token}"}
        client.post("/api/auth/register", json={"username": "bob", "password": "x"}, headers=headers)
        resp = client.post("/api/auth/register", json={"username": "bob", "password": "y"}, headers=headers)
        assert resp.status_code == 409


class TestTenantIsolation:
    def test_non_admin_sees_only_own_tenant(self, client: TestClient, admin_token: str, brain_dir: Path):
        headers = {"Authorization": f"Bearer {admin_token}"}
        client.post("/api/auth/register", json={"username": "alice", "password": "pass", "tenant_id": "team-a"}, headers=headers)

        from agent_brain.memory.store.items_store import ItemsStore
        store = ItemsStore(items_dir=brain_dir / "items")
        item_a = MemoryItem(
            id="mem-20260101-000010-tenant-a",
            type=MemoryType("fact"), title="Team A fact", summary="For team A",
            tenant_id="team-a", created_at=datetime.now(timezone.utc),
        )
        item_b = MemoryItem(
            id="mem-20260101-000011-tenant-b",
            type=MemoryType("fact"), title="Team B fact", summary="For team B",
            tenant_id="team-b", created_at=datetime.now(timezone.utc),
        )
        store.write(item_a, "A content")
        store.write(item_b, "B content")

        login_resp = client.post("/api/auth/login", json={"username": "alice", "password": "pass"})
        alice_token = login_resp.json()["token"]
        alice_headers = {"Authorization": f"Bearer {alice_token}"}

        resp = client.get("/api/items", headers=alice_headers)
        items = resp.json()["items"]
        ids = [it["id"] for it in items]
        assert "mem-20260101-000010-tenant-a" in ids
        assert "mem-20260101-000011-tenant-b" not in ids

    def test_admin_sees_all_tenants(self, client: TestClient, admin_token: str, brain_dir: Path):
        from agent_brain.memory.store.items_store import ItemsStore
        store = ItemsStore(items_dir=brain_dir / "items")
        for tid in ["x", "y"]:
            item = MemoryItem(
                id=f"mem-20260101-000020-tenant-{tid}",
                type=MemoryType("fact"), title=f"Fact {tid}", summary="s",
                tenant_id=tid, created_at=datetime.now(timezone.utc),
            )
            store.write(item, f"body {tid}")

        headers = {"Authorization": f"Bearer {admin_token}"}
        resp = client.get("/api/items", headers=headers)
        ids = [it["id"] for it in resp.json()["items"]]
        assert "mem-20260101-000020-tenant-x" in ids
        assert "mem-20260101-000020-tenant-y" in ids


class TestCreateItem:
    def test_create_item(self, client: TestClient, admin_token: str, brain_dir: Path):
        headers = {"Authorization": f"Bearer {admin_token}"}
        resp = client.post("/api/items", json={
            "type": "fact",
            "title": "Test create via API",
            "summary": "Created from web admin",
            "body": "Some body content",
            "tags": ["web-created"],
            "project": "test-proj",
        }, headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"].startswith("mem-")
        assert "path" in data

    def test_create_item_blocks_critical_audit_finding(
        self,
        client: TestClient,
        admin_token: str,
        brain_dir: Path,
    ):
        headers = {"Authorization": f"Bearer {admin_token}"}
        resp = client.post("/api/items", json={
            "type": "fact",
            "title": "private key recipe",
            "summary": "unsafe",
            "body": "-----BEGIN " + "RSA PRIVATE KEY-----",
        }, headers=headers)

        assert resp.status_code == 400
        assert resp.json()["detail"]["status"] == "blocked"
        assert not list((brain_dir / "items").glob("*.md"))

    def test_create_item_then_list(self, client: TestClient, admin_token: str, brain_dir: Path):
        headers = {"Authorization": f"Bearer {admin_token}"}
        client.post("/api/items", json={
            "type": "decision",
            "title": "Test decision",
            "summary": "A decision",
        }, headers=headers)
        resp = client.get("/api/items", headers=headers)
        assert resp.json()["total"] >= 1

    def test_create_item_missing_fields(self, client: TestClient, admin_token: str, brain_dir: Path):
        headers = {"Authorization": f"Bearer {admin_token}"}
        resp = client.post("/api/items", json={"type": "fact"}, headers=headers)
        assert resp.status_code == 422


class TestRetentionTouch:
    def test_touch_updates_access(self, client: TestClient, admin_token: str, seed_items):
        headers = {"Authorization": f"Bearer {admin_token}"}
        resp = client.post("/api/items/mem-20260101-000000-test-fact/touch", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["access_count"] == 1
        assert "last_accessed" in data

    def test_touch_increments(self, client: TestClient, admin_token: str, seed_items):
        headers = {"Authorization": f"Bearer {admin_token}"}
        client.post("/api/items/mem-20260101-000000-test-fact/touch", headers=headers)
        resp = client.post("/api/items/mem-20260101-000000-test-fact/touch", headers=headers)
        assert resp.json()["access_count"] == 2

    def test_touch_not_found(self, client: TestClient, admin_token: str, brain_dir):
        headers = {"Authorization": f"Bearer {admin_token}"}
        resp = client.post("/api/items/nonexistent/touch", headers=headers)
        assert resp.status_code == 404


class TestActivity:
    def test_activity_timeline(self, client: TestClient, admin_token: str, seed_items):
        headers = {"Authorization": f"Bearer {admin_token}"}
        resp = client.get("/api/activity?days=30", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert "timeline" in data
        assert "type_totals" in data
        assert "recent" in data
        assert len(data["recent"]) == 3

    def test_activity_type_totals(self, client: TestClient, admin_token: str, seed_items):
        headers = {"Authorization": f"Bearer {admin_token}"}
        data = client.get("/api/activity?days=365", headers=headers).json()
        assert "fact" in data["type_totals"]
        assert data["type_totals"]["fact"] == 1


class TestHealthDetail:
    def test_health_detail(self, client: TestClient, admin_token: str, seed_items):
        headers = {"Authorization": f"Bearer {admin_token}"}
        resp = client.get("/api/health-detail", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert "grade" in data
        assert "total_items" in data
        assert data["grade"] in ("A", "B", "C", "D", "?")

    def test_health_detail_governance(self, client: TestClient, admin_token: str, seed_items):
        headers = {"Authorization": f"Bearer {admin_token}"}
        data = client.get("/api/health-detail", headers=headers).json()
        if data["governance"]:
            assert "total_issues" in data["governance"]
            assert "duplicates" in data["governance"]


class TestBackup:
    def test_create_backup(self, client: TestClient, admin_token: str, seed_items):
        headers = {"Authorization": f"Bearer {admin_token}"}
        resp = client.post("/api/backup", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["items"] == 3
        assert "timestamp" in data

    def test_list_backups(self, client: TestClient, admin_token: str, seed_items):
        headers = {"Authorization": f"Bearer {admin_token}"}
        client.post("/api/backup", headers=headers)
        resp = client.get("/api/backups", headers=headers)
        assert resp.status_code == 200
        assert len(resp.json()["backups"]) >= 1


class TestBodyEdit:
    def test_update_body(self, client: TestClient, admin_token: str, seed_items):
        headers = {"Authorization": f"Bearer {admin_token}"}
        resp = client.put(
            "/api/items/mem-20260101-000000-test-fact/body",
            json={"body": "Updated body content here"},
            headers=headers,
        )
        assert resp.status_code == 200
        assert resp.json()["body_length"] == len("Updated body content here")
        detail = client.get("/api/items/mem-20260101-000000-test-fact", headers=headers).json()
        assert "Updated body content" in detail["body"]

    def test_update_body_not_found(self, client: TestClient, admin_token: str, brain_dir):
        headers = {"Authorization": f"Bearer {admin_token}"}
        resp = client.put("/api/items/nonexistent/body", json={"body": "x"}, headers=headers)
        assert resp.status_code == 404


class TestEvolve:
    def test_evolve_preview(self, client: TestClient, admin_token: str, seed_items):
        headers = {"Authorization": f"Bearer {admin_token}"}
        resp = client.post("/api/evolve", json={"apply": False}, headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert "scanned_items" in data
        assert "proposals" in data
        assert data["executed"] == 0


class TestGraph:
    def test_full_graph(self, client: TestClient, admin_token: str, seed_items):
        resp = client.get("/api/graph", headers={"Authorization": f"Bearer {admin_token}"})
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["nodes"]) == 3

    def test_link_unlink(self, client: TestClient, admin_token: str, seed_items):
        headers = {"Authorization": f"Bearer {admin_token}"}
        resp = client.post("/api/link", json={"source": "mem-20260101-000000-test-fact", "target": "mem-20260101-000001-test-decision", "label": "related"}, headers=headers)
        assert resp.status_code == 200
        assert resp.json()["linked"] is True

        resp2 = client.delete("/api/link?source=mem-20260101-000000-test-fact&target=mem-20260101-000001-test-decision", headers=headers)
        assert resp2.status_code == 200
        assert resp2.json()["unlinked"] is True


class TestPagination:
    def test_offset_pagination(self, client: TestClient, admin_token: str, seed_items):
        headers = {"Authorization": f"Bearer {admin_token}"}
        resp = client.get("/api/items?limit=2&offset=0", headers=headers)
        data = resp.json()
        assert len(data["items"]) == 2
        assert data["total"] == 3
        assert data["offset"] == 0
        resp2 = client.get("/api/items?limit=2&offset=2", headers=headers)
        data2 = resp2.json()
        assert len(data2["items"]) == 1
        assert data2["offset"] == 2

    def test_sort_by_title(self, client: TestClient, admin_token: str, seed_items):
        headers = {"Authorization": f"Bearer {admin_token}"}
        resp = client.get("/api/items?sort=title&order=asc", headers=headers)
        titles = [it["title"] for it in resp.json()["items"]]
        assert titles == sorted(titles)

    def test_keyword_filter(self, client: TestClient, admin_token: str, seed_items):
        headers = {"Authorization": f"Bearer {admin_token}"}
        resp = client.get("/api/items?q=GIL", headers=headers)
        data = resp.json()
        assert data["total"] == 1
        assert "GIL" in data["items"][0]["title"]


class TestBatchTag:
    def test_add_remove_tags(self, client: TestClient, admin_token: str, seed_items):
        headers = {"Authorization": f"Bearer {admin_token}"}
        ids = [s.id for s in seed_items[:2]]
        resp = client.post("/api/items/batch-tag", json={
            "ids": ids, "add_tags": ["important"], "remove_tags": ["test"],
        }, headers=headers)
        assert resp.status_code == 200
        assert resp.json()["updated"] == 2

        detail = client.get(f"/api/items/{ids[0]}", headers=headers).json()
        assert "important" in detail["item"]["tags"]
        assert "test" not in detail["item"]["tags"]


class TestRelated:
    def test_related_items(self, client: TestClient, admin_token: str, seed_items):
        headers = {"Authorization": f"Bearer {admin_token}"}
        resp = client.get("/api/items/mem-20260101-000000-test-fact/related?top_k=2", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["item_id"] == "mem-20260101-000000-test-fact"
        assert isinstance(data["related"], list)

    def test_related_not_found(self, client: TestClient, admin_token: str, brain_dir):
        headers = {"Authorization": f"Bearer {admin_token}"}
        resp = client.get("/api/items/nonexistent/related", headers=headers)
        assert resp.status_code == 404


class TestResponseTiming:
    def test_response_time_header(self, client: TestClient, brain_dir: Path):
        resp = client.get("/api/health")
        assert "X-Response-Time" in resp.headers
        assert resp.headers["X-Response-Time"].endswith("ms")


class TestMerge:
    def test_merge_items(self, client: TestClient, admin_token: str, seed_items):
        headers = {"Authorization": f"Bearer {admin_token}"}
        ids = [s.id for s in seed_items[:2]]
        resp = client.post("/api/items/merge", json={
            "ids": ids,
            "title": "Merged fact+decision",
            "summary": "Combined item",
        }, headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["source_count"] == 2
        assert data["merged_id"].startswith("mem-")
        assert data["originals_kept"] is False

    def test_merge_keep_originals(self, client: TestClient, admin_token: str, seed_items):
        headers = {"Authorization": f"Bearer {admin_token}"}
        ids = [s.id for s in seed_items[:2]]
        resp = client.post("/api/items/merge", json={
            "ids": ids,
            "title": "Merged with keep",
            "summary": "Kept originals",
            "keep_originals": True,
        }, headers=headers)
        assert resp.status_code == 200
        assert resp.json()["originals_kept"] is True
        for item_id in ids:
            detail = client.get(f"/api/items/{item_id}", headers=headers)
            assert detail.status_code == 200

    def test_merge_too_few(self, client: TestClient, admin_token: str, seed_items):
        headers = {"Authorization": f"Bearer {admin_token}"}
        resp = client.post("/api/items/merge", json={
            "ids": [seed_items[0].id],
            "title": "Fail",
            "summary": "Fail",
        }, headers=headers)
        assert resp.status_code == 400


class TestProjectsAndTags:
    def test_list_projects(self, client: TestClient, admin_token: str, seed_items):
        headers = {"Authorization": f"Bearer {admin_token}"}
        resp = client.get("/api/projects", headers=headers)
        assert resp.status_code == 200
        names = [p["name"] for p in resp.json()["projects"]]
        assert "alpha" in names

    def test_list_tags(self, client: TestClient, admin_token: str, seed_items):
        headers = {"Authorization": f"Bearer {admin_token}"}
        resp = client.get("/api/tags", headers=headers)
        assert resp.status_code == 200
        names = [t["name"] for t in resp.json()["tags"]]
        assert "test" in names


class TestClone:
    def test_clone_item(self, client: TestClient, admin_token: str, seed_items):
        headers = {"Authorization": f"Bearer {admin_token}"}
        resp = client.post("/api/items/mem-20260101-000000-test-fact/clone", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["source_id"] == "mem-20260101-000000-test-fact"
        assert data["id"].startswith("mem-")
        assert "clone" in data["id"]

        detail = client.get(f"/api/items/{data['id']}", headers=headers).json()
        assert detail["item"]["title"] == "Python GIL behavior"
        assert "cloned" in detail["item"]["tags"]

    def test_clone_not_found(self, client: TestClient, admin_token: str, brain_dir):
        headers = {"Authorization": f"Bearer {admin_token}"}
        resp = client.post("/api/items/nonexistent/clone", headers=headers)
        assert resp.status_code == 404


class TestBatchUpdate:
    def test_batch_update_confidence(self, client: TestClient, admin_token: str, seed_items):
        headers = {"Authorization": f"Bearer {admin_token}"}
        ids = [item.id for item in seed_items[:2]]
        resp = client.post("/api/items/batch-update", headers=headers, json={
            "ids": ids,
            "updates": {"confidence": 0.95},
        })
        assert resp.status_code == 200
        assert resp.json()["updated"] == 2
        for item_id in ids:
            detail = client.get(f"/api/items/{item_id}", headers=headers).json()
            assert detail["item"]["confidence"] == 0.95

    def test_batch_update_empty(self, client: TestClient, admin_token: str, brain_dir):
        headers = {"Authorization": f"Bearer {admin_token}"}
        resp = client.post("/api/items/batch-update", headers=headers, json={
            "ids": [], "updates": {},
        })
        assert resp.status_code == 400


class TestFulltextSearch:
    def test_fulltext_body_match(self, client: TestClient, admin_token: str, seed_items):
        headers = {"Authorization": f"Bearer {admin_token}"}
        resp = client.get("/api/search/fulltext?q=Python", headers=headers)
        assert resp.status_code == 200
        results = resp.json()["results"]
        assert any(r["id"] == seed_items[0].id for r in results)

    def test_fulltext_no_match(self, client: TestClient, admin_token: str, seed_items):
        headers = {"Authorization": f"Bearer {admin_token}"}
        resp = client.get("/api/search/fulltext?q=zzzznonexistent", headers=headers)
        assert resp.status_code == 200
        assert resp.json()["count"] == 0

    def test_fulltext_with_type_filter(self, client: TestClient, admin_token: str, seed_items):
        headers = {"Authorization": f"Bearer {admin_token}"}
        resp = client.get("/api/search/fulltext?q=Body&type=fact", headers=headers)
        assert resp.status_code == 200
        for r in resp.json()["results"]:
            assert r["type"] == "fact"


class TestPinItems:
    def test_pin_and_unpin(self, client: TestClient, admin_token: str, seed_items):
        headers = {"Authorization": f"Bearer {admin_token}"}
        item_id = seed_items[0].id
        resp = client.post(f"/api/items/{item_id}/pin", headers=headers)
        assert resp.status_code == 200
        assert resp.json()["pinned"] is True

        pinned = client.get("/api/items/pinned", headers=headers).json()
        assert any(p["id"] == item_id for p in pinned["items"])

        resp2 = client.post(f"/api/items/{item_id}/pin", headers=headers)
        assert resp2.json()["pinned"] is False

        pinned2 = client.get("/api/items/pinned", headers=headers).json()
        assert not any(p["id"] == item_id for p in pinned2["items"])


class TestCSVExport:
    def test_export_csv(self, client: TestClient, admin_token: str, seed_items):
        headers = {"Authorization": f"Bearer {admin_token}"}
        resp = client.get("/api/export/csv", headers=headers)
        assert resp.status_code == 200
        assert "text/csv" in resp.headers.get("content-type", "")
        lines = resp.text.strip().split("\n")
        assert lines[0].startswith("id,type,title")
        assert len(lines) >= 4  # header + 3 seed items

    def test_export_csv_filtered(self, client: TestClient, admin_token: str, seed_items):
        headers = {"Authorization": f"Bearer {admin_token}"}
        resp = client.get("/api/export/csv?type=fact", headers=headers)
        assert resp.status_code == 200
        lines = resp.text.strip().split("\n")
        assert len(lines) == 2  # header + 1 fact


class TestAuditLog:
    def test_audit_after_create(self, client: TestClient, admin_token: str, brain_dir):
        headers = {"Authorization": f"Bearer {admin_token}"}
        client.post("/api/items", headers=headers, json={
            "type": "fact", "title": "Audit test", "summary": "Testing audit log",
        })
        resp = client.get("/api/audit", headers=headers)
        assert resp.status_code == 200
        entries = resp.json()["entries"]
        assert any(e["action"] == "create" for e in entries)

    def test_audit_after_delete(self, client: TestClient, admin_token: str, seed_items):
        headers = {"Authorization": f"Bearer {admin_token}"}
        item_id = seed_items[0].id
        client.delete(f"/api/items/{item_id}", headers=headers)
        resp = client.get("/api/audit", headers=headers)
        assert resp.status_code == 200
        entries = resp.json()["entries"]
        assert any(e["action"] == "delete" and item_id in e["detail"] for e in entries)


class TestMarkdownExport:
    def test_export_markdown_zip(self, client: TestClient, admin_token: str, seed_items):
        headers = {"Authorization": f"Bearer {admin_token}"}
        resp = client.get("/api/export/markdown", headers=headers)
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "application/zip"
        import zipfile
        import io
        zf = zipfile.ZipFile(io.BytesIO(resp.content))
        names = zf.namelist()
        assert len(names) == 3
        for name in names:
            assert name.endswith(".md")

    def test_export_markdown_filtered(self, client: TestClient, admin_token: str, seed_items):
        headers = {"Authorization": f"Bearer {admin_token}"}
        resp = client.get("/api/export/markdown?type=fact", headers=headers)
        assert resp.status_code == 200
        import zipfile
        import io
        zf = zipfile.ZipFile(io.BytesIO(resp.content))
        assert len(zf.namelist()) == 1


class TestTagManagement:
    def test_rename_tag(self, client: TestClient, admin_token: str, seed_items):
        headers = {"Authorization": f"Bearer {admin_token}"}
        resp = client.post("/api/tags/rename", headers=headers, json={
            "old_name": "auto", "new_name": "automated"
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["old_name"] == "auto"
        assert data["new_name"] == "automated"

    def test_delete_tag(self, client: TestClient, admin_token: str, seed_items):
        headers = {"Authorization": f"Bearer {admin_token}"}
        resp = client.post("/api/tags/delete", headers=headers, json={
            "tag_name": "auto"
        })
        assert resp.status_code == 200
        assert resp.json()["tag_name"] == "auto"

    def test_tag_ops_admin_only(self, client: TestClient, brain_dir):
        from web.auth import create_token
        token = create_token({"username": "viewer", "tenant_id": "default", "role": "user"})
        headers = {"Authorization": f"Bearer {token}"}
        resp = client.post("/api/tags/rename", headers=headers, json={
            "old_name": "a", "new_name": "b"
        })
        assert resp.status_code == 403


class TestSSEEvents:
    def test_sse_requires_token(self, client: TestClient, brain_dir):
        resp = client.get("/api/events")
        assert resp.status_code == 401

    def test_sse_invalid_token(self, client: TestClient, brain_dir):
        resp = client.get("/api/events?token=invalid")
        assert resp.status_code == 401


class TestWebhooks:
    def test_add_and_list_webhooks(self, client: TestClient, admin_token: str, brain_dir):
        headers = {"Authorization": f"Bearer {admin_token}"}
        resp = client.post("/api/webhooks", headers=headers, json={"url": "https://example.com/hook"})
        assert resp.status_code == 200
        assert resp.json()["total"] == 1
        resp = client.get("/api/webhooks", headers=headers)
        assert resp.status_code == 200
        assert len(resp.json()["webhooks"]) >= 1

    def test_remove_webhook(self, client: TestClient, admin_token: str, brain_dir):
        headers = {"Authorization": f"Bearer {admin_token}"}
        client.post("/api/webhooks", headers=headers, json={"url": "https://example.com/remove-me"})
        resp = client.delete("/api/webhooks?url=https://example.com/remove-me", headers=headers)
        assert resp.status_code == 200


class TestAdvancedFilters:
    def test_confidence_filter(self, client: TestClient, admin_token: str, seed_items):
        headers = {"Authorization": f"Bearer {admin_token}"}
        resp = client.get("/api/items?conf_min=0.6&conf_max=0.8", headers=headers)
        assert resp.status_code == 200
        for item in resp.json()["items"]:
            assert 0.6 <= item["confidence"] <= 0.8

    def test_date_filter(self, client: TestClient, admin_token: str, seed_items):
        headers = {"Authorization": f"Bearer {admin_token}"}
        resp = client.get("/api/items?since=2020-01-01T00:00:00", headers=headers)
        assert resp.status_code == 200
        assert resp.json()["total"] >= 0


class TestImportStrategy:
    def _make_import_rec(self, item_id, **overrides):
        rec = {
            "id": item_id, "type": "fact", "title": "Python GIL behavior",
            "summary": "GIL summary", "created_at": "2026-01-01T00:00:00+00:00",
            "tags": ["merged-tag", "new-tag"],
        }
        rec.update(overrides)
        return rec

    def test_import_merge_tags(self, client: TestClient, admin_token: str, seed_items):
        headers = {"Authorization": f"Bearer {admin_token}"}
        item_id = seed_items[0].id
        rec = self._make_import_rec(item_id)
        payload = {"items": [{"frontmatter": rec, "body": "Extra merged content"}], "strategy": "merge"}
        resp = client.post("/api/import", json=payload, headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["merged"] == 1
        assert data["imported"] == 0
        detail = client.get(f"/api/items/{item_id}", headers=headers)
        item_data = detail.json()["item"]
        assert "merged-tag" in item_data["tags"]
        assert "test" in item_data["tags"]

    def test_import_skip(self, client: TestClient, admin_token: str, seed_items):
        headers = {"Authorization": f"Bearer {admin_token}"}
        item_id = seed_items[0].id
        rec = self._make_import_rec(item_id)
        payload = {"items": [{"frontmatter": rec, "body": ""}], "strategy": "skip"}
        resp = client.post("/api/import", json=payload, headers=headers)
        assert resp.status_code == 200
        assert resp.json()["skipped"] == 1

    def test_import_invalid_strategy(self, client: TestClient, admin_token: str, brain_dir):
        headers = {"Authorization": f"Bearer {admin_token}"}
        payload = {"items": [], "strategy": "invalid"}
        resp = client.post("/api/import", json=payload, headers=headers)
        assert resp.status_code == 400


class TestFulltextHighlight:
    def test_highlight_marks(self, client: TestClient, admin_token: str, seed_items):
        headers = {"Authorization": f"Bearer {admin_token}"}
        resp = client.get("/api/search/fulltext?q=Python&highlight=true", headers=headers)
        assert resp.status_code == 200
        results = resp.json()["results"]
        if results:
            assert "<mark>" in results[0].get("snippet", "")

    def test_no_highlight_default(self, client: TestClient, admin_token: str, seed_items):
        headers = {"Authorization": f"Bearer {admin_token}"}
        resp = client.get("/api/search/fulltext?q=Python", headers=headers)
        assert resp.status_code == 200
        results = resp.json()["results"]
        if results:
            assert "<mark>" not in results[0].get("snippet", "")


class TestItemLinks:
    def test_create_and_list_links(self, client: TestClient, admin_token: str, seed_items):
        headers = {"Authorization": f"Bearer {admin_token}"}
        s, t = seed_items[0].id, seed_items[1].id
        resp = client.post("/api/links", json={"source_id": s, "target_id": t, "relation": "supports"}, headers=headers)
        assert resp.status_code == 200
        assert resp.json()["link"]["relation"] == "supports"
        resp2 = client.get(f"/api/links/{s}", headers=headers)
        assert resp2.status_code == 200
        assert resp2.json()["count"] == 1

    def test_duplicate_link_rejected(self, client: TestClient, admin_token: str, seed_items):
        headers = {"Authorization": f"Bearer {admin_token}"}
        s, t = seed_items[0].id, seed_items[1].id
        client.post("/api/links", json={"source_id": s, "target_id": t}, headers=headers)
        resp = client.post("/api/links", json={"source_id": s, "target_id": t}, headers=headers)
        assert resp.status_code == 409

    def test_unlink(self, client: TestClient, admin_token: str, seed_items):
        headers = {"Authorization": f"Bearer {admin_token}"}
        s, t = seed_items[0].id, seed_items[2].id
        client.post("/api/links", json={"source_id": s, "target_id": t}, headers=headers)
        resp = client.delete(f"/api/links?source_id={s}&target_id={t}", headers=headers)
        assert resp.status_code == 200
        assert resp.json()["removed"] == 1


class TestItemHistory:
    def test_update_creates_snapshot(self, client: TestClient, admin_token: str, seed_items):
        headers = {"Authorization": f"Bearer {admin_token}"}
        item_id = seed_items[0].id
        client.patch(f"/api/items/{item_id}", json={"title": "Updated title"}, headers=headers)
        resp = client.get(f"/api/items/{item_id}/history", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] >= 1
        assert any(s["title"] == "Python GIL behavior" for s in data["snapshots"])

    def test_get_specific_snapshot(self, client: TestClient, admin_token: str, seed_items):
        headers = {"Authorization": f"Bearer {admin_token}"}
        item_id = seed_items[0].id
        client.patch(f"/api/items/{item_id}", json={"confidence": 0.9}, headers=headers)
        resp = client.get(f"/api/items/{item_id}/history/0", headers=headers)
        assert resp.status_code == 200
        assert "frontmatter" in resp.json()
        assert "body" in resp.json()

    def test_nonexistent_history(self, client: TestClient, admin_token: str, brain_dir):
        headers = {"Authorization": f"Bearer {admin_token}"}
        resp = client.get("/api/items/mem-00000000-000000-no-exist/history", headers=headers)
        assert resp.status_code == 200
        assert resp.json()["count"] == 0


class TestBackupRestore:
    def test_backup_and_restore(self, client: TestClient, admin_token: str, seed_items):
        headers = {"Authorization": f"Bearer {admin_token}"}
        resp = client.post("/api/backup", headers=headers)
        assert resp.status_code == 200
        backup_name = resp.json()["backup"].split("/")[-1]
        client.delete(f"/api/items/{seed_items[0].id}", headers=headers)
        resp2 = client.post(f"/api/backups/{backup_name}/restore", headers=headers)
        assert resp2.status_code == 200
        assert resp2.json()["restored"] >= 3
        resp3 = client.get(f"/api/items/{seed_items[0].id}", headers=headers)
        assert resp3.status_code == 200

    def test_restore_nonexistent(self, client: TestClient, admin_token: str, brain_dir):
        headers = {"Authorization": f"Bearer {admin_token}"}
        resp = client.post("/api/backups/does-not-exist/restore", headers=headers)
        assert resp.status_code == 404


class TestRateLimit:
    def test_rate_limit_disabled_in_test(self, client: TestClient, admin_token: str, brain_dir):
        for _ in range(5):
            resp = client.get("/api/health")
            assert resp.status_code == 200

"""Tests for inferred preference profile helpers."""

from __future__ import annotations

from datetime import datetime, timezone

from agent_brain.contracts.memory_item import MemoryItem, MemoryType, Sensitivity
from agent_brain.memory.store.items_store import ItemsStore


def _item(
    suffix: str,
    *,
    project: str | None = "scope-alpha",
    tenant_id: str | None = "tenant-a",
    session: str | None = None,
    tags: list[str] | None = None,
    sensitivity: Sensitivity = Sensitivity.internal,
    gain_score: float = 1.0,
    support_count: int = 1,
    validity: dict[str, object] | None = None,
) -> MemoryItem:
    return MemoryItem(
        id=f"mem-20260703-120000-{suffix}",
        type=MemoryType.decision,
        created_at=datetime.now(timezone.utc),
        title=f"Decision {suffix}",
        summary=f"Summary {suffix}",
        project=project,
        tenant_id=tenant_id,
        session=session,
        tags=tags or ["signal-alpha"],
        sensitivity=sensitivity,
        gain_score=gain_score,
        support_count=support_count,
        validity=validity or {},
    )


def _write_items(store: ItemsStore, *items: MemoryItem) -> None:
    for item in items:
        store.write(item, f"Body for {item.id}")


def test_preference_types_and_formatter_are_split_and_reexported():
    from agent_brain.memory.governance.evolve import preference
    from agent_brain.memory.governance.evolve.preference_format import format_preference_profile
    from agent_brain.memory.governance.evolve.preference_types import PreferenceProfile, PreferenceSignal

    assert preference.PreferenceProfile is PreferenceProfile
    assert preference.PreferenceSignal is PreferenceSignal
    assert preference.format_preference_profile is format_preference_profile

    profile = PreferenceProfile(
        generated_at=datetime.now(timezone.utc),
        signals=[
            PreferenceSignal(
                dimension="topic",
                preference="偏好 retrieval 相关方案",
                anti_preference=None,
                confidence=0.8,
                evidence_count=3,
                tags=["retrieval"],
            )
        ],
        decision_patterns=["Use deterministic tests"],
    )

    text = format_preference_profile(profile)

    assert "Inferred User Preferences" in text
    assert "偏好 retrieval 相关方案" in text
    assert "Use deterministic tests" in text


def test_infer_preferences_isolates_tenant_and_project_scope(tmp_brain_dir):
    from agent_brain.memory.governance.evolve.preference import infer_preferences
    from agent_brain.memory.scope import ScopeContext

    store = ItemsStore(tmp_brain_dir / "items")
    _write_items(
        store,
        _item("alpha-one", tags=["signal-alpha"]),
        _item("alpha-two", tags=["signal-alpha"]),
        _item("beta-one", project="scope-beta", tenant_id="tenant-b", tags=["signal-beta"]),
        _item("beta-two", project="scope-beta", tenant_id="tenant-b", tags=["signal-beta"]),
    )

    profile = infer_preferences(
        store,
        scope=ScopeContext(project="scope-alpha", tenant_id="tenant-a"),
    )

    assert profile.top_projects == [("scope-alpha", 2)]
    assert {tag for tag, _ in profile.top_tags} == {"signal-alpha"}
    assert any(signal.tags == ["signal-alpha"] for signal in profile.signals)
    assert not any("signal-beta" in signal.tags for signal in profile.signals)


def test_infer_preferences_does_not_cross_project_without_relationship(tmp_brain_dir):
    from agent_brain.memory.governance.evolve.preference import infer_preferences
    from agent_brain.memory.scope import ScopeContext

    store = ItemsStore(tmp_brain_dir / "items")
    _write_items(
        store,
        _item("alpha-anchor", tags=["signal-alpha"]),
        _item("beta-one", project="scope-beta", tags=["signal-beta"]),
        _item("beta-two", project="scope-beta", tags=["signal-beta"]),
    )

    profile = infer_preferences(store, scope=ScopeContext(project="scope-alpha"))

    assert not any("signal-beta" in signal.tags for signal in profile.signals)


def test_infer_preferences_can_borrow_explicitly_related_scope(tmp_brain_dir):
    from agent_brain.memory.governance.evolve.preference import infer_preferences
    from agent_brain.memory.scope import ScopeContext

    store = ItemsStore(tmp_brain_dir / "items")
    related_one = _item("beta-one", project="scope-beta", tags=["signal-beta"])
    related_two = _item("beta-two", project="scope-beta", tags=["signal-beta"])
    _write_items(
        store,
        _item("alpha-anchor", tags=["signal-alpha"]),
        related_one,
        related_two,
    )

    profile = infer_preferences(
        store,
        scope=ScopeContext(project="scope-alpha", related_item_ids=(related_one.id, related_two.id)),
    )

    related_signals = [signal for signal in profile.signals if signal.tags == ["signal-beta"]]
    assert related_signals
    assert related_signals[0].scope_match == "related"
    assert set(related_signals[0].source_item_ids) == {related_one.id, related_two.id}


def test_infer_preferences_excludes_sensitive_items_by_default(tmp_brain_dir):
    from agent_brain.memory.governance.evolve.preference import infer_preferences
    from agent_brain.memory.scope import ScopeContext

    store = ItemsStore(tmp_brain_dir / "items")
    _write_items(
        store,
        _item("public-one", tags=["signal-alpha"], sensitivity=Sensitivity.internal),
        _item("public-two", tags=["signal-alpha"], sensitivity=Sensitivity.internal),
        _item("private-one", tags=["signal-private"], sensitivity=Sensitivity.private),
        _item("private-two", tags=["signal-private"], sensitivity=Sensitivity.secret),
    )

    profile = infer_preferences(store, scope=ScopeContext(project="scope-alpha"))

    assert any("signal-alpha" in signal.tags for signal in profile.signals)
    assert not any("signal-private" in signal.tags for signal in profile.signals)


def test_graph_scope_resolver_uses_evidence_edges_without_domain_relation_names(tmp_brain_dir):
    from agent_brain.memory.scope import related_item_ids_from_graph
    from agent_brain.platform.embedding import HashingEmbedder
    from agent_brain.platform.indexing.index import HubIndex

    store = ItemsStore(tmp_brain_dir / "items")
    source = _item("source")
    target = _item("target", project="scope-beta")
    _write_items(store, source, target)

    idx = HubIndex(tmp_brain_dir / "index.db", embedding_dim=8)
    embedder = HashingEmbedder(dim=8)
    for item in (source, target):
        idx.upsert(item, f"Body for {item.id}", embedder.embed(item.title))
    idx.add_ref(source.id, target.id, "relates-x")

    related = related_item_ids_from_graph(idx, seed_item_ids=(source.id,), depth=1)

    assert related == {target.id}


def test_project_scope_resolver_treats_explicit_project_as_authoritative(tmp_brain_dir):
    from agent_brain.memory.scope import ProjectScopeResolver

    store = ItemsStore(tmp_brain_dir / "items")

    resolution = ProjectScopeResolver(store).resolve(explicit_project="scope-explicit")

    assert resolution.project == "scope-explicit"
    assert resolution.status == "resolved"
    assert resolution.confidence == 1.0
    assert resolution.evidence[0].source == "explicit_project"


def test_project_scope_resolver_derives_candidate_from_git_workspace(tmp_path):
    from agent_brain.memory.scope import ProjectScopeResolver

    brain = tmp_path / "brain"
    workspace = tmp_path / "workspace-alpha"
    nested = workspace / "src" / "pkg"
    (brain / "items").mkdir(parents=True)
    (workspace / ".git").mkdir(parents=True)
    nested.mkdir(parents=True)
    store = ItemsStore(brain / "items")

    resolution = ProjectScopeResolver(store).resolve(cwd=str(nested))

    assert resolution.project == "workspace-alpha"
    assert resolution.status == "resolved"
    assert resolution.confidence >= 0.7
    assert resolution.evidence[0].source == "git_root"


def test_project_scope_resolver_uses_session_continuity(tmp_brain_dir):
    from agent_brain.memory.scope import ProjectScopeResolver

    store = ItemsStore(tmp_brain_dir / "items")
    _write_items(
        store,
        _item("session-one", project="scope-session", session="session-1"),
        _item("session-two", project="scope-session", session="session-1"),
        _item("other-session", project="scope-other", session="session-2"),
    )

    resolution = ProjectScopeResolver(store).resolve(session_id="session-1")

    assert resolution.project == "scope-session"
    assert resolution.status == "resolved"
    assert any(evidence.source == "session_history" for evidence in resolution.evidence)


def test_project_scope_resolver_uses_seed_item_project(tmp_brain_dir):
    from agent_brain.memory.scope import ProjectScopeResolver

    store = ItemsStore(tmp_brain_dir / "items")
    seed = _item("seed-project", project="scope-seed")
    _write_items(
        store,
        seed,
        _item("other-project", project="scope-other"),
    )

    resolution = ProjectScopeResolver(store).resolve(seed_item_ids=[seed.id])

    assert resolution.project == "scope-seed"
    assert resolution.status == "resolved"
    assert any(evidence.source == "seed_item" for evidence in resolution.evidence)


def test_project_scope_resolver_uses_historical_cwd_evidence(tmp_path):
    from agent_brain.memory.scope import ProjectScopeResolver

    brain = tmp_path / "brain"
    workspace = tmp_path / "workspace-beta"
    current = workspace / "src"
    (brain / "items").mkdir(parents=True)
    current.mkdir(parents=True)
    store = ItemsStore(brain / "items")
    _write_items(
        store,
        _item("cwd-one", project="scope-cwd", validity={"cwd": str(workspace)}),
        _item("cwd-two", project="scope-cwd", validity={"repo": str(workspace)}),
    )

    resolution = ProjectScopeResolver(store).resolve(cwd=str(current))

    assert resolution.project == "scope-cwd"
    assert resolution.status == "resolved"
    assert any(evidence.source == "validity_scope" for evidence in resolution.evidence)


def test_project_scope_resolver_refuses_ambiguous_history(tmp_path):
    from agent_brain.memory.scope import ProjectScopeResolver

    brain = tmp_path / "brain"
    workspace = tmp_path / "workspace-gamma"
    current = workspace / "src"
    (brain / "items").mkdir(parents=True)
    current.mkdir(parents=True)
    store = ItemsStore(brain / "items")
    _write_items(
        store,
        _item("ambiguous-one", project="scope-one", validity={"cwd": str(workspace)}),
        _item("ambiguous-two", project="scope-two", validity={"cwd": str(workspace)}),
    )

    resolution = ProjectScopeResolver(store).resolve(cwd=str(current))

    assert resolution.project is None
    assert resolution.status == "ambiguous"
    assert {candidate.project for candidate in resolution.candidates} == {"scope-one", "scope-two"}

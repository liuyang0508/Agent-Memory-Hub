"""Protocol/security tests use an explicitly synthetic vector backend, not model evidence."""

import pytest

from agent_brain.native_sync.hub import Hub
from agent_brain.native_sync.storage import database
from agent_brain.platform.embedding import HashingEmbedder
from test_hub import asset


class SyntheticBackend:
    semantic = True  # Test injection only: this is NOT a quality evaluation.
    model_id = "synthetic-test-only"
    model_version = "1"
    dim = 3

    def __init__(self):
        self.seen = []
        self.on_embed = None

    def embed(self, text):
        self.seen.append(text)
        if self.on_embed:
            self.on_embed()
        if "approval" in text or "复核" in text:
            return [1, 0, 0]
        if "secret" in text:
            return [0, 1, 0]
        return [0, 0, 1]


def make(tmp_path):
    backend = SyntheticBackend()
    hub = Hub(tmp_path / "hub.sqlite", semantic_backend=backend)
    who = hub.provision("one", "device", tenant="one")
    return hub, who["token"], backend


def test_unbuilt_index_is_explicit_and_does_not_embed(tmp_path):
    hub, token, backend = make(tmp_path)
    hub.put(token, asset("answer", body="approval requires two people"))
    result = hub.retrieve(token, "approval")
    assert result["items"][0]["asset_id"] == "answer"
    assert result["retrieval"]["reasons"] == ["semantic_index_not_built"]
    assert not backend.seen


def test_default_roles_filter_before_candidate_limit(tmp_path):
    hub, token, _ = make(tmp_path)
    for i in range(105):
        hub.put(
            token,
            asset(
                str(i),
                path=f"rollout_summaries/{i}.md",
                title="approval",
                body="approval failed no result",
            ),
        )
    hub.put(token, asset("answer", path="topic.md", title="", body="approval requires 复核"))
    result = hub.retrieve(token, "approval")
    assert [i["asset_id"] for i in result["items"]] == ["answer"]
    assert any(
        i["role"] == "conversation_summary"
        for i in hub.retrieve(token, "approval", include_evidence=True)["items"]
    )


def test_semantic_cross_language_and_tenant_project_filter_before_models(tmp_path):
    hub, token, backend = make(tmp_path)
    other = hub.provision("two", "other", tenant="two")
    hub.put(other["token"], asset("hidden", body="CROSS_ORG secret approval"))
    hub.put(token, asset("answer", title="", body="两人复核", project="approved"))
    hub.put(token, asset("other-project", body="secret approval", project="elsewhere"))
    assert hub.rebuild_semantic(token)["indexed"] == 2
    assert not any("CROSS_ORG" in text for text in backend.seen)
    backend.seen.clear()
    seen = []
    hub.reranker = lambda query, items: seen.extend(items) or [1] * len(items)
    result = hub.retrieve(token, "approval", project_filter="approved")
    assert [i["asset_id"] for i in result["items"]] == ["answer"]
    assert result["retrieval"]["mode"] == "hybrid"
    assert [i["asset_id"] for i in seen] == ["answer"]
    assert backend.seen == ["approval"]
    # A separate build can never read the first tenant's texts.
    backend.seen.clear()
    assert hub.rebuild_semantic(other["token"])["indexed"] == 1
    assert backend.seen == ["订单验签\nCROSS_ORG secret approval"]


def test_updated_deleted_and_incomplete_versions_never_use_old_vectors(tmp_path):
    hub, token, backend = make(tmp_path)
    hub.put(token, asset("old", title="", body="两人复核"))
    hub.rebuild_semantic(token)
    hub.put(token, asset("old", title="", body="unrelated", base_revision=1))
    result = hub.retrieve(token, "approval")
    assert result["items"] == []
    assert "semantic_index_incomplete_rebuild_required" in result["retrieval"]["reasons"]
    hub.rebuild_semantic(token)
    hub.put(token, asset("old", body="", deleted=True, base_revision=2))
    assert hub.retrieve(token, "unrelated")["items"] == []
    hub.put(
        token,
        asset(
            "broken", body="approval", document_id="missing", document_hash="a" * 64, part_count=2
        ),
    )
    assert hub.rebuild_semantic(token)["indexed"] == 0
    assert hub.retrieve(token, "approval")["items"] == []


def test_write_during_build_is_not_locked_and_stale_snapshot_skipped(tmp_path):
    hub, token, backend = make(tmp_path)
    hub.put(token, asset("racing", body="复核"))

    def update():
        backend.on_embed = None
        hub.put(token, asset("racing", body="new unrelated", base_revision=1))

    backend.on_embed = update
    assert hub.rebuild_semantic(token)["skipped_changed"] == 1
    assert hub.retrieve(token, "approval")["items"] == []


def test_update_during_query_is_not_returned(tmp_path):
    hub, token, backend = make(tmp_path)
    hub.put(token, asset("racing", body="复核"))
    hub.rebuild_semantic(token)

    def update():
        backend.on_embed = None
        hub.put(token, asset("racing", body="new unrelated", base_revision=1))

    backend.on_embed = update
    assert hub.retrieve(token, "approval")["items"] == []


def test_duplicates_preserve_sources_and_model_mismatch_degrades(tmp_path):
    hub, token, backend = make(tmp_path)
    for key in ["one", "two"]:
        hub.put(token, asset(key, body="approval"))
    hub.rebuild_semantic(token)
    result = hub.retrieve(token, "approval")
    assert len(result["items"]) == 1
    assert len(result["items"][0]["duplicate_sources"]) == 1
    backend.model_version = "2"
    result = hub.retrieve(token, "approval")
    assert result["retrieval"]["reasons"] == ["semantic_index_model_mismatch_rebuild_required"]
    assert result["retrieval"]["mode"] == "lexical"


def test_hashing_cannot_be_reported_as_semantic_and_revocation_wins(tmp_path):
    hub, token, _ = make(tmp_path)
    hub._encoder = HashingEmbedder()
    with pytest.raises(ValueError, match="unavailable"):
        hub.rebuild_semantic(token)
    with database(hub.path) as db:
        db.execute("UPDATE devices SET revoked=1")
    with pytest.raises(PermissionError):
        hub.retrieve(token, "")


def test_legacy_ranking_imports_are_identical():
    from agent_brain.memory.recall.retrieval_fusion import rrf_fusion as old
    from agent_brain.retrieval.fusion import rrf_fusion as new
    from agent_brain.memory.recall.retrieval_mmr import mmr_rerank as old_mmr
    from agent_brain.retrieval.mmr import mmr_rerank as new_mmr

    assert old is new and old_mmr is new_mmr


def test_model_failure_is_explicit_lexical_fallback(tmp_path):
    hub, token, backend = make(tmp_path)
    hub.put(token, asset("answer", body="approval"))
    hub.rebuild_semantic(token)

    def broken(text):
        raise RuntimeError("private implementation detail")

    backend.embed = broken
    result = hub.retrieve(token, "approval")
    assert result["items"][0]["asset_id"] == "answer"
    assert result["retrieval"]["reasons"] == ["semantic_query_failed:RuntimeError"]
    assert result["retrieval"]["mode"] == "lexical"


def test_empty_semantic_result_is_not_a_degraded_model(tmp_path):
    hub, token, backend = make(tmp_path)
    hub.put(token, asset("answer", body="复核"))
    hub.rebuild_semantic(token)
    result = hub.retrieve(token, "secret")
    assert result["items"] == []
    assert result["retrieval"]["mode"] == "hybrid"
    assert not result["retrieval"]["degraded"]


def test_exact_asset_id_and_missing_config(tmp_path):
    hub, token, backend = make(tmp_path)
    hub.put(token, asset("EXACT_ID_9", body="复核"))
    hub.rebuild_semantic(token)
    hub._encoder = None
    result = hub.retrieve(token, "EXACT_ID_9")
    assert [i["asset_id"] for i in result["items"]] == ["EXACT_ID_9"]
    assert result["retrieval"]["reasons"] == ["semantic_model_not_configured"]


def test_exact_authorized_asset_id_wins_over_semantic_association(tmp_path):
    hub, token, backend = make(tmp_path)
    hub.put(token, asset("approval", title="", body="secret"))
    hub.put(token, asset("other", title="", body="approval 复核"))
    hub.rebuild_semantic(token)
    assert hub.retrieve(token, "approval", limit=1)["items"][0]["asset_id"] == "approval"


def test_semantic_window_points_at_current_original_body(tmp_path):
    hub, token, _ = make(tmp_path)
    item = hub.put(token, asset("answer", title="title", body="两人复核"))
    hub.rebuild_semantic(token)
    result = hub.retrieve(token, "approval")["items"][0]
    window = result["semantic_match"]
    assert window["source_field"] == "body"
    assert window["offset_unit"] == "unicode_code_points"
    assert item["body"][window["char_start"] : window["char_end"]] == "两人复核"
    assert window["aggregation"] == "max_window_cosine"


def test_transcript_window_does_not_mislabel_index_text_as_body(tmp_path):
    hub, token, _ = make(tmp_path)
    hub.put(
        token, asset("history", kind="transcript", body="raw transcript", index_text="两人复核")
    )
    hub.rebuild_semantic(token)
    result = hub.retrieve(token, "approval", kinds=["transcript"])["items"][0]
    window = result["semantic_match"]
    assert window["source_field"] == "index_text"
    assert result["index_text"][window["char_start"] : window["char_end"]] == "两人复核"

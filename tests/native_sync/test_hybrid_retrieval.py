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
    hub.rebuild_semantic(token, kinds=["transcript"])
    result = hub.retrieve(token, "approval", kinds=["transcript"])["items"][0]
    window = result["semantic_match"]
    assert window["source_field"] == "index_text"
    assert result["index_text"][window["char_start"] : window["char_end"]] == "两人复核"


def test_unrelated_chinese_query_with_only_single_character_overlap_abstains(tmp_path):
    hub, token, _ = make(tmp_path)
    hub.put(token, asset("refund", title="", body="退款的处理时限是七天。"))
    result = hub.retrieve(token, "火星基地种植土豆的成本是多少")
    assert result["items"] == []
    assert result["retrieval"]["candidates"]["lexical"] == 0


@pytest.mark.parametrize(
    "path",
    [
        "./rollout_summaries/alias.md",
        ".\\rollout_summaries\\alias.md",
        "././rollout_summaries//alias.md",
        "rollout_summaries/./alias.md",
        "./raw_memories.md",
        ".\\raw_memories.md",
    ],
)
def test_roles_use_identical_path_normalization_before_sql_limit(tmp_path, path):
    from agent_brain.native_sync.asset_roles import asset_role

    hub, token, _ = make(tmp_path)
    for i in range(105):
        hub.put(
            token,
            asset(
                "alias-" + str(i),
                path=path,
                title="approval",
                body="approval failed no answer " + str(i),
            ),
        )
    hub.put(
        token, asset("topic", path="topic.md", title="", body="approval requires two reviewers")
    )
    assert asset_role("codex-cli", path, "memory") in {"conversation_summary", "raw_memory"}
    result = hub.retrieve(token, "approval")
    assert [i["asset_id"] for i in result["items"]] == ["topic"]
    assert result["retrieval"]["semantic_index"]["eligible"] == 1
    assert any(
        i["role"] in {"conversation_summary", "raw_memory"}
        for i in hub.retrieve(token, "approval", include_evidence=True)["items"]
    )


def test_asset_api_accepts_source_path_aliases_without_changing_source_identity(tmp_path):
    from fastapi.testclient import TestClient
    from agent_brain.native_sync.api import create_app

    hub, token, _ = make(tmp_path)
    client = TestClient(create_app(hub))
    for i, path in enumerate(["./rollout_summaries/alias.md", ".\\raw_memories.md"]):
        result = client.post(
            "/native/v1/assets",
            json=asset("alias" + str(i), path=path),
            headers={"Authorization": "Bearer " + token},
        )
        assert result.status_code == 200
        assert result.json()["path"] == path
    assert hub.retrieve(token, "订单")["items"] == []


@pytest.mark.parametrize(
    "field,value",
    [
        ("path", "renamed.md"),
        ("project", "another-project"),
        ("title", "renamed title"),
        ("kind", "rule"),
        ("upstream", ["reference"]),
    ],
)
def test_multipart_metadata_mismatch_is_not_verified_or_embedded(tmp_path, field, value):
    from agent_brain.native_sync.common import digest

    hub, token, backend = make(tmp_path)
    hub.put(token, asset("reference", body="other"))
    common = dict(
        title="",
        path="doc.md",
        document_id="doc",
        document_hash=digest("approval detail"),
        part_count=2,
    )
    hub.put(token, asset("p0", body="approval ", part_index=0, **common))
    p1 = asset("p1", body="detail", part_index=1, **common)
    hub.put(token, p1)
    assert hub.retrieve(token, "approval")["items"][0]["asset_id"] == "p0"
    hub.rebuild_semantic(token)
    hub.put(token, {**p1, "base_revision": 1, field: value})
    assert hub.retrieve(token, "approval")["items"] == []
    backend.seen.clear()
    assert hub.rebuild_semantic(token)["indexed"] == 1  # only reference
    assert not any("approval" in text for text in backend.seen)


def test_duplicate_single_part_document_identity_is_incomplete(tmp_path):
    from agent_brain.native_sync.common import digest

    hub, token, _ = make(tmp_path)
    for key in ["p0", "p1"]:
        hub.put(
            token,
            asset(
                key, body="approval", document_id="duplicate-doc", document_hash=digest("approval")
            ),
        )
    assert hub.retrieve(token, "approval")["items"] == []


def test_multipart_body_hash_must_match_even_when_document_hash_is_valid(tmp_path):
    from agent_brain.native_sync.common import digest

    hub, token, _ = make(tmp_path)
    for index, body in enumerate(["approval ", "detail"]):
        hub.put(
            token,
            asset(
                "p" + str(index),
                body=body,
                part_index=index,
                part_count=2,
                document_id="doc",
                document_hash=digest("approval detail"),
            ),
        )
    with database(hub.path) as db:
        db.execute(
            "UPDATE assets SET payload=json_set(payload,'$.body_hash','corrupt') WHERE id='p1'"
        )
    assert hub.retrieve(token, "approval")["items"] == []


@pytest.mark.parametrize("different_scope", ["store", "owner", "tenant"])
def test_multipart_completion_never_borrows_other_source_scope(tmp_path, different_scope):
    from agent_brain.native_sync.common import digest

    hub, token, _ = make(tmp_path)
    other_token = token
    other_store = "store-1"
    if different_scope == "store":
        other_store = "other-store"
    else:
        other_token = hub.provision(
            "different", "different", tenant="one" if different_scope == "owner" else "two"
        )["token"]
    common = dict(document_id="shared-id", document_hash=digest("approval detail"), part_count=2)
    hub.put(token, asset("p0", body="approval ", part_index=0, **common))
    hub.put(
        other_token, asset("p1", body="detail", source_store=other_store, part_index=1, **common)
    )
    assert hub.retrieve(token, "approval")["items"] == []
    assert hub.rebuild_semantic(token)["indexed"] == 0


def test_incremental_build_reuses_exact_windows_and_encodes_only_changes(tmp_path):
    hub, token, backend = make(tmp_path)
    for key in ["keep", "change", "delete"]:
        hub.put(token, asset(key, body="approval " + key))
    first = hub.rebuild_semantic(token)
    assert (first["encoded"], first["reused"], first["indexed"]) == (3, 0, 3)
    backend.seen.clear()
    second = hub.rebuild_semantic(token)
    assert (second["encoded"], second["reused"], second["indexed"]) == (0, 3, 3)
    assert backend.seen == []
    hub.put(token, asset("change", body="updated 复核", base_revision=1))
    hub.put(token, asset("delete", deleted=True, body="", base_revision=1))
    hub.put(token, asset("new", body="new approval"))
    refreshed = hub.rebuild_semantic(token)
    assert (refreshed["encoded"], refreshed["reused"], refreshed["indexed"]) == (2, 1, 3)
    assert len(backend.seen) == 2
    assert all("keep" not in text and "delete" not in text for text in backend.seen)
    with database(hub.path) as db:
        rows = db.execute(
            "SELECT asset_id,revision FROM native_semantic_vectors ORDER BY asset_id"
        ).fetchall()
    assert [(r["asset_id"], r["revision"]) for r in rows] == [
        ("change", 2),
        ("keep", 1),
        ("new", 1),
    ]
    backend.seen.clear()
    backend.model_version = "new-model-version"
    changed_model = hub.rebuild_semantic(token)
    assert (changed_model["encoded"], changed_model["reused"]) == (3, 0)
    assert len(backend.seen) == 3


def test_reused_source_revision_is_rechecked_before_atomic_refresh(tmp_path):
    hub, token, backend = make(tmp_path)
    hub.put(token, asset("cached", body="approval"))
    hub.rebuild_semantic(token)
    hub.put(token, asset("fresh", body="fresh approval"))

    def race():
        backend.on_embed = None
        hub.put(token, asset("cached", body="new unrelated", base_revision=1))

    backend.on_embed = race
    result = hub.rebuild_semantic(token)
    assert (result["reused"], result["encoded"], result["indexed"], result["skipped_changed"]) == (
        1,
        1,
        1,
        1,
    )
    with database(hub.path) as db:
        assert [r[0] for r in db.execute("SELECT asset_id FROM native_semantic_vectors")] == [
            "fresh"
        ]


def test_revoked_refresh_does_not_replace_previous_generation(tmp_path):
    hub, token, backend = make(tmp_path)
    hub.put(token, asset("cached", body="approval"))
    hub.rebuild_semantic(token)
    hub.put(token, asset("fresh", body="fresh approval"))

    def revoke():
        backend.on_embed = None
        with database(hub.path) as db:
            db.execute("UPDATE devices SET revoked=1")

    backend.on_embed = revoke
    with pytest.raises(PermissionError):
        hub.rebuild_semantic(token)
    with database(hub.path) as db:
        assert [r[0] for r in db.execute("SELECT asset_id FROM native_semantic_vectors")] == [
            "cached"
        ]
    backend.seen.clear()
    with pytest.raises(PermissionError):
        hub.rebuild_semantic(token)
    assert backend.seen == []


def test_same_revision_with_mismatched_cached_hash_is_encoded_again(tmp_path):
    hub, token, backend = make(tmp_path)
    hub.put(token, asset("item", body="approval"))
    hub.rebuild_semantic(token)
    with database(hub.path) as db:
        db.execute("UPDATE native_semantic_vectors SET content_hash='mismatch'")
    backend.seen.clear()
    result = hub.rebuild_semantic(token)
    assert (result["reused"], result["encoded"]) == (0, 1)
    assert len(backend.seen) == 1


def test_default_build_filters_large_supporting_assets_before_encoding(tmp_path):
    hub, token, backend = make(tmp_path)
    hub.put(token, asset("topic", path="topic.md", title="", body="TOPIC approval"))
    hub.put(token, asset("navigation", path="MEMORY.md", title="", body="NAVIGATION approval"))
    for i in range(12):
        for prefix, kind, path in [
            ("transcript", "transcript", "chat.jsonl"),
            ("config", "config", "config.json"),
            ("summary", "memory", "./rollout_summaries/session.md"),
            ("raw", "memory", ".\\raw_memories.md"),
        ]:
            hub.put(
                token,
                asset(prefix + str(i), kind=kind, path=path, body="EXCLUDED_LARGE_CONTENT " * 1000),
            )
    result = hub.rebuild_semantic(token)
    assert result["kinds"] == ["memory"] and result["include_evidence"] is False
    assert result["indexed"] == result["encoded"] == 2
    assert len(backend.seen) == 2 and not any("EXCLUDED" in text for text in backend.seen)
    with database(hub.path) as db:
        assert db.execute("SELECT count(*) FROM assets").fetchone()[0] == 50


def test_explicit_scope_expansion_then_narrowing_preserves_sources_and_reports_partial(tmp_path):
    import json

    hub, token, backend = make(tmp_path)
    for key, kind, path in [
        ("topic", "memory", "topic.md"),
        ("summary", "memory", "./rollout_summaries/s.md"),
        ("transcript", "transcript", "chat.jsonl"),
        ("config", "config", "config.json"),
    ]:
        hub.put(token, asset(key, kind=kind, path=path, title="", body="复核 " + key))
    first = hub.rebuild_semantic(token)
    assert (first["encoded"], first["reused"]) == (1, 0)
    with database(hub.path) as db:
        original_descriptor = json.loads(
            db.execute("SELECT descriptor FROM native_semantic_meta").fetchone()[0]
        )
        sources = [tuple(row) for row in db.execute("SELECT * FROM assets ORDER BY id")]
    backend.seen.clear()
    expanded = hub.rebuild_semantic(
        token, kinds=["memory", "transcript", "config"], include_evidence=True
    )
    assert (expanded["indexed"], expanded["encoded"], expanded["reused"]) == (4, 3, 1)
    assert expanded["include_evidence"] is True
    assert set(expanded["kinds"]) == {"memory", "transcript", "config"}
    assert not hub.retrieve(token, "approval", kinds=["transcript"])["retrieval"]["degraded"]
    backend.seen.clear()
    narrowed = hub.rebuild_semantic(token)
    assert (narrowed["indexed"], narrowed["encoded"], narrowed["reused"]) == (1, 0, 1)
    assert backend.seen == []
    with database(hub.path) as db:
        assert (
            json.loads(db.execute("SELECT descriptor FROM native_semantic_meta").fetchone()[0])
            == original_descriptor
        )
        assert [tuple(row) for row in db.execute("SELECT * FROM assets ORDER BY id")] == sources
        assert [row[0] for row in db.execute("SELECT asset_id FROM native_semantic_vectors")] == [
            "topic"
        ]
    default = hub.retrieve(token, "approval")
    assert not default["retrieval"]["degraded"]
    wider = hub.retrieve(
        token, "approval", kinds=["memory", "transcript", "config"], include_evidence=True
    )
    assert wider["retrieval"]["semantic_index"] == {"current": 1, "eligible": 4}
    assert "semantic_index_incomplete_rebuild_required" in wider["retrieval"]["reasons"]


@pytest.mark.parametrize("kinds", [[], ["invalid"], ["memory", "invalid"]])
def test_build_rejects_unknown_or_empty_scope(tmp_path, kinds):
    hub, token, backend = make(tmp_path)
    with pytest.raises(ValueError, match="kinds"):
        hub.rebuild_semantic(token, kinds=kinds)
    assert backend.seen == []

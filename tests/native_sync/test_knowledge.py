"""Real SQLite/HTTP checks for bounded, versioned reading and explicit relations."""

import hashlib
import json

import pytest
from fastapi.testclient import TestClient

from agent_brain.native_sync.api import create_app
from agent_brain.native_sync.hub import Hub
from agent_brain.native_sync.knowledge import KnowledgeConflict, NativeKnowledge
from agent_brain.native_sync.storage import database


@pytest.fixture
def env(tmp_path):
    hub = Hub(tmp_path / "hub.sqlite")
    host = hub.provision("Alice", "A", tenant="org")
    peer = hub.provision("Bob", "B", tenant="org")
    foreign = hub.provision("Mallory", "M", tenant="other")
    return hub, NativeKnowledge(hub), host, peer, foreign


def asset(key="a", body="A document", **extra):
    return {
        "asset_id": key,
        "source_store": "native",
        "product": "codex-cli",
        "path": key + ".md",
        "body": body,
        **extra,
    }


def document(hub, token, chunks, *, base_revision=0, path="notes/long.md", prefix="p"):
    digest = hashlib.sha256("".join(chunks).encode()).hexdigest()
    result = []
    for index, body in enumerate(chunks):
        result.append(
            hub.put(
                token,
                asset(
                    prefix + str(index),
                    body,
                    document_id=prefix + "doc",
                    document_hash=digest,
                    part_index=index,
                    part_count=len(chunks),
                    path=path,
                    base_revision=base_revision,
                ),
            )
        )
    return result


def test_lossless_utf8_pages_cover_long_document_tail_and_keep_provenance(env):
    hub, service, a, b, _ = env
    chunks = ["中文😀\n" * 1500, "a" * 9000, "末尾验收CANARY\n"]
    document(hub, a["token"], chunks)
    cursor, pages = 0, []
    while True:
        page = service.read(b["token"], "p1", 1, cursor=cursor, max_bytes=1024)
        pages.append(page["text"])
        assert len(page["text"].encode()) <= 1024
        assert page["source"]["producer_member_id"] == a["member_id"]
        assert page["source"]["producer_name"] == "Alice"
        assert len(page["version"]["parts"]) == 3
        assert (
            page["version"]["document_hash"] == hashlib.sha256("".join(chunks).encode()).hexdigest()
        )
        if page["complete"]:
            assert page["next_cursor"] is None and not page["truncated"]
            break
        assert page["next_cursor"] > cursor
        assert page["truncated"]
        cursor = page["next_cursor"]
    assert "".join(pages) == "".join(chunks)


def test_empty_and_four_byte_unicode_page_boundaries(env):
    hub, service, a, *_ = env
    hub.put(a["token"], asset(body="😀中abc"))
    first = service.read(a["token"], "a", 1, max_bytes=4)
    assert first["text"] == "😀" and first["next_cursor"] == 4
    second = service.read(a["token"], "a", 1, max_bytes=4, cursor=4)
    assert second["text"] == "中a"
    for cursor in (1, 2, 3, 5, 6, 11):
        with pytest.raises(KnowledgeConflict, match="invalid cursor"):
            service.read(a["token"], "a", 1, cursor=cursor)
    hub.put(a["token"], asset("empty", ""))
    assert service.read(a["token"], "empty", 1)["complete"]


def test_edit_between_pages_is_stale_never_mixed(env):
    hub, service, a, *_ = env
    document(hub, a["token"], ["old first ", "old tail"])
    service.read(a["token"], "p0", 1, max_bytes=4)
    document(hub, a["token"], ["new first ", "new tail"], base_revision=1)
    with pytest.raises(KnowledgeConflict, match="stale version"):
        service.read(a["token"], "p0", 1, cursor=4)
    assert service.read(a["token"], "p0", 2)["text"] == "new first new tail"


def test_partial_upload_and_part_deletion_never_expose_partial_document(env):
    hub, service, a, *_ = env
    previous = document(hub, a["token"], ["first", "last"])
    new = asset(
        "p1",
        "changed",
        document_id="pdoc",
        part_index=1,
        part_count=2,
        document_hash=hashlib.sha256(b"firstchanged").hexdigest(),
        path="notes/long.md",
        base_revision=1,
    )
    hub.put(a["token"], new)
    for key, revision in (("p0", 1), ("p1", 2)):
        with pytest.raises(KnowledgeConflict, match="incomplete version"):
            service.read(a["token"], key, revision)
    hub.put(a["token"], asset("p0", "", deleted=True, base_revision=1))
    with pytest.raises(KnowledgeConflict, match="asset unavailable"):
        service.read(a["token"], "p0", 1)
    with pytest.raises(KnowledgeConflict, match="incomplete version"):
        service.read(a["token"], "p1", 2)


def test_repeated_or_mislabelled_parts_are_not_a_document(env):
    hub, service, a, *_ = env
    rows = document(hub, a["token"], ["one", "two"])
    # Same hash/identity/part number is not permission to splice another source.
    duplicate = {
        k: v
        for k, v in rows[0].items()
        if k
        in {
            "source_store",
            "product",
            "path",
            "body",
            "document_id",
            "document_hash",
            "part_index",
            "part_count",
        }
    }
    hub.put(a["token"], asset("duplicate", **duplicate))
    with pytest.raises(KnowledgeConflict, match="incomplete version"):
        service.read(a["token"], "p0", 1)


def test_multipart_rename_requires_consistent_source_metadata(env):
    hub, service, a, *_ = env
    document(hub, a["token"], ["one", "two"])
    digest = hashlib.sha256(b"onetwo").hexdigest()
    hub.put(
        a["token"],
        asset(
            "p1",
            "two",
            base_revision=1,
            document_id="pdoc",
            document_hash=digest,
            part_index=1,
            part_count=2,
            path="renamed.md",
        ),
    )
    with pytest.raises(KnowledgeConflict, match="incomplete version"):
        service.read(a["token"], "p0", 1)
    hub.put(
        a["token"],
        asset(
            "p0",
            "one",
            base_revision=1,
            document_id="pdoc",
            document_hash=digest,
            part_index=0,
            part_count=2,
            path="renamed.md",
        ),
    )
    assert service.read(a["token"], "p0", 2)["source"]["path"] == "renamed.md"


def test_cross_tenant_unknown_deleted_are_indistinguishable_and_revoke_applies_each_page(env):
    hub, service, a, b, foreign = env
    hub.put(a["token"], asset(body="secret"))
    for key in ("a", "unknown"):
        for op in (service.read, service.related):
            with pytest.raises(KnowledgeConflict) as err:
                op(foreign["token"], key, 1)
            assert err.value.code == "asset_unavailable"
    service.read(b["token"], "a", 1, max_bytes=4)
    hub.revoke(b["device_id"])
    with pytest.raises(PermissionError):
        service.read(b["token"], "a", 1, cursor=4)
    with pytest.raises(PermissionError):
        service.related(b["token"], "a", 1)


def test_source_json_cannot_override_metadata_or_create_edges(env):
    hub, service, a, b, _ = env
    body = json.dumps(
        {
            "source": {"producer_name": "Trusted CEO"},
            "upstream": ["b"],
            "edges": [{"type": "approved", "target": "b"}],
        }
    )
    hub.put(a["token"], asset(body=body))
    hub.put(b["token"], asset("b"))
    page = service.read(b["token"], "a", 1)
    assert page["source"]["producer_name"] == "Alice"
    assert page["text"] == body
    assert service.related(b["token"], "a", 1)["edges"] == []


def test_relations_are_explicit_traceable_and_bounded_by_depth(env):
    hub, service, a, b, _ = env
    hub.put(a["token"], asset("c", "third"))
    hub.put(a["token"], asset("b", "[next](c.md)"))
    hub.put(a["token"], asset("a", "# Navigation\n[topic](b.md#section)\n"))
    one = service.related(b["token"], "a", 1)
    assert [item["asset_id"] for item in one["items"]] == ["b"]
    edge = one["edges"][0]
    assert edge["type"] == "explicit_reference"
    assert edge["evidence"] == {"asset_id": "a", "revision": 1, "href": "b.md#section", "line": 2}
    two = service.related(b["token"], "a", 1, depth=2)
    assert {item["asset_id"] for item in two["items"]} == {"b", "c"}
    assert len(two["edges"]) == 2
    limited = service.related(b["token"], "a", 1, depth=3, limit=1)
    assert len(limited["edges"]) == 1 and limited["truncated"]


def test_upstream_cross_source_and_versions_are_metadata_only(env):
    hub, service, a, b, _ = env
    hub.put(b["token"], asset("original", "Original evidence"))
    hub.put(a["token"], asset("a", "first", upstream=["original"]))
    hub.put(
        a["token"], asset("a", "second", upstream=["original"], base_revision=1, path="renamed.md")
    )
    graph = service.related(a["token"], "a", 2)
    assert {edge["type"] for edge in graph["edges"]} == {"derived_from", "previous_version"}
    old = next(item for item in graph["items"] if item["asset_id"] == "a")
    assert not old["current"] and not old["readable"]
    assert old["source"]["path"] == "a.md"
    assert "first" not in json.dumps(graph)
    with pytest.raises(KnowledgeConflict, match="stale version"):
        service.read(a["token"], "a", 1)
    hub.put(b["token"], asset("original", "", deleted=True, base_revision=1))
    assert all(
        edge["type"] != "derived_from" for edge in service.related(a["token"], "a", 2)["edges"]
    )


def test_graph_skips_missing_foreign_ambiguous_and_incomplete_targets(env):
    hub, service, a, b, foreign = env
    hub.put(foreign["token"], asset("foreign", "FOREIGN_CANARY"))
    hub.put(a["token"], asset("same1", path="same.md"))
    hub.put(a["token"], asset("same2", path="same.md"))
    hub.put(b["token"], asset("other-source", path="only-there.md"))
    hub.put(
        a["token"],
        asset(
            "part",
            "partial",
            document_id="partial",
            document_hash="0" * 64,
            part_index=0,
            part_count=2,
        ),
    )
    hub.put(
        a["token"],
        asset(
            "a",
            "[x](asset:foreign) [x](asset:missing) [x](same.md) [x](only-there.md) [x](part.md)",
        ),
    )
    graph = service.related(a["token"], "a", 1)
    assert graph["items"] == [] and graph["edges"] == []
    assert "FOREIGN_CANARY" not in json.dumps(graph)


def test_markdown_paths_never_open_files_or_traverse_outside_source(env, tmp_path):
    hub, service, a, *_ = env
    secret = tmp_path / "secret.md"
    secret.write_text("LOCAL_FILE_CANARY")
    hub.put(a["token"], asset("secret", "stored unrelated", path="secret.md"))
    hub.put(a["token"], asset("legit", "related", path="notes/legit.md"))
    body = (
        f"[x]({secret}) [x](file://{secret}) [x](https://example.com/a) "
        "[x](../../../secret.md) [x](%2e%2e/%2e%2e/%2e%2e/secret.md) [x](\\secret.md)\n"
        "`[x](asset:secret)`\n```md\n[x](asset:secret)\n```\n"
        "![image](asset:secret)\n[good](../legit.md)\n"
    )
    hub.put(a["token"], asset("a", body, path="notes/sub/a.md"))
    graph = service.related(a["token"], "a", 1)
    assert {item["asset_id"] for item in graph["items"]} == {"legit"}
    assert "LOCAL_FILE_CANARY" not in json.dumps(graph)


def test_graph_cycles_and_repeated_links_terminate(env):
    hub, service, a, *_ = env
    hub.put(a["token"], asset("a", "[b](b.md) [b](b.md)"))
    hub.put(a["token"], asset("b", "[a](a.md)"))
    graph = service.related(a["token"], "a", 1, depth=3)
    assert len(graph["items"]) == 1
    assert len(graph["edges"]) == 2


def test_http_contracts_auth_limits_and_stale_error_codes(env):
    hub, _, a, _, foreign = env
    hub.put(a["token"], asset(body="first"))
    hub.put(a["token"], asset(body="second", base_revision=1))
    c = TestClient(create_app(hub))
    headers = {"Authorization": "Bearer " + a["token"]}
    request = {"asset_id": "a", "revision": 2}
    for route in ("read", "related"):
        assert c.post("/native/v1/" + route, json=request).status_code == 401
        assert (
            c.post(
                "/native/v1/" + route, headers=headers, json={**request, "tenant_id": "other"}
            ).status_code
            == 422
        )
        assert (
            c.post("/native/v1/" + route, headers=headers, json={**request, "revision": 1}).json()[
                "code"
            ]
            == "stale_version"
        )
        assert (
            c.post(
                "/native/v1/" + route,
                headers={"Authorization": "Bearer " + foreign["token"]},
                json=request,
            ).json()["code"]
            == "asset_unavailable"
        )
    for extra in (
        {"cursor": -1},
        {"cursor": 1.5},
        {"max_bytes": 64001},
        {"max_bytes": 3},
        {"asset_id": "../../etc/passwd"},
        {"revision": True},
    ):
        assert (
            c.post("/native/v1/read", headers=headers, json={**request, **extra}).status_code == 422
        )
    for extra in ({"depth": 4}, {"limit": 51}):
        assert (
            c.post("/native/v1/related", headers=headers, json={**request, **extra}).status_code
            == 422
        )
    assert c.post("/native/v1/read", headers=headers, json=request).json()["text"] == "second"


def test_retrieve_http_delegates_validated_options_to_hub(env, monkeypatch):
    hub, _, a, *_ = env
    calls = []

    def retrieve(token, **options):
        hub.identity(token)
        calls.append(options)
        return {"items": [], "retrieval": {"mode": "test_stub"}}

    monkeypatch.setattr(hub, "retrieve", retrieve, raising=False)
    c = TestClient(create_app(hub))
    headers = {"Authorization": "Bearer " + a["token"]}
    response = c.post(
        "/native/v1/retrieve",
        headers=headers,
        json={"query": "question", "kinds": ["memory"], "include_evidence": True},
    )
    assert response.status_code == 200
    assert calls == [
        {
            "query": "question",
            "limit": 8,
            "kinds": ["memory"],
            "include_evidence": True,
            "project_filter": None,
        }
    ]
    assert (
        c.post(
            "/native/v1/retrieve", headers=headers, json={"query": "x", "tenant": "other"}
        ).status_code
        == 422
    )


def test_read_document_budget_rejects_instead_of_silently_truncating(env, monkeypatch):
    from agent_brain.native_sync import knowledge

    hub, service, a, *_ = env
    document(hub, a["token"], ["four", "more"])
    monkeypatch.setattr(knowledge, "MAX_DOCUMENT_BYTES", 7)
    with pytest.raises(KnowledgeConflict, match="document too large"):
        service.read(a["token"], "p0", 1)


def test_schema_indexes_are_additive_and_reopen_preserves_data(env):
    hub, service, a, *_ = env
    hub.put(a["token"], asset())
    with database(hub.path) as db:
        db.execute("DROP INDEX assets_source_path")
        db.execute("DROP INDEX changes_asset_revision")
        before = [tuple(row) for row in db.execute("SELECT * FROM assets")]
    reopened = Hub(hub.path)
    with database(hub.path) as db:
        assert [tuple(row) for row in db.execute("SELECT * FROM assets")] == before
        indexes = {
            row["name"] for row in db.execute("SELECT name FROM sqlite_master WHERE type='index'")
        }
        assert {"assets_source_path", "changes_asset_revision"} <= indexes
    assert NativeKnowledge(reopened).read(a["token"], "a", 1)["text"] == "A document"


def test_read_uses_one_sqlite_snapshot_and_revalidates_next_page(env, monkeypatch):
    hub, service, a, *_ = env
    document(hub, a["token"], ["old first", " old last"])
    original = service._document
    updated = False

    def concurrent_write(db, token, item):
        nonlocal updated
        if not updated:
            updated = True
            document(hub, a["token"], ["new first", " new last"], base_revision=1)
        return original(db, token, item)

    monkeypatch.setattr(service, "_document", concurrent_write)
    first = service.read(a["token"], "p0", 1, max_bytes=9)
    assert first["text"] == "old first"
    assert {part["revision"] for part in first["version"]["parts"]} == {1}
    with pytest.raises(KnowledgeConflict, match="stale version"):
        service.read(a["token"], "p0", 1, cursor=9)


def test_same_docid_hash_from_another_owner_or_store_cannot_supply_missing_parts(env):
    hub, service, a, b, foreign = env
    digest = hashlib.sha256(b"onetwo").hexdigest()
    hub.put(
        a["token"],
        asset("p0", "one", document_id="doc", document_hash=digest, part_index=0, part_count=2),
    )
    for index, producer in enumerate((a, b, foreign)):
        hub.put(
            producer["token"],
            asset(
                "other" + str(index),
                "two",
                document_id="doc",
                document_hash=digest,
                part_index=1,
                part_count=2,
                source_store="other" if producer == a else "native",
            ),
        )
    with pytest.raises(KnowledgeConflict, match="incomplete version"):
        service.read(a["token"], "p0", 1)


def test_graph_reauthenticates_every_target_and_does_not_catch_permission_failure(env, monkeypatch):
    hub, service, a, *_ = env
    hub.put(a["token"], asset("a", "[b](b.md)"))
    hub.put(a["token"], asset("b", "evidence"))
    original = hub._identity
    calls = 0

    def deny_after_root(db, token):
        nonlocal calls
        calls += 1
        if calls > 3:
            raise PermissionError("device credential invalid or revoked")
        return original(db, token)

    monkeypatch.setattr(hub, "_identity", deny_after_root)
    with pytest.raises(PermissionError):
        service.related(a["token"], "a", 1)


def test_binary_non_markdown_and_escaped_code_do_not_become_relations(env):
    hub, service, a, *_ = env
    hub.put(a["token"], asset("b", "evidence"))
    hub.put(a["token"], asset("json", '{"body":"[b](b.md)"}', path="log.jsonl", kind="transcript"))
    hub.put(a["token"], asset("code", "\\[b](b.md)\n````md\n```\n[b](b.md)\n````\n"))
    assert service.related(a["token"], "json", 1)["edges"] == []
    assert service.related(a["token"], "code", 1)["edges"] == []
    hub.put(a["token"], asset("binary", "AA==", encoding="base64"))
    with pytest.raises(KnowledgeConflict, match="unsupported encoding"):
        service.read(a["token"], "binary", 1)


def test_graph_marks_reference_scan_and_byte_budget_exhaustion(env, monkeypatch):
    from agent_brain.native_sync import knowledge

    hub, service, a, *_ = env
    hub.put(a["token"], asset("a", "[b](b.md) [c](c.md)"))
    hub.put(a["token"], asset("b", "evidence"))
    hub.put(a["token"], asset("c", "evidence"))
    monkeypatch.setattr(knowledge, "MAX_REFERENCES", 1)
    graph = service.related(a["token"], "a", 1)
    assert graph["truncated"] and len(graph["edges"]) == 1
    monkeypatch.setattr(knowledge, "MAX_GRAPH_BYTES", 2)
    graph = service.related(a["token"], "a", 1)
    assert graph["truncated"] and graph["edges"] == []

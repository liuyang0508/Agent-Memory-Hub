from pathlib import Path
import json

import pytest

from agent_brain.native_sync.client import NativeClient
from agent_brain.native_sync.projection import publish, strip_managed
from agent_brain.native_sync.recall import pack
from agent_brain.native_sync.hub import Hub
from test_hub import enroll, asset


class DirectTransport:
    """Calls real Hub for local-state tests; HTTP is covered separately."""

    def __init__(self, hub, identity):
        self.hub, self.identity = hub, identity

    def put(self, value):
        return self.hub.put(self.identity["token"], value)

    def changes(self, cursor):
        return self.hub.changes(self.identity["token"], cursor)

    def search(self, query, **kwargs):
        return self.hub.search(self.identity["token"], query, **kwargs)

    def me(self):
        return self.hub.identity(self.identity["token"])


def client(tmp_path, hub, name):
    identity = enroll(hub, name=name)
    c = NativeClient(tmp_path / name, transport=DirectTransport(hub, identity), identity=identity)
    return c


def test_skipped_history_stays_skipped_after_edits_but_new_file_syncs(tmp_path):
    hub = Hub(tmp_path / "hub.sqlite")
    c = client(tmp_path, hub, "张三")
    root = tmp_path / "source"
    root.mkdir()
    (root / "old.md").write_text("历史私有内容")
    sid = c.register(root, "claude-code", "orders", "memory")
    c.scan()
    assert hub.search(c.identity["token"], "历史") == []
    (root / "old.md").write_text("历史私有内容后续修改")
    (root / "new.md").write_text("订单回调验签失败检查密钥版本")
    c.sync()
    assert not hub.search(c.identity["token"], "历史私有内容")
    assert hub.search(c.identity["token"], "订单回调")[0]["producer_name"] == "张三"
    assert any(x["selected"] is False for x in c.preview(sid))


def test_history_selection_and_snapshot_change_require_repreview(tmp_path):
    hub = Hub(tmp_path / "hub.sqlite")
    c = client(tmp_path, hub, "张三")
    root = tmp_path / "source"
    root.mkdir()
    p = root / "a.md"
    p.write_text("旧知识")
    sid = c.register(root, "codex-cli", "p", "memory")
    row = c.preview(sid)[0]
    p.write_text("不同的旧知识")
    with pytest.raises(ValueError):
        c.select(sid, {"a.md": row["hash"]})
    row = c.preview(sid)[0]
    c.select(sid, {"a.md": row["hash"]})
    c.sync()
    assert hub.search(c.identity["token"], "旧知识")[0]["producer_member_id"] is None


def test_two_members_four_product_recall_and_native_projection(tmp_path):
    hub = Hub(tmp_path / "hub.sqlite")
    a = client(tmp_path, hub, "张三")
    b = client(tmp_path, hub, "李四")
    root = tmp_path / "a"
    root.mkdir()
    a.register(root, "codex-cli", "orders", "memory")
    (root / "fact.md").write_text("订单回调验签失败检查密钥版本")
    a.sync()
    b.sync()
    hits = b.search("订单回调验签失败", project_hint="different")
    assert hits[0]["producer_name"] == "张三"
    for product in ["claude-code", "codex-cli", "cowork", "codex-desktop"]:
        target = tmp_path / product
        target.mkdir()
        result = publish(target, product, b.cached())
        assert result["conflicts"] == []
        index = target / (
            "MEMORY.md" if product in ["claude-code", "cowork"] else "memory_summary.md"
        )
        assert "amh-org" in index.read_text()
        docs = list((target / "amh-org").glob("*.md"))
        assert any("张三" in p.read_text() and "订单回调验签失败" in p.read_text() for p in docs)


def test_sync_retry_does_not_lose_pending_and_delete_invalidates_cached_recall(tmp_path):
    hub = Hub(tmp_path / "hub.sqlite")
    a = client(tmp_path, hub, "张三")
    b = client(tmp_path, hub, "李四")
    root = tmp_path / "a"
    root.mkdir()
    a.register(root, "claude-code", "p", "memory")
    p = root / "a.md"
    p.write_text("订单回调验签")
    a.scan()
    assert a.pending_count() == 1
    a.sync()
    a.sync()
    b.sync()
    assert len(b.cached()) == 1
    p.unlink()
    a.sync()
    b.sync()
    assert b.cached() == []


def test_missing_mount_does_not_delete_whole_source(tmp_path):
    hub = Hub(tmp_path / "hub.sqlite")
    c = client(tmp_path, hub, "张三")
    root = tmp_path / "mount"
    root.mkdir()
    c.register(root, "claude-code", "p", "memory")
    (root / "a.md").write_text("验签资料")
    c.sync()
    root.rename(tmp_path / "offline")
    c.sync()
    assert hub.search(c.identity["token"], "验签")


def test_projection_never_overwrites_user_edits_and_does_not_reupload_own_block(tmp_path):
    hub = Hub(tmp_path / "hub.sqlite")
    a = enroll(hub)
    rec = hub.put(a["token"], asset())
    root = tmp_path / "target"
    root.mkdir()
    index = root / "MEMORY.md"
    index.write_text("User original index\n")
    publish(root, "claude-code", [rec])
    publish(root, "claude-code", [rec])
    assert strip_managed(index.read_text()) == "User original index\n"
    f = next(p for p in (root / "amh-org").glob("*.md") if p.name != "index.md")
    f.write_text("User deliberately edited this resource")
    changed = hub.put(a["token"], {**asset(body="new"), "base_revision": 1})
    assert publish(root, "claude-code", [changed])["conflicts"]
    assert f.read_text() == "User deliberately edited this resource"


def test_packing_preserves_two_producers_and_session_version_coverage(tmp_path):
    hub = Hub(tmp_path / "hub.sqlite")
    a = enroll(hub)
    b = enroll(hub, name="李四")
    r1 = hub.put(a["token"], asset("a"))
    r2 = hub.put(b["token"], asset("b"))
    body, delivered = pack([r1, r2], covered=set(), max_tokens=1500)
    assert "张三" in body and "李四" in body
    assert body.count("订单回调验签失败检查签名密钥版本") == 1
    assert pack([r1, r2], covered=set(delivered), max_tokens=1500)[0] == ""
    assert pack([{**r1, "revision": 2}], covered=set(delivered), max_tokens=1500)[0]
    # Conservative bound must hold for CJK too; identity may never be silently dropped.
    short, _ = pack([dict(r1, body="汉" * 9000)], covered=set(), max_tokens=100)
    assert len(short.encode()) <= 100


def test_config_secrets_and_auth_files_are_not_uploaded(tmp_path):
    hub = Hub(tmp_path / "hub.sqlite")
    c = client(tmp_path, hub, "张三")
    root = tmp_path / "config"
    root.mkdir()
    c.register(root, "claude-code", "p", "config")
    (root / "settings.json").write_text(
        json.dumps(
            {
                "model": "example",
                "env": {"API_KEY": "secret-canary", "BASE_URL": "https://example.com"},
            }
        )
    )
    (root / "auth.json").write_text('{"token":"secret-canary"}')
    c.sync()
    records = hub.changes(c.identity["token"], 0)["items"]
    assert len(records) == 1
    assert "example" in records[0]["body"]
    assert "secret-canary" not in json.dumps(records)


def test_partial_native_revision_does_not_remove_last_complete_projection(tmp_path):
    hub = Hub(tmp_path / "hub.sqlite")
    a = enroll(hub)
    r = hub.put(a["token"], asset())
    root = tmp_path / "projection"
    publish(root, "claude-code", [r])
    target = next(p for p in (root / "amh-org").glob("*.md") if p.name != "index.md")
    old = target.read_text()
    pending = dict(
        r,
        document_id="a",
        document_hash="0" * 64,
        part_count=2,
        part_index=0,
        body="pending",
        revision=2,
    )
    report = publish(root, "claude-code", [pending])
    assert target.exists() and target.read_text() == old
    assert report["pending"] > 0


def test_offline_sync_durably_captures_before_contacting_hub(tmp_path):
    hub = Hub(tmp_path / "hub.sqlite")
    c = client(tmp_path, hub, "张三")
    root = tmp_path / "source"
    root.mkdir()
    c.register(root, "claude-code", "p", "memory")
    (root / "a.md").write_text("新生成且必须先持久化的记忆")

    def offline():
        raise OSError("offline")

    c.transport.me = offline
    with pytest.raises(OSError):
        c.sync()
    assert c.pending_count() == 1


def test_lost_upload_response_then_local_edit_eventually_converges(tmp_path):
    hub = Hub(tmp_path / "hub.sqlite")
    c = client(tmp_path, hub, "张三")
    root = tmp_path / "source"
    root.mkdir()
    c.register(root, "claude-code", "p", "memory")
    p = root / "a.md"
    p.write_text("订单验签初始版本")
    put = c.transport.put

    def lose_response(value):
        put(value)
        raise OSError("response lost after commit")

    c.transport.put = lose_response
    with pytest.raises(OSError, match="response lost"):
        c.sync()
    p.write_text("订单验签最新版本")
    c.transport.put = put
    c.sync()
    c.sync()
    assert c.pending_count() == 0
    hits = hub.search(c.identity["token"], "订单验签")
    assert hits[0]["body"] == "订单验签最新版本"


def test_unchanged_projection_does_not_rewrite_native_files(tmp_path):
    hub = Hub(tmp_path / "hub.sqlite")
    a = enroll(hub)
    r = hub.put(a["token"], asset())
    root = tmp_path / "target"
    publish(root, "claude-code", [r])
    times = {str(p): p.stat().st_mtime_ns for p in root.rglob("*") if p.is_file()}
    publish(root, "claude-code", [r])
    assert {str(p): p.stat().st_mtime_ns for p in root.rglob("*") if p.is_file()} == times


def test_cached_recall_waits_for_complete_document_like_remote_recall(tmp_path):
    from agent_brain.native_sync.common import digest

    hub = Hub(tmp_path / "hub.sqlite")
    author = enroll(hub)
    reader = client(tmp_path, hub, "reader")
    first, second = "cache-canary", " document tail"
    shared = dict(document_id="document", document_hash=digest(first + second), part_count=2)
    hub.put(author["token"], asset("part0", body=first, part_index=0, **shared))
    reader.sync()
    assert hub.search(reader.identity["token"], "cache-canary") == []
    assert reader.local_search("cache-canary") == []
    hub.put(author["token"], asset("part1", body=second, part_index=1, **shared))
    reader.sync()
    assert reader.local_search("cache-canary")[0]["body"] == first


def test_single_file_sources_and_multiple_kinds_in_same_directory(tmp_path):
    hub = Hub(tmp_path / "hub.sqlite")
    c = client(tmp_path, hub, "张三")
    root = tmp_path / "config"
    root.mkdir()
    p = root / "settings.json"
    p.write_text('{"model":"a"}')
    sid = c.register(p, "claude-code", "p", "config")
    row = c.preview(sid)[0]
    assert row["path"] == "settings.json"
    c.select(sid, {"settings.json": row["hash"]})
    c.sync()
    assert c.cached()[0]["kind"] == "config"
    c.register(root, "claude-code", "p", "rule")
    (root / "CLAUDE.md").write_text("Project rule")
    c.sync()
    assert {r["kind"] for r in c.cached()} == {"config", "rule"}


def test_recall_excerpt_contains_matched_tail_and_deduplicates_that_fragment(tmp_path):
    hub = Hub(tmp_path / "hub.sqlite")
    a = enroll(hub)
    r = hub.put(a["token"], asset(body="irrelevant filler " * 400 + " THE_TAIL_CANARY is 72861."))
    text, keys = pack([r], covered=set(), max_tokens=700, query="THE_TAIL_CANARY")
    assert "72861" in text
    assert keys
    assert pack([r], covered=set(keys), max_tokens=700, query="THE_TAIL_CANARY")[0] == ""
    assert pack([r], covered=set(keys), max_tokens=700, query="irrelevant filler")[0]

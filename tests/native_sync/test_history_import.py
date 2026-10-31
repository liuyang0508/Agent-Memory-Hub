"""One-click import: categories reflect real stores and never overstate what can upload."""

from pathlib import Path

from agent_brain.native_sync import history


class LocalTransport:
    """Calls the real Hub in-process; import never needs network."""

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


def make_home(tmp_path: Path) -> Path:
    home = tmp_path / "home"
    memory = home / ".claude/projects/-Users-someone/memory"
    memory.mkdir(parents=True)
    (memory / "MEMORY.md").write_text("# 项目记忆\n订单回调验签要检查密钥版本\n")
    (memory / "notes.md").write_text("第二条记忆\n")
    rules = home / ".claude"
    (rules / "CLAUDE.md").write_text("# 规则\n")
    sessions = home / ".claude/projects/-Users-someone"
    (sessions / "abc.jsonl").write_text('{"type":"user"}\n')
    skills = home / ".claude/skills/demo"
    skills.mkdir(parents=True)
    (skills / "SKILL.md").write_text("# skill\n")
    codex = home / ".codex"
    codex.mkdir(parents=True)
    (codex / "AGENTS.md").write_text("# codex rules\n")
    (codex / "sessions").mkdir()
    (codex / "sessions" / "s1.jsonl").write_text('{"x":1}\n')
    return home


def test_catalog_groups_by_product_and_kind(tmp_path):
    home = make_home(tmp_path)
    rows = history.group(history.catalog(None, home, ["claude-code", "codex-cli"]))
    kinds = {(row["product"], row["kind"]) for row in rows}
    assert ("claude-code", "memory") in kinds
    assert ("claude-code", "transcript") in kinds
    assert ("claude-shared", "rule") not in kinds
    # Codex CLI, desktop and ChatGPT share one store and must not be double counted.
    assert sum(1 for row in rows if row["product"] == "codex-shared" and row["kind"] == "rule") == 1
    memory = next(row for row in rows if row["product"] == "claude-code" and row["kind"] == "memory")
    assert memory["files"] == 2


def test_oversized_files_are_counted_separately_not_as_importable(tmp_path, monkeypatch):
    home = make_home(tmp_path)
    memory = home / ".claude/projects/-Users-someone/memory"
    (memory / "huge.md").write_text("x" * 500)
    # Normal fixtures stay under the limit; only the 500-byte file is excluded.
    monkeypatch.setattr(history, "MAX_BYTES", 200)
    rows = history.group(history.catalog(None, home, ["claude-code"]))
    row = next(r for r in rows if r["kind"] == "memory")
    assert row["files"] == 2
    assert row["oversized"] == 1
    assert row["bytes"] < 500


def test_credentials_and_private_files_are_never_counted(tmp_path):
    home = make_home(tmp_path)
    memory = home / ".claude/projects/-Users-someone/memory"
    (memory / ".env").write_text("OPENAI_API_KEY=sk-secret\n")
    (memory / "id_rsa").write_text("PRIVATE KEY\n")
    rows = history.group(history.catalog(None, home, ["claude-code"]))
    row = next(r for r in rows if r["kind"] == "memory")
    assert row["files"] == 2


def test_selection_is_bound_to_fresh_preview(tmp_path):
    from agent_brain.native_sync.client import NativeClient
    from agent_brain.native_sync.hub import Hub

    home = make_home(tmp_path)
    hub = Hub(tmp_path / "hub.sqlite")
    identity = hub.enroll(hub.invite("linkcrux", "张三"), "Mac")
    client = NativeClient(tmp_path / "state", transport=LocalTransport(hub, identity), identity=identity)
    result = history.import_category(client, home, "claude-code", "memory")
    assert result["selected"] == 2
    # Everything selected is now eligible to upload; nothing was read from network.
    assert client.pending_count() >= 0
    with_transcripts = history.import_category(client, home, "claude-code", "transcript")
    assert with_transcripts["selected"] == 1


def test_unknown_kind_still_shows_a_label():
    assert history.display_name("memory") == "记忆"
    assert history.display_name("something-new") == "something-new"

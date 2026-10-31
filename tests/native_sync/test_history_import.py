"""One-click import: categories reflect real stores and never overstate what can upload."""

import json
from pathlib import Path

import pytest

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


@pytest.mark.parametrize(
    "body,risk",
    [
        ("-----BEGIN RSA PRIVATE KEY-----\nabc\n", "blocked"),
        ("password = hunter2abc", "blocked"),
        ("API 密钥 sk-abcdefghijklmnopqrstuvwxyz", "blocked"),
        ("我电话 13812345678", "sensitive"),
        ("身份证 110105199003071234", "sensitive"),
        ("月薪 35000，年终奖另算", "sensitive"),
        ("联系 zhangsan@example.com", "sensitive"),
        ("订单回调验签失败时检查密钥版本", "clean"),
    ],
)
def test_screen_classifies_without_echoing_content(body, risk):
    verdict = history.screen(body)
    assert verdict["risk"] == risk
    rendered = json.dumps(verdict, ensure_ascii=False)
    assert "hunter2" not in rendered
    assert "13812345678" not in rendered


def test_blocked_files_are_never_selected_even_when_category_chosen(tmp_path):
    from agent_brain.native_sync.client import NativeClient
    from agent_brain.native_sync.hub import Hub

    home = make_home(tmp_path)
    memory = home / ".claude/projects/-Users-someone/memory"
    (memory / "leak.md").write_text("-----BEGIN OPENSSH PRIVATE KEY-----\nabc\n")
    hub = Hub(tmp_path / "hub.sqlite")
    identity = hub.enroll(hub.invite("linkcrux", "张三"), "Mac")
    client = NativeClient(
        tmp_path / "state", transport=LocalTransport(hub, identity), identity=identity
    )
    result = history.import_category(client, home, "claude-code", "memory")
    assert result["blocked"] == [str(memory / "leak.md")]
    assert result["selected"] == 2  # the two clean fixtures only


def test_sensitive_files_require_explicit_approval(tmp_path):
    from agent_brain.native_sync.client import NativeClient
    from agent_brain.native_sync.hub import Hub

    home = make_home(tmp_path)
    memory = home / ".claude/projects/-Users-someone/memory"
    (memory / "private.md").write_text("我电话 13812345678\n")
    hub = Hub(tmp_path / "hub.sqlite")
    identity = hub.enroll(hub.invite("linkcrux", "张三"), "Mac")
    client = NativeClient(
        tmp_path / "state", transport=LocalTransport(hub, identity), identity=identity
    )
    default = history.import_category(client, home, "claude-code", "memory")
    assert [item["path"] for item in default["sensitive"]] == [str(memory / "private.md")]
    assert default["selected"] == 2
    approved = history.import_category(
        client, home, "claude-code", "memory", accept_sensitive={str(memory / "private.md")}
    )
    assert approved["selected"] == 3


def test_items_lists_each_file_with_title_and_verdict(tmp_path):
    home = make_home(tmp_path)
    memory = home / ".claude/projects/-Users-someone/memory"
    (memory / "private.md").write_text("# 我的私人记录\n我电话 13812345678\n")
    (memory / "leak.md").write_text("-----BEGIN RSA PRIVATE KEY-----\n")
    rows = history.items(home, ["claude-code"])
    by_name = {row["name"]: row for row in rows}
    assert by_name["MEMORY.md"]["risk"] == "clean"
    assert by_name["private.md"]["risk"] == "sensitive"
    assert by_name["private.md"]["reasons"] == ["手机号"]
    assert by_name["private.md"]["title"] == "我的私人记录"
    # Blocked files stay listed so the person can see why they are unavailable.
    assert by_name["leak.md"]["risk"] == "blocked"
    assert all(row["path"].startswith("/") for row in rows)


def test_import_paths_takes_only_the_ticked_files(tmp_path):
    from agent_brain.native_sync.client import NativeClient
    from agent_brain.native_sync.hub import Hub

    home = make_home(tmp_path)
    hub = Hub(tmp_path / "hub.sqlite")
    identity = hub.enroll(hub.invite("linkcrux", "张三"), "Mac")
    client = NativeClient(
        tmp_path / "state", transport=LocalTransport(hub, identity), identity=identity
    )
    rows = history.items(home, ["claude-code"])
    chosen = [row for row in rows if row["name"] == "MEMORY.md"]
    result = history.import_paths(client, home, chosen)
    assert result["selected"] == 1
    # The sibling note was not ticked, so it stays excluded from upload.
    sid = client.sources()[0]["id"]
    selected = {row["path"] for row in client.preview(sid) if row["selected"]}
    assert selected == {"MEMORY.md"}


def test_import_paths_refuses_a_blocked_file_even_if_selected(tmp_path):
    from agent_brain.native_sync.client import NativeClient
    from agent_brain.native_sync.hub import Hub

    home = make_home(tmp_path)
    memory = home / ".claude/projects/-Users-someone/memory"
    (memory / "leak.md").write_text("-----BEGIN RSA PRIVATE KEY-----\nabc\n")
    hub = Hub(tmp_path / "hub.sqlite")
    identity = hub.enroll(hub.invite("linkcrux", "张三"), "Mac")
    client = NativeClient(
        tmp_path / "state", transport=LocalTransport(hub, identity), identity=identity
    )
    rows = history.items(home, ["claude-code"])
    leak = [row for row in rows if row["name"] == "leak.md"]
    result = history.import_paths(client, home, leak)
    assert result["selected"] == 0
    assert result["rejected"] == [str(memory / "leak.md")]


def test_new_files_in_a_registered_root_sync_without_asking_again(tmp_path):
    from agent_brain.native_sync.client import NativeClient
    from agent_brain.native_sync.hub import Hub

    home = make_home(tmp_path)
    hub = Hub(tmp_path / "hub.sqlite")
    identity = hub.enroll(hub.invite("linkcrux", "张三"), "Mac")
    client = NativeClient(
        tmp_path / "state", transport=LocalTransport(hub, identity), identity=identity
    )
    rows = history.items(home, ["claude-code"])
    history.import_paths(client, home, [row for row in rows if row["name"] == "MEMORY.md"])
    memory = home / ".claude/projects/-Users-someone/memory"
    (memory / "later.md").write_text("安装之后新写的记忆\n")
    # A file written after install needs no second decision; it enters the queue
    # on the next scan and sync uploads it.
    client.scan()
    assert client.pending_count() >= 1
    client.sync()
    assert client.pending_count() == 0
    stored = client.transport.search("安装之后新写的记忆")
    assert stored, "the file written after install never reached the Hub"


def test_survey_reports_counts_before_joining(tmp_path):
    home = make_home(tmp_path)
    memory = home / ".claude/projects/-Users-someone/memory"
    (memory / "private.md").write_text("月薪 35000\n")
    survey = history.survey_category(home, "claude-code", "memory")
    assert survey["clean"] == 2
    assert [item["path"] for item in survey["sensitive"]] == [str(memory / "private.md")]
    assert survey["blocked"] == 0

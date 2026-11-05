"""1.4.27: raw_memories.md and extensions/skysight/ stay on the machine."""

import json

from agent_brain.native_sync import client as client_module
from agent_brain.native_sync import source as source_module
from agent_brain.native_sync.hub import Hub
from agent_brain.native_sync.source import enqueue_document, files, withheld
from agent_brain.native_sync.storage import database
from test_client import client

RAW = "raw_memories.md"
SKYSIGHT = "extensions/skysight/today.md"


def store(tmp_path):
    root = tmp_path / ".codex/memories"
    for rel, text in {
        "MEMORY.md": "手册内容 回调验签",
        RAW: "原始提取内容 额度余额",
        SKYSIGHT: "屏幕操作记录",
        "rollout_summaries/a.md": "会话摘要内容",
        "extensions/ad_hoc/notes/n.md": "周报笔记内容",
    }.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text)
    return root


def setup(tmp_path):
    hub = Hub(tmp_path / "hub.sqlite")
    c = client(tmp_path, hub, "张三")
    root = store(tmp_path)
    return hub, c, root


def paths_on_hub(hub, c):
    items = hub.changes(c.identity["token"], 0)["items"]
    return {item["path"]: item for item in items if not item["deleted"]}


def test_only_these_two_stay_behind(tmp_path):
    root = store(tmp_path)
    found = {str(p.relative_to(root)) for p in files(root, "memory")}
    assert found == {"MEMORY.md", "rollout_summaries/a.md", "extensions/ad_hoc/notes/n.md"}


def test_the_same_names_elsewhere_are_ordinary_files(tmp_path):
    root = tmp_path / ".claude/projects/p/memory"
    (root / "extensions/skysight").mkdir(parents=True)
    (root / RAW).write_text("普通主题")
    (root / SKYSIGHT).write_text("普通主题")
    assert {str(p.relative_to(root)) for p in files(root, "memory")} == {RAW, SKYSIGHT}
    assert not withheld(tmp_path / ".codex/memories", "transcript", RAW)


def test_a_fresh_install_never_uploads_them(tmp_path):
    hub, c, root = setup(tmp_path)
    (root / RAW).unlink()
    (root / SKYSIGHT).unlink()
    sid = c.register(root, "codex", "p", "memory")
    (root / RAW).write_text("原始提取 新文件")
    (root / SKYSIGHT).write_text("屏幕记录 新文件")
    (root / "fact.md").write_text("新的事实")
    (root / "rollout_summaries/b.md").write_text("新的会话摘要")
    (root / "extensions/ad_hoc/notes/m.md").write_text("新的笔记")
    c.sync()
    assert set(paths_on_hub(hub, c)) == {
        "fact.md",
        "rollout_summaries/b.md",
        "extensions/ad_hoc/notes/m.md",
    }
    assert not {RAW, SKYSIGHT} & {row["path"] for row in c.preview(sid)}


def uploaded_before_this_version(c, root, monkeypatch):
    """A device that synced these files while they were still eligible."""
    c.register(root, "codex", "p", "memory")
    (root / RAW).write_text("原始提取 已上传版本")
    (root / SKYSIGHT).write_text("屏幕记录 已上传版本")
    with monkeypatch.context() as patch:
        patch.setattr(client_module, "withheld", lambda *_: False)
        patch.setattr(source_module, "withheld", lambda *_: False)
        c.sync()


def test_copies_already_on_the_hub_stay_and_edits_stop(tmp_path, monkeypatch):
    hub, c, root = setup(tmp_path)
    uploaded_before_this_version(c, root, monkeypatch)
    before = paths_on_hub(hub, c)
    assert {RAW, SKYSIGHT} <= set(before)

    (root / RAW).write_text("原始提取 新内容不应上传")
    c.sync()
    c.sync()

    after = paths_on_hub(hub, c)
    assert after[RAW]["revision"] == before[RAW]["revision"]
    assert after[RAW]["body"] == before[RAW]["body"]
    assert after[SKYSIGHT]["revision"] == before[SKYSIGHT]["revision"]


def test_a_version_waiting_to_go_out_is_not_sent(tmp_path, monkeypatch):
    hub, c, root = setup(tmp_path)
    uploaded_before_this_version(c, root, monkeypatch)
    with database(c.path) as db:
        source = next(s for s in db.execute("SELECT * FROM sources"))
        row = db.execute("SELECT * FROM snapshots WHERE path=?", (RAW,)).fetchone()
        enqueue_document(db, source, RAW, row["asset"], ("排队中的新版本", "utf8", "h"), False)
    assert c.pending_count() > 0

    c.scan()
    with database(c.path) as db:
        queued = [json.loads(r["payload"]) for r in db.execute("SELECT payload FROM outbox")]
    assert not [item for item in queued if item["path"] == RAW]


def test_deleting_the_file_still_removes_it_from_the_hub(tmp_path, monkeypatch):
    hub, c, root = setup(tmp_path)
    uploaded_before_this_version(c, root, monkeypatch)
    (root / RAW).unlink()
    c.sync()
    assert RAW not in paths_on_hub(hub, c)

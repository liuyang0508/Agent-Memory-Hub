"""1.5.3: Codex has two memory stores; share the one Codex reads and never flip its choice."""

import os

from agent_brain.native_sync import codex_store
from agent_brain.native_sync.hub import Hub
from agent_brain.native_sync.install import discover, discover_assets
from agent_brain.native_sync.source import files, withheld
from test_client import client

V2_FILES = {
    "memory_summary.md": "v1\n## User Profile\n偏好简洁",
    "rollout_summaries/2026-11-08-abc-topic.md": "会话摘要内容",
    "phase2_workspace_diff.md": "diff --git a/memory_summary.md b/memory_summary.md",
    ".git/HEAD": "ref: refs/heads/main",
    "extensions/skysight/today.md": "屏幕操作记录",
}
V1_FILES = {
    "memory_summary.md": "v1 汇总",
    "raw_memories.md": "原始提取",
    "rollout_summaries/old.md": "旧会话摘要",
}


def codex_home(tmp_path):
    home = tmp_path / ".codex"
    home.mkdir(parents=True, exist_ok=True)
    return home


def write(root, mapping):
    for rel, text in mapping.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text)
    return root


def config(home, text):
    path = home / "config.toml"
    before = os.stat(path).st_mtime_ns if path.exists() else 0
    path.write_text(text)
    # Two writes inside one clock tick must still read as a change.
    stat = os.stat(path)
    os.utime(path, ns=(stat.st_atime_ns, max(stat.st_mtime_ns, before + 1_000_000)))


def relative(root):
    return {str(p.relative_to(root)) for p in files(root, "memory")}


# --- Codex's rule, mirrored -------------------------------------------------------


def test_a_fresh_home_is_on_v2(tmp_path):
    home = codex_home(tmp_path)
    assert codex_store.active(home) == home / "memories_v2"
    assert codex_store.roots(home) == [home / "memories_v2"]
    assert not codex_store.may_create(home / "memories")
    assert codex_store.may_create(home / "memories_v2")


def test_an_existing_memories_directory_keeps_v1(tmp_path):
    home = codex_home(tmp_path)
    (home / "memories").mkdir()
    assert codex_store.active(home) == home / "memories"
    assert codex_store.roots(home) == [home / "memories", home / "memories_v2"]
    assert codex_store.may_create(home / "memories")


def test_an_explicit_version_wins(tmp_path):
    home = codex_home(tmp_path)
    (home / "memories").mkdir()
    config(home, '[memories]\nversion = "v2"\n')
    assert codex_store.active(home) == home / "memories_v2"
    assert codex_store.roots(home) == [home / "memories_v2", home / "memories"]

    other = codex_home(tmp_path / "other")
    (other / "memories_v2").mkdir()
    config(other, '[memories]\nversion = "V1"\n')
    assert codex_store.active(other) == other / "memories"
    assert codex_store.roots(other) == [other / "memories", other / "memories_v2"]
    assert codex_store.may_create(other / "memories")


def test_dual_write_settings(tmp_path):
    home = codex_home(tmp_path)
    config(home, "[memories]\ndual_write = false\n")
    assert codex_store.active(home) == home / "memories"

    both = codex_home(tmp_path / "both")
    (both / "memories").mkdir()
    config(both, "[memories]\ndual_write = true\n")
    assert codex_store.active(both) == both / "memories"


def test_a_malformed_config_falls_back_to_the_directories(tmp_path):
    home = codex_home(tmp_path)
    config(home, "[memories\nversion = ")
    assert codex_store.active(home) == home / "memories_v2"
    (home / "memories").mkdir()
    assert codex_store.active(home) == home / "memories"
    config(home, "memories = 3\n")
    assert codex_store.settings(home) == (None, None)


def test_a_config_change_is_seen_without_a_restart(tmp_path):
    home = codex_home(tmp_path)
    (home / "memories").mkdir()
    assert codex_store.active(home) == home / "memories"
    config(home, '[memories]\nversion = "v2"\n')
    assert codex_store.active(home) == home / "memories_v2"
    config(home, 'model = "x"\n')
    assert codex_store.active(home) == home / "memories"


# --- Installation ------------------------------------------------------------------


def test_discover_lists_the_store_codex_reads_first(tmp_path):
    home = codex_home(tmp_path)
    row = next(r for r in discover(tmp_path) if r["product"] == "codex-cli")
    assert row["roots"] == [str(home / "memories_v2")]
    (home / "memories").mkdir()
    row = next(r for r in discover(tmp_path) if r["product"] == "codex-cli")
    assert row["roots"] == [str(home / "memories"), str(home / "memories_v2")]


def test_discover_assets_binds_both_stores_when_present(tmp_path):
    home = codex_home(tmp_path)
    (home / "memories_v2").mkdir()
    bound = {b["root"] for b in discover_assets(tmp_path, "codex-cli") if b["kind"] == "memory"}
    assert bound == {str(home / "memories_v2")}
    (home / "memories").mkdir()
    bound = {b["root"] for b in discover_assets(tmp_path, "codex-cli") if b["kind"] == "memory"}
    assert bound == {str(home / "memories"), str(home / "memories_v2")}


# --- What leaves the machine ---------------------------------------------------------


def test_a_v2_store_shares_memory_but_not_codex_artifacts(tmp_path):
    root = write(codex_home(tmp_path) / "memories_v2", V2_FILES)
    assert relative(root) == {"memory_summary.md", "rollout_summaries/2026-11-08-abc-topic.md"}
    assert withheld(root, "memory", "phase2_workspace_diff.md")
    assert not withheld(root, "transcript", "phase2_workspace_diff.md")


def test_the_store_codex_no_longer_reads_is_withheld(tmp_path):
    home = codex_home(tmp_path)
    v1 = write(home / "memories", V1_FILES)
    v2 = write(home / "memories_v2", V2_FILES)
    assert relative(v1) == {"memory_summary.md", "rollout_summaries/old.md"}
    assert relative(v2) == set()
    assert withheld(v2, "memory", "memory_summary.md")

    config(home, '[memories]\nversion = "v2"\n')
    assert relative(v1) == set()
    assert relative(v2) == {"memory_summary.md", "rollout_summaries/2026-11-08-abc-topic.md"}
    assert withheld(v1, "memory", "memory_summary.md")


def test_other_products_directories_are_untouched(tmp_path):
    root = write(tmp_path / ".claude/projects/p/memory", {"phase2_workspace_diff.md": "普通主题"})
    assert relative(root) == {"phase2_workspace_diff.md"}


def paths_on_hub(hub, c):
    items = hub.changes(c.identity["token"], 0)["items"]
    return {item["path"]: item for item in items if not item["deleted"]}


def test_moving_to_v2_keeps_v1_copies_and_moves_uploads(tmp_path):
    hub = Hub(tmp_path / "hub.sqlite")
    c = client(tmp_path, hub, "张三")
    home = codex_home(tmp_path)
    v1, v2 = home / "memories", home / "memories_v2"
    v1.mkdir()
    v2.mkdir()
    c.register(v1, "codex", "p", "memory")
    c.register(v2, "codex", "p", "memory")
    write(v1, {"fact.md": "v1 事实"})
    write(v2, {"memory_summary.md": "过早的 v2 汇总"})
    c.sync()
    before = paths_on_hub(hub, c)
    assert set(before) == {"fact.md"}

    config(home, '[memories]\nversion = "v2"\n')
    write(v1, {"fact.md": "v1 事实 改动后不应上传"})
    write(v2, V2_FILES)
    c.sync()
    c.sync()
    after = paths_on_hub(hub, c)
    assert set(after) == {
        "fact.md",
        "memory_summary.md",
        "rollout_summaries/2026-11-08-abc-topic.md",
    }
    assert after["fact.md"]["revision"] == before["fact.md"]["revision"]
    assert after["fact.md"]["body"] == before["fact.md"]["body"]
    assert after["memory_summary.md"]["body"] == V2_FILES["memory_summary.md"]

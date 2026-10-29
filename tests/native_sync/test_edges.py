import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agent_brain.native_sync.hub import Hub
from agent_brain.native_sync.api import create_app
from agent_brain.native_sync.client import NativeClient
from agent_brain.native_sync.materialize import materialize
from agent_brain.native_sync.hooks import run
from test_client import client
from test_hub import enroll, asset


def test_large_history_skipped_even_when_originally_unreadable(tmp_path):
    hub = Hub(tmp_path / "hub.sqlite")
    c = client(tmp_path, hub, "张三")
    root = tmp_path / "source"
    root.mkdir()
    p = root / "history.md"
    p.write_bytes(b"\xff\xfe")
    c.register(root, "claude-code", "p", "memory")
    p.write_text("之前未选的旧历史现在可读")
    c.sync()
    assert hub.changes(c.identity["token"], 0)["items"] == []


def test_complete_large_transcript_and_binary_skill_roundtrip(tmp_path):
    hub = Hub(tmp_path / "hub.sqlite")
    a = client(tmp_path, hub, "张三")
    b = client(tmp_path, hub, "李四")
    root = tmp_path / "transcripts"
    root.mkdir()
    a.register(root, "codex-cli", "p", "transcript")
    transcript = "".join(
        json.dumps({"role": "user", "content": "记录正文" + str(i)}, ensure_ascii=False) + "\n"
        for i in range(30000)
    )
    (root / "session.jsonl").write_text(transcript)
    skill = tmp_path / "skills"
    skill.mkdir()
    a.register(skill, "claude-code", "p", "skill")
    folder = skill / "demo"
    folder.mkdir()
    (folder / "SKILL.md").write_text(
        "---\nname: demo\ndescription: demo skill\n---\nUse assets/sample.bin"
    )
    binary = bytes(range(256)) * 5000
    (folder / "sample.bin").write_bytes(binary)
    a.sync()
    b.sync()
    destination = tmp_path / "restored"
    report = materialize(destination, b.cached(), kinds={"transcript", "skill"})
    assert not report["conflicts"]
    outputs = list(destination.rglob("*"))
    assert any(
        p.is_file() and p.name == "session.jsonl" and p.read_text() == transcript for p in outputs
    )
    assert any(p.is_file() and p.name == "sample.bin" and p.read_bytes() == binary for p in outputs)


def test_source_config_credentials_are_redacted_and_portable_env_retained(tmp_path):
    hub = Hub(tmp_path / "hub.sqlite")
    c = client(tmp_path, hub, "张三")
    root = tmp_path / "cfg"
    root.mkdir()
    c.register(root, "claude-code", "p", "config")
    (root / "settings.json").write_text(
        json.dumps(
            {
                "env": {"ANTHROPIC_API_KEY": "SECRET", "BASE_URL": "https://api.example.com"},
                "model": "x",
            }
        )
    )
    c.sync()
    body = c.cached()[0]["body"]
    assert "SECRET" not in body
    assert "BASE_URL" in body


def test_materialize_rejects_traversal_and_preserves_local_changes(tmp_path):
    hub = Hub(tmp_path / "hub.sqlite")
    a = enroll(hub)
    bad = hub.put(a["token"], asset(path="../escape", kind="skill"))
    with pytest.raises(ValueError):
        materialize(tmp_path / "out", [bad], kinds={"skill"})
    good = hub.put(a["token"], asset("good", path="demo/SKILL.md", kind="skill"))
    out = tmp_path / "out"
    materialize(out, [good], kinds={"skill"})
    p = next(out.rglob("SKILL.md"))
    p.write_text("local changed")
    updated = hub.put(
        a["token"],
        {
            **asset("good", path="demo/SKILL.md", kind="skill", body="replacement"),
            "base_revision": 1,
        },
    )
    assert materialize(out, [updated], kinds={"skill"})["conflicts"]
    assert p.read_text() == "local changed"


def test_paused_hook_does_not_fetch_or_schedule(tmp_path):
    (tmp_path / "paused").touch()
    assert (
        run(
            tmp_path,
            "claude-code",
            {"session_id": "s", "hook_event_name": "UserPromptSubmit", "prompt": "x"},
        )
        == {}
    )
    assert not (tmp_path / "wake").exists()


def test_expired_invite_and_bounded_requests(tmp_path):
    hub = Hub(tmp_path / "hub.sqlite")
    with pytest.raises(ValueError):
        hub.enroll(hub.invite("linkcrux", "张三", ttl=-1), "Mac")
    c = TestClient(create_app(hub))
    result = c.post(
        "/native/v1/search", content=b"x" * 5_100_000, headers={"content-type": "application/json"}
    )
    assert result.status_code == 413


def test_native_skill_activation_preserves_package_paths_and_identity(tmp_path):
    from agent_brain.native_sync.assets import activate_skills

    hub = Hub(tmp_path / "hub.sqlite")
    a = enroll(hub)
    skill = hub.put(
        a["token"],
        asset(
            "skill",
            path="demo/SKILL.md",
            kind="skill",
            body="---\nname: demo\ndescription: Demo skill\n---\nRead scripts/check.py",
        ),
    )
    script = hub.put(
        a["token"],
        asset("script", path="demo/scripts/check.py", kind="skill", body='print("hello")'),
    )
    root = tmp_path / "native-skills"
    report = activate_skills(root, [skill, script])
    assert report["packages"] == 1
    entries = list(root.glob("*/SKILL.md"))
    assert len(entries) == 1
    assert "张三" in entries[0].read_text()
    assert (entries[0].parent / "scripts/check.py").read_text() == 'print("hello")'


def test_rule_activation_does_not_overwrite_user_instructions(tmp_path):
    from agent_brain.native_sync.assets import activate_rules

    hub = Hub(tmp_path / "hub.sqlite")
    a = enroll(hub)
    record = hub.put(
        a["token"], asset("rule", kind="rule", body="Use the staging endpoint for demos.")
    )
    p = tmp_path / "AGENTS.md"
    p.write_text("Existing user instruction\n")
    activate_rules(p, [record])
    activate_rules(p, [record])
    assert p.read_text().startswith("Existing user instruction\n")
    assert p.read_text().count("Use the staging endpoint") == 1
    assert "张三" in p.read_text()


def test_portable_config_applies_only_supported_same_family_fields(tmp_path):
    from agent_brain.native_sync.assets import apply_config

    hub = Hub(tmp_path / "hub.sqlite")
    a = enroll(hub)
    r = hub.put(
        a["token"],
        asset(
            "config",
            kind="config",
            body=json.dumps(
                {"model": "new-model", "personality": "friendly", "unknown": "keep-as-source"}
            ),
        ),
    )
    target = tmp_path / "config.toml"
    target.write_text('model = "old"\n[features]\nmemories = true\n')
    result = apply_config(target, r, "codex-cli")
    import tomllib

    actual = tomllib.loads(target.read_text())
    assert actual["model"] == "new-model" and actual["features"]["memories"] is True
    assert "unknown" not in actual and result["skipped"] == ["unknown"]
    with pytest.raises(ValueError):
        apply_config(tmp_path / "settings.json", r, "claude-code")

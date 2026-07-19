from dataclasses import dataclass
import json
from pathlib import Path

import pytest


@dataclass
class Harness:
    adapter: object
    settings: Path
    mcp_paths: tuple[Path, ...]
    stable_repo: Path
    adapter_name: str


def _stable_repo(root: Path) -> Path:
    root.mkdir()
    (root / "pyproject.toml").write_text("[project]\nname='agent-memory-hub'\n")
    hooks = root / "agent_runtime_kit" / "hooks"
    hooks.mkdir(parents=True)
    for name in ("inject-context.sh", "session-end-signal.sh"):
        script = hooks / name
        script.write_text("#!/bin/sh\nexit 0\n")
        script.chmod(0o755)
    python = root / ".venv" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.write_text("#!/bin/sh\nexit 0\n")
    python.chmod(0o755)
    return root


@pytest.fixture(params=("qoder", "qoder_work"))
def harness(request, tmp_path: Path, monkeypatch) -> Harness:
    stable = _stable_repo(tmp_path / "stable")
    brain = tmp_path / "brain"
    brain.mkdir()
    if request.param == "qoder":
        from agent_brain.agent_integrations import qoder as module

        settings = tmp_path / ".qoder" / "settings.json"
        shared = tmp_path / "Qoder" / "SharedClientCache" / "mcp.json"
        user = tmp_path / "Qoder" / "User" / "mcp.json"
        extension = (
            tmp_path / "Qoder" / "SharedClientCache" / "extension" / "local" / "mcp.json"
        )
        monkeypatch.setattr(module, "SETTINGS_PATH", settings)
        monkeypatch.setattr(module, "AWARENESS_PATH", tmp_path / ".qoder" / "AGENTS.md")
        monkeypatch.setattr(module, "MCP_CONFIG_PATH", shared)
        monkeypatch.setattr(module, "MCP_USER_CONFIG_PATH", user)
        monkeypatch.setattr(module, "MCP_EXTENSION_CONFIG_PATH", extension)
        monkeypatch.setattr(module, "QODER_PROJECTS_DIR", tmp_path / ".qoder" / "projects")
        monkeypatch.setattr(module, "QODER_MEMORIES_DIR", tmp_path / ".qoder" / "memories")
        monkeypatch.setattr(module, "QODER_LOCAL_DB_PATH", tmp_path / "missing.db")
        adapter = module.QoderAdapter(brain, repo_dir=stable)
        return Harness(adapter, settings, (user, shared, extension), stable, "qoder")

    from agent_brain.agent_integrations import qoder_work as module

    settings = tmp_path / ".qoderwork" / "settings.json"
    mcp = tmp_path / ".qoderwork" / "mcp.json"
    monkeypatch.setattr(module, "SETTINGS_PATH", settings)
    monkeypatch.setattr(module, "MCP_CONFIG_PATH", mcp)
    monkeypatch.setattr(
        module,
        "AWARENESS_PATH",
        tmp_path / ".qoderwork" / "awareness" / "main" / "AGENTS.md",
    )
    monkeypatch.setattr(
        module,
        "QODERWORK_PROJECTS_DIR",
        tmp_path / ".qoderwork" / "projects",
    )
    monkeypatch.setattr(module, "QODERWORK_SKILLS_DIR", tmp_path / ".qoderwork" / "skills")
    adapter = module.QoderWorkAdapter(brain, repo_dir=stable)
    return Harness(adapter, settings, (mcp,), stable, "qoder_work")


def _entry(*commands: str, **metadata: object) -> dict:
    return {
        "matcher": metadata.pop("matcher", ""),
        **metadata,
        "hooks": [
            {"type": "command", "command": command, "foreignMeta": index}
            for index, command in enumerate(commands)
        ],
    }


def _seed_drift(harness: Harness) -> list[str]:
    foreign = ["foreign-before", "foreign-mixed", "foreign-after"]
    payload = {
        "foreignTopLevel": {"preserve": True},
        "hooks": {
            "UserPromptSubmit": [
                _entry(foreign[0], matcher="first", custom="one"),
                _entry(
                    "/private/tmp/amh-bench-x/agent_runtime_kit/hooks/inject-context.sh",
                    foreign[1],
                    matcher="mixed",
                    custom="two",
                ),
                _entry("/old/worktree/agent_runtime_kit/hooks/inject-context.sh"),
                _entry("/old/worktree/agent_runtime_kit/hooks/session-end-signal.sh"),
                _entry(foreign[2], matcher="last", custom="three"),
            ],
            "Stop": [
                _entry(foreign[0], matcher="stop-first"),
                _entry("/old/worktree/agent_runtime_kit/hooks/session-end-signal.sh"),
                _entry(foreign[2], matcher="stop-last"),
            ],
            "ForeignEvent": [_entry("foreign-event", matcher="foreign-event")],
        },
    }
    harness.settings.parent.mkdir(parents=True)
    harness.settings.write_text(json.dumps(payload))
    for path in harness.mcp_paths:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "foreignMcpTopLevel": {"preserve": True},
                    "mcpServers": {
                        "foreign-server": {"command": "foreign-mcp"},
                        "agent-memory-hub": {
                            "command": "/old/worktree/.venv/bin/python",
                            "args": ["-m", "agent_brain.interfaces.mcp.server"],
                            "env": {
                                "BRAIN_DIR": "/old/brain",
                                "PYTHONPATH": "/old/worktree",
                            },
                            "enabled": True,
                        },
                    },
                }
            )
        )
    return foreign


def _commands(payload: dict, event: str) -> list[str]:
    return [
        hook["command"]
        for entry in payload["hooks"][event]
        for hook in entry["hooks"]
        if isinstance(hook, dict) and isinstance(hook.get("command"), str)
    ]


def _managed(commands: list[str], name: str) -> list[str]:
    return [command for command in commands if f"/agent_runtime_kit/hooks/{name}" in command]


def test_install_converges_hooks_mcp_and_preserves_foreign(harness: Harness) -> None:
    foreign = _seed_drift(harness)

    harness.adapter.install()

    payload = json.loads(harness.settings.read_text())
    prompt = _commands(payload, "UserPromptSubmit")
    stop = _commands(payload, "Stop")
    assert len(_managed(prompt, "inject-context.sh")) == 1
    assert _managed(prompt, "session-end-signal.sh") == []
    assert len(_managed(stop, "session-end-signal.sh")) == 1
    assert _managed(stop, "inject-context.sh") == []
    assert prompt[0].endswith(
        str(harness.stable_repo / "agent_runtime_kit/hooks/inject-context.sh")
    )
    assert [command for command in prompt if command in foreign] == foreign
    assert [command for command in stop if command in foreign] == [foreign[0], foreign[2]]
    assert payload["foreignTopLevel"] == {"preserve": True}
    assert payload["hooks"]["ForeignEvent"] == [
        _entry("foreign-event", matcher="foreign-event")
    ]
    assert "amh-bench" not in harness.settings.read_text()
    assert "/old/worktree" not in harness.settings.read_text()
    for path in harness.mcp_paths:
        config = json.loads(path.read_text())
        server = config["mcpServers"]["agent-memory-hub"]
        assert server["command"] == str(harness.stable_repo / ".venv/bin/python")
        assert server["env"]["PYTHONPATH"] == str(harness.stable_repo)
        assert server["env"]["BRAIN_DIR"] == str(harness.adapter.brain_dir)
        assert config["foreignMcpTopLevel"] == {"preserve": True}
        assert config["mcpServers"]["foreign-server"] == {"command": "foreign-mcp"}


def test_install_is_byte_idempotent_after_convergence(harness: Harness) -> None:
    _seed_drift(harness)
    harness.adapter.install()
    first = {path: path.read_bytes() for path in (harness.settings, *harness.mcp_paths)}

    harness.adapter.install()

    assert {path: path.read_bytes() for path in first} == first


def test_malformed_settings_fail_without_overwrite(harness: Harness) -> None:
    harness.settings.parent.mkdir(parents=True)
    harness.settings.write_text("{not json")
    before = harness.settings.read_bytes()

    with pytest.raises(RuntimeError, match="malformed"):
        harness.adapter.install()

    assert harness.settings.read_bytes() == before


def test_non_list_event_fails_without_rewriting_settings(harness: Harness) -> None:
    harness.settings.parent.mkdir(parents=True)
    harness.settings.write_text(json.dumps({"hooks": {"UserPromptSubmit": {}}}))
    before = harness.settings.read_bytes()

    with pytest.raises(RuntimeError, match=r"hooks\.UserPromptSubmit must be a list"):
        harness.adapter.install()

    assert harness.settings.read_bytes() == before


def test_uninstall_removes_all_managed_handlers_and_preserves_foreign(harness: Harness) -> None:
    foreign = _seed_drift(harness)
    harness.adapter.install()
    installed = json.loads(harness.settings.read_text())
    foreign_entries = {
        event: [
            {
                **entry,
                "hooks": [
                    hook
                    for hook in entry["hooks"]
                    if hook.get("command") in {*foreign, "foreign-event"}
                ],
            }
            for entry in installed["hooks"][event]
            if any(
                hook.get("command") in {*foreign, "foreign-event"}
                for hook in entry.get("hooks", [])
            )
        ]
        for event in ("UserPromptSubmit", "Stop", "ForeignEvent")
    }

    harness.adapter.uninstall()

    payload = json.loads(harness.settings.read_text())
    assert payload["hooks"] == foreign_entries
    serialized = harness.settings.read_text()
    assert "inject-context.sh" not in serialized
    assert "session-end-signal.sh" not in serialized

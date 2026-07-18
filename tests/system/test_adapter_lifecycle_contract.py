from pathlib import Path


def _isolate_codex(monkeypatch, tmp_path: Path) -> tuple[Path, Path, Path]:
    from agent_brain.agent_integrations import codex as codex_module

    config_dir = tmp_path / ".codex"
    agents = config_dir / "AGENTS.md"
    hooks = config_dir / "hooks.json"
    config = config_dir / "config.toml"
    monkeypatch.setattr(codex_module, "AGENTS_MD", agents)
    monkeypatch.setattr(codex_module, "CODEX_HOOKS_JSON", hooks)
    monkeypatch.setattr(codex_module, "CODEX_CONFIG_TOML", config)
    return agents, hooks, config


def test_codex_lifecycle_is_repeatable_and_owned_only(tmp_path: Path, monkeypatch) -> None:
    from agent_brain.agent_integrations.lifecycle_records import iter_lifecycle_records
    from agent_brain.product.adapter_onboarding import execute_adapter_action

    _agents, hooks, _config = _isolate_codex(monkeypatch, tmp_path)
    hooks.parent.mkdir(parents=True)
    hooks.write_text(
        '{"UserPromptSubmit":[{"matcher":"","hooks":[{"type":"command","command":"user-hook"}]}]}',
        encoding="utf-8",
    )

    results = [
        execute_adapter_action(tmp_path, "codex", "install", verifier="pytest"),
        execute_adapter_action(tmp_path, "codex", "install", verifier="pytest"),
        execute_adapter_action(tmp_path, "codex", "repair", verifier="pytest"),
        execute_adapter_action(tmp_path, "codex", "upgrade", verifier="pytest"),
        execute_adapter_action(tmp_path, "codex", "uninstall", verifier="pytest"),
        execute_adapter_action(tmp_path, "codex", "uninstall", verifier="pytest"),
    ]

    assert [result.status for result in results] == ["passed"] * 6
    assert [result.reason_code for result in results] == ["OK"] * 6
    assert "user-hook" in hooks.read_text(encoding="utf-8")
    assert all(result.schema_version == "amh-adapter-lifecycle-result/v1" for result in results)
    assert all(result.provenance for result in results)
    assert results[0].state_after["states"]["installed"] is True
    assert results[0].state_after["states"]["configured"] is True
    assert results[3].state_after["states"]["doctor_passed"] is True
    assert results[4].state_after["states"]["installed"] is False
    assert results[5].state_after["states"]["installed"] is False
    actions = [record.action for record in iter_lifecycle_records(tmp_path, adapter="codex")]
    assert actions == ["install", "install", "repair", "upgrade", "uninstall", "uninstall"]


def test_upgrade_failure_restores_owned_snapshot(tmp_path: Path, monkeypatch) -> None:
    from agent_brain.agent_integrations import codex as codex_module
    from agent_brain.product.adapter_onboarding import execute_adapter_action

    agents, hooks, config = _isolate_codex(monkeypatch, tmp_path)
    installed = execute_adapter_action(tmp_path, "codex", "install", verifier="pytest")
    assert installed.status == "passed"
    before = {
        agents: agents.read_bytes(),
        hooks: hooks.read_bytes(),
        config: config.read_bytes(),
    }
    original_install = codex_module.CodexAdapter.install

    def corrupt_then_fail(self):
        agents.write_text("partial-upgrade", encoding="utf-8")
        hooks.write_text("{}", encoding="utf-8")
        raise RuntimeError("simulated upgrade failure")

    monkeypatch.setattr(codex_module.CodexAdapter, "install", corrupt_then_fail)
    failed = execute_adapter_action(tmp_path, "codex", "upgrade", verifier="pytest")
    monkeypatch.setattr(codex_module.CodexAdapter, "install", original_install)

    assert failed.status == "failed"
    assert failed.reason_code == "INTERNAL_ERROR"
    assert failed.rollback_status == "passed"
    assert failed.backup_id
    assert {path: path.read_bytes() for path in before} == before


def test_two_pilot_batches_declare_owned_paths(tmp_path: Path) -> None:
    from agent_brain.agent_integrations.registry import get_adapter

    for name in ("codex", "qoder", "claude_code", "qoder_work"):
        paths = get_adapter(name, tmp_path).owned_paths()
        assert paths, name
        assert all(isinstance(path, Path) for path in paths)
        assert len(paths) == len(set(paths))


def test_claude_code_uses_same_lifecycle_contract(tmp_path: Path, monkeypatch) -> None:
    from agent_brain.agent_integrations import claude_code as claude_module
    from agent_brain.product.adapter_onboarding import execute_adapter_action

    settings = tmp_path / ".claude" / "settings.json"
    awareness = tmp_path / ".claude" / "CLAUDE.md"
    monkeypatch.setattr(claude_module, "SETTINGS_PATH", settings)
    monkeypatch.setattr(claude_module, "AWARENESS_PATH", awareness)
    settings.parent.mkdir(parents=True)
    settings.write_text(
        '{"hooks":{"UserPromptSubmit":[{"matcher":"","hooks":[{"type":"command","command":"user-hook"}]}]}}',
        encoding="utf-8",
    )

    results = [
        execute_adapter_action(tmp_path, "claude_code", "install", verifier="pytest"),
        execute_adapter_action(tmp_path, "claude_code", "upgrade", verifier="pytest"),
        execute_adapter_action(tmp_path, "claude_code", "uninstall", verifier="pytest"),
    ]

    assert [result.status for result in results] == ["passed"] * 3
    assert "user-hook" in settings.read_text(encoding="utf-8")


def test_qoder_uses_same_lifecycle_contract(tmp_path: Path, monkeypatch) -> None:
    from agent_brain.agent_integrations import qoder as qoder_module
    from agent_brain.product.adapter_onboarding import execute_adapter_action

    root = tmp_path / ".qoder"
    monkeypatch.setattr(qoder_module, "SETTINGS_PATH", root / "settings.json")
    monkeypatch.setattr(qoder_module, "AWARENESS_PATH", root / "AGENTS.md")
    monkeypatch.setattr(qoder_module, "QODER_PROJECTS_DIR", root / "projects")
    monkeypatch.setattr(qoder_module, "QODER_MEMORIES_DIR", root / "memories")
    monkeypatch.setattr(qoder_module, "MCP_USER_CONFIG_PATH", root / "User" / "mcp.json")
    monkeypatch.setattr(qoder_module, "MCP_CONFIG_PATH", root / "SharedClientCache" / "mcp.json")
    monkeypatch.setattr(
        qoder_module,
        "MCP_EXTENSION_CONFIG_PATH",
        root / "SharedClientCache" / "extension" / "local" / "mcp.json",
    )

    results = [
        execute_adapter_action(tmp_path, "qoder", "install", verifier="pytest"),
        execute_adapter_action(tmp_path, "qoder", "upgrade", verifier="pytest"),
        execute_adapter_action(tmp_path, "qoder", "uninstall", verifier="pytest"),
    ]

    assert [result.status for result in results] == ["passed"] * 3


def test_qoder_work_uses_same_lifecycle_contract(tmp_path: Path, monkeypatch) -> None:
    from agent_brain.agent_integrations import qoder_work as qoder_work_module
    from agent_brain.product.adapter_onboarding import execute_adapter_action

    root = tmp_path / ".qoderwork"
    monkeypatch.setattr(qoder_work_module, "SETTINGS_PATH", root / "settings.json")
    monkeypatch.setattr(qoder_work_module, "MCP_CONFIG_PATH", root / "mcp.json")
    monkeypatch.setattr(
        qoder_work_module,
        "AWARENESS_PATH",
        root / "awareness" / "main" / "AGENTS.md",
    )
    monkeypatch.setattr(qoder_work_module, "QODERWORK_PROJECTS_DIR", root / "projects")
    monkeypatch.setattr(qoder_work_module, "QODERWORK_SKILLS_DIR", root / "skills")

    results = [
        execute_adapter_action(tmp_path, "qoder_work", "install", verifier="pytest"),
        execute_adapter_action(tmp_path, "qoder_work", "upgrade", verifier="pytest"),
        execute_adapter_action(tmp_path, "qoder_work", "uninstall", verifier="pytest"),
    ]

    assert [result.status for result in results] == ["passed"] * 3

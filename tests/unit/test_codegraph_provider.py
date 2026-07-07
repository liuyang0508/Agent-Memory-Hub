from __future__ import annotations

import subprocess

import pytest

from agent_brain.codegraph.provider import (
    CodeGraphInvocationError,
    CodeGraphUnavailableError,
    CodebaseMemoryMcpProvider,
    derive_codebase_memory_project,
)


def test_derive_codebase_memory_project_matches_external_normalization():
    assert derive_codebase_memory_project("/tmp/bench/my project+测试") == "tmp-bench-my-project"
    assert derive_codebase_memory_project("///") == "root"


def test_provider_invokes_codebase_memory_cli_raw(monkeypatch, tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    seen: dict[str, object] = {}

    def fake_run(args, **kwargs):
        seen["args"] = args
        seen["cwd"] = kwargs["cwd"]
        seen["timeout"] = kwargs["timeout"]
        return subprocess.CompletedProcess(
            args=args,
            returncode=0,
            stdout='{"project":"tmp-repo","total_nodes":2}\n',
            stderr="",
        )

    monkeypatch.setattr(subprocess, "run", fake_run)

    provider = CodebaseMemoryMcpProvider(binary="/opt/bin/codebase-memory-mcp", timeout_s=3.5)
    result = provider.call(
        "get_architecture",
        {"project": "tmp-repo", "aspects": ["structure"]},
        repo_path=repo,
    )

    assert result == {"project": "tmp-repo", "total_nodes": 2}
    assert seen["args"] == [
        "/opt/bin/codebase-memory-mcp",
        "cli",
        "--json",
        "get_architecture",
        '{"aspects":["structure"],"project":"tmp-repo"}',
    ]
    assert seen["cwd"] == str(repo)
    assert seen["timeout"] == 3.5


def test_provider_unwraps_mcp_content_text(monkeypatch, tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()

    def fake_run(args, **kwargs):
        return subprocess.CompletedProcess(
            args=args,
            returncode=0,
            stdout='{"content":[{"type":"text","text":"{\\"status\\":\\"indexed\\",\\"nodes\\":3}"}]}',
            stderr="",
        )

    monkeypatch.setattr(subprocess, "run", fake_run)

    provider = CodebaseMemoryMcpProvider(binary="/opt/bin/codebase-memory-mcp")
    assert provider.call("index_repository", {"repo_path": str(repo)}, repo_path=repo) == {
        "status": "indexed",
        "nodes": 3,
    }


def test_provider_requires_binary(monkeypatch):
    monkeypatch.delenv("AMH_CODEGRAPH_BINARY", raising=False)
    monkeypatch.setattr("shutil.which", lambda _name: None)

    with pytest.raises(CodeGraphUnavailableError):
        CodebaseMemoryMcpProvider().call("list_projects", {})


def test_provider_reports_nonzero_exit(monkeypatch, tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()

    def fake_run(args, **kwargs):
        return subprocess.CompletedProcess(
            args=args,
            returncode=7,
            stdout="",
            stderr="project not found",
        )

    monkeypatch.setattr(subprocess, "run", fake_run)

    provider = CodebaseMemoryMcpProvider(binary="/opt/bin/codebase-memory-mcp")
    with pytest.raises(CodeGraphInvocationError) as excinfo:
        provider.call("get_architecture", {"project": "missing"}, repo_path=repo)

    assert "get_architecture failed" in str(excinfo.value)
    assert "project not found" in str(excinfo.value)

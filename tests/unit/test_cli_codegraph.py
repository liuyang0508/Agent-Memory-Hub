from __future__ import annotations

import json

from typer.testing import CliRunner

from agent_brain.interfaces.cli import app
from agent_brain.interfaces.cli.commands import codegraph as codegraph_cmd


runner = CliRunner()


def test_codegraph_search_does_not_overwrite_top_level_memory_search() -> None:
    from agent_brain.interfaces import cli
    from agent_brain.interfaces.cli.commands import query as query_cmd

    assert cli.search is query_cmd.search


def test_codegraph_project_name_command():
    result = runner.invoke(app, ["codegraph", "project-name", "/tmp/bench/my project+测试"])

    assert result.exit_code == 0, result.output
    assert result.output.strip() == "tmp-bench-my-project"


def test_codegraph_architecture_command_outputs_json(monkeypatch, tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    seen: dict[str, object] = {}

    class FakeProvider:
        def __init__(self, binary=None, timeout_s=10.0):
            seen["binary"] = binary
            seen["timeout_s"] = timeout_s

        def architecture(self, *, repo_path, project=None, aspects=None):
            seen["repo_path"] = str(repo_path)
            seen["project"] = project
            seen["aspects"] = aspects
            return {"project": project, "total_nodes": 12}

    monkeypatch.setattr(codegraph_cmd, "CodebaseMemoryMcpProvider", FakeProvider)

    result = runner.invoke(
        app,
        [
            "codegraph",
            "architecture",
            "--repo",
            str(repo),
            "--project",
            "custom-project",
            "--aspect",
            "structure",
            "--aspect",
            "clusters",
            "--binary",
            "/opt/cbm",
            "--timeout",
            "2.5",
            "--format",
            "json",
        ],
    )

    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == {"project": "custom-project", "total_nodes": 12}
    assert seen == {
        "binary": "/opt/cbm",
        "timeout_s": 2.5,
        "repo_path": str(repo),
        "project": "custom-project",
        "aspects": ["structure", "clusters"],
    }

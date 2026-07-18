import asyncio
import json
import os
from pathlib import Path
import subprocess

from typer.testing import CliRunner

from agent_brain.interfaces.cli import app


def test_disabled_adapter_does_not_disable_core_cli_or_mcp(tmp_path: Path) -> None:
    from agent_brain.agent_integrations.release_controls import set_adapter_release
    from agent_brain.interfaces.mcp.server import mcp

    set_adapter_release(tmp_path, "qoder", "disabled", reason="test kill switch")

    runner = CliRunner()
    previous = os.environ.get("BRAIN_DIR")
    os.environ["BRAIN_DIR"] = str(tmp_path)
    try:
        cli = runner.invoke(app, ["stats", "--format", "json"])
    finally:
        if previous is None:
            os.environ.pop("BRAIN_DIR", None)
        else:
            os.environ["BRAIN_DIR"] = previous
    tools = asyncio.run(mcp.list_tools())
    tool_names = {tool.name for tool in tools}

    assert cli.exit_code == 0, cli.output
    assert isinstance(json.loads(cli.output), dict)
    assert {"search_memory", "read_memory", "write_memory"} <= tool_names


def test_disabled_qoder_hook_returns_clean_empty_protocol(tmp_path: Path) -> None:
    from agent_brain.agent_integrations.release_controls import set_adapter_release
    from agent_brain.agent_integrations.runtime_events import runtime_event_summary

    set_adapter_release(tmp_path, "qoder", "disabled", reason="test kill switch")
    repo = Path(__file__).resolve().parents[2]
    hook = repo / "agent_runtime_kit" / "hooks" / "inject-context.sh"
    payload = json.dumps({
        "prompt": "召回不应执行",
        "session_id": "disabled-qoder-session",
        "cwd": str(tmp_path),
        "hook_event_name": "UserPromptSubmit",
    })
    result = subprocess.run(
        ["bash", str(hook)],
        input=payload,
        text=True,
        capture_output=True,
        env={
            **os.environ,
            "BRAIN_DIR": str(tmp_path),
            "AGENT_MEMORY_HUB_ADAPTER": "qoder",
            "AGENT_MEMORY_HUB_HOOK_OUTPUT_FORMAT": "json",
            "MEMORY_PYTHON": str(repo / ".venv" / "bin" / "python"),
        },
        timeout=10,
        check=False,
    )

    assert result.returncode == 0
    assert result.stdout.strip() == "{}"
    assert result.stderr == ""
    runtime = runtime_event_summary(tmp_path, "qoder")
    assert runtime.last_event is not None
    assert runtime.last_event["event_name"] == "AdapterDisabled"

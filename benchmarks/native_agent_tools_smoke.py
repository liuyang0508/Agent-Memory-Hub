"""Exercise a real Codex host against synthetic organization data and real MCP/HTTP.

Uses an isolated CODEX_HOME and temporary database. It never installs global
configuration or sends native/private memories to a model. The current account's
auth file is copied privately for the subprocess, then removed with the temp dir.
This is a tool-integration smoke test, not a recall-quality benchmark.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import tomllib

import uvicorn

from agent_brain.native_sync.api import create_app
from agent_brain.native_sync.client import NativeClient
from agent_brain.native_sync.hub import Hub


def run(binary: str) -> dict:
    account_home = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex")))
    auth = account_home / "auth.json"
    if not auth.is_file():
        raise RuntimeError("Existing Codex account credentials are required; no login is performed")
    settings_path = account_home / "config.toml"
    settings = tomllib.loads(settings_path.read_text()) if settings_path.exists() else {}
    if settings.get("model_provider", "openai") != "openai":
        raise RuntimeError("This isolated smoke test requires the configured OpenAI provider")
    repository = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix="amh-agent-research-") as folder:
        root = Path(folder)
        root.chmod(0o700)
        hub = Hub(root / "hub.sqlite")
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        sock.listen()
        port = sock.getsockname()[1]
        server = uvicorn.Server(
            uvicorn.Config(create_app(hub), log_level="error", access_log=False)
        )
        worker = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
        worker.start()
        try:
            for _ in range(100):
                if server.started:
                    break
                time.sleep(0.02)
            assert server.started
            identity = hub.provision("Synthetic Author", "synthetic-device", tenant="synthetic-org")
            client = NativeClient.adopt(
                root / "profile", f"http://127.0.0.1:{port}", identity, "test"
            )
            source = root / "source"
            source.mkdir()
            client.register(source, "claude-code", "zinnia", "memory")
            (source / "release.md").write_text(
                "# Zinnia release checklist\n"
                + "Routine checklist background. Preserve the original source attribution.\n" * 220
                + "\nThe release requires exactly three distinct reviewer sign-offs.\n"
                + "Emergency rollback approval is defined in [exception policy](exception.md).\n"
            )
            (source / "exception.md").write_text(
                "# Zinnia exception policy\n"
                "Emergency rollback must be authorized by the duty incident commander.\n"
            )
            client.sync()
            isolated = root / "codex"
            isolated.mkdir(mode=0o700)
            shutil.copyfile(auth, isolated / "auth.json")
            (isolated / "auth.json").chmod(0o600)
            bootstrap = (
                "import runpy,sys; sys.path.insert(0, "
                + repr(str(repository))
                + "); runpy.run_module('agent_brain.native_sync.org_mcp', run_name='__main__')"
            )
            lines = [
                'model_provider = "openai"',
                'approval_policy = "never"',
                'sandbox_mode = "read-only"',
            ]
            for key in ("model", "model_reasoning_effort"):
                if isinstance(settings.get(key), str):
                    lines.append(key + " = " + json.dumps(settings[key]))
            lines += [
                "[features]",
                "memories = false",
                "hooks = false",
                "[mcp_servers.amh-org]",
                "command = " + json.dumps(sys.executable),
                "args = " + json.dumps(["-c", bootstrap, "--state", str(client.root)]),
                "startup_timeout_sec = 30",
                "tool_timeout_sec = 20",
            ]
            (isolated / "config.toml").write_text("\n".join(lines) + "\n")
            workspace = root / "workspace"
            workspace.mkdir()
            schema = root / "answer-schema.json"
            schema.write_text(
                json.dumps(
                    {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "reviewers": {"type": "integer"},
                            "rollback_authorizer": {"type": "string"},
                            "source_assets": {"type": "array", "items": {"type": "string"}},
                        },
                        "required": ["reviewers", "rollback_authorizer", "source_assets"],
                    }
                )
            )
            prompt = (
                "Use only the three amh-org MCP tools. Do not use shell, files, web or other tools. "
                "For synthetic project Zinnia, find the release checklist. Read the entire checklist "
                "using versioned pages, then use related_org_memory to follow its explicit reference "
                "to the exception policy and read that policy. Reuse one research_id; do not exceed "
                "the tools' budgets. Report how many reviewer sign-offs are required, which role "
                "authorizes emergency rollback, and the source asset IDs. Do not guess from excerpts."
            )
            env = {
                **os.environ,
                "CODEX_HOME": str(isolated),
                "NO_PROXY": "127.0.0.1,localhost",
                "no_proxy": "127.0.0.1,localhost",
                "PYTHONDONTWRITEBYTECODE": "1",
            }
            started = time.monotonic()
            result = subprocess.run(
                [
                    binary,
                    "exec",
                    "--ephemeral",
                    "--skip-git-repo-check",
                    "--json",
                    "--color",
                    "never",
                    "--cd",
                    str(workspace),
                    "--output-schema",
                    str(schema),
                    "-o",
                    str(root / "answer.json"),
                    "-",
                ],
                input=prompt,
                text=True,
                capture_output=True,
                env=env,
                timeout=180,
            )
            events = []
            for line in result.stdout.splitlines():
                try:
                    events.append(json.loads(line))
                except ValueError:
                    continue
            items = [
                event.get("item", {}) for event in events if isinstance(event.get("item"), dict)
            ]
            calls = [
                item
                for item in items
                if item.get("type") == "mcp_tool_call" and item.get("status") == "completed"
            ]
            tools = [item.get("tool") for item in calls]
            answer_file = root / "answer.json"
            answer = json.loads(answer_file.read_text()) if answer_file.exists() else None
            passed = (
                result.returncode == 0
                and answer is not None
                and answer.get("reviewers") == 3
                and "duty incident commander" in answer.get("rollback_authorizer", "").lower()
                and {"search_org_memory", "read_org_memory", "related_org_memory"} <= set(tools)
                and tools.count("read_org_memory") >= 3
                and not any(
                    item.get("type") in {"command_execution", "web_search", "file_change"}
                    for item in items
                )
            )
            # No raw transcript or auth material leaves this private temporary fixture.
            return {
                "passed": passed,
                "returncode": result.returncode,
                "tools": tools,
                "answer": answer,
                "elapsed_seconds": round(time.monotonic() - started, 2),
                "usage": next(
                    (
                        event.get("usage")
                        for event in reversed(events)
                        if event.get("type") == "turn.completed"
                    ),
                    None,
                ),
                "event_types": sorted({event.get("type", "") for event in events}),
                "item_types": sorted({item.get("type", "") for item in items}),
                "errors": [
                    event.get("message", "")[:300]
                    for event in events
                    if event.get("type") == "error"
                ],
                "stderr_tail": result.stderr[-1200:] if result.returncode else "",
            }
        finally:
            server.should_exit = True
            worker.join(5)
            sock.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--codex-bin", default="codex")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = run(args.codex_bin)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(report, ensure_ascii=False))

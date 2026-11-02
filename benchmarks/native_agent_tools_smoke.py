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


def run(binary: str, *, case: str = "references") -> dict:
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
        hub = Hub(
            root / "hub.sqlite",
            semantic_model=(
                "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
                if case == "rewrite"
                else None
            ),
        )
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
            if case == "rewrite":
                (source / "release.md").write_text(
                    "常规会议日程记录。" * 300
                    + "\n恢复旧配置需要先停止队列，再切换到上个发布版本。"
                )
                (source / "exception.md").write_text("客户退款在七个工作日内退回原支付账户。")
            client.sync()
            if case == "rewrite":
                hub.rebuild_semantic(identity["token"])
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
            if case == "rewrite":
                schema.write_text(
                    json.dumps(
                        {
                            "type": "object",
                            "additionalProperties": False,
                            "properties": {
                                "steps": {"type": "array", "items": {"type": "string"}},
                                "source_assets": {"type": "array", "items": {"type": "string"}},
                            },
                            "required": ["steps", "source_assets"],
                        }
                    )
                )
                prompt = (
                    "只使用amh-org的三项只读工具，不用shell、文件或网络等其他工具。"
                    "请只依据组织资料用中文回答：怎样回滚到之前的部署？"
                    "第一步以原问题逐字搜索；若没有匹配，按工具说明改写查询继续找，"
                    "最多三次搜索，复用同一research_id。找到后读取原文核对步骤，"
                    "给出操作顺序和来源资产ID。找不到不能自行编造。"
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
            queries = [
                item.get("arguments", {}).get("query")
                for item in calls
                if item.get("tool") == "search_org_memory"
                and isinstance(item.get("arguments"), dict)
            ]
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
            if case == "rewrite":
                steps = " ".join(answer.get("steps", [])) if answer else ""
                passed = (
                    result.returncode == 0
                    and "停止" in steps
                    and "队列" in steps
                    and "版本" in steps
                    and "切换" in steps
                    and tools.count("search_org_memory") >= 2
                    and tools.count("read_org_memory") >= 1
                    and not any(
                        item.get("type") in {"command_execution", "web_search", "file_change"}
                        for item in items
                    )
                )
            # No raw transcript or auth material leaves this private temporary fixture.
            return {
                "passed": passed,
                "case": case,
                "returncode": result.returncode,
                "tools": tools,
                "queries": queries,
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
    parser.add_argument("--case", choices=["references", "rewrite"], default="references")
    args = parser.parse_args()
    report = run(args.codex_bin, case=args.case)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(report, ensure_ascii=False))

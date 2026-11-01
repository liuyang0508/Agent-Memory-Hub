"""Protocol/file-adapter matrix; these are not claims of live vendor-client acceptance."""

import itertools
import json
import subprocess
import sys
from pathlib import Path

import pytest

from agent_brain.native_sync.client import NativeClient
from agent_brain.native_sync.projection import publish
from test_runtime import live as live

PRODUCTS = ["claude-code", "codex-cli", "cowork", "codex-desktop"]


@pytest.mark.parametrize(
    "source_product,target_product", list(itertools.product(PRODUCTS, PRODUCTS))
)
def test_two_members_cloud_and_native_file_paths(live, tmp_path, source_product, target_product):
    hub, url = live
    author = NativeClient.adopt(tmp_path / "author", url, hub.provision("甲成员", "A"), "A")
    reader = NativeClient.adopt(tmp_path / "reader", url, hub.provision("乙成员", "B"), "B")
    source = tmp_path / "source"
    source.mkdir()
    author.register(source, source_product, "source-project", "memory")
    (source / "验签.md").write_text("订单回调验签的验收码为 PROTOCOL-72861。")
    author.sync()
    # Cloud-only: target has no replica or native resource.
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "agent_brain.native_sync.hooks",
            "--state",
            str(reader.root),
            "--product",
            target_product,
        ],
        input=json.dumps(
            {
                "hook_event_name": "UserPromptSubmit",
                "session_id": "different-member",
                "cwd": "another-project",
                "prompt": "订单回调验签的验收码是什么",
            }
        ),
        text=True,
        capture_output=True,
        timeout=3,
    )
    context = json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]
    assert "PROTOCOL-72861" in context and "甲成员" in context
    assert author.identity["member_id"] in context
    # Explicit opt-in: default cloud mode must not maintain organization bodies.
    assert not reader.cached()
    reader.config["projection"] = True
    from agent_brain.native_sync.common import atomic_json

    atomic_json(reader.root / "config.json", reader.config)
    reader.configure_storage()
    # Native-files-only: no invocation of the recall hook.
    reader.sync()
    native = tmp_path / "native"
    report = publish(native, target_product, reader.cached())
    assert not report["conflicts"] and not report["pending"]
    index = native / (
        "MEMORY.md" if target_product in ("claude-code", "cowork") else "memory_summary.md"
    )
    assert "amh-org/index.md" in index.read_text()
    resources = [p for p in (native / "amh-org").glob("*.md") if p.name != "index.md"]
    assert len(resources) == 1
    assert "甲成员" in resources[0].read_text() and "PROTOCOL-72861" in resources[0].read_text()

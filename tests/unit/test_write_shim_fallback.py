"""Stage C contract: ``write-memory.sh`` never loses a write when Python is gone.

The hook shim must degrade to appending a durable pending record under
``$BRAIN_DIR/pending/`` whenever the Python ``memory write`` path is unreachable
(or when ``MEMORY_HUB_FORCE_PENDING=1`` forces the offline path). A later
``memory sync-pending`` drains that queue through the one WriteService funnel, so
the markdown pool eventually converges. These tests exercise the shim as a real
subprocess (the way a hook invokes it) and then replay the record in-process.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

# Repo root anchored to this file so the shim path is independent of the cwd
# pytest happens to run from.
REPO_ROOT = Path(__file__).resolve().parents[2]
SHIM = REPO_ROOT / "agent_runtime_kit" / "tools" / "write-memory.sh"
SEARCH_SHIM = REPO_ROOT / "agent_runtime_kit" / "tools" / "search-memory.sh"
INJECT_HOOK = REPO_ROOT / "agent_runtime_kit" / "hooks" / "inject-context.sh"
PYTHON_RESOLVER = REPO_ROOT / "agent_runtime_kit" / "tools" / "_resolve-python.sh"


def test_shim_falls_back_to_pending_when_python_broken(tmp_path):
    env = dict(
        os.environ,
        BRAIN_DIR=str(tmp_path),
        MEMORY_HUB_FORCE_PENDING="1",  # shim honors this to skip python
    )
    p = subprocess.run(
        [str(SHIM), "--type", "fact", "--title", "shim t", "--summary", "s"],
        input="body\n",
        text=True,
        capture_output=True,
        env=env,
    )
    assert p.returncode == 0, p.stderr
    pend = list((tmp_path / "pending").glob("*.jsonl"))
    assert len(pend) == 1
    rec = json.loads(pend[0].read_text().splitlines()[0])
    assert rec["op"] == "write"
    assert rec["item"]["title"] == "shim t"
    assert rec["item"]["body"] == "body"


def test_python_resolver_does_not_trip_errexit_when_python_is_incomplete(tmp_path):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_python = fake_bin / "python3"
    fake_python.write_text("#!/usr/bin/env bash\nexit 1\n", encoding="utf-8")
    fake_python.chmod(0o755)

    script = tmp_path / "source-resolver.sh"
    script.write_text(
        "\n".join(
            [
                "#!/usr/bin/env bash",
                "set -euo pipefail",
                f"source {PYTHON_RESOLVER}",
                'printf "after:%s:%s\\n" "$_PYTHON_OK" "$MEMORY_PYTHON"',
            ]
        ),
        encoding="utf-8",
    )
    script.chmod(0o755)

    env = dict(
        os.environ,
        PATH=f"{fake_bin}:/usr/bin:/bin",
        AGENT_MEMORY_HUB_PYTHON_IMPORTS="amh_missing_module_for_resolver_test",
    )
    p = subprocess.run(
        ["/bin/bash", str(script)],
        text=True,
        capture_output=True,
        env=env,
    )

    assert p.returncode == 0, p.stderr
    assert p.stdout.startswith("after:1:")


def _counting_python_wrapper(tmp_path: Path) -> tuple[Path, Path]:
    invocation_log = tmp_path / "python-invocations"
    wrapper = tmp_path / "verified-python"
    wrapper.write_text(
        "#!/usr/bin/env bash\n"
        f"printf 'probe\\n' >> {invocation_log!s}\n"
        f"exec {sys.executable} \"$@\"\n",
        encoding="utf-8",
    )
    wrapper.chmod(0o755)
    return wrapper, invocation_log


def test_python_resolver_reuses_parent_verified_python_without_reimport(tmp_path):
    verified_python, invocation_log = _counting_python_wrapper(tmp_path)
    script = tmp_path / "source-resolver.sh"
    script.write_text(
        "\n".join(
            [
                "#!/usr/bin/env bash",
                "set -euo pipefail",
                f"source {PYTHON_RESOLVER}",
                f"/bin/bash -c 'set -euo pipefail; source {PYTHON_RESOLVER}; "
                "printf \"%s:%s:%s\\\\n\" \"$_PYTHON_OK\" \"$MEMORY_PYTHON\" "
                "\"$AGENT_MEMORY_HUB_PYTHON_RESOLVED\"'",
            ]
        ),
        encoding="utf-8",
    )
    script.chmod(0o755)

    result = subprocess.run(
        ["/bin/bash", str(script)],
        env={
            **os.environ,
            "AGENT_MEMORY_HUB_PYTHON": str(verified_python),
        },
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == f"0:{verified_python}:1"
    assert invocation_log.read_text(encoding="utf-8").splitlines() == ["probe"]


def test_python_resolver_rejects_stale_unbound_marker_for_independent_shim(tmp_path):
    invocation_log = tmp_path / "broken-python-invoked"
    broken_python = tmp_path / "broken-python"
    broken_python.write_text(
        f"#!/usr/bin/env bash\ntouch {invocation_log}\nexit 97\n",
        encoding="utf-8",
    )
    broken_python.chmod(0o755)

    result = subprocess.run(
        [str(SEARCH_SHIM), "--help"],
        env={
            **os.environ,
            "MEMORY_PYTHON": str(broken_python),
            "AGENT_MEMORY_HUB_PYTHON_RESOLVED": "1",
            "PYTHONPATH": str(REPO_ROOT),
        },
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert "Usage" in result.stdout
    assert invocation_log.exists(), "stale verdict must trigger a fresh import probe"


def test_python_resolver_rejects_changed_interpreter_identity(tmp_path):
    verified_python, invocation_log = _counting_python_wrapper(tmp_path)
    script = tmp_path / "identity-change.sh"
    script.write_text(
        "\n".join(
            [
                "#!/usr/bin/env bash",
                "set -euo pipefail",
                f"source {PYTHON_RESOLVER}",
                f"printf '#!/usr/bin/env bash\\nexit 97\\n' > {verified_python}",
                f"chmod +x {verified_python}",
                f"/bin/bash -c 'set -euo pipefail; source {PYTHON_RESOLVER}; "
                "test \"$MEMORY_PYTHON\" != \"$AGENT_MEMORY_HUB_PYTHON\"'",
            ]
        ),
        encoding="utf-8",
    )
    script.chmod(0o755)

    result = subprocess.run(
        ["/bin/bash", str(script)],
        env={
            **os.environ,
            "AGENT_MEMORY_HUB_PYTHON": str(verified_python),
            "PYTHONPATH": str(REPO_ROOT),
        },
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert invocation_log.read_text(encoding="utf-8").splitlines() == ["probe"]


@pytest.mark.parametrize("change", ["replace_target", "retarget_symlink"])
def test_python_resolver_rejects_changed_symlink_target(tmp_path, change):
    target, invocation_log = _counting_python_wrapper(tmp_path)
    symlink_python = tmp_path / "python-link"
    symlink_python.symlink_to(target)
    replacement = tmp_path / "replacement-python"
    replacement.write_text("#!/usr/bin/env bash\nexit 97\n", encoding="utf-8")
    replacement.chmod(0o755)
    if change == "replace_target":
        mutation = f"cp {replacement} {target}"
    else:
        mutation = f"rm {symlink_python}; ln -s {replacement} {symlink_python}"
    script = tmp_path / f"symlink-{change}.sh"
    script.write_text(
        "\n".join(
            [
                "#!/usr/bin/env bash",
                "set -euo pipefail",
                f"source {PYTHON_RESOLVER}",
                mutation,
                f"/bin/bash -c 'set -euo pipefail; source {PYTHON_RESOLVER}; "
                "test \"$MEMORY_PYTHON\" != \"$AGENT_MEMORY_HUB_PYTHON\"'",
            ]
        ),
        encoding="utf-8",
    )
    script.chmod(0o755)

    result = subprocess.run(
        ["/bin/bash", str(script)],
        env={
            **os.environ,
            "AGENT_MEMORY_HUB_PYTHON": str(symlink_python),
            "PYTHONPATH": str(REPO_ROOT),
        },
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert invocation_log.read_text(encoding="utf-8").splitlines() == ["probe"]


def test_python_resolver_rejects_path_mismatch_while_creator_is_alive(tmp_path):
    verified_python, invocation_log = _counting_python_wrapper(tmp_path)
    other_python = tmp_path / "other-python"
    other_python.write_text("#!/usr/bin/env bash\nexit 97\n", encoding="utf-8")
    other_python.chmod(0o755)
    script = tmp_path / "path-mismatch.sh"
    script.write_text(
        "\n".join(
            [
                "#!/usr/bin/env bash",
                "set -euo pipefail",
                f"source {PYTHON_RESOLVER}",
                f"MEMORY_PYTHON={other_python} /bin/bash -c 'set -euo pipefail; "
                f"source {PYTHON_RESOLVER}; test \"$MEMORY_PYTHON\" = {verified_python}'",
            ]
        ),
        encoding="utf-8",
    )
    script.chmod(0o755)

    result = subprocess.run(
        ["/bin/bash", str(script)],
        env={
            **os.environ,
            "AGENT_MEMORY_HUB_PYTHON": str(verified_python),
            "PYTHONPATH": str(REPO_ROOT),
        },
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert invocation_log.read_text(encoding="utf-8").splitlines() == ["probe", "probe"]


def test_python_resolver_rejects_marker_after_creator_exits(tmp_path):
    verified_python, invocation_log = _counting_python_wrapper(tmp_path)
    parent = subprocess.run(
        [
            "/bin/bash",
            "-c",
            f"set -euo pipefail; source {PYTHON_RESOLVER}; env | "
            "grep -E '^(MEMORY_PYTHON|AGENT_MEMORY_HUB_PYTHON_RESOLVED)'",
        ],
        env={
            **os.environ,
            "AGENT_MEMORY_HUB_PYTHON": str(verified_python),
            "PYTHONPATH": str(REPO_ROOT),
        },
        capture_output=True,
        text=True,
        check=True,
    )
    inherited = {
        line.split("=", 1)[0]: line.split("=", 1)[1]
        for line in parent.stdout.splitlines()
    }
    result = subprocess.run(
        [
            "/bin/bash",
            "-c",
            f"set -euo pipefail; source {PYTHON_RESOLVER}; "
            f"test \"$MEMORY_PYTHON\" = {verified_python}",
        ],
        env={
            **os.environ,
            **inherited,
            "AGENT_MEMORY_HUB_PYTHON": str(verified_python),
            "PYTHONPATH": str(REPO_ROOT),
        },
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert invocation_log.read_text(encoding="utf-8").splitlines() == ["probe", "probe"]


def test_shim_pending_record_includes_validity_scope(tmp_path):
    env = dict(
        os.environ,
        BRAIN_DIR=str(tmp_path),
        MEMORY_HUB_FORCE_PENDING="1",
    )
    p = subprocess.run(
        [
            str(SHIM),
            "--type", "signal",
            "--title", "scoped signal",
            "--summary", "s",
            "--cwd", "/repo/current",
            "--adapter", "codex",
        ],
        input="body\n",
        text=True,
        capture_output=True,
        env=env,
    )

    assert p.returncode == 0, p.stderr
    pend = list((tmp_path / "pending").glob("*.jsonl"))
    rec = json.loads(pend[0].read_text().splitlines()[0])
    assert rec["item"]["validity"]["cwd"] == "/repo/current"
    assert rec["item"]["validity"]["adapter"] == "codex"


def test_shim_pending_record_includes_source_refs(tmp_path):
    env = dict(
        os.environ,
        BRAIN_DIR=str(tmp_path),
        MEMORY_HUB_FORCE_PENDING="1",
    )
    p = subprocess.run(
        [
            str(SHIM),
            "--type", "fact",
            "--title", "ref pending",
            "--summary", "s",
            "--ref-file", "/repo/evidence.md",
            "--ref-url", "https://example.test/evidence",
            "--ref-resource", "res-20260611-010203-demo-a1b2c3d4",
            "--ref-extraction", "ext-20260611-010204-demo-e5f6a7b8",
        ],
        input="body\n",
        text=True,
        capture_output=True,
        env=env,
    )

    assert p.returncode == 0, p.stderr
    pend = list((tmp_path / "pending").glob("*.jsonl"))
    rec = json.loads(pend[0].read_text().splitlines()[0])
    assert rec["item"]["refs"]["files"] == ["/repo/evidence.md"]
    assert rec["item"]["refs"]["urls"] == ["https://example.test/evidence"]
    assert rec["item"]["refs"]["resources"] == ["res-20260611-010203-demo-a1b2c3d4"]
    assert rec["item"]["refs"]["extractions"] == ["ext-20260611-010204-demo-e5f6a7b8"]


def test_shim_pending_record_replays_into_pool(tmp_brain):
    """The buffered record drains cleanly through the real PendingQueue funnel."""
    env = dict(
        os.environ,
        BRAIN_DIR=str(tmp_brain),
        MEMORY_HUB_FORCE_PENDING="1",
    )
    p = subprocess.run(
        [
            str(SHIM),
            "--type", "fact",
            "--title", "replay me",
            "--summary", "s",
            "--tags", "harvested,decision",
            "--cwd", "/repo/current",
            "--adapter", "codex",
        ],
        input="body text\n",
        text=True,
        capture_output=True,
        env=env,
    )
    assert p.returncode == 0, p.stderr

    from agent_brain.memory.store.pending import PendingQueue

    stats = PendingQueue().apply(safe_only=True)
    assert stats.written == 1
    assert PendingQueue().depth() == 0

    items = list((tmp_brain / "items").glob("*.md"))
    assert items
    assert any("replay me" in it.read_text(encoding="utf-8") for it in items)
    from agent_brain.memory.store.items_store import ItemsStore

    item = next(item for item, _body in ItemsStore(tmp_brain / "items").iter_all())
    assert item.validity.cwd == "/repo/current"
    assert item.validity.adapter == "codex"
    assert (tmp_brain / "sources" / "writes" / f"{item.id}.json").exists()


def test_shim_uses_configured_python_when_path_cli_is_broken(tmp_path):
    """A hook can pin a healthy Python even when PATH has a broken CLI env."""
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    for name in ("python3", "memory"):
        fake = fake_bin / name
        fake.write_text("#!/bin/sh\nexit 97\n", encoding="utf-8")
        fake.chmod(0o755)

    brain = tmp_path / "brain"
    env = dict(
        os.environ,
        AGENT_MEMORY_HUB_PYTHON=sys.executable,
        BRAIN_DIR=str(brain),
        MEMORY_HUB_TEST_EMBEDDING="1",
        PATH=f"{fake_bin}:{os.environ['PATH']}",
        PYTHONPATH=f"{REPO_ROOT}:{os.environ.get('PYTHONPATH', '')}",
    )
    p = subprocess.run(
        [
            str(SHIM),
            "--type",
            "fact",
            "--title",
            "configured python",
            "--summary",
            "shim should bypass broken PATH python",
            "--tags",
            "runtime,shim",
        ],
        input="**事实**\nconfigured python works\n\n**来源**\ntest\n\n**有效期**\ncurrent\n",
        text=True,
        capture_output=True,
        cwd=REPO_ROOT,
        env=env,
    )

    assert p.returncode == 0, p.stderr
    assert "queued:" not in p.stdout
    items = list((brain / "items").glob("*.md"))
    assert len(items) == 1
    assert "configured python" in items[0].read_text(encoding="utf-8")
    assert not list((brain / "pending").glob("*.jsonl"))


def test_search_shim_defaults_embedding_offline():
    """Cold interactive recall must not block on model downloads by default."""
    content = SEARCH_SHIM.read_text(encoding="utf-8")
    assert 'MEMORY_HUB_EMBEDDING_OFFLINE="${MEMORY_HUB_EMBEDDING_OFFLINE:-1}"' in content
    assert 'memory_cli search "$@"' in content


def test_prompt_injection_hook_uses_context_firewall():
    """Auto-injected context should pass through the before-inject firewall."""
    content = INJECT_HOOK.read_text(encoding="utf-8")
    assert "--context-firewall" in content


def test_prompt_injection_hook_delegates_full_prompt_to_routed_gateway():
    """The shell adapter must not reproduce admission or parse human CLI text."""
    content = INJECT_HOOK.read_text(encoding="utf-8")
    assert '"$RECALL_PROMPT"' in content
    assert '"--routed-recall"' in content
    assert '"--format" "hook-json"' in content
    assert "AGENT_MEMORY_HUB_RAW_QUERY" not in content
    assert 'if [ -z "$KEYWORDS" ]' not in content
    assert "no matches" not in content.lower()


def test_prompt_injection_hook_exports_one_verified_python_before_child_tools():
    content = INJECT_HOOK.read_text(encoding="utf-8")

    source_at = content.index('source "$PYTHON_RESOLVER"')
    runtime_event_at = content.index('[ -x "$RECORD_TOOL" ]')
    assert source_at < runtime_event_at
    assert "export MEMORY_PYTHON AGENT_MEMORY_HUB_PYTHON_RESOLVED" in content
    assert "unset AGENT_MEMORY_HUB_PYTHON_RESOLVED" in content

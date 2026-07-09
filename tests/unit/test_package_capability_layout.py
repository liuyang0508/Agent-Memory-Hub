import importlib
import os
from pathlib import Path
import subprocess
import sys
import zipfile


ROOT = Path(__file__).resolve().parents[2]


def test_agent_brain_is_the_only_python_package_entrypoint():
    pkg = importlib.import_module("agent_brain")

    assert pkg.__version__
    assert (ROOT / "agent_brain").is_dir()
    assert not (ROOT / "agent_memory_hub").exists()
    assert not (ROOT / "memory_hub").exists()


def test_agent_runtime_kit_owns_agent_facing_assets():
    runtime = ROOT / "agent_runtime_kit"

    assert (runtime / "AGENT_MEMORY_DISCIPLINE.md").is_file()
    assert (runtime / "hooks" / "inject-context.sh").is_file()
    assert (runtime / "tools" / "write-memory.sh").is_file()
    assert (runtime / "mcp" / "server.sh").is_file()
    assert (runtime / "templates" / "remember.md.template").is_file()


def test_wheel_contains_runtime_kit_and_public_evaluation_assets(tmp_path):
    wheelhouse = tmp_path / "wheelhouse"
    wheelhouse.mkdir()

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "wheel",
            str(ROOT),
            "--no-deps",
            "-w",
            str(wheelhouse),
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert result.returncode == 0, result.stderr
    wheels = sorted(wheelhouse.glob("agent_memory_hub-*.whl"))
    assert wheels, result.stdout
    with zipfile.ZipFile(wheels[-1]) as wheel:
        names = set(wheel.namelist())

    expected_assets = {
        "agent_runtime_kit/AGENT_MEMORY_DISCIPLINE.md",
        "agent_runtime_kit/hooks/inject-context.sh",
        "agent_runtime_kit/hooks/session-end-signal.sh",
        "agent_runtime_kit/tools/search-memory.sh",
        "agent_runtime_kit/tools/write-memory.sh",
        "agent_runtime_kit/mcp/server.sh",
        "agent_runtime_kit/mcp/server.py",
        "agent_runtime_kit/templates/remember.md.template",
        "docs/evaluation/amh-evaluation-report.html",
        "docs/evaluation/amh-evaluation-report.json",
        "docs/evaluation/amh-evaluation-report.zh.md",
    }
    assert expected_assets <= names


def test_repository_root_has_no_old_runtime_or_example_dirs():
    for obsolete in ["brain", "memory_hub", "agent_memory_hub", "templates", "tools", "demo", "experiments"]:
        assert not (ROOT / obsolete).exists()

    for expected in [
        "agent_brain",
        "agent_runtime_kit",
        "web",
        "benchmarks",
        "deploy",
        "docs",
        "tests",
    ]:
        assert (ROOT / expected).is_dir()


def test_readme_remains_the_packaging_readme():
    readme = ROOT / "README.md"
    pyproject = ROOT / "pyproject.toml"

    assert readme.is_file()
    assert 'readme = "README.md"' in pyproject.read_text(encoding="utf-8")


def test_agent_brain_has_no_compatibility_facade_packages():
    forbidden = [
        "adapters",
        "audit",
        "capabilities",
        "cli",
        "client",
        "core",
        "evolve",
        "governance",
        "harvest",
        "hermes",
        "integrations",
        "mcp",
        "reasoning",
        "spec",
    ]

    offenders = [name for name in forbidden if (ROOT / "agent_brain" / name).exists()]

    assert offenders == []


def test_agent_brain_has_clear_layered_owners():
    expected_dirs = [
        "interfaces/cli",
        "interfaces/mcp",
        "interfaces/sdk",
        "contracts",
        "platform/indexing",
        "memory/store",
        "memory/recall",
        "memory/context",
        "memory/governance/audit",
        "memory/governance/evolve",
        "memory/governance/reasoning",
        "memory/evidence/harvest",
        "memory/evidence/integrations",
        "agent_integrations",
        "agent_integrations/hermes",
        "observability",
    ]

    for rel in expected_dirs:
        assert (ROOT / "agent_brain" / rel).is_dir()


def test_current_source_uses_canonical_package_layers():
    source_roots = [
        ROOT / "agent_brain",
        ROOT / "web",
        ROOT / "benchmarks",
        ROOT / "deploy",
    ]
    forbidden_fragments = [
        "agent_brain.adapters",
        "agent_brain.audit",
        "agent_brain.capabilities",
        "agent_brain.client",
        "agent_brain.core",
        "agent_brain.evolve",
        "agent_brain.governance",
        "agent_brain.harvest",
        "agent_brain.hermes",
        "agent_brain.integrations",
        "agent_brain.mcp",
        "agent_brain.reasoning",
        "agent_brain.spec",
    ]
    offenders: list[str] = []

    for root in source_roots:
        if root.is_file():
            paths = [root]
        else:
            paths = list(root.rglob("*.py"))
        for path in paths:
            text = path.read_text(encoding="utf-8")
            if any(fragment in text for fragment in forbidden_fragments):
                offenders.append(str(path.relative_to(ROOT)))

    assert offenders == []


def test_current_docs_and_source_do_not_treat_adapter_compat_package_as_owner():
    current_files = [
        ROOT / "agent_brain" / "agent_integrations" / "__init__.py",
        ROOT / "README.md",
        ROOT / "README.zh.md",
        ROOT / "docs" / "architecture.md",
        ROOT / "tests" / "unit" / "test_cli_adapter.py",
    ]

    offenders = [
        str(path.relative_to(ROOT))
        for path in current_files
        if any(
            old_path in path.read_text(encoding="utf-8")
            for old_path in [
                "agent_brain/adapters/",
                "agent_brain/capabilities/",
                "agent_brain/core/",
            ]
        )
    ]

    assert offenders == []


def test_capability_packages_expose_primary_services():
    expected_modules = [
        "agent_brain.memory.store.write_service",
        "agent_brain.memory.store.items_store",
        "agent_brain.memory.recall.retrieval",
        "agent_brain.memory.recall.retrieval_fusion",
        "agent_brain.memory.context.context_loading",
        "agent_brain.memory.context.context_firewall",
        "agent_brain.memory.governance.review_queue",
        "agent_brain.memory.governance.lifecycle_review",
        "agent_brain.memory.governance.audit.scanner",
        "agent_brain.memory.governance.drift",
        "agent_brain.memory.governance.evolve.engine",
        "agent_brain.memory.governance.reasoning.causal_chain",
        "agent_brain.memory.evidence.resource_store",
        "agent_brain.memory.evidence.harvest.harvester",
        "agent_brain.memory.evidence.integrations.obsidian",
        "agent_brain.agent_integrations.claude_code",
        "agent_brain.agent_integrations.hermes.provider",
        "agent_brain.observability.observability",
        "web.app",
    ]

    for module_name in expected_modules:
        assert importlib.import_module(module_name)


def test_agent_brain_module_entrypoint_runs():
    result = subprocess.run(
        [sys.executable, "-m", "agent_brain.interfaces.cli", "--help"],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert "Agent Memory Hub CLI" in result.stdout


def test_mcp_launcher_ignores_stale_memory_mcp_on_path(tmp_path):
    """The Codex MCP launcher must not call an unrelated memory-mcp from PATH."""
    bad_bin = tmp_path / "bin"
    bad_bin.mkdir()
    stale = bad_bin / "memory-mcp"
    stale.write_text("#!/bin/sh\necho stale-memory-mcp >&2\nexit 42\n", encoding="utf-8")
    stale.chmod(0o755)

    env = dict(
        os.environ,
        PATH=f"{bad_bin}{os.pathsep}{os.environ.get('PATH', '')}",
        BRAIN_DIR=str(tmp_path / "brain"),
        MEMORY_HUB_TEST_EMBEDDING="1",
    )
    initialize = (
        '{"jsonrpc":"2.0","id":1,"method":"initialize","params":'
        '{"protocolVersion":"2024-11-05","capabilities":{},'
        '"clientInfo":{"name":"pytest","version":"0"}}}\n'
    )

    result = subprocess.run(
        [str(ROOT / "agent_runtime_kit" / "mcp" / "server.sh")],
        input=initialize,
        capture_output=True,
        text=True,
        env=env,
        timeout=10,
    )

    assert result.returncode == 0, result.stderr
    assert "stale-memory-mcp" not in result.stderr
    assert '"serverInfo"' in result.stdout

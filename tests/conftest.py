from pathlib import Path

import pytest


@pytest.fixture
def tmp_brain_dir(tmp_path: Path) -> Path:
    """Disposable BRAIN_DIR for each test, isolated from real ~/.agent-memory-hub."""
    brain = tmp_path / "brain"
    (brain / "items").mkdir(parents=True)
    return brain


@pytest.fixture
def tmp_brain(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the brain data dir at an isolated temp dir.

    Sets ``BRAIN_DIR`` so every component that resolves the brain location from
    the environment (WriteService, pending queue, doctor, harvester) lands in the
    same throwaway tree, and forces the deterministic HashingEmbedder via
    ``MEMORY_HUB_TEST_EMBEDDING=1`` so indexing is fast and offline (no model
    download). Returns the brain dir (the parent of ``items/``).
    """
    monkeypatch.setenv("BRAIN_DIR", str(tmp_path))
    monkeypatch.setenv("MEMORY_HUB_TEST_EMBEDDING", "1")
    (tmp_path / "items").mkdir(parents=True, exist_ok=True)
    return tmp_path


@pytest.fixture
def fixtures_dir() -> Path:
    """Path to tests/fixtures/."""
    return Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def prevent_repo_root_awareness_leak() -> None:
    """Fail fast if tests leak adapter workspace awareness into the repo root."""
    repo_agents = Path(__file__).resolve().parents[1] / "AGENTS.md"
    existed_before = repo_agents.exists()

    yield

    if existed_before or not repo_agents.exists():
        return
    try:
        content = repo_agents.read_text(encoding="utf-8")
    except OSError:
        return
    if "agent-memory-hub-awareness" not in content:
        return
    repo_agents.unlink()
    pytest.fail(f"test leaked adapter awareness into repo root: {repo_agents}")


@pytest.fixture(autouse=True)
def isolate_qoder_adapter_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep Qoder-family adapter tests from touching real user/workspace files.

    Qoder and QoderWork can discover recent workspace roots from their project
    stores and write ``AGENTS.md`` awareness files into those workspaces. Unit
    tests must exercise that behavior only through explicit temporary fixtures.
    Keep default settings/MCP/awareness path constants intact because tests also
    verify that public contract.
    """
    from agent_brain.agent_integrations import qoder as qoder_mod
    from agent_brain.agent_integrations import qoder_work as qw_mod

    monkeypatch.setenv("AGENT_MEMORY_HUB_DISABLE_WORKSPACE_AWARENESS", "1")

    qoder_home = tmp_path / ".qoder"
    qoder_projects_dir = qoder_home / "projects"
    qoder_memories_dir = qoder_home / "memories"
    monkeypatch.setenv("AGENT_MEMORY_HUB_QODER_PROJECTS_DIR", str(qoder_projects_dir))
    monkeypatch.setenv("AGENT_MEMORY_HUB_QODER_MEMORIES_DIR", str(qoder_memories_dir))
    monkeypatch.setattr(qoder_mod, "QODER_PROJECTS_DIR", qoder_projects_dir, raising=False)
    monkeypatch.setattr(qoder_mod, "QODER_MEMORIES_DIR", qoder_memories_dir, raising=False)

    qoderwork_home = tmp_path / ".qoderwork"
    projects_dir = qoderwork_home / "projects"
    skills_dir = qoderwork_home / "skills"
    monkeypatch.setenv("AGENT_MEMORY_HUB_QODERWORK_PROJECTS_DIR", str(projects_dir))
    monkeypatch.setenv("AGENT_MEMORY_HUB_QODERWORK_SKILLS_DIR", str(skills_dir))
    monkeypatch.setattr(qw_mod, "QODERWORK_PROJECTS_DIR", projects_dir, raising=False)
    monkeypatch.setattr(qw_mod, "QODERWORK_SKILLS_DIR", skills_dir, raising=False)

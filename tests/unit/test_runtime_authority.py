from pathlib import Path

from agent_brain.agent_integrations.runtime_authority import resolve_runtime_authority


def _repo(root: Path, *, with_memory: bool = True) -> Path:
    root.mkdir(parents=True)
    (root / "pyproject.toml").write_text("[project]\nname='agent-memory-hub'\n")
    hooks = root / "agent_runtime_kit" / "hooks"
    hooks.mkdir(parents=True)
    for name in ("inject-context.sh", "session-end-signal.sh"):
        path = hooks / name
        path.write_text("#!/bin/sh\nexit 0\n")
        path.chmod(0o755)
    if with_memory:
        target = root / ".venv" / "bin" / "memory"
        target.parent.mkdir(parents=True)
        target.write_text("#!/bin/sh\nexit 0\n")
        target.chmod(0o755)
    return root


def _managed_shim(path: Path, root: Path) -> None:
    path.parent.mkdir(parents=True)
    path.write_text(f'#!/bin/sh\nexec "{root / ".venv/bin/memory"}" "$@"\n')


def test_explicit_repo_dir_wins_over_managed_shim(tmp_path: Path) -> None:
    explicit = _repo(tmp_path / "explicit")
    stable = _repo(tmp_path / "stable")
    shim = tmp_path / "bin" / "memory"
    _managed_shim(shim, stable)

    result = resolve_runtime_authority(
        explicit_repo_dir=explicit,
        module_repo_dir=tmp_path / "unused",
        shim_path=shim,
    )

    assert result.valid is True
    assert result.root == explicit.resolve()
    assert result.source == "explicit"


def test_managed_shim_beats_feature_worktree(tmp_path: Path) -> None:
    stable = _repo(tmp_path / "stable")
    worktree = _repo(tmp_path / "feature")
    shim = tmp_path / "bin" / "memory"
    _managed_shim(shim, stable)

    result = resolve_runtime_authority(
        explicit_repo_dir=None,
        module_repo_dir=worktree,
        shim_path=shim,
    )

    assert result.valid is True
    assert result.root == stable.resolve()
    assert result.source == "managed-shim"


def test_missing_shim_allows_first_install_fallback(tmp_path: Path) -> None:
    module_root = _repo(tmp_path / "checkout", with_memory=False)

    result = resolve_runtime_authority(
        explicit_repo_dir=None,
        module_repo_dir=module_root,
        shim_path=tmp_path / "missing-memory",
    )

    assert result.valid is True
    assert result.root == module_root.resolve()
    assert result.source == "module-fallback"


def test_existing_malformed_shim_fails_closed(tmp_path: Path) -> None:
    module_root = _repo(tmp_path / "checkout", with_memory=False)
    shim = tmp_path / "bin" / "memory"
    shim.parent.mkdir(parents=True)
    shim.write_text("#!/bin/sh\nexec /deleted/worktree/memory $@\n")

    result = resolve_runtime_authority(
        explicit_repo_dir=None,
        module_repo_dir=module_root,
        shim_path=shim,
    )

    assert result.valid is False
    assert result.source == "invalid-managed-shim"
    assert "memory doctor --fix" in (result.error or "")


def test_managed_shim_target_must_exist_and_match_repo_contract(tmp_path: Path) -> None:
    module_root = _repo(tmp_path / "checkout", with_memory=False)
    invalid = tmp_path / "not-amh"
    target = invalid / ".venv" / "bin" / "memory"
    target.parent.mkdir(parents=True)
    target.write_text("#!/bin/sh\nexit 0\n")
    shim = tmp_path / "bin" / "memory"
    _managed_shim(shim, invalid)

    result = resolve_runtime_authority(
        explicit_repo_dir=None,
        module_repo_dir=module_root,
        shim_path=shim,
    )

    assert result.valid is False
    assert "invalid AMH runtime root" in (result.error or "")


def test_managed_shim_target_must_be_absolute(tmp_path: Path) -> None:
    module_root = _repo(tmp_path / "checkout", with_memory=False)
    shim = tmp_path / "bin" / "memory"
    shim.parent.mkdir(parents=True)
    shim.write_text('#!/bin/sh\nexec "../stable/.venv/bin/memory" "$@"\n')

    result = resolve_runtime_authority(
        explicit_repo_dir=None,
        module_repo_dir=module_root,
        shim_path=shim,
    )

    assert result.valid is False
    assert "absolute" in (result.error or "")


def test_require_raises_for_invalid_authority(tmp_path: Path) -> None:
    module_root = _repo(tmp_path / "checkout", with_memory=False)
    shim = tmp_path / "bin" / "memory"
    shim.parent.mkdir(parents=True)
    shim.write_text("not a managed shim\n")

    result = resolve_runtime_authority(
        explicit_repo_dir=None,
        module_repo_dir=module_root,
        shim_path=shim,
    )

    try:
        result.require()
    except RuntimeError as exc:
        assert "memory doctor --fix" in str(exc)
    else:
        raise AssertionError("invalid runtime authority must fail closed")

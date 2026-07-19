# Qoder / QoderWork Config Convergence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Qoder and QoderWork long-lived Hook/MCP configuration converge to one stable AMH runtime without deleting third-party handlers, and prove the contract locally, in required CI, and against the real user configuration.

**Architecture:** Add a small runtime-authority resolver for long-lived adapter paths and a shared event reconciler that rebuilds only AMH-owned handlers. Qoder-family installers use those primitives; doctor independently checks exact cardinality, command, path and ordering. A dedicated convergence suite is promoted into `adapter-governance`, while live migration runs only from the stable `main` checkout after remote checks pass.

**Tech Stack:** Python 3.11/3.12, pathlib, dataclasses, JSON, pytest, GitHub Actions, Typer adapter lifecycle CLI, POSIX Hook protocol.

---

## File map

- Create `agent_brain/agent_integrations/runtime_authority.py`: resolve and validate the long-lived AMH checkout used by GUI configs.
- Create `tests/unit/test_runtime_authority.py`: isolated explicit/shim/fallback/fail-closed authority tests.
- Modify `agent_brain/agent_integrations/hook_config.py`: add ownership-safe event reconciliation and removal primitives.
- Create `tests/unit/test_hook_config.py`: low-level duplicate, mixed-entry, ordering and idempotence tests.
- Modify `agent_brain/agent_integrations/qoder.py`: use stable runtime authority and shared reconciliation for install/uninstall/diagnose.
- Modify `agent_brain/agent_integrations/qoder_work.py`: apply the same contract with QoderWork-specific paths.
- Modify `agent_brain/agent_integrations/qoder_diagnostics.py`: enforce exactly-one handler and exact command; require executable scripts.
- Create `tests/unit/test_qoder_config_convergence.py`: end-to-end isolated config/MCP convergence for both adapters.
- Modify `.github/workflows/governance-gates.yml`: execute convergence tests in the required adapter job.
- Modify `tests/unit/test_ci_governance_contract.py`: pin the new required test path and fail-closed behavior.
- Modify `scripts/generate-adapter-governance.py`: include convergence implementation files and render the new truth boundary.
- Modify `tests/unit/test_adapter_governance_report.py`: assert the committed report exposes the convergence contract.
- Regenerate `docs/evaluation/stage3-adapter-productization-report.json` and `docs/evaluation/stage3-adapter-productization-readiness.zh.md`.
- Modify `CHANGELOG.md`: document durable migration and upgrade behavior.

### Task 1: Resolve a stable long-lived runtime authority

**Files:**
- Create: `agent_brain/agent_integrations/runtime_authority.py`
- Create: `tests/unit/test_runtime_authority.py`

- [ ] **Step 1: Write failing authority tests**

Create `tests/unit/test_runtime_authority.py` with explicit coverage of every authority branch:

```python
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
    assert "memory doctor --fix" in result.error


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
    assert "invalid AMH runtime root" in result.error
```

- [ ] **Step 2: Run the new tests and confirm RED**

Run:

```bash
MEMORY_HUB_TEST_EMBEDDING=1 python \
  -m pytest tests/unit/test_runtime_authority.py -q
```

Expected: collection fails because `runtime_authority` does not exist.

- [ ] **Step 3: Implement the authority resolver**

Create `agent_brain/agent_integrations/runtime_authority.py` with this public contract:

```python
"""Resolve the stable AMH checkout used by long-lived adapter configuration."""
from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import re
from typing import Literal

RuntimeAuthoritySource = Literal[
    "explicit",
    "managed-shim",
    "module-fallback",
    "invalid-managed-shim",
]
_MANAGED_SHIM = re.compile(r'^#!/bin/sh\nexec "([^"]+)" "\$@"\n$')


@dataclass(frozen=True)
class RuntimeAuthority:
    root: Path
    source: RuntimeAuthoritySource
    error: str | None = None

    @property
    def valid(self) -> bool:
        return self.error is None

    def require(self) -> Path:
        if self.error is not None:
            raise RuntimeError(self.error)
        return self.root


def managed_memory_shim_path() -> Path:
    user_bin = os.environ.get("AGENT_MEMORY_HUB_BIN")
    return (Path(user_bin) if user_bin else Path.home() / ".local" / "bin") / "memory"


def _root_valid(root: Path) -> bool:
    return (
        (root / "pyproject.toml").is_file()
        and (root / "agent_runtime_kit" / "hooks" / "inject-context.sh").is_file()
        and (root / "agent_runtime_kit" / "hooks" / "session-end-signal.sh").is_file()
    )


def _invalid(module_root: Path, detail: str) -> RuntimeAuthority:
    return RuntimeAuthority(
        root=module_root.resolve(),
        source="invalid-managed-shim",
        error=f"{detail}; run memory doctor --fix before repairing adapters",
    )


def resolve_runtime_authority(
    *,
    explicit_repo_dir: Path | None,
    module_repo_dir: Path,
    shim_path: Path | None = None,
) -> RuntimeAuthority:
    module_root = Path(module_repo_dir).expanduser()
    if explicit_repo_dir is not None:
        root = Path(explicit_repo_dir).expanduser().resolve()
        if not _root_valid(root):
            return RuntimeAuthority(root, "explicit", f"invalid AMH runtime root: {root}")
        return RuntimeAuthority(root, "explicit")

    shim = shim_path or managed_memory_shim_path()
    if not shim.exists():
        root = module_root.resolve()
        if not _root_valid(root):
            return RuntimeAuthority(root, "module-fallback", f"invalid AMH runtime root: {root}")
        return RuntimeAuthority(root, "module-fallback")

    try:
        content = shim.read_text(encoding="utf-8", errors="strict")
    except OSError as exc:
        return _invalid(module_root, f"cannot read managed memory shim {shim}: {exc}")
    match = _MANAGED_SHIM.fullmatch(content)
    if match is None:
        return _invalid(module_root, f"unrecognized managed memory shim: {shim}")
    target = Path(match.group(1)).expanduser()
    if not target.is_absolute():
        return _invalid(module_root, f"managed memory target must be absolute: {target}")
    if not target.is_file():
        return _invalid(module_root, f"managed memory target does not exist: {target}")
    suffixes = (
        Path(".venv/bin/memory"),
        Path(".venv/Scripts/memory.exe"),
    )
    root = next(
        (target.parents[len(suffix.parts) - 1] for suffix in suffixes if target.parts[-len(suffix.parts):] == suffix.parts),
        None,
    )
    if root is None or not _root_valid(root):
        return _invalid(module_root, f"invalid AMH runtime root for managed target: {target}")
    return RuntimeAuthority(root.resolve(), "managed-shim")


__all__ = [
    "RuntimeAuthority",
    "managed_memory_shim_path",
    "resolve_runtime_authority",
]
```

- [ ] **Step 4: Run authority tests and static checks**

Run:

```bash
MEMORY_HUB_TEST_EMBEDDING=1 python \
  -m pytest tests/unit/test_runtime_authority.py -q
ruff check \
  agent_brain/agent_integrations/runtime_authority.py tests/unit/test_runtime_authority.py
```

Expected: all authority tests pass and Ruff reports `All checks passed!`.

- [ ] **Step 5: Commit runtime authority**

```bash
git add agent_brain/agent_integrations/runtime_authority.py tests/unit/test_runtime_authority.py
git commit -m "feat: resolve stable adapter runtime authority"
```

### Task 2: Add ownership-safe Hook event reconciliation

**Files:**
- Modify: `agent_brain/agent_integrations/hook_config.py`
- Create: `tests/unit/test_hook_config.py`

- [ ] **Step 1: Write failing low-level reconciliation tests**

Create `tests/unit/test_hook_config.py`:

```python
from agent_brain.agent_integrations.hook_config import (
    reconcile_managed_hub_hook_event,
    remove_managed_hub_hook_handlers,
)


def _entry(*commands: str) -> dict:
    return {
        "matcher": "",
        "hooks": [{"type": "command", "command": command} for command in commands],
    }


def _commands(entries: list) -> list[str]:
    return [
        hook["command"]
        for entry in entries
        if isinstance(entry, dict)
        for hook in entry.get("hooks", [])
        if isinstance(hook, dict) and isinstance(hook.get("command"), str)
    ]


def test_reconcile_removes_cross_checkout_duplicates_and_wrong_event_script() -> None:
    entries = [
        _entry("/tmp/amh-bench-x/agent_runtime_kit/hooks/inject-context.sh"),
        _entry("~/.config/superpowers/worktrees/amh/old/agent_runtime_kit/hooks/session-end-signal.sh"),
        _entry("/stable/agent_runtime_kit/hooks/inject-context.sh"),
    ]

    changed = reconcile_managed_hub_hook_event(
        entries,
        expected_script_path="/stable/agent_runtime_kit/hooks/inject-context.sh",
        expected_command="ENV=1 /stable/agent_runtime_kit/hooks/inject-context.sh",
        managed_script_names={"inject-context.sh", "session-end-signal.sh"},
        place_first=True,
    )

    assert changed is True
    assert _commands(entries) == ["ENV=1 /stable/agent_runtime_kit/hooks/inject-context.sh"]


def test_reconcile_preserves_foreign_handlers_and_relative_order() -> None:
    entries = [
        _entry("foreign-before"),
        _entry(
            "/old/agent_runtime_kit/hooks/inject-context.sh",
            "foreign-mixed",
        ),
        _entry("foreign-after"),
    ]

    reconcile_managed_hub_hook_event(
        entries,
        expected_script_path="/stable/agent_runtime_kit/hooks/inject-context.sh",
        expected_command="/stable/agent_runtime_kit/hooks/inject-context.sh",
        managed_script_names={"inject-context.sh", "session-end-signal.sh"},
        place_first=True,
    )

    assert _commands(entries) == [
        "/stable/agent_runtime_kit/hooks/inject-context.sh",
        "foreign-before",
        "foreign-mixed",
        "foreign-after",
    ]


def test_stop_uses_first_managed_slot_without_reordering_foreign_entries() -> None:
    entries = [_entry("foreign-before"), _entry("/old/agent_runtime_kit/hooks/session-end-signal.sh"), _entry("foreign-after")]

    reconcile_managed_hub_hook_event(
        entries,
        expected_script_path="/stable/agent_runtime_kit/hooks/session-end-signal.sh",
        expected_command="/stable/agent_runtime_kit/hooks/session-end-signal.sh",
        managed_script_names={"inject-context.sh", "session-end-signal.sh"},
        place_first=False,
    )

    assert _commands(entries) == [
        "foreign-before",
        "/stable/agent_runtime_kit/hooks/session-end-signal.sh",
        "foreign-after",
    ]


def test_reconcile_is_byte_structure_idempotent() -> None:
    entries = [_entry("/stable/agent_runtime_kit/hooks/inject-context.sh")]
    kwargs = {
        "expected_script_path": "/stable/agent_runtime_kit/hooks/inject-context.sh",
        "expected_command": "/stable/agent_runtime_kit/hooks/inject-context.sh",
        "managed_script_names": {"inject-context.sh", "session-end-signal.sh"},
        "place_first": True,
    }

    assert reconcile_managed_hub_hook_event(entries, **kwargs) is False
    assert reconcile_managed_hub_hook_event(entries, **kwargs) is False


def test_remove_managed_handlers_keeps_unknown_and_foreign_commands() -> None:
    entries = [
        _entry("foreign", "/old/agent_runtime_kit/hooks/inject-context.sh"),
        _entry("/custom/agent_runtime_kit/hooks/future-event.sh"),
    ]

    removed = remove_managed_hub_hook_handlers(
        entries,
        managed_script_names={"inject-context.sh", "session-end-signal.sh"},
    )

    assert removed == 1
    assert _commands(entries) == [
        "foreign",
        "/custom/agent_runtime_kit/hooks/future-event.sh",
    ]
```

- [ ] **Step 2: Run Hook config tests and confirm RED**

Run:

```bash
python \
  -m pytest tests/unit/test_hook_config.py -q
```

Expected: import fails because the two new functions are absent.

- [ ] **Step 3: Implement the shared mutation primitives**

Add focused helpers to `hook_config.py`. The implementation must compare the rebuilt structure with the original, remove only token-recognized AMH script names, and use a dedicated canonical entry so moving AMH first never moves a foreign mixed handler:

```python
from collections.abc import Collection


def _strip_managed_handlers(
    entries: list,
    *,
    managed_script_names: Collection[str],
) -> tuple[list, int, int | None]:
    rebuilt: list = []
    removed = 0
    first_managed_slot: int | None = None
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get("hooks"), list):
            rebuilt.append(entry)
            continue
        filtered: list = []
        entry_removed = 0
        for hook in entry["hooks"]:
            command = str(hook.get("command", "")) if isinstance(hook, dict) else ""
            owned = isinstance(hook, dict) and any(
                command_references_hub_hook_script(command, name)
                for name in managed_script_names
            )
            if owned:
                entry_removed += 1
                removed += 1
            else:
                filtered.append(hook)
        if entry_removed and first_managed_slot is None:
            first_managed_slot = len(rebuilt)
        if filtered:
            updated = dict(entry)
            updated["hooks"] = filtered
            rebuilt.append(updated)
    return rebuilt, removed, first_managed_slot


def remove_managed_hub_hook_handlers(
    entries: list,
    *,
    managed_script_names: Collection[str],
) -> int:
    rebuilt, removed, _slot = _strip_managed_handlers(
        entries,
        managed_script_names=managed_script_names,
    )
    if rebuilt != entries:
        entries[:] = rebuilt
    return removed


def reconcile_managed_hub_hook_event(
    entries: list,
    *,
    expected_script_path: str,
    expected_command: str,
    managed_script_names: Collection[str],
    place_first: bool,
) -> bool:
    expected_name = Path(expected_script_path).name
    if expected_name not in managed_script_names:
        raise ValueError(f"expected script is not managed: {expected_name}")
    original = list(entries)
    rebuilt, _removed, first_slot = _strip_managed_handlers(
        entries,
        managed_script_names=managed_script_names,
    )
    canonical = {
        "matcher": "",
        "hooks": [{"type": "command", "command": expected_command}],
    }
    position = 0 if place_first else min(
        first_slot if first_slot is not None else len(rebuilt),
        len(rebuilt),
    )
    rebuilt.insert(position, canonical)
    if rebuilt == original:
        return False
    entries[:] = rebuilt
    return True
```

Use `expected_script_path` in a validation assertion that its basename is in `managed_script_names`; this prevents a caller from creating an unmanaged canonical entry. Export both public helpers in `__all__`.

- [ ] **Step 4: Run focused tests and existing Hook helper consumers**

Run:

```bash
python -m pytest \
  tests/unit/test_hook_config.py \
  tests/unit/test_adapters.py::TestClaudeCodeAdapterRealInstall \
  tests/unit/test_adapters.py::TestCodexAdapterRealInstall -q
```

Expected: new low-level tests pass and existing Codex/Claude contracts remain green.

- [ ] **Step 5: Commit Hook reconciliation**

```bash
git add agent_brain/agent_integrations/hook_config.py tests/unit/test_hook_config.py
git commit -m "feat: reconcile managed hook handlers safely"
```

### Task 3: Integrate convergence into Qoder and QoderWork

**Files:**
- Modify: `agent_brain/agent_integrations/qoder.py`
- Modify: `agent_brain/agent_integrations/qoder_work.py`
- Create: `tests/unit/test_qoder_config_convergence.py`

- [ ] **Step 1: Build an isolated two-adapter fixture and failing install tests**

Create `tests/unit/test_qoder_config_convergence.py`. The fixture must monkeypatch every user path and pass an explicit stable repo so the test cannot touch real HOME:

```python
from dataclasses import dataclass
import json
from pathlib import Path

import pytest


@dataclass
class Harness:
    adapter: object
    settings: Path
    mcp_paths: tuple[Path, ...]
    stable_repo: Path
    adapter_name: str


def _stable_repo(root: Path) -> Path:
    root.mkdir()
    (root / "pyproject.toml").write_text("[project]\nname='agent-memory-hub'\n")
    hooks = root / "agent_runtime_kit" / "hooks"
    hooks.mkdir(parents=True)
    for name in ("inject-context.sh", "session-end-signal.sh"):
        script = hooks / name
        script.write_text("#!/bin/sh\nexit 0\n")
        script.chmod(0o755)
    python = root / ".venv" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.write_text("#!/bin/sh\nexit 0\n")
    python.chmod(0o755)
    return root


@pytest.fixture(params=("qoder", "qoder_work"))
def harness(request, tmp_path: Path, monkeypatch) -> Harness:
    stable = _stable_repo(tmp_path / "stable")
    brain = tmp_path / "brain"
    brain.mkdir()
    if request.param == "qoder":
        from agent_brain.agent_integrations import qoder as module
        settings = tmp_path / ".qoder" / "settings.json"
        shared = tmp_path / "Qoder" / "SharedClientCache" / "mcp.json"
        user = tmp_path / "Qoder" / "User" / "mcp.json"
        extension = tmp_path / "Qoder" / "SharedClientCache" / "extension" / "local" / "mcp.json"
        monkeypatch.setattr(module, "SETTINGS_PATH", settings)
        monkeypatch.setattr(module, "AWARENESS_PATH", tmp_path / ".qoder" / "AGENTS.md")
        monkeypatch.setattr(module, "MCP_CONFIG_PATH", shared)
        monkeypatch.setattr(module, "MCP_USER_CONFIG_PATH", user)
        monkeypatch.setattr(module, "MCP_EXTENSION_CONFIG_PATH", extension)
        monkeypatch.setattr(module, "QODER_PROJECTS_DIR", tmp_path / ".qoder" / "projects")
        monkeypatch.setattr(module, "QODER_MEMORIES_DIR", tmp_path / ".qoder" / "memories")
        monkeypatch.setattr(module, "QODER_LOCAL_DB_PATH", tmp_path / "missing.db")
        adapter = module.QoderAdapter(brain, repo_dir=stable)
        return Harness(adapter, settings, (user, shared, extension), stable, "qoder")

    from agent_brain.agent_integrations import qoder_work as module
    settings = tmp_path / ".qoderwork" / "settings.json"
    mcp = tmp_path / ".qoderwork" / "mcp.json"
    monkeypatch.setattr(module, "SETTINGS_PATH", settings)
    monkeypatch.setattr(module, "MCP_CONFIG_PATH", mcp)
    monkeypatch.setattr(module, "AWARENESS_PATH", tmp_path / ".qoderwork" / "awareness" / "main" / "AGENTS.md")
    monkeypatch.setattr(module, "QODERWORK_PROJECTS_DIR", tmp_path / ".qoderwork" / "projects")
    monkeypatch.setattr(module, "QODERWORK_SKILLS_DIR", tmp_path / ".qoderwork" / "skills")
    adapter = module.QoderWorkAdapter(brain, repo_dir=stable)
    return Harness(adapter, settings, (mcp,), stable, "qoder_work")


def _seed_drift(harness: Harness) -> list[str]:
    foreign = ["foreign-before", "foreign-mixed", "foreign-after"]
    payload = {
        "hooks": {
            "UserPromptSubmit": [
                {"matcher": "", "hooks": [{"type": "command", "command": foreign[0]}]},
                {"matcher": "", "hooks": [
                    {"type": "command", "command": "/private/tmp/amh-bench-x/agent_runtime_kit/hooks/inject-context.sh"},
                    {"type": "command", "command": foreign[1]},
                ]},
                {"matcher": "", "hooks": [{"type": "command", "command": "/old/worktree/agent_runtime_kit/hooks/inject-context.sh"}]},
                {"matcher": "", "hooks": [{"type": "command", "command": foreign[2]}]},
            ],
            "Stop": [
                {"matcher": "", "hooks": [{"type": "command", "command": foreign[0]}]},
                {"matcher": "", "hooks": [{"type": "command", "command": "/old/worktree/agent_runtime_kit/hooks/session-end-signal.sh"}]},
                {"matcher": "", "hooks": [{"type": "command", "command": foreign[2]}]},
            ],
        }
    }
    harness.settings.parent.mkdir(parents=True)
    harness.settings.write_text(json.dumps(payload))
    for path in harness.mcp_paths:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"mcpServers": {"agent-memory-hub": {
            "command": "/old/worktree/.venv/bin/python",
            "args": ["-m", "agent_brain.interfaces.mcp.server"],
            "env": {"BRAIN_DIR": "/old/brain", "PYTHONPATH": "/old/worktree"},
            "enabled": True,
        }}}))
    return foreign
```

Add tests asserting one canonical prompt/stop handler, foreign command equality, stable MCP fields, absence of old paths, and second-install byte equality.

- [ ] **Step 2: Run convergence tests and confirm RED**

Run:

```bash
MEMORY_HUB_TEST_EMBEDDING=1 python \
  -m pytest tests/unit/test_qoder_config_convergence.py -q
```

Expected: duplicate handler assertions fail for both adapters.

- [ ] **Step 3: Resolve runtime authority in both constructors**

In both adapters, retain the explicit constructor injection while default instances use the managed shim:

```python
module_repo_dir = Path(__file__).resolve().parents[2]
self.runtime_authority = resolve_runtime_authority(
    explicit_repo_dir=repo_dir,
    module_repo_dir=module_repo_dir,
)
self.repo_dir = self.runtime_authority.root
self.hooks_dir = self.repo_dir / "agent_runtime_kit" / "hooks"
```

At the start of `install()`, call `self.runtime_authority.require()` before creating or changing any user file. Keep `diagnose()` non-throwing; Task 4 adds a diagnostic check.

- [ ] **Step 4: Replace per-event update logic with the shared reconciler**

For both Qoder adapters, replace `_hook_script_present`, `_update_hook_command`, and `_move_hook_entry_first` flow with:

```python
managed_script_names = frozenset(self.HOOK_SCRIPTS.values())
changed_events: list[str] = []
for event in self.HOOK_EVENTS:
    script = self.hooks_dir / self.HOOK_SCRIPTS[event]
    entries = hooks.setdefault(event, [])
    if not isinstance(entries, list):
        raise RuntimeError(f"refuse to overwrite {SETTINGS_PATH}: hooks.{event} must be a list")
    changed = _reconcile_managed_hub_hook_event(
        entries,
        expected_script_path=str(script),
        expected_command=self._hook_command(event, script),
        managed_script_names=managed_script_names,
        place_first=event == "UserPromptSubmit",
    )
    if changed:
        changed_events.append(event)
```

Remove now-unused private mover methods and imports.

- [ ] **Step 5: Make uninstall remove all managed cross-checkout handlers**

For each supported event in both adapters:

```python
entries = hooks.get(event, [])
if not isinstance(entries, list):
    continue
removed += _remove_managed_hub_hook_handlers(
    entries,
    managed_script_names=frozenset(self.HOOK_SCRIPTS.values()),
)
```

Do not use the current-checkout-only `_hook_belongs_to` predicate on Qoder-family uninstall after this change.

- [ ] **Step 6: Complete high-level convergence assertions**

Add these tests to `test_qoder_config_convergence.py` using the fixture above:

```python
def _managed(commands: list[str], name: str) -> list[str]:
    return [command for command in commands if f"/agent_runtime_kit/hooks/{name}" in command]


def test_install_converges_hooks_mcp_and_preserves_foreign(harness: Harness) -> None:
    foreign = _seed_drift(harness)
    harness.adapter.install()
    payload = json.loads(harness.settings.read_text())
    prompt = [hook["command"] for entry in payload["hooks"]["UserPromptSubmit"] for hook in entry["hooks"]]
    stop = [hook["command"] for entry in payload["hooks"]["Stop"] for hook in entry["hooks"]]
    assert len(_managed(prompt, "inject-context.sh")) == 1
    assert len(_managed(stop, "session-end-signal.sh")) == 1
    assert prompt[0].endswith(str(harness.stable_repo / "agent_runtime_kit/hooks/inject-context.sh"))
    assert [command for command in prompt if command in foreign] == foreign
    assert [command for command in stop if command in foreign] == [foreign[0], foreign[2]]
    assert "amh-bench" not in harness.settings.read_text()
    assert "/old/worktree" not in harness.settings.read_text()
    for path in harness.mcp_paths:
        server = json.loads(path.read_text())["mcpServers"]["agent-memory-hub"]
        assert server["env"]["PYTHONPATH"] == str(harness.stable_repo)
        assert server["env"]["BRAIN_DIR"] == str(harness.adapter.brain_dir)


def test_install_is_byte_idempotent_after_convergence(harness: Harness) -> None:
    _seed_drift(harness)
    harness.adapter.install()
    first = {path: path.read_bytes() for path in (harness.settings, *harness.mcp_paths)}
    harness.adapter.install()
    assert {path: path.read_bytes() for path in first} == first


def test_malformed_settings_fail_without_overwrite(harness: Harness) -> None:
    harness.settings.parent.mkdir(parents=True)
    harness.settings.write_text("{not json")
    before = harness.settings.read_bytes()
    with pytest.raises(RuntimeError, match="malformed"):
        harness.adapter.install()
    assert harness.settings.read_bytes() == before


def test_non_list_event_fails_without_rewriting_settings(harness: Harness) -> None:
    harness.settings.parent.mkdir(parents=True)
    harness.settings.write_text(json.dumps({"hooks": {"UserPromptSubmit": {}}}))
    before = harness.settings.read_bytes()
    with pytest.raises(RuntimeError, match="hooks.UserPromptSubmit must be a list"):
        harness.adapter.install()
    assert harness.settings.read_bytes() == before
```

Add an uninstall test that calls install, then uninstall, and proves every foreign command and its matcher/custom metadata remain byte-structurally equal while both known AMH script names are absent.

- [ ] **Step 7: Run both new and existing adapter suites**

Run:

```bash
MEMORY_HUB_TEST_EMBEDDING=1 python -m pytest \
  tests/unit/test_runtime_authority.py \
  tests/unit/test_hook_config.py \
  tests/unit/test_qoder_config_convergence.py \
  tests/unit/test_adapters.py -q
```

Expected: all tests pass; the existing adapter count remains 181 passed before adding new files.

- [ ] **Step 8: Commit Qoder integration**

```bash
git add \
  agent_brain/agent_integrations/qoder.py \
  agent_brain/agent_integrations/qoder_work.py \
  tests/unit/test_qoder_config_convergence.py
git commit -m "fix: converge qoder hook and mcp configuration"
```

### Task 4: Make doctor fail closed on non-converged configuration

**Files:**
- Modify: `agent_brain/agent_integrations/qoder_diagnostics.py`
- Modify: `agent_brain/agent_integrations/qoder.py`
- Modify: `agent_brain/agent_integrations/qoder_work.py`
- Modify: `tests/unit/test_qoder_config_convergence.py`

- [ ] **Step 1: Add failing doctor tests**

Add parametrized tests proving doctor rejects duplicates, wrong-event scripts, exact command drift and invalid runtime authority without echoing foreign commands:

```python
@pytest.mark.parametrize("fault", ("duplicate", "wrong-script", "command-drift"))
def test_doctor_rejects_non_converged_hooks(harness: Harness, fault: str) -> None:
    _seed_drift(harness)
    harness.adapter.install()
    payload = json.loads(harness.settings.read_text())
    entries = payload["hooks"]["UserPromptSubmit"]
    if fault == "duplicate":
        entries.append({"matcher": "", "hooks": [{"type": "command", "command": "/old/agent_runtime_kit/hooks/inject-context.sh"}]})
    elif fault == "wrong-script":
        entries[0]["hooks"][0]["command"] = "/stable/agent_runtime_kit/hooks/session-end-signal.sh"
    else:
        entries[0]["hooks"][0]["command"] += " --unsafe-drift"
    harness.settings.write_text(json.dumps(payload))

    report = harness.adapter.diagnose().to_dict()
    check = next(item for item in report["checks"] if item["name"].endswith("settings hooks"))
    assert check["status"] == "error"
    assert "foreign-before" not in check["detail"]
```

Add a focused test where the managed shim exists but is malformed and the adapter is constructed without explicit `repo_dir`; `install()` must raise before any settings file is created, while doctor returns an error check.

Also add a valid-shim integration test: construct the adapter without explicit `repo_dir`, point the managed shim at an isolated stable repo while the imported module lives elsewhere, and assert both `adapter.repo_dir` and generated Hook/MCP commands use the stable repo.

- [ ] **Step 2: Run doctor tests and confirm RED**

Run:

```bash
python -m pytest \
  tests/unit/test_qoder_config_convergence.py -k doctor -q
```

Expected: at least duplicate and exact-command drift are incorrectly accepted by current diagnostics.

- [ ] **Step 3: Strengthen shared Qoder-compatible diagnostics**

Change `diagnose_settings_hooks()` to accept `expected_commands: dict[str, str]`. For each event, enumerate only handlers whose shell tokens end in one of `hook_scripts.values()`, then enforce:

```python
if len(managed) != 1:
    problems.append(f"{event}: expected exactly 1 managed handler, found {len(managed)}")
elif not command_references_path(command, str(hooks_dir / hook_scripts[event])):
    problems.append(f"{event}: managed handler points to the wrong script")
elif command != expected_commands[event]:
    problems.append(f"{event}: managed handler command does not match the canonical command")
```

Return one error check with semicolon-separated safe problem labels; never include foreign commands. Update `diagnose_hook_scripts()` to treat a missing file or `not os.access(path, os.X_OK)` as an error.

Add a regression test that removes the executable bit from one isolated Hook script and asserts doctor reports the script check as error.

- [ ] **Step 4: Add runtime authority diagnostics and expected commands**

In each adapter add:

```python
def _diagnose_runtime_authority(self) -> AdapterDiagnosticCheck:
    if not self.runtime_authority.valid:
        return AdapterDiagnosticCheck(
            name=f"{self.get_config().agent_name} runtime authority",
            status="error",
            detail=str(self.runtime_authority.error),
            fix="run: memory doctor --fix, then repair this adapter",
        )
    return AdapterDiagnosticCheck(
        name=f"{self.get_config().agent_name} runtime authority",
        status="ok",
        detail=f"stable runtime root selected via {self.runtime_authority.source}",
    )
```

Place it before settings/MCP checks. Pass a full event-to-command map into `diagnose_settings_hooks()`:

```python
expected_commands = {
    event: self._hook_command(event, self.hooks_dir / self.HOOK_SCRIPTS[event])
    for event in self.HOOK_EVENTS
}
```

- [ ] **Step 5: Run diagnostics and lifecycle regression tests**

Run:

```bash
MEMORY_HUB_TEST_EMBEDDING=1 python -m pytest \
  tests/unit/test_qoder_config_convergence.py \
  tests/unit/test_adapters.py \
  tests/unit/test_adapter_lifecycle_records.py \
  tests/system/test_adapter_lifecycle_contract.py -q
```

Expected: all tests pass, and no verify path promotes a doctor error.

- [ ] **Step 6: Commit diagnostic hardening**

```bash
git add \
  agent_brain/agent_integrations/qoder_diagnostics.py \
  agent_brain/agent_integrations/qoder.py \
  agent_brain/agent_integrations/qoder_work.py \
  tests/unit/test_qoder_config_convergence.py
git commit -m "fix: fail closed on qoder config drift"
```

### Task 5: Promote convergence into required adapter governance

**Files:**
- Modify: `.github/workflows/governance-gates.yml`
- Modify: `tests/unit/test_ci_governance_contract.py`
- Modify: `scripts/generate-adapter-governance.py`
- Modify: `tests/unit/test_adapter_governance_report.py`
- Modify: `docs/evaluation/stage3-adapter-productization-report.json`
- Modify: `docs/evaluation/stage3-adapter-productization-readiness.zh.md`
- Modify: `CHANGELOG.md`

- [ ] **Step 1: Add failing CI/report contract assertions**

Require the workflow and committed report to expose the new gate:

```python
# tests/unit/test_ci_governance_contract.py
assert "tests/unit/test_qoder_config_convergence.py" in commands
assert "tests/unit/test_runtime_authority.py" in commands
assert "tests/unit/test_hook_config.py" in commands

# tests/unit/test_adapter_governance_report.py
assert report["config_convergence"] == {
    "adapters": ["qoder", "qoder_work"],
    "hook_cardinality": 1,
    "required_check": "adapter-governance",
    "runtime_authority": "managed-memory-shim",
    "schema_version": "amh-adapter-config-convergence/v1",
}
```

- [ ] **Step 2: Run the contract tests and confirm RED**

Run:

```bash
python -m pytest \
  tests/unit/test_ci_governance_contract.py \
  tests/unit/test_adapter_governance_report.py -q
```

Expected: workflow paths and `config_convergence` are absent.

- [ ] **Step 3: Add the required tests to `adapter-governance`**

Append these paths to the existing pytest command, before system tests:

```yaml
          tests/unit/test_runtime_authority.py
          tests/unit/test_hook_config.py
          tests/unit/test_qoder_config_convergence.py
```

Keep the job name, no `continue-on-error`, and the existing committed report check unchanged.

- [ ] **Step 4: Extend the generated governance contract**

Add the five implementation files to `IMPLEMENTATION_PATHS`:

```python
"agent_brain/agent_integrations/runtime_authority.py",
"agent_brain/agent_integrations/hook_config.py",
"agent_brain/agent_integrations/qoder.py",
"agent_brain/agent_integrations/qoder_work.py",
"agent_brain/agent_integrations/qoder_diagnostics.py",
```

Add this stable field to the report payload:

```python
"config_convergence": {
    "schema_version": "amh-adapter-config-convergence/v1",
    "adapters": ["qoder", "qoder_work"],
    "hook_cardinality": 1,
    "runtime_authority": "managed-memory-shim",
    "required_check": "adapter-governance",
},
```

Render a Markdown section stating that the machine gate proves config ownership/cardinality and stable runtime selection, but client effectiveness still requires fresh transcript/context evidence.

- [ ] **Step 5: Regenerate report outputs and update the changelog**

Run:

```bash
python scripts/generate-adapter-governance.py
```

Add a `CHANGELOG.md` bullet explaining that Qoder-family repair now prunes stale cross-checkout duplicates, preserves foreign hooks, canonicalizes MCP paths, and may require a client restart for fresh effectiveness evidence.

- [ ] **Step 6: Verify the complete governance job locally**

Run exactly the required job payload:

```bash
MEMORY_HUB_TEST_EMBEDDING=1 python -m pytest \
  tests/unit/test_adapter_manifests.py \
  tests/unit/test_adapter_lifecycle_records.py \
  tests/unit/test_adapter_release_controls.py \
  tests/unit/test_adapter_governance_report.py \
  tests/unit/test_runtime_authority.py \
  tests/unit/test_hook_config.py \
  tests/unit/test_qoder_config_convergence.py \
  tests/system/test_adapter_lifecycle_contract.py \
  tests/system/test_adapter_core_isolation.py -q
python \
  scripts/generate-adapter-governance.py --check
```

Expected: pytest is green and generator prints `adapter-governance: PASS`.

- [ ] **Step 7: Commit CI and evidence changes**

```bash
git add \
  .github/workflows/governance-gates.yml \
  tests/unit/test_ci_governance_contract.py \
  scripts/generate-adapter-governance.py \
  tests/unit/test_adapter_governance_report.py \
  docs/evaluation/stage3-adapter-productization-report.json \
  docs/evaluation/stage3-adapter-productization-readiness.zh.md \
  CHANGELOG.md
git commit -m "ci: require qoder config convergence evidence"
```

### Task 6: Execute complete local verification

**Files:**
- No product file changes expected; fix only failures caused by this branch.

- [ ] **Step 1: Run focused red/green regression suite**

```bash
MEMORY_HUB_TEST_EMBEDDING=1 python -m pytest \
  tests/unit/test_runtime_authority.py \
  tests/unit/test_hook_config.py \
  tests/unit/test_qoder_config_convergence.py \
  tests/unit/test_adapters.py \
  tests/unit/test_ci_governance_contract.py \
  tests/unit/test_adapter_governance_report.py -q
```

Expected: all focused tests pass.

- [ ] **Step 2: Run lint, type and whitespace gates**

```bash
ruff check .
python scripts/check_mypy_baseline.py
git diff --check
```

Expected: Ruff and diff pass; mypy reports no new fingerprints. If the host Python 3.14 parser reproduces a documented baseline infrastructure error, rerun in an isolated Python 3.12 `.[dev]` environment before classifying it.

- [ ] **Step 3: Run full supported Python 3.12 unit suite**

Use a temporary Python 3.12 environment installed with `pip install -e ".[dev]"`, raise the macOS descriptor limit, then run:

```bash
ulimit -n 4096
MEMORY_HUB_TEST_EMBEDDING=1 python -m pytest tests/unit -q --disable-warnings
```

Expected: zero failures; only explicitly documented opt-in skips.

- [ ] **Step 4: Run system, conformance and Hook suites**

```bash
MEMORY_HUB_TEST_EMBEDDING=1 python -m pytest tests/system -q --disable-warnings
MEMORY_HUB_TEST_EMBEDDING=1 python -m pytest tests/conformance -q --disable-warnings
bash agent_runtime_kit/hooks/test-hook.sh
python scripts/check-recall-quality.py
python scripts/generate-adapter-governance.py --check
```

Expected: every command exits 0; Hook shell reports 6 passed / 0 failed.

- [ ] **Step 5: Audit the approved spec line by line**

Open `docs/superpowers/specs/2026-07-19-qoder-config-convergence-design.md` and record evidence for all 12 completion-definition items. Any missing, indirect or stale evidence remains incomplete.

- [ ] **Step 6: Commit any verification-only correction**

If verification required an in-scope code correction, commit only that correction and its regression test:

```bash
git add -u
git commit -m "fix: close qoder convergence verification gap"
```

If no files changed, do not create an empty commit.

### Task 7: Direct-push, repair the real machine, and capture client truth

**Files:**
- Runtime user configs under `~/.qoder`, `~/.qoderwork`, and Qoder Application Support are migrated by explicit CLI commands after `main` is stable.
- Update `tests/fixtures/adapter_productization_evidence.json` and generated stage-three outputs only if fresh client evidence actually satisfies the existing schema.

- [ ] **Step 1: Finish the branch using the approved direct-main workflow**

Use `superpowers:verification-before-completion` and `superpowers:finishing-a-development-branch`. Confirm remote `main` is still an ancestor, fast-forward local root `main`, and push without a PR:

```bash
git fetch origin main
git merge-base --is-ancestor origin/main HEAD
git push origin HEAD:main
```

Expected: non-force fast-forward to the exact branch HEAD.

- [ ] **Step 2: Wait for every required remote check**

Use `gh run list`, `gh run view`, and `gh run watch` until `unit (3.11)`, `unit (3.12)`, `hook-tests`, `security`, `benchmark-integrity`, `docker-smoke`, `recall-quality`, and `adapter-governance` are terminal success. Do not repair live configs from a branch path while remote `main` is unsettled.

- [ ] **Step 3: Capture a low-sensitive before snapshot**

From the stable root checkout, record only counts and AMH-owned paths in `/tmp`, not full configs:

```bash
jq '[.hooks.UserPromptSubmit[]?.hooks[]?.command | select(contains("agent_runtime_kit/hooks/"))] | length' ~/.qoder/settings.json
jq '[.hooks.UserPromptSubmit[]?.hooks[]?.command | select(contains("agent_runtime_kit/hooks/"))] | length' ~/.qoderwork/settings.json
```

Expected pre-migration evidence on this machine: Qoder 4, QoderWork 3.

- [ ] **Step 4: Repair both adapters only from stable `main`**

```bash
cd <stable-agent-memory-hub-checkout>
memory adapter repair qoder
memory adapter repair qoder_work
```

Expected: both lifecycle results report a successful repair transaction; a later verify may remain context-blocked until the GUI produces fresh evidence.

- [ ] **Step 5: Verify real config cardinality, provenance and foreign preservation**

Run safe projections that print only AMH commands and counts:

```bash
jq '[.hooks.UserPromptSubmit[]?.hooks[]?.command | select(contains("agent_runtime_kit/hooks/"))]' ~/.qoder/settings.json
jq '[.hooks.UserPromptSubmit[]?.hooks[]?.command | select(contains("agent_runtime_kit/hooks/"))]' ~/.qoderwork/settings.json
rg -n 'amh-bench-|superpowers/worktrees|/old/worktree' \
  ~/.qoder/settings.json ~/.qoderwork/settings.json \
  "$HOME/Library/Application Support/Qoder/User/mcp.json" \
  "$HOME/Library/Application Support/Qoder/SharedClientCache/mcp.json" \
  "$HOME/Library/Application Support/Qoder/SharedClientCache/extension/local/mcp.json" \
  ~/.qoderwork/mcp.json
```

Expected: one prompt Hook per adapter and `rg` exits 1 because no stale reference remains. Compare the known foreign command list from the before snapshot without logging unrelated config values.

- [ ] **Step 6: Run doctor, verify and real Hook protocol probes**

```bash
memory adapter doctor qoder --format json
memory adapter doctor qoder_work --format json
memory adapter verify qoder --format json
memory adapter verify qoder_work --format json
python scripts/run-hook-recall-evidence.py --adapter qoder --output /tmp/qoder-hook-evidence.json
python scripts/check-hook-recall-evidence.py --adapter qoder --require-clean /tmp/qoder-hook-evidence.json
```

Expected: doctor contains no error checks; real Hook evidence is pass. Verify is pass only if fresh client context exists, otherwise it must retain `CONTEXT_MISSING`/`EVIDENCE_STALE` rather than fabricate success.

- [ ] **Step 7: Obtain fresh Qoder-family client evidence when the apps are available**

Use the `computer-use` skill to restart/refresh Qoder and QoderWork and submit a prompt naming a known AMH project. Re-run both verify commands. If transcript-level AMH context/MCP evidence arrives, update the real-machine evidence fixture and regenerate the stage-three outputs in a small follow-up commit; if it does not, write an AMH `signal` with current blocker, impact and the single expected user action.

- [ ] **Step 8: Synchronize local root and clean the temporary worktree**

Fast-forward root `main` to remote, preserve `findings.md`, `progress.md`, and `task_plan.md`, remove only the completed `qoder-config-convergence` worktree, and delete the merged local feature branch.

- [ ] **Step 9: Write the durable AMH artifact memory**

Record the final commit, Actions run URLs, before/after counts, stable runtime authority, test totals and any honest client blocker using the required artifact body sections `**产出物**` and `**用途**`. Do not store full config, prompt, transcript, token or secrets.

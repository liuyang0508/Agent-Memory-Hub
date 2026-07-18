from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import pytest

from agent_brain.interfaces.sdk import MemoryClient
from agent_brain.memory.store.items_store import ItemsStore
from agent_brain.memory.store.write_service import WriteService
from agent_brain.platform.indexing.index import HubIndex


def test_hub_index_context_manager_closes_idempotently(tmp_path: Path) -> None:
    index = HubIndex(tmp_path / "index.db", embedding_dim=8)

    with index as entered:
        assert entered is index
        assert index.connection.execute("select 1").fetchone() == (1,)

    index.close()
    with pytest.raises(sqlite3.ProgrammingError):
        index.connection.execute("select 1")


@pytest.mark.skipif(not Path("/dev/fd").is_dir(), reason="requires dev-fd")
def test_memory_client_context_manager_returns_fd_to_baseline(tmp_path: Path) -> None:
    before = len(os.listdir("/dev/fd"))

    for _ in range(30):
        with MemoryClient(brain_dir=tmp_path) as client:
            client._components.get_index()

    after = len(os.listdir("/dev/fd"))
    assert after <= before + 3


def test_write_service_for_brain_closes_only_its_owned_index(tmp_path: Path) -> None:
    owned = WriteService.for_brain(tmp_path / "owned")
    owned_index = owned._index
    assert owned_index is not None

    owned.close()
    owned.close()
    with pytest.raises(sqlite3.ProgrammingError):
        owned_index.connection.execute("select 1")

    class InjectedIndex:
        close_calls = 0

        def close(self) -> None:
            self.close_calls += 1

    injected_index = InjectedIndex()
    injected = WriteService(
        ItemsStore(tmp_path / "injected" / "items"),
        index=injected_index,
        brain_dir=tmp_path / "injected",
    )

    injected.close()
    assert injected_index.close_calls == 0


def test_managed_cli_components_close_index_on_success_and_error(monkeypatch) -> None:
    from agent_brain.interfaces.cli import _shared

    class FakeIndex:
        def __init__(self) -> None:
            self.close_calls = 0

        def close(self) -> None:
            self.close_calls += 1

    success_index = FakeIndex()
    monkeypatch.setattr(
        _shared._cli,
        "_open_components",
        lambda: (object(), success_index, object()),
    )
    with _shared._managed_components() as components:
        assert components[1] is success_index
    assert success_index.close_calls == 1

    error_index = FakeIndex()
    monkeypatch.setattr(
        _shared._cli,
        "_open_components",
        lambda: (object(), error_index, object()),
    )
    with pytest.raises(RuntimeError, match="fixture failure"):
        with _shared._managed_components():
            raise RuntimeError("fixture failure")
    assert error_index.close_calls == 1


def test_command_components_uses_typer_context_provider(monkeypatch) -> None:
    from agent_brain.interfaces.cli import _shared

    sentinel = (object(), object(), object())

    class FakeContext:
        def __init__(self) -> None:
            self.resource = None

        def with_resource(self, resource):
            self.resource = resource
            return sentinel

    context = FakeContext()
    monkeypatch.setattr(_shared, "_get_current_context", lambda *, silent: context)

    assert _shared._command_components(hook=True) is sentinel
    assert context.resource is not None

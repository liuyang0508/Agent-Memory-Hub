"""Web cache lifecycle must not leak SQLite file descriptors across brain dirs."""
from __future__ import annotations

import sqlite3
from pathlib import Path


def test_state_store_cache_eviction_closes_old_connection(tmp_path: Path, monkeypatch):
    import web.state_store as st

    monkeypatch.setattr(st, "_MAX_STATE_CACHE_ENTRIES", 2, raising=False)
    st._state_cache.clear()

    stores = []
    for i in range(3):
        brain = tmp_path / f"brain-{i}"
        (brain / "items").mkdir(parents=True)
        stores.append(st.get_state_store(brain))

    try:
        assert len(st._state_cache) == 2
        with pytest_raises_closed_sqlite():
            stores[0].connection.execute("SELECT 1")
    finally:
        st._state_cache.clear()


def test_component_cache_eviction_and_clear_close_old_indexes(tmp_path: Path, monkeypatch):
    import web._base as base

    monkeypatch.setattr(base, "_MAX_COMPONENT_CACHE_ENTRIES", 2, raising=False)
    base._components_cache.clear()

    components = []
    for i in range(3):
        brain = tmp_path / f"brain-{i}"
        (brain / "items").mkdir(parents=True)
        monkeypatch.setenv("BRAIN_DIR", str(brain))
        components.append(base._components())

    try:
        assert len(base._components_cache) == 2
        with pytest_raises_closed_sqlite():
            components[0][1].connection.execute("SELECT 1")

        latest_index = components[-1][1]
        base._components_cache.clear()
        assert len(base._components_cache) == 0
        with pytest_raises_closed_sqlite():
            latest_index.connection.execute("SELECT 1")
    finally:
        base._components_cache.clear()


class pytest_raises_closed_sqlite:
    def __enter__(self):
        return None

    def __exit__(self, exc_type, exc, tb):
        if exc_type is None:
            raise AssertionError("expected sqlite connection to be closed")
        return exc_type is sqlite3.ProgrammingError

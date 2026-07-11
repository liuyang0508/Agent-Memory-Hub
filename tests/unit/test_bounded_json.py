"""Aggregate budgets for descriptor-anchored JSON sidecar reads."""

from __future__ import annotations

from pathlib import Path


def test_bounded_json_directory_counts_invalid_bytes_and_marks_total_budget_exhaustion(
    tmp_path: Path,
    monkeypatch,
) -> None:
    import agent_brain.platform.bounded_json as bounded_json

    sidecars = tmp_path / "sidecars"
    sidecars.mkdir()
    malformed = b'{"bad":'
    valid = b'{"ok":1}'
    (sidecars / "a-malformed.json").write_bytes(malformed)
    (sidecars / "b-valid.json").write_bytes(valid)
    (sidecars / "c-over-budget.json").write_bytes(b"{}")
    monkeypatch.setattr(
        bounded_json,
        "MAX_JSON_TOTAL_BYTES",
        len(malformed) + len(valid),
        raising=False,
    )

    with bounded_json.open_bounded_json_directory(sidecars) as reader:
        assert reader is not None
        assert reader.read_object("a-malformed.json") is None
        assert reader.read_object("b-valid.json") == {"ok": 1}
        assert reader.budget_exhausted is False
        assert reader.read_object("c-over-budget.json") is None
        assert reader.budget_exhausted is True
        assert reader.read_object("b-valid.json") is None

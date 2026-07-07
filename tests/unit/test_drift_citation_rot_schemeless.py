"""P2-9 regression: scheme-less www. citations must not abort the drift scan."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import urllib.request

import pytest

from agent_brain.memory.governance.drift import DriftDetector, DriftType
from agent_brain.contracts.memory_item import MemoryItem, MemoryType, Refs


class MockItemsStore:
    def __init__(self, items: list[tuple[MemoryItem, str]]):
        self._items = items

    def iter_all(self) -> Any:
        return iter(self._items)


def _make_item(item_id: str, body_note: str = "") -> MemoryItem:
    return MemoryItem(
        id=item_id,
        type=MemoryType.decision,
        created_at=datetime.now(timezone.utc),
        project="test-project",
        title="Has a citation",
        summary="summary",
        tags=[],
        refs=Refs(),
    )


class _FakeResp:
    status = 200

    def __enter__(self) -> "_FakeResp":
        return self

    def __exit__(self, *exc: Any) -> bool:
        return False


def _fake_urlopen(req: Any, timeout: Any = None) -> _FakeResp:
    """Mimic stdlib urllib: scheme-less URLs raise ValueError, others 200."""
    url = req.full_url
    if "://" not in url:
        raise ValueError(f"unknown url type: {url!r}")
    return _FakeResp()


def test_scheme_less_www_url_does_not_abort_scan(monkeypatch: pytest.MonkeyPatch) -> None:
    # Body contains a bare www. citation with no scheme.
    item = _make_item("mem-20250101-120000-citation")
    body = "Reference: www.example.com/page for details."
    store = MockItemsStore([(item, body)])

    monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen)

    detector = DriftDetector(store, check_urls=True, url_timeout=0.1)

    # Before the fix: urlopen on 'www.example.com/page' raises ValueError,
    # which is uncaught and aborts detect(). After the fix: the URL is
    # normalized to https:// -> 200 OK -> no citation-rot finding.
    report = detector.detect()

    assert all(
        f.drift_type != DriftType.CITATION_ROT for f in report.findings
    ), "normalized www. URL returned 200 and must not be flagged as rot"

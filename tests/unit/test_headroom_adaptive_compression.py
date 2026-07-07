from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from agent_brain.contracts.memory_item import MemoryItem, MemoryType


def test_adaptive_compressor_routes_and_groups_search_results() -> None:
    from agent_brain.memory.context.adaptive_compression import compress_text

    content = "\n".join(
        [
            "src/app.py:10:def unrelated(): pass",
            "src/app.py:20:def target_handler(): return True",
            "src/app.py:30:target_handler()",
            "src/app.py:40:print('noise')",
            "tests/test_app.py:5:def test_target_handler(): pass",
            "tests/test_app.py:20:assert target_handler() is True",
            "README.md:8:general target documentation",
        ]
    )

    result = compress_text(
        content,
        budget_chars=180,
        detail_uri="memory://items/mem-search/body",
        query="target handler",
    )

    assert result.content_type == "search_results"
    assert result.strategy == "search_topn"
    assert result.reversible is True
    assert result.compressed_chars < result.original_chars
    assert "src/app.py" in result.text
    assert "tests/test_app.py" in result.text
    assert "target_handler" in result.text
    assert "omitted" in result.text
    assert result.metrics["tokens_saved"] > 0


def test_adaptive_compressor_keeps_log_failures_tracebacks_and_summary() -> None:
    from agent_brain.memory.context.adaptive_compression import compress_text

    content = "\n".join(
        [
            *(f"INFO task {i} completed" for i in range(40)),
            "ERROR failed to connect to database",
            "Traceback (most recent call last):",
            '  File "app.py", line 12, in main',
            "ConnectionError: refused",
            "=== short test summary info ===",
            "FAILED tests/test_db.py::test_connect - ConnectionError",
            *(f"DEBUG retry noise {i}" for i in range(40)),
        ]
    )

    result = compress_text(content, budget_chars=260, detail_uri="memory://items/mem-log/body")

    assert result.content_type == "build_log"
    assert result.strategy == "log_errors"
    assert result.reversible is True
    assert "ERROR failed to connect" in result.text
    assert "Traceback" in result.text
    assert "FAILED tests/test_db.py" in result.text
    assert "DEBUG retry noise 39" not in result.text
    assert result.compression_ratio < 0.6


def test_context_pack_uses_content_router_for_detail_body() -> None:
    from agent_brain.memory.context.context_packing import build_context_pack

    item = MemoryItem(
        id="mem-20260621-150000-context-search",
        type=MemoryType.episode,
        created_at=datetime(2026, 6, 21, tzinfo=timezone.utc),
        title="Search output evidence",
        summary="Search output evidence summary",
        refs={"files": ["src/app.py"]},
    )
    body = "\n".join(f"src/app.py:{line}:target handler noise {line}" for line in range(1, 80))

    pack = build_context_pack(item, body, requested="detail", budget_tokens=60)

    assert pack.selected_view == "detail"
    assert pack.compression_strategy == "search_topn"
    assert pack.compression_content_type == "search_results"
    assert pack.text != body
    assert pack.packed_tokens <= 60
    assert pack.reversible is True
    assert pack.detail_uri == "memory://items/mem-20260621-150000-context-search/body"


def test_headroom_bridge_persists_ccr_sidecar_when_no_detail_uri(tmp_path: Path, monkeypatch) -> None:
    from agent_brain.platform.headroom_integration import compress_with_headroom, retrieve_compressed_original

    monkeypatch.setenv("MEMORY_HUB_HEADROOM_EXTERNAL", "0")
    original = "\n".join(f"logs/app.log:{i}:ERROR noisy failure {i}" for i in range(40))

    result = compress_with_headroom(original, budget_chars=220, brain_dir=tmp_path, query="failure")
    recovered = retrieve_compressed_original(result.ccr_key, brain_dir=tmp_path)

    assert result.provider == "amh-local"
    assert result.reversible is True
    assert result.ccr_key
    assert result.ccr_marker in result.text
    assert recovered == original

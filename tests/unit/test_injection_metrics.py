"""Contracts for neutral, Web-safe injection telemetry sanitization."""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_injection_metric_sanitizer_binds_ordered_trace_before_filtering() -> None:
    from agent_brain.memory.context.injection_metrics import sanitize_pack_metrics

    first = "mem-20260711-010101-first"
    dirty = "mem-20260711-010102-dirty\nSECRET"
    second = "mem-20260711-010103-second"
    payload = {
        "candidate_count": 2,
        "included_count": 2,
        "excluded_count": 0,
        "selected_views": {"overview": 2},
        "retrieval_trace": [
            {"final_rank": 1},
            {"final_rank": 999},
            {"final_rank": 3},
        ],
    }

    sanitized = sanitize_pack_metrics(
        payload,
        cohort_item_ids=(first, dirty, second),
        allowed_item_ids=(first, second),
    )

    assert sanitized["included_count"] == 2
    assert sanitized["retrieval_trace"] == [
        {"final_rank": 1},
        {"final_rank": 3},
    ]

    short = sanitize_pack_metrics(
        {**payload, "retrieval_trace": payload["retrieval_trace"][:2]},
        cohort_item_ids=(first, dirty, second),
        allowed_item_ids=(first, second),
    )
    assert "retrieval_trace" not in short


def test_injection_metric_numbers_stop_at_javascript_safe_integer() -> None:
    from agent_brain.memory.context.injection_metrics import (
        MAX_SAFE_INTEGER,
        sanitize_pack_metrics,
    )

    item_id = "mem-20260711-010104-safe"
    safe = sanitize_pack_metrics(
        {
            "candidate_count": 1,
            "included_count": 1,
            "excluded_count": 0,
            "packed_tokens": MAX_SAFE_INTEGER,
            "retrieval_trace": [
                {
                    "final_rank": MAX_SAFE_INTEGER,
                    "final_score": float(MAX_SAFE_INTEGER),
                }
            ],
        },
        cohort_item_ids=(item_id,),
        allowed_item_ids=(item_id,),
    )
    assert safe["packed_tokens"] == MAX_SAFE_INTEGER
    assert safe["retrieval_trace"][0]["final_rank"] == MAX_SAFE_INTEGER

    oversized = sanitize_pack_metrics(
        {
            "candidate_count": MAX_SAFE_INTEGER + 1,
            "included_count": 1,
            "excluded_count": MAX_SAFE_INTEGER,
            "packed_tokens": MAX_SAFE_INTEGER + 1,
            "retrieval_trace": [
                {
                    "final_rank": MAX_SAFE_INTEGER + 1,
                    "final_score": float(MAX_SAFE_INTEGER + 1),
                }
            ],
        },
        cohort_item_ids=(item_id,),
        allowed_item_ids=(item_id,),
    )
    assert oversized == {}


def test_data_flow_and_chain_log_depend_on_neutral_metrics_layer() -> None:
    data_flow_path = ROOT / "agent_brain" / "observability" / "data_flow.py"
    chain_log_path = ROOT / "agent_brain" / "product" / "chain_log.py"
    data_flow = data_flow_path.read_text(encoding="utf-8")
    chain_log = chain_log_path.read_text(encoding="utf-8")
    tree = ast.parse(data_flow)
    imported_modules = {
        node.module or ""
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    }

    assert not any(module.startswith("agent_brain.product") for module in imported_modules)
    neutral_import = "agent_brain.memory.context.injection_metrics"
    assert neutral_import in data_flow
    assert neutral_import in chain_log

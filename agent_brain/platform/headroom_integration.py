"""Optional Headroom bridge with AMH-local compression.

Headroom is useful as a context compression layer, but AMH must remain usable
without the external package or proxy. This module detects and uses Headroom
opportunistically; otherwise it uses AMH's own content router and CCR sidecar
to emit a reversible compact pack.
"""

from __future__ import annotations

import importlib
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agent_brain.memory.context.adaptive_compression import (
    compress_text,
    retrieve_compressed_original as _retrieve_compressed_original,
)


@dataclass(frozen=True)
class HeadroomStatus:
    available: bool
    provider: str
    reason: str
    cli_path: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "available": self.available,
            "provider": self.provider,
            "reason": self.reason,
            "cli_path": self.cli_path,
        }


@dataclass(frozen=True)
class HeadroomCompressionResult:
    text: str
    provider: str
    reversible: bool
    detail_uri: str | None
    retrieve_hint: str
    original_chars: int
    compressed_chars: int
    content_type: str = "plain_text"
    strategy: str = "unknown"
    compression_ratio: float = 1.0
    tokens_saved: int = 0
    ccr_key: str | None = None
    ccr_marker: str | None = None
    metrics: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        return {
            "text": self.text,
            "provider": self.provider,
            "reversible": self.reversible,
            "detail_uri": self.detail_uri,
            "retrieve_hint": self.retrieve_hint,
            "original_chars": self.original_chars,
            "compressed_chars": self.compressed_chars,
            "content_type": self.content_type,
            "strategy": self.strategy,
            "compression_ratio": self.compression_ratio,
            "tokens_saved": self.tokens_saved,
            "ccr_key": self.ccr_key,
            "ccr_marker": self.ccr_marker,
            "metrics": dict(self.metrics),
        }


def headroom_status() -> HeadroomStatus:
    if os.environ.get("MEMORY_HUB_HEADROOM_EXTERNAL") == "0":
        return HeadroomStatus(False, "amh-local", "disabled by MEMORY_HUB_HEADROOM_EXTERNAL=0")
    try:
        importlib.import_module("headroom")
        return HeadroomStatus(True, "python-package", "headroom python package importable")
    except Exception:
        cli = shutil.which("headroom")
        if cli:
            return HeadroomStatus(True, "cli", "headroom CLI found", cli_path=cli)
    return HeadroomStatus(False, "amh-local", "headroom package/cli not available")


def compress_with_headroom(
    text: str,
    *,
    budget_chars: int = 1200,
    detail_uri: str | None = None,
    query: str | None = None,
    brain_dir: Path | None = None,
) -> HeadroomCompressionResult:
    """Compress text through Headroom when available, else AMH-local routing."""

    status = headroom_status()
    if status.available and status.provider == "python-package":
        result = _try_python_package(text, budget_chars=budget_chars, detail_uri=detail_uri, query=query)
        if result is not None:
            return result
    if status.available and status.provider == "cli" and status.cli_path:
        result = _try_cli(status.cli_path, text, budget_chars=budget_chars, detail_uri=detail_uri)
        if result is not None:
            return result
    return _local_adaptive_pack(
        text,
        budget_chars=budget_chars,
        detail_uri=detail_uri,
        query=query,
        brain_dir=brain_dir,
    )


def _try_python_package(
    text: str,
    *,
    budget_chars: int,
    detail_uri: str | None,
    query: str | None,
) -> HeadroomCompressionResult | None:
    try:
        module = importlib.import_module("headroom")
        compress = getattr(module, "compress", None)
        if compress is None:
            return None
        compressed = compress(text, query=query) if query is not None else compress(text)
        if not isinstance(compressed, str):
            compressed = str(compressed)
        return _result(
            compressed[: max(budget_chars, 1)],
            provider="headroom-python",
            original=text,
            detail_uri=detail_uri,
        )
    except Exception:
        return None


def _try_cli(
    cli_path: str,
    text: str,
    *,
    budget_chars: int,
    detail_uri: str | None,
) -> HeadroomCompressionResult | None:
    try:
        completed = subprocess.run(
            [cli_path, "compress"],
            input=text,
            text=True,
            capture_output=True,
            check=False,
            timeout=5,
        )
    except Exception:
        return None
    if completed.returncode != 0 or not completed.stdout:
        return None
    return _result(
        completed.stdout[: max(budget_chars, 1)],
        provider="headroom-cli",
        original=text,
        detail_uri=detail_uri,
    )


def _local_adaptive_pack(
    text: str,
    *,
    budget_chars: int,
    detail_uri: str | None,
    query: str | None,
    brain_dir: Path | None,
) -> HeadroomCompressionResult:
    compressed = compress_text(
        text,
        budget_chars=budget_chars,
        detail_uri=detail_uri,
        query=query,
        brain_dir=brain_dir,
    )
    result_text = compressed.text
    if detail_uri and detail_uri not in result_text:
        result_text = _append_detail_uri(result_text, detail_uri, budget_chars)
    return HeadroomCompressionResult(
        text=result_text,
        provider="amh-local",
        reversible=compressed.reversible,
        detail_uri=detail_uri,
        retrieve_hint=_retrieve_hint(detail_uri=detail_uri, ccr_key=compressed.ccr_key),
        original_chars=len(text),
        compressed_chars=len(result_text),
        content_type=compressed.content_type,
        strategy=compressed.strategy,
        compression_ratio=round(len(result_text) / max(1, len(text)), 6),
        tokens_saved=compressed.tokens_saved,
        ccr_key=compressed.ccr_key,
        ccr_marker=compressed.ccr_marker,
        metrics=compressed.metrics,
    )


def retrieve_compressed_original(key: str | None, *, brain_dir: Path) -> str | None:
    """Retrieve an AMH-local CCR sidecar payload by key."""

    return _retrieve_compressed_original(brain_dir, key)


def _result(
    text: str,
    *,
    provider: str,
    original: str,
    detail_uri: str | None,
) -> HeadroomCompressionResult:
    hint = _retrieve_hint(detail_uri=detail_uri, ccr_key=None)
    return HeadroomCompressionResult(
        text=text,
        provider=provider,
        reversible=bool(detail_uri),
        detail_uri=detail_uri,
        retrieve_hint=hint,
        original_chars=len(original),
        compressed_chars=len(text),
        content_type="external",
        strategy=provider,
        compression_ratio=round(len(text) / max(1, len(original)), 6),
    )


def _retrieve_hint(*, detail_uri: str | None, ccr_key: str | None) -> str:
    if detail_uri:
        return f"retrieve detail from {detail_uri}"
    if ccr_key:
        return f"memory headroom retrieve {ccr_key}"
    return "original text not persisted"


def _append_detail_uri(text: str, detail_uri: str, budget_chars: int) -> str:
    suffix = f"\n\n[detail] {detail_uri}"
    if len(text) + len(suffix) <= budget_chars:
        return f"{text}{suffix}".strip()
    head_budget = max(0, budget_chars - len(suffix))
    if head_budget <= 3:
        return suffix.strip()[:budget_chars]
    return f"{text[: head_budget - 3].rstrip()}...{suffix}".strip()


__all__ = [
    "HeadroomCompressionResult",
    "HeadroomStatus",
    "compress_with_headroom",
    "headroom_status",
    "retrieve_compressed_original",
]

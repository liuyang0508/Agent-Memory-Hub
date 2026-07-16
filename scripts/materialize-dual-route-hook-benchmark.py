#!/usr/bin/env python3
"""Build the public synthetic brain used by the real-hook latency benchmark."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from typing import Sequence, TextIO

from agent_brain.contracts.memory_item import MemoryItem, MemoryType
from agent_brain.memory.store.items_store import ItemsStore
from agent_brain.platform.embedding import HashingEmbedder
from agent_brain.platform.indexing.index import HubIndex


FIXTURE_ID = "dual-route-hook-public-v1"
PAYLOAD_PATH = "tests/fixtures/dual_route_hook_benchmark_payload.json"
ITEM_ID = "mem-20260717-000000-public-dual-route-hook-benchmark"
SENTINEL = "PUBLIC DUAL ROUTE BENCHMARK SENTINEL"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--brain-dir", type=Path, required=True)
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    stdout: TextIO = sys.stdout,
) -> int:
    args = _parser().parse_args(argv)
    brain_dir = args.brain_dir.expanduser()
    if brain_dir.exists() or brain_dir.is_symlink():
        raise SystemExit("brain dir must not already exist")
    brain_dir.mkdir(parents=True, mode=0o700)

    item = MemoryItem(
        id=ITEM_ID,
        type=MemoryType.fact,
        created_at=datetime(2026, 7, 17, tzinfo=timezone.utc),
        title="Public dual-route hook benchmark fixture",
        summary=f"{SENTINEL} validates real adapter hook recall latency.",
        project="agent-memory-hub",
        tags=["public-fixture", "dual-route", "hook-benchmark"],
        sensitivity="public",
        confidence=0.9,
        refs={"urls": ["https://example.test/dual-route-hook-benchmark"]},
    )
    body = (
        "**事实**\n"
        f"{SENTINEL} validates real adapter hook recall latency and protocol behavior.\n\n"
        "**来源**\nPublic synthetic release-readiness fixture.\n\n"
        "**有效期**\nOnly for the reproducible dual-route hook benchmark.\n"
    )
    ItemsStore(brain_dir / "items").write(item, body)
    embedder = HashingEmbedder()
    index = HubIndex(brain_dir / "index.db", embedding_dim=embedder.dim)
    try:
        index.upsert(item, body, embedding=embedder.embed(body))
    finally:
        index.close()

    json.dump(
        {"fixture_id": FIXTURE_ID, "item_count": 1, "payload": PAYLOAD_PATH},
        stdout,
        sort_keys=True,
        separators=(",", ":"),
    )
    stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

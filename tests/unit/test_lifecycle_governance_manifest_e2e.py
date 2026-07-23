from __future__ import annotations

import json
import subprocess
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from agent_brain.contracts.memory_item import MemoryItem, MemoryType
from agent_brain.memory.governance.lifecycle_review import (
    LifecycleReviewAction,
    apply_lifecycle_review_actions,
)
from agent_brain.memory.store.items_store import ItemsStore
from agent_brain.platform.indexing.index import HubIndex
from agent_brain.product.governance_readiness import (
    build_memory_lifecycle_readiness,
)


def _tree_snapshot(root: Path) -> dict[str, tuple[str, bytes | None]]:
    return {
        path.relative_to(root).as_posix(): (
            "dir" if path.is_dir() else "file",
            None if path.is_dir() else path.read_bytes(),
        )
        for path in sorted(root.rglob("*"))
    }


def _manifest_item(item_id: str, *, item_type: MemoryType) -> MemoryItem:
    return MemoryItem(
        id=item_id,
        type=item_type,
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        project="lifecycle-manifest-smoke",
        title=item_id,
        summary=f"{item_id} summary",
        tags=["lifecycle", "manifest"],
    )


def test_lifecycle_238_plus_80_manifest_preview_apply_and_reopen(
    tmp_path: Path,
) -> None:
    brain = tmp_path / "brain"
    store = ItemsStore(brain / "items")
    mechanical_actions: list[LifecycleReviewAction] = []
    substantive_actions: list[LifecycleReviewAction] = []
    archived_bytes: dict[str, bytes] = {}
    supersede_pairs: list[tuple[str, str]] = []
    keep_ids: list[str] = []
    index = HubIndex(brain / "index.db")
    try:
        for offset in range(238):
            item = _manifest_item(
                f"mem-20260101-{120000 + offset:06d}-mechanical-{offset:03d}",
                item_type=MemoryType.signal,
            )
            path = store.write(item, f"mechanical body {offset}")
            archived_bytes[item.id] = path.read_bytes()
            index.upsert(item, f"mechanical body {offset}", embedding=None)
            mechanical_actions.append(LifecycleReviewAction("archive", item.id))

        for offset in range(20):
            item = _manifest_item(
                f"mem-20260102-{130000 + offset:06d}-business-archive-{offset:03d}",
                item_type=MemoryType.signal,
            )
            path = store.write(item, f"business archive body {offset}")
            archived_bytes[item.id] = path.read_bytes()
            index.upsert(item, f"business archive body {offset}", embedding=None)
            substantive_actions.append(LifecycleReviewAction("archive", item.id))

        for offset in range(20):
            obsolete = _manifest_item(
                f"mem-20260102-{140000 + offset:06d}-business-old-{offset:03d}",
                item_type=MemoryType.signal,
            )
            replacement = _manifest_item(
                f"mem-20260723-{150000 + offset:06d}-business-new-{offset:03d}",
                item_type=MemoryType.fact,
            )
            store.write(obsolete, f"obsolete body {offset}")
            store.write(replacement, f"replacement body {offset}")
            index.upsert(obsolete, f"obsolete body {offset}", embedding=None)
            index.upsert(replacement, f"replacement body {offset}", embedding=None)
            supersede_pairs.append((obsolete.id, replacement.id))
            substantive_actions.append(
                LifecycleReviewAction(
                    "supersede",
                    obsolete.id,
                    replacement.id,
                )
            )

        for offset in range(40):
            item = _manifest_item(
                f"mem-20260102-{160000 + offset:06d}-business-keep-{offset:03d}",
                item_type=MemoryType.signal,
            )
            store.write(item, f"keep body {offset}")
            index.upsert(item, f"keep body {offset}", embedding=None)
            keep_ids.append(item.id)
            substantive_actions.append(
                LifecycleReviewAction("keep-active", item.id)
            )
    finally:
        index.close()

    assert len(mechanical_actions) == 238
    assert len(substantive_actions) == 80
    assert Counter(action.action for action in substantive_actions) == {
        "archive": 20,
        "supersede": 20,
        "keep-active": 40,
    }
    assert len({action.item_id for action in substantive_actions}) == 80
    actions = [*mechanical_actions, *substantive_actions]

    before = _tree_snapshot(brain)
    preview = apply_lifecycle_review_actions(
        brain_dir=brain,
        items_store=store,
        actions=actions,
        apply=False,
        index_repair=True,
    )

    assert len(preview["results"]) == 318
    assert {row["status"] for row in preview["results"]} == {"ready"}
    assert _tree_snapshot(brain) == before

    applied = apply_lifecycle_review_actions(
        brain_dir=brain,
        items_store=store,
        actions=actions,
        apply=True,
        index_repair=True,
    )

    assert len(applied["results"]) == 318
    assert {row["status"] for row in applied["results"]} == {"applied"}
    assert not any(row["index_repair_required"] for row in applied["results"])
    for item_id, original in archived_bytes.items():
        assert not (store.items_dir / f"{item_id}.md").exists()
        assert (
            store.items_dir / "archived" / f"{item_id}.md"
        ).read_bytes() == original
    for obsolete_id, replacement_id in supersede_pairs:
        obsolete, _ = store.get(obsolete_id)
        replacement, _ = store.get(replacement_id)
        assert obsolete.superseded_by == replacement_id
        assert obsolete_id in replacement.refs.mems
    for item_id in keep_ids:
        kept, _ = store.get(item_id)
        assert kept.superseded_by is None
        assert kept.validity.observed_at is not None
    assert len(list(store.iter_all(include_archived=True))) == 338

    reopened_store = ItemsStore(brain / "items")
    reopened_ids = {item.id for item, _body in reopened_store.iter_all()}
    reopened_index = HubIndex(brain / "index.db")
    try:
        assert reopened_index.all_ids() == reopened_ids
        assert reopened_index.get_superseded_ids() == {
            obsolete_id for obsolete_id, _replacement_id in supersede_pairs
        }
    finally:
        reopened_index.close()

    ledger_rows = [
        json.loads(line)
        for line in (
            brain / "runtime" / "lifecycle-actions.jsonl"
        ).read_text(encoding="utf-8").splitlines()
    ]
    assert Counter(row["action"] for row in ledger_rows) == {
        "supersede": 20,
        "keep-active": 40,
    }
    readiness = build_memory_lifecycle_readiness(brain)
    assert readiness.metrics["archived_count"] == 258
    assert readiness.metrics["superseded_count"] == 20
    assert readiness.metrics["active_count"] == 60
    assert readiness.metrics["review_queue_count"] == 0
    assert readiness.metrics["index_health_status"] == "clean"
    assert readiness.metrics["supersession_drift_count"] == 0
    assert readiness.metrics["lifecycle_ledger_unavailable"] is False

    script = (
        "import json; from pathlib import Path; "
        "from agent_brain.memory.store.items_store import ItemsStore; "
        "from agent_brain.product.governance_readiness import "
        "build_memory_lifecycle_readiness; "
        f"brain=Path({str(brain)!r}); "
        "lane=build_memory_lifecycle_readiness(brain); "
        "print(json.dumps({'active_ids':sorted(item.id for item,_ in "
        "ItemsStore(brain/'items').iter_all()),'metrics':lane.metrics},"
        "sort_keys=True))"
    )
    completed = subprocess.run(
        [sys.executable, "-c", script],
        text=True,
        capture_output=True,
        timeout=30,
        check=True,
    )
    reopened = json.loads(completed.stdout)
    assert reopened["active_ids"] == sorted(reopened_ids)
    assert reopened["metrics"] == readiness.metrics

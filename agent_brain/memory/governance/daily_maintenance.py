"""At-most-once-per-day safe local governance runner for lifecycle hooks."""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from agent_brain.memory.governance.auto_governance import AutoGovernanceCycle
from agent_brain.memory.governance.outcome_feedback import (
    apply_task_outcome_feedback_batch,
)
from agent_brain.memory.recall.adaptive_learning import refresh_learning_profile
from agent_brain.memory.store.items_store import ItemsStore
from agent_brain.platform.indexing.index import HubIndex


def run_daily_maintenance(brain_dir: Path) -> dict[str, object]:
    brain = Path(brain_dir)
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    state_dir = brain / "runtime" / "daily-governance"
    state_dir.mkdir(parents=True, exist_ok=True)
    completed = state_dir / f"{day}.json"
    lock = state_dir / f"{day}.lock"
    if completed.exists():
        return {"status": "already_completed", "day": day}
    try:
        descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        return {"status": "already_running", "day": day}
    os.close(descriptor)

    index = None
    try:
        if (brain / "index.db").exists():
            index = HubIndex(brain / "index.db")
        items_store = ItemsStore(brain / "items")
        feedback = apply_task_outcome_feedback_batch(
            brain,
            items_store=items_store,
            index=index,
        )
        learning = refresh_learning_profile(brain)
        report = AutoGovernanceCycle(
            brain_dir=brain,
            items_store=items_store,
            index=index,
            include_index=False,
            include_evolve=True,
        ).run(apply=True)
        payload = {
            "status": "completed",
            "day": day,
            "applied_count": report.applied_count,
            "feedback_applied_count": feedback.applied_count,
            "learning": learning.to_dict(),
            "blocked_count": report.blocked_count,
            "review_required_count": report.review_required_count,
            "applied_actions": [
                {
                    "action": action.action,
                    "item_ids": action.item_ids,
                    "details": action.details,
                }
                for action in report.actions
                if action.applied
            ],
            "review_actions": [
                {
                    "action": action.action,
                    "item_ids": action.item_ids,
                    "details": action.details,
                }
                for action in report.actions
                if action.risk == "review_required"
            ],
        }
        completed.write_text(
            json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.chmod(completed, 0o600)
        return payload
    finally:
        if index is not None:
            index.close()
        try:
            lock.unlink()
        except FileNotFoundError:
            pass


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--brain-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    print(json.dumps(run_daily_maintenance(args.brain_dir), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["run_daily_maintenance"]

"""Local verification evidence for adapter capability promotion.

Static adapter evidence can say an integration is install-ready. A verified
claim needs a local, auditable record that doctor/runtime checks actually passed.
"""

from __future__ import annotations

import argparse
import json
from collections import deque
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Literal

from agent_brain.platform.bounded_jsonl import iter_bounded_jsonl


ADAPTER_VERIFICATIONS_RELATIVE_PATH = "runtime/adapter-verifications.jsonl"
VerificationStatus = Literal["passed", "failed"]


@dataclass(frozen=True)
class AdapterVerificationRecord:
    adapter: str
    status: VerificationStatus
    timestamp: str
    verifier: str
    evidence: list[str]
    note: str | None = None

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class AdapterVerificationSummary:
    verified: bool
    count: int
    last_record: dict[str, object] | None
    evidence: tuple[str, ...]


def adapter_verifications_path(brain_dir: Path) -> Path:
    return Path(brain_dir) / ADAPTER_VERIFICATIONS_RELATIVE_PATH


def record_adapter_verification(
    brain_dir: Path,
    *,
    adapter: str,
    status: VerificationStatus,
    verifier: str,
    evidence: list[str] | tuple[str, ...],
    note: str | None = None,
    now: datetime | None = None,
) -> AdapterVerificationRecord:
    record = AdapterVerificationRecord(
        adapter=adapter,
        status=status,
        timestamp=_timestamp(now),
        verifier=verifier,
        evidence=list(evidence),
        note=note,
    )
    path = adapter_verifications_path(brain_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record.to_dict(), ensure_ascii=False, sort_keys=True) + "\n")
    return record


def iter_adapter_verifications(
    brain_dir: Path,
    *,
    adapter: str | None = None,
    limit: int | None = None,
) -> Iterator[AdapterVerificationRecord]:
    path = adapter_verifications_path(brain_dir)
    records = _iter_parsed_adapter_verifications(path, adapter=adapter)
    if limit is None:
        return records
    if type(limit) is not int or limit <= 0:
        return iter(())
    return iter(deque(records, maxlen=limit))


def _iter_parsed_adapter_verifications(
    path: Path,
    *,
    adapter: str | None,
) -> Iterator[AdapterVerificationRecord]:
    for data in iter_bounded_jsonl(path):
        try:
            record = AdapterVerificationRecord(
                adapter=str(data["adapter"]),
                status=str(data["status"]),
                timestamp=str(data["timestamp"]),
                verifier=str(data.get("verifier") or "unknown"),
                evidence=[str(entry) for entry in data.get("evidence") or []],
                note=data.get("note"),
            )
        except (KeyError, TypeError, ValueError, OverflowError):
            continue
        if adapter and record.adapter != adapter:
            continue
        yield record


def adapter_verification_summary(brain_dir: Path, adapter: str) -> AdapterVerificationSummary:
    count = 0
    last = None
    for record in iter_adapter_verifications(brain_dir, adapter=adapter):
        count += 1
        last = record
    verified = bool(last and last.status == "passed")
    evidence = tuple(last.evidence) if last else ()
    return AdapterVerificationSummary(
        verified=verified,
        count=count,
        last_record=last.to_dict() if last else None,
        evidence=evidence,
    )


def _timestamp(now: datetime | None) -> str:
    value = now or datetime.now(timezone.utc)
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Record adapter verification evidence.")
    parser.add_argument("--brain-dir", type=Path, default=Path.home() / ".agent-memory-hub")
    parser.add_argument("--adapter", required=True)
    parser.add_argument("--status", choices=["passed", "failed"], required=True)
    parser.add_argument("--verifier", default="manual")
    parser.add_argument("--evidence", action="append", default=[])
    parser.add_argument("--note")
    args = parser.parse_args(argv)
    record = record_adapter_verification(
        args.brain_dir,
        adapter=args.adapter,
        status=args.status,
        verifier=args.verifier,
        evidence=args.evidence,
        note=args.note,
    )
    print(json.dumps(record.to_dict(), ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "ADAPTER_VERIFICATIONS_RELATIVE_PATH",
    "AdapterVerificationRecord",
    "AdapterVerificationSummary",
    "adapter_verification_summary",
    "adapter_verifications_path",
    "iter_adapter_verifications",
    "record_adapter_verification",
]

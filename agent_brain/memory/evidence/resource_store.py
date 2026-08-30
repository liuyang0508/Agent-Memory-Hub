from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

from agent_brain.contracts.resource import (
    ExtractionRecord,
    ResourceRecord,
    validate_extraction_id,
    validate_resource_id,
)

_log = logging.getLogger(__name__)


@dataclass
class EvidenceSkipRecord:
    path: Path
    reason: str


@dataclass
class EvidenceScanStats:
    skipped: list[EvidenceSkipRecord] = field(default_factory=list)

    @property
    def skipped_count(self) -> int:
        return len(self.skipped)


class ResourceStore:
    """Local JSON registry for resources and extraction evidence."""

    def __init__(self, root_dir: Path) -> None:
        self.root_dir = Path(root_dir)
        self.resources_dir = self.root_dir / "resources"
        self.extractions_dir = self.root_dir / "extractions"
        self.resources_dir.mkdir(parents=True, exist_ok=True)
        self.extractions_dir.mkdir(parents=True, exist_ok=True)
        self.last_scan = EvidenceScanStats()

    def write_resource(self, record: ResourceRecord) -> Path:
        path = self.resources_dir / f"{record.id}.json"
        if path.exists():
            raise FileExistsError(f"Resource {record.id} already exists at {path}")
        self._write_json(path, record.model_dump(mode="json", exclude_none=False))
        return path

    def write_extraction(self, record: ExtractionRecord) -> Path:
        path = self.extractions_dir / f"{record.id}.json"
        if path.exists():
            raise FileExistsError(f"Extraction {record.id} already exists at {path}")
        resource_path = self.resources_dir / f"{record.resource_id}.json"
        if not resource_path.exists():
            raise FileNotFoundError(f"Resource {record.resource_id} not found")
        self._write_json(path, record.model_dump(mode="json", exclude_none=False))
        return path

    def get_resource(self, resource_id: str) -> ResourceRecord:
        validate_resource_id(resource_id)
        path = self.resources_dir / f"{resource_id}.json"
        if not path.exists():
            raise FileNotFoundError(f"Resource {resource_id} not found")
        return ResourceRecord.model_validate(self._read_json(path))

    def get_extraction(self, extraction_id: str) -> ExtractionRecord:
        validate_extraction_id(extraction_id)
        path = self.extractions_dir / f"{extraction_id}.json"
        if not path.exists():
            raise FileNotFoundError(f"Extraction {extraction_id} not found")
        return ExtractionRecord.model_validate(self._read_json(path))

    def iter_resources(self) -> Iterator[ResourceRecord]:
        self.last_scan = EvidenceScanStats()
        for path in sorted(self.resources_dir.glob("*.json")):
            try:
                record = ResourceRecord.model_validate(self._read_json(path))
            except Exception as error:  # noqa: BLE001 - isolate corrupt records.
                self._record_skip(path, error)
                continue
            yield record

    def iter_extractions(self, resource_id: str | None = None) -> Iterator[ExtractionRecord]:
        self.last_scan = EvidenceScanStats()
        for path in sorted(self.extractions_dir.glob("*.json")):
            try:
                record = ExtractionRecord.model_validate(self._read_json(path))
            except Exception as error:  # noqa: BLE001 - isolate corrupt records.
                self._record_skip(path, error)
                continue
            if resource_id is None or record.resource_id == resource_id:
                yield record

    def _record_skip(self, path: Path, error: BaseException) -> None:
        reason = f"{type(error).__name__}: {error}".splitlines()[0][:200]
        self.last_scan.skipped.append(EvidenceSkipRecord(path=path, reason=reason))
        _log.debug("skip evidence %s: %s", path.name, reason)

    @staticmethod
    def _write_json(path: Path, data: dict[str, Any]) -> None:
        path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    @staticmethod
    def _read_json(path: Path) -> dict[str, Any]:
        loaded: object = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(loaded, dict):
            raise ValueError(f"Evidence record must be a JSON object: {path}")
        return cast(dict[str, Any], loaded)


__all__ = ["ResourceStore"]

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

from agent_brain.contracts.resource import ExtractionRecord, ResourceRecord


class ResourceStore:
    """Local JSON registry for resources and extraction evidence."""

    def __init__(self, root_dir: Path) -> None:
        self.root_dir = Path(root_dir)
        self.resources_dir = self.root_dir / "resources"
        self.extractions_dir = self.root_dir / "extractions"
        self.resources_dir.mkdir(parents=True, exist_ok=True)
        self.extractions_dir.mkdir(parents=True, exist_ok=True)

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
        path = self.resources_dir / f"{resource_id}.json"
        if not path.exists():
            raise FileNotFoundError(f"Resource {resource_id} not found")
        return ResourceRecord.model_validate(self._read_json(path))

    def get_extraction(self, extraction_id: str) -> ExtractionRecord:
        path = self.extractions_dir / f"{extraction_id}.json"
        if not path.exists():
            raise FileNotFoundError(f"Extraction {extraction_id} not found")
        return ExtractionRecord.model_validate(self._read_json(path))

    def iter_resources(self) -> Iterator[ResourceRecord]:
        for path in sorted(self.resources_dir.glob("*.json")):
            yield ResourceRecord.model_validate(self._read_json(path))

    def iter_extractions(self, resource_id: str | None = None) -> Iterator[ExtractionRecord]:
        for path in sorted(self.extractions_dir.glob("*.json")):
            record = ExtractionRecord.model_validate(self._read_json(path))
            if resource_id is None or record.resource_id == resource_id:
                yield record

    @staticmethod
    def _write_json(path: Path, data: dict) -> None:
        path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    @staticmethod
    def _read_json(path: Path) -> dict:
        return json.loads(path.read_text(encoding="utf-8"))


__all__ = ["ResourceStore"]

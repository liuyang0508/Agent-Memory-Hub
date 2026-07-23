from __future__ import annotations

import json
import logging
import os
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
from agent_brain.memory.store.durable_fs import SecureDirectory
from agent_brain.platform.secure_io import (
    HardenedFallbackDirectory,
    close_descriptor,
    open_directory_path_without_symlinks,
    open_or_create_directory_path_without_symlinks,
    open_regular_file_at,
    secure_dir_fd_mutation_supported,
)

_log = logging.getLogger(__name__)
_STRICT_SECURE_MUTATION = os.name == "posix"


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
        self._secure = secure_dir_fd_mutation_supported()
        self._fallback_resources: HardenedFallbackDirectory | None = None
        self._fallback_extractions: HardenedFallbackDirectory | None = None
        if _STRICT_SECURE_MUTATION and not self._secure:
            raise OSError("SECURE_RESOURCE_STORE_UNAVAILABLE")
        if self._secure:
            with SecureDirectory(
                open_or_create_directory_path_without_symlinks(self.root_dir)
            ) as root:
                with root.child("resources", create=True), root.child(
                    "extractions", create=True
                ):
                    pass
        else:
            _log.warning("RESOURCE_STORE_SECURE_IO_UNAVAILABLE")
            fallback_root = HardenedFallbackDirectory.open_or_create(self.root_dir)
            self._fallback_resources = fallback_root.child("resources", create=True)
            self._fallback_extractions = fallback_root.child(
                "extractions",
                create=True,
            )
        self.last_scan = EvidenceScanStats()

    def write_resource(self, record: ResourceRecord) -> Path:
        path = self.resources_dir / f"{record.id}.json"
        data = self._json_bytes(record.model_dump(mode="json", exclude_none=False))
        if self._secure:
            with self._open_root() as root, root.child("resources") as resources:
                resources.atomic_create(path.name, data)
        else:
            self._fallback_directory("resources").exclusive_create(path.name, data)
        return path

    def write_extraction(self, record: ExtractionRecord) -> Path:
        path = self.extractions_dir / f"{record.id}.json"
        data = self._json_bytes(record.model_dump(mode="json", exclude_none=False))
        if self._secure:
            with self._open_root() as root, root.child("resources") as resources:
                descriptor = open_regular_file_at(
                    resources.fd,
                    f"{record.resource_id}.json",
                )
                close_descriptor(descriptor)
                with root.child("extractions") as extractions:
                    extractions.atomic_create(path.name, data)
        else:
            self._fallback_directory("resources").read_regular(
                f"{record.resource_id}.json"
            )
            self._fallback_directory("extractions").exclusive_create(path.name, data)
        return path

    def get_resource(self, resource_id: str) -> ResourceRecord:
        validate_resource_id(resource_id)
        path = self.resources_dir / f"{resource_id}.json"
        return ResourceRecord.model_validate(self._read_json(path))

    def get_extraction(self, extraction_id: str) -> ExtractionRecord:
        validate_extraction_id(extraction_id)
        path = self.extractions_dir / f"{extraction_id}.json"
        return ExtractionRecord.model_validate(self._read_json(path))

    def iter_resources(self) -> Iterator[ResourceRecord]:
        self.last_scan = EvidenceScanStats()
        for path, data in self._iter_json("resources"):
            try:
                record = ResourceRecord.model_validate(data)
            except Exception as error:  # noqa: BLE001 - isolate corrupt records.
                self._record_skip(path, error)
                continue
            yield record

    def iter_extractions(self, resource_id: str | None = None) -> Iterator[ExtractionRecord]:
        self.last_scan = EvidenceScanStats()
        for path, data in self._iter_json("extractions"):
            try:
                record = ExtractionRecord.model_validate(data)
            except Exception as error:  # noqa: BLE001 - isolate corrupt records.
                self._record_skip(path, error)
                continue
            if resource_id is None or record.resource_id == resource_id:
                yield record

    def _record_skip(self, path: Path, error: BaseException) -> None:
        reason = f"{type(error).__name__}: {error}".splitlines()[0][:200]
        self.last_scan.skipped.append(EvidenceSkipRecord(path=path, reason=reason))
        _log.debug("skip evidence %s: %s", path.name, reason)

    def _open_root(self) -> SecureDirectory:
        return SecureDirectory(open_directory_path_without_symlinks(self.root_dir))

    @staticmethod
    def _json_bytes(data: dict[str, Any]) -> bytes:
        return (
            json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        ).encode("utf-8")

    def _read_json(self, path: Path) -> dict[str, Any]:
        if self._secure:
            with self._open_root() as root, root.child(path.parent.name) as directory:
                return self._read_json_at(directory.fd, path.name, path)
        return self._decode_json(
            self._fallback_directory(path.parent.name).read_regular(path.name),
            path,
        )

    def _iter_json(self, directory_name: str) -> Iterator[tuple[Path, dict[str, Any]]]:
        directory_path = self.root_dir / directory_name
        if not self._secure:
            fallback_directory = self._fallback_directory(directory_name)
            for name in fallback_directory.names(suffix=".json"):
                path = directory_path / name
                try:
                    yield path, self._read_json(path)
                except Exception as error:  # noqa: BLE001 - isolate corrupt records.
                    self._record_skip(path, error)
            return
        with self._open_root() as root, root.child(directory_name) as secure_directory:
            with os.scandir(secure_directory.fd) as entries:
                names = sorted(
                    entry.name
                    for entry in entries
                    if isinstance(entry.name, str) and entry.name.endswith(".json")
                )
            for name in names:
                path = directory_path / name
                try:
                    yield path, self._read_json_at(secure_directory.fd, name, path)
                except Exception as error:  # noqa: BLE001 - isolate corrupt records.
                    self._record_skip(path, error)

    def _fallback_directory(self, name: str) -> HardenedFallbackDirectory:
        directory = (
            self._fallback_resources
            if name == "resources"
            else self._fallback_extractions
        )
        if directory is None:
            raise OSError("RESOURCE_STORE_FALLBACK_UNAVAILABLE")
        return directory

    @classmethod
    def _read_json_at(
        cls,
        directory_descriptor: int,
        name: str,
        path: Path,
    ) -> dict[str, Any]:
        descriptor = open_regular_file_at(directory_descriptor, name)
        try:
            with os.fdopen(descriptor, "rb", buffering=0) as handle:
                descriptor = -1
                return cls._decode_json(handle.read(), path)
        finally:
            if descriptor >= 0:
                close_descriptor(descriptor)

    @staticmethod
    def _decode_json(raw: bytes, path: Path) -> dict[str, Any]:
        loaded: object = json.loads(raw.decode("utf-8"))
        if not isinstance(loaded, dict):
            raise ValueError(f"Evidence record must be a JSON object: {path}")
        return cast(dict[str, Any], loaded)


__all__ = ["ResourceStore"]

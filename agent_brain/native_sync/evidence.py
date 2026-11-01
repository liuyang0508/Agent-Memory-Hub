"""Content-free organization provenance for cloud-only clients.

Keep stable hashes and authenticated source identities so an exact copy can be
recognized without downloading/storing another member's memory text. Never use
this metadata as a substitute for content in a model prompt or native projection.
"""

from __future__ import annotations

from typing import Any

FIELDS = frozenset(
    {
        "asset_id",
        "tenant_id",
        "revision",
        "source_device_id",
        "source_store",
        "product",
        "kind",
        "document_id",
        "document_hash",
        "body_hash",
        "part_index",
        "part_count",
        "deleted",
        "producer_unknown",
        "producer_member_id",
        "uploader_member_id",
        "upstream",
        "verified",
    }
)


def metadata(record: dict[str, Any]) -> dict[str, Any]:
    """Allowlist fields: adding a new payload field cannot accidentally persist text."""
    return {key: value for key, value in record.items() if key in FIELDS}


def complete_versions(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Group metadata for a complete version; omit deleted, mixed and partial parts.

    Document hashes identify content, not authorship. Only the Hub's original
    producer fields supply identity; an identical file never proves a new author.
    """
    groups: dict[tuple[Any, ...], list[dict[str, Any]]] = {}
    for r in records:
        if r.get("deleted") or r.get("verified") is not True:
            continue
        key = (
            r["tenant_id"],
            r["source_device_id"],
            r["source_store"],
            r.get("document_id") or r["asset_id"],
            r.get("document_hash") or r["body_hash"],
        )
        groups.setdefault(key, []).append(r)
    result = []
    for group in groups.values():
        group.sort(key=lambda r: r.get("part_index", 0))
        first = group[0]
        count = first.get("part_count", 1)
        if len(group) != count or any(r.get("part_count", 1) != count for r in group):
            continue
        if [r.get("part_index", 0) for r in group] != list(range(count)):
            continue
        result.append(
            {
                **first,
                "body_hash": first.get("document_hash") or first["body_hash"],
                "assembled_revisions": [(r["asset_id"], r["revision"]) for r in group],
            }
        )
    return result

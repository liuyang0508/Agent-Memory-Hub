"""Pattern detection via MinHash LSH — zero-model, pure mechanical.

Scans L0 items, computes n-gram shingling fingerprints, clusters similar items
via Locality-Sensitive Hashing, and outputs PatternClusters for downstream
crystallization. No LLM calls, no network — offline by design.
"""
from __future__ import annotations

import hashlib
import re
import struct
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Iterable

from agent_brain.contracts.memory_item import AbstractionLayer, MemoryItem

CRYSTALLIZE_THRESHOLD = 3
_SHINGLE_SIZE = 3
_NUM_HASHES = 64
_BAND_SIZE = 4
_NUM_BANDS = _NUM_HASHES // _BAND_SIZE

_MAX_HASH = (1 << 32) - 1
_MERSENNE_PRIME = (1 << 61) - 1


@dataclass
class PatternCluster:
    fingerprint: str
    item_ids: list[str]
    support_count: int
    representative_text: str
    project: str | None
    tags: list[str] = field(default_factory=list)


def _tokenize(text: str) -> list[str]:
    return re.findall(r"[\w一-鿿]+", text.lower())


def _shingles(tokens: list[str], k: int = _SHINGLE_SIZE) -> set[str]:
    if len(tokens) < k:
        return {" ".join(tokens)} if tokens else {""}
    return {" ".join(tokens[i : i + k]) for i in range(len(tokens) - k + 1)}


def _hash32(s: str) -> int:
    return struct.unpack("<I", hashlib.md5(s.encode("utf-8")).digest()[:4])[0]


def _minhash_signature(shingle_set: set[str], num_hashes: int = _NUM_HASHES) -> list[int]:
    if not shingle_set:
        return [_MAX_HASH] * num_hashes
    hashes = [_hash32(s) for s in shingle_set]
    sig = []
    for i in range(num_hashes):
        a = (i * 1103515245 + 12345) & _MAX_HASH
        b = (i * 6364136223846793005 + 1) & _MAX_HASH
        min_val = _MAX_HASH
        for h in hashes:
            val = ((a * h + b) % _MERSENNE_PRIME) & _MAX_HASH
            if val < min_val:
                min_val = val
        sig.append(min_val)
    return sig


def _lsh_buckets(signature: list[int]) -> list[str]:
    buckets = []
    for band_idx in range(_NUM_BANDS):
        start = band_idx * _BAND_SIZE
        band = tuple(signature[start : start + _BAND_SIZE])
        bucket_key = f"b{band_idx}:{hashlib.md5(str(band).encode()).hexdigest()[:12]}"
        buckets.append(bucket_key)
    return buckets


def _item_text(item: MemoryItem) -> str:
    parts = [item.title, item.summary]
    if item.tags:
        parts.extend(item.tags)
    return " ".join(parts)


def detect_patterns(
    items: Iterable[tuple[MemoryItem, str]],
    *,
    threshold: int = CRYSTALLIZE_THRESHOLD,
    only_l0: bool = True,
) -> list[PatternCluster]:
    """Detect recurring patterns among items via MinHash LSH clustering.

    Returns clusters with support_count >= threshold, sorted by support descending.
    """
    candidates: list[tuple[MemoryItem, str]] = []
    for item, body in items:
        if only_l0 and item.abstraction != AbstractionLayer.L0:
            continue
        candidates.append((item, body))

    if not candidates:
        return []

    bucket_to_ids: dict[str, set[str]] = defaultdict(set)
    id_to_item: dict[str, tuple[MemoryItem, str]] = {}

    for item, body in candidates:
        id_to_item[item.id] = (item, body)
        text = _item_text(item)
        tokens = _tokenize(text)
        shingle_set = _shingles(tokens)
        sig = _minhash_signature(shingle_set)
        for bucket in _lsh_buckets(sig):
            bucket_to_ids[bucket].add(item.id)

    # Union-Find to merge items sharing any bucket
    parent: dict[str, str] = {}

    def find(x: str) -> str:
        while parent.get(x, x) != x:
            parent[x] = parent.get(parent[x], parent[x])
            x = parent[x]
        return x

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    for bucket, ids in bucket_to_ids.items():
        if len(ids) < 2:
            continue
        id_list = list(ids)
        root = id_list[0]
        for other in id_list[1:]:
            union(root, other)

    # Collect clusters
    clusters_map: dict[str, list[str]] = defaultdict(list)
    for item_id in id_to_item:
        clusters_map[find(item_id)].append(item_id)

    results: list[PatternCluster] = []
    for root_id, member_ids in clusters_map.items():
        if len(member_ids) < threshold:
            continue

        items_in_cluster = [id_to_item[mid] for mid in member_ids]
        projects = [it.project for it, _ in items_in_cluster if it.project]
        all_tags: set[str] = set()
        for it, _ in items_in_cluster:
            all_tags.update(it.tags)

        best = max(items_in_cluster, key=lambda p: p[0].confidence)
        fingerprint = hashlib.sha256(
            "|".join(sorted(member_ids)).encode()
        ).hexdigest()[:16]

        results.append(
            PatternCluster(
                fingerprint=fingerprint,
                item_ids=sorted(member_ids),
                support_count=len(member_ids),
                representative_text=f"{best[0].title}: {best[0].summary}",
                project=projects[0] if projects else None,
                tags=sorted(all_tags),
            )
        )

    results.sort(key=lambda c: -c.support_count)
    return results

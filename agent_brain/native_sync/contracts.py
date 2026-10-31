"""Stdlib-only extension contracts; importing a client never imports the Hub stack."""

from __future__ import annotations

from typing import Any, Protocol


class SyncTransport(Protocol):
    """Authenticated operations required by one device's synchronization engine.

    Implementations bind credentials at construction; caller-supplied tenant and
    producer fields must not override that identity. ``put`` must be idempotent
    across lost acknowledgements. ``changes`` returns an ordered, tenant-scoped
    page whose cursor is committed together with the replica by NativeClient.

    PermissionError means access was rejected (never retry with cached authority).
    OSError/TimeoutError means transient unavailability (preserve the queue and
    back off). ValueError means a record/protocol conflict (isolate that record).
    JSON payloads remain dictionaries here; server validation lives in models.py,
    so portable Hook runtimes do not acquire a Pydantic/FastAPI dependency.
    """

    def me(self) -> dict[str, Any]: ...

    def put(self, value: dict[str, Any]) -> dict[str, Any]: ...

    def changes(self, cursor: int) -> dict[str, Any]: ...

    def search(self, query: str, **kwargs: Any) -> list[dict[str, Any]]: ...

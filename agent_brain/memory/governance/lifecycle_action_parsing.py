"""Pure parsers for lifecycle CLI action arguments."""

from __future__ import annotations


def parse_escaped_id_pair(value: str) -> tuple[str, str] | None:
    r"""Parse ``OLD:NEW`` while allowing ``\:`` and ``\\`` inside IDs."""
    fields = [""]
    escaped = False
    for character in value:
        if escaped:
            if character not in {":", "\\"}:
                return None
            fields[-1] += character
            escaped = False
            continue
        if character == "\\":
            escaped = True
            continue
        if character == ":":
            if len(fields) == 2:
                return None
            fields.append("")
            continue
        fields[-1] += character
    if escaped or len(fields) != 2 or not all(fields):
        return None
    return fields[0], fields[1]


__all__ = ["parse_escaped_id_pair"]

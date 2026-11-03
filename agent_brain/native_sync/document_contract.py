"""Source metadata that must agree across all chunks of one document version.

Source owner/store/document/hash are additionally fixed by the authorized SQL
scope. Chunk ids/revisions/bodies and mutable display names may differ.
"""

DOCUMENT_FIELDS = (
    "product",
    "kind",
    "path",
    "project",
    "title",
    "producer_member_id",
    "producer_unknown",
    "upstream",
)

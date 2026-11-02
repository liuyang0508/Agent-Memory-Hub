"""Knowledge facets: declared metadata, nature tiers, trusted times and decay math."""

import math

import pytest

from agent_brain.native_sync import facets

CLAUDE_MEMORY = """---
name: linkcrux-workspace-layout
description: "Where code and notes go"
metadata:
  node_type: memory
  type: feedback
  originSessionId: 93f1160f
  modified: 2026-10-29T04:08:12.900Z
---

Body text with a [[user-profile-liuyang]] link and [[amh-project|alias]].

## Rules

```
[[not-a-link-inside-code]]
### not a heading
```

### Details
"""


def test_frontmatter_flattens_one_nested_level_without_overriding_top_level():
    block = facets.frontmatter(CLAUDE_MEMORY)
    assert block["name"] == "linkcrux-workspace-layout"
    assert block["description"] == "Where code and notes go"
    assert block["type"] == "feedback" and block["modified"] == "2026-10-29T04:08:12.900Z"
    assert facets.frontmatter("no front matter\n---\nx: y\n---\n") == {}
    assert facets.frontmatter("---\ntype: top\nmetadata:\n  type: nested\n---\n")["type"] == "top"


def test_wikilinks_and_sections_skip_fenced_code():
    assert facets.wikilinks(CLAUDE_MEMORY) == ["user-profile-liuyang", "amh-project"]
    assert facets.sections(CLAUDE_MEMORY) == ["Rules", "Details"]


def test_source_meta_is_bounded_and_round_trips_server_validation():
    meta = facets.source_meta(CLAUDE_MEMORY, mtime=1_700_000_000.5)
    assert meta["mtime"] == 1_700_000_000.5
    assert meta["declared"] == {
        "name": "linkcrux-workspace-layout",
        "description": "Where code and notes go",
        "type": "feedback",
        "modified": "2026-10-29T04:08:12.900Z",
    }
    assert facets.validate_source_meta(meta) == meta
    assert facets.validate_source_meta(None) == {}
    assert facets.source_meta("plain", mtime=None) == {}
    with pytest.raises(ValueError):
        facets.validate_source_meta({"declared": {"body": "smuggled text"}})
    with pytest.raises(ValueError):
        facets.validate_source_meta({"links": ["x" * 300]})
    with pytest.raises(ValueError):
        facets.validate_source_meta({"mtime": "yesterday"})
    with pytest.raises(ValueError):
        facets.validate_source_meta({"unknown": 1})


def test_parse_time_accepts_iso_date_epoch_and_rejects_noise():
    assert facets.parse_time("2026-10-29T04:08:12.900Z") == pytest.approx(1793246892.9)
    assert facets.parse_time("2026-10-29") == facets.parse_time("2026/10/29")
    assert facets.parse_time(1700000000) == 1700000000.0
    assert facets.parse_time("1700000000") == 1700000000.0
    assert facets.parse_time(True) is None
    assert facets.parse_time("soon") is None
    assert facets.parse_time(float("nan")) is None
    assert facets.filename_date("notes/2026-11-03-weekly.md") == facets.parse_time("2026-11-03")
    assert facets.filename_date("v2026-11-030.md") is None


def test_nature_prefers_declared_over_location_over_body_cues():
    declared = {"kind": "memory", "source_meta": {"declared": {"type": "user"}}}
    assert facets.nature(declared, "topic_memory") == {"value": "preference", "provenance": "declared"}
    status = {"kind": "memory", "source_meta": {"declared": {"status": "临时"}}}
    assert facets.nature(status, "topic_memory")["value"] == "project_state"
    assert facets.nature({"kind": "rule"}, "rule") == {"value": "rule", "provenance": "parsed"}
    assert facets.nature({"kind": "memory"}, "navigation")["value"] == "reference"
    assert facets.nature({"kind": "memory"}, "conversation_summary")["value"] == "episode"
    assert facets.nature({"kind": "transcript"}, "transcript")["value"] == "episode"
    inferred = facets.nature({"kind": "memory", "body": "灰度发布进行中，待验证"}, "topic_memory")
    assert inferred == {"value": "project_state", "provenance": "inferred"}
    assert facets.nature({"kind": "memory", "body": "We decided to keep SQLite"}, "topic_memory") == {
        "value": "decision",
        "provenance": "inferred",
    }
    assert facets.nature({"kind": "memory", "body": "plain fact"}, "topic_memory") == {
        "value": "unknown",
        "provenance": "unknown",
    }


def test_times_never_substitute_observation_for_content_time():
    record = {"path": "2026-01-10-plan.md", "observed_at": 1_800_000_000.0}
    result = facets.times(record)
    assert result["content_created"] == {"value": facets.parse_time("2026-01-10"), "source": "parsed"}
    assert result["content_modified"] is None
    assert result["observed"]["source"] == "server"
    assert facets.reference_time(result) == (facets.parse_time("2026-01-10"), "content_created")
    assert facets.reference_time(facets.times({"observed_at": 1.0})) == (None, "unknown")
    with_meta = facets.times(
        {"source_meta": {"mtime": 1_750_000_000.0, "declared": {"created": "2025-01-01"}}},
        {"confirmed_at": 1_760_000_000.0, "last_cited": 1_759_000_000.0},
    )
    assert with_meta["content_modified"] == {"value": 1_750_000_000.0, "source": "parsed"}
    assert facets.reference_time(with_meta) == (1_760_000_000.0, "confirmed")
    assert with_meta["last_cited"]["value"] == 1_759_000_000.0


def test_declared_filter_only_on_explicit_supersession_or_expiry():
    now = facets.parse_time("2026-11-03")
    assert facets.declared_filter({"source_meta": {}}, now=now) is None
    assert facets.declared_filter(
        {"source_meta": {"declared": {"superseded_by": "asset_new"}}}, now=now
    ) == {"reason": "superseded", "by": "asset_new"}
    expired = facets.declared_filter(
        {"source_meta": {"declared": {"expires": "2026-01-01"}}}, now=now
    )
    assert expired["reason"] == "expired" and expired["at"].startswith("2026-01-01")
    assert facets.declared_filter({"source_meta": {"declared": {"expires": "2099-01-01"}}}, now=now) is None


def test_decay_is_bounded_reranking_and_unknown_time_does_not_decay():
    now = facets.parse_time("2026-11-03")
    fresh = {"kind": "memory", "source_meta": {"declared": {"type": "feedback", "modified": "2026-11-01"}}}
    old = {"kind": "memory", "source_meta": {"declared": {"type": "project", "modified": "2026-01-01"}}}
    unknown = {"kind": "memory"}
    a, b, c = (facets.decay(r, "topic_memory", None, now=now) for r in (fresh, old, unknown))
    assert a["half_life_days"] == 365 and b["half_life_days"] == 21 and c["half_life_days"] == 60
    assert a["weight"] > b["weight"]
    assert b["coefficient"] == 0.01 and b["weight"] == pytest.approx(1 + 0.05 * math.tanh(math.log(0.01)), abs=1e-5)
    assert c == {**c, "coefficient": 0.5, "reason": "time_unknown_neutral", "age_days": None}
    assert c["weight"] == pytest.approx(1 + 0.05 * math.tanh(math.log(0.5)), abs=1e-5)
    assert a["weight"] > c["weight"] > b["weight"]  # dated fresh > undated > dated stale
    assert all(0.95 <= r["weight"] <= 1.05 for r in (a, b, c))
    boosted = facets.decay(fresh, "topic_memory", {"delivered": 80, "support": 10, "gain": 5}, now=now)
    assert boosted["multipliers"] == {"access": 1.35, "support": 1.18, "gain": 1.15, "contradiction": 1.0}
    assert boosted["coefficient"] == 1.35
    contradicted = facets.decay(fresh, "topic_memory", {"contradict": 3}, now=now)
    assert contradicted["multipliers"]["contradiction"] == pytest.approx(0.76)
    assert contradicted["weight"] < a["weight"]

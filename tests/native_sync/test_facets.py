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


def test_frontmatter_ignores_third_level_flow_lists_and_tolerates_bom():
    # Only two levels: a third-level key is neither top nor nested, and the
    # top level still wins over the second.
    deep = "---\nmetadata:\n  inner:\n    status: deep\n    type: deep\n  type: shallow\nstatus: top\n---\n"
    assert facets.frontmatter(deep) == {"type": "shallow", "status": "top"}
    # Lists are not scalars: a flow list is ignored, block list items are not keys.
    listed = "---\nsuperseded_by: [a, b]\nexpires:\n  - 2026-01-01\nname: ok\n---\n"
    assert facets.frontmatter(listed) == {"name": "ok"}
    assert facets.declared(listed) == {"name": "ok"}
    assert facets.frontmatter('---\nname: "[quoted]"\n---\n') == {"name": "[quoted]"}
    # A UTF-8 byte-order mark before the opening fence is tolerated.
    bom = "﻿" + CLAUDE_MEMORY
    assert facets.frontmatter(bom)["type"] == "feedback"
    assert facets.declared(bom) == facets.declared(CLAUDE_MEMORY)


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


def test_source_meta_mtime_uses_the_hub_bounds_and_empty_containers_vanish():
    # A bad clock (year 2103) or a zero mtime is dropped client-side, so the
    # upload is not refused for one hint; the Hub's parse_time has the same bound.
    assert "mtime" not in facets.source_meta("plain", mtime=4_200_000_000.0)
    assert "mtime" not in facets.source_meta("plain", mtime=0.0)
    assert facets.source_meta("plain", mtime=4_102_444_799.0)["mtime"] == 4_102_444_799.0
    with pytest.raises(ValueError):
        facets.validate_source_meta({"mtime": 4_200_000_000.0})
    # {"links": []} and {"declared": {}} carry nothing: same as no metadata,
    # so they cannot change the content hash or bump a revision.
    assert facets.validate_source_meta({"links": []}) == {}
    assert facets.validate_source_meta({"declared": {}, "sections": [], "links": []}) == {}
    assert facets.validate_source_meta({"links": ["x"], "sections": []}) == {"links": ["x"]}


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


def test_parse_time_pads_loose_dates_reads_milliseconds_and_survives_huge_ints():
    day = facets.parse_time("2026-11-03")
    assert facets.parse_time("2026-11-3") == day
    assert facets.parse_time("2026/11/3") == day
    assert facets.parse_time("2026/1/3") == facets.parse_time("2026-01-03")
    assert facets.parse_time("2026-11-3T04:08:12Z") == facets.parse_time("2026-11-03T04:08:12Z")
    assert facets.parse_time(1_700_000_000_123) == pytest.approx(1_700_000_000.123)
    assert facets.parse_time("1700000000123") == pytest.approx(1_700_000_000.123)
    assert facets.parse_time(1_700_000_000_123.0) == pytest.approx(1_700_000_000.123)
    assert facets.parse_time(99_999_999_999) is None  # 11 digits: neither seconds nor ms
    assert facets.parse_time(10**400) is None  # float() overflow, not a 500
    assert facets.parse_time(-(10**400)) is None
    assert facets.parse_time(4_102_444_800) is None
    # A pure date as a bound runs to the last second of that day (UTC).
    assert facets.parse_time("2026-11-03", end_of_day=True) == day + 86399
    assert facets.parse_time("2026-11-3", end_of_day=True) == day + 86399
    assert facets.parse_time("2026-11-03T00:00:00", end_of_day=True) == day
    assert facets.parse_time(1700000000, end_of_day=True) == 1700000000.0


def test_nature_prefers_declared_over_location_over_body_cues():
    declared = {"kind": "memory", "source_meta": {"declared": {"type": "user"}}}
    assert facets.nature(declared, "topic_memory") == {
        "value": "preference",
        "provenance": "declared",
    }
    status = {"kind": "memory", "source_meta": {"declared": {"status": "临时"}}}
    assert facets.nature(status, "topic_memory")["value"] == "project_state"
    assert facets.nature({"kind": "rule"}, "rule") == {"value": "rule", "provenance": "parsed"}
    assert facets.nature({"kind": "memory"}, "navigation")["value"] == "reference"
    assert facets.nature({"kind": "memory"}, "conversation_summary")["value"] == "episode"
    assert facets.nature({"kind": "transcript"}, "transcript")["value"] == "episode"
    inferred = facets.nature({"kind": "memory", "body": "灰度发布进行中，待验证"}, "topic_memory")
    assert inferred == {"value": "project_state", "provenance": "inferred"}
    assert facets.nature(
        {"kind": "memory", "body": "We decided to keep SQLite"}, "topic_memory"
    ) == {
        "value": "decision",
        "provenance": "inferred",
    }
    assert facets.nature({"kind": "memory", "body": "plain fact"}, "topic_memory") == {
        "value": "unknown",
        "provenance": "unknown",
    }


def test_body_cues_only_count_in_the_title_or_the_first_lines():
    # A rule file that mentions "尚未" in paragraph ten is still a rule-like
    # document, not project state; the cue must sit where the note says what it is.
    deep = "\n".join(
        ["规则 %d：所有提交必须复核。" % i for i in range(1, 10)] + ["这条规则尚未覆盖 Windows。"]
    )
    assert facets.nature({"kind": "memory", "body": deep}, "topic_memory")["value"] == "unknown"
    late = "\n".join(["第 %d 段。" % i for i in range(1, 8)] + ["团队决定用 SQLite。"])
    assert facets.nature({"kind": "memory", "body": late}, "topic_memory")["value"] == "unknown"
    assert (
        facets.nature({"kind": "memory", "title": "灰度 TODO", "body": deep}, "topic_memory")[
            "value"
        ]
        == "project_state"
    )
    early = "第一段。\n\n第二段。\n\n团队决定用 SQLite。\n" + "\n".join(
        "后文 %d" % i for i in range(20)
    )
    assert facets.nature({"kind": "memory", "body": early}, "topic_memory") == {
        "value": "decision",
        "provenance": "inferred",
    }
    # Front matter lines do not use up the head; blank lines do not count.
    fronted = "---\nname: x\n---\n\n\n进行中：灰度发布\n" + deep
    assert facets.nature({"kind": "memory", "body": fronted}, "topic_memory")["value"] == (
        "project_state"
    )


def test_times_never_substitute_observation_for_content_time():
    record = {"path": "2026-01-10-plan.md", "observed_at": 1_800_000_000.0}
    result = facets.times(record)
    assert result["content_created"] == {
        "value": facets.parse_time("2026-01-10"),
        "source": "parsed",
    }
    assert result["content_modified"] is None
    assert result["file_mtime"] is None
    assert result["observed"]["source"] == "server"
    assert facets.reference_time(result) == (facets.parse_time("2026-01-10"), "content_created")
    assert facets.reference_time(facets.times({"observed_at": 1.0})) == (None, "unknown")
    with_meta = facets.times(
        {"source_meta": {"mtime": 1_750_000_000.0, "declared": {"created": "2025-01-01"}}},
        {"confirmed_at": 1_760_000_000.0, "last_cited": 1_759_000_000.0},
    )
    # The file mtime is its own entry; it never poses as the content's modification.
    assert with_meta["content_modified"] is None
    assert with_meta["file_mtime"] == {"value": 1_750_000_000.0, "source": "parsed"}
    assert facets.reference_time(with_meta) == (1_760_000_000.0, "confirmed")
    assert with_meta["last_cited"]["value"] == 1_759_000_000.0


def test_file_mtime_is_the_last_resort_reference_below_file_name_dates():
    # A January plan cloned in November: the clone time must not make it fresh.
    cloned = facets.times({"path": "2026-01-10-plan.md", "source_meta": {"mtime": 1_770_000_000.0}})
    assert facets.reference_time(cloned) == (facets.parse_time("2026-01-10"), "content_created")
    declared = facets.times(
        {
            "path": "2026-01-10-plan.md",
            "source_meta": {"mtime": 1_770_000_000.0, "declared": {"modified": "2026-09-01"}},
        }
    )
    assert facets.reference_time(declared) == (facets.parse_time("2026-09-01"), "content_modified")
    only_mtime = facets.times({"path": "plan.md", "source_meta": {"mtime": 1_770_000_000.0}})
    assert facets.reference_time(only_mtime) == (1_770_000_000.0, "file_mtime")
    assert facets.reference_time(facets.times({"path": "plan.md"})) == (None, "unknown")


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
    assert (
        facets.declared_filter({"source_meta": {"declared": {"expires": "2099-01-01"}}}, now=now)
        is None
    )


def test_pure_date_expiry_lasts_until_the_end_of_that_day():
    # 2026-11-03 08:00 Beijing is 2026-11-03 00:00 UTC: a notice that
    # "expires: 2026-11-03" is still valid for the whole day.
    morning = facets.parse_time("2026-11-03T00:00:00+00:00")
    record = {"source_meta": {"declared": {"expires": "2026-11-03"}}}
    assert facets.declared_filter(record, now=morning) is None
    assert facets.declared_filter(record, now=morning + 86399) is None
    gone = facets.declared_filter(record, now=morning + 86400)
    assert gone["reason"] == "expired" and gone["at"] == "2026-11-03T23:59:59+00:00"
    loose = {"source_meta": {"declared": {"valid_until": "2026/11/3"}}}
    assert facets.declared_filter(loose, now=morning + 86399) is None
    timed = {"source_meta": {"declared": {"expires": "2026-11-03T00:00:00"}}}
    assert facets.declared_filter(timed, now=morning + 1)["reason"] == "expired"


def test_declared_filter_keeps_candidates_whose_supersession_cannot_be_honoured():
    now = facets.parse_time("2026-11-03")
    old = {"asset_id": "old", "source_meta": {"declared": {"superseded_by": "new"}}}
    # Confirmed target: dropped as before (the verdict shape is unchanged).
    assert facets.declared_filter(old, now=now, existing={"new": None}) == {
        "reason": "superseded",
        "by": "new",
    }
    assert facets.declared_filter(old, now=now, existing={"new"}) == {
        "reason": "superseded",
        "by": "new",
    }
    # Typo or not-yet-synced successor: kept, with the reason for the trace.
    assert facets.declared_filter(old, now=now, existing={}) == {
        "reason": "superseded_target_missing",
        "by": "new",
        "drop": False,
    }
    # Self-reference: kept.
    selfish = {"asset_id": "old", "source_meta": {"declared": {"superseded_by": "old"}}}
    assert facets.declared_filter(selfish, now=now, existing={"old": "old"}) == {
        "reason": "superseded_self",
        "by": "old",
        "drop": False,
    }
    # A↔B: both exist and each names the other; neither is dropped.
    assert facets.declared_filter(old, now=now, existing={"new": "old"}) == {
        "reason": "superseded_cycle",
        "by": "new",
        "drop": False,
    }
    # A kept note never shadows a past expiry.
    expired = {
        **old,
        "source_meta": {"declared": {"superseded_by": "new", "expires": "2026-01-01"}},
    }
    assert facets.declared_filter(expired, now=now, existing={})["reason"] == "expired"
    # Without a verified set the declaration is taken at its word, as before.
    assert facets.declared_filter(old, now=now)["reason"] == "superseded"


def test_decay_is_bounded_reranking_and_unknown_time_does_not_decay():
    now = facets.parse_time("2026-11-03")
    fresh = {
        "kind": "memory",
        "source_meta": {"declared": {"type": "feedback", "modified": "2026-11-01"}},
    }
    old = {
        "kind": "memory",
        "source_meta": {"declared": {"type": "project", "modified": "2026-01-01"}},
    }
    unknown = {"kind": "memory"}
    a, b, c = (facets.decay(r, "topic_memory", None, now=now) for r in (fresh, old, unknown))
    assert a["half_life_days"] == 365 and b["half_life_days"] == 21 and c["half_life_days"] == 60
    assert a["weight"] > b["weight"]
    assert b["coefficient"] == 0.01 and b["weight"] == pytest.approx(
        1 + 0.05 * math.tanh(math.log(0.01)), abs=1e-5
    )
    assert c == {**c, "coefficient": 0.5, "reason": "time_unknown_neutral", "age_days": None}
    assert c["weight"] == pytest.approx(1 + 0.05 * math.tanh(math.log(0.5)), abs=1e-5)
    assert a["weight"] > c["weight"] > b["weight"]  # dated fresh > undated > dated stale
    assert all(0.95 <= r["weight"] <= 1.05 for r in (a, b, c))
    boosted = facets.decay(
        fresh, "topic_memory", {"delivered": 80, "support": 10, "gain": 5}, now=now
    )
    assert boosted["multipliers"] == {
        "access": 1.35,
        "support": 1.18,
        "gain": 1.15,
        "contradiction": 1.0,
    }
    # The multipliers' product (≤ 1.83) is well inside the e cap, so the
    # real upper bound is about +2.7% while a retention of 1 would allow +3.8%.
    assert boosted["coefficient"] == pytest.approx(a["retention"] * 1.35 * 1.18 * 1.15, abs=1e-3)
    assert 1.025 < boosted["weight"] < 1 + 0.05 * math.tanh(1)
    assert facets.decay(fresh, "topic_memory", {"gain": 1e9}, now=now)["coefficient"] <= math.e
    contradicted = facets.decay(fresh, "topic_memory", {"contradict": 3}, now=now)
    assert contradicted["multipliers"]["contradiction"] == pytest.approx(0.76)
    assert contradicted["weight"] < a["weight"]

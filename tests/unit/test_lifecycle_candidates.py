from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from agent_brain.contracts.memory_item import MemoryItem, MemoryType


BASE_TIME = datetime(2026, 7, 19, 10, 0, tzinfo=timezone.utc)


def _item(
    item_id: str,
    *,
    created_at: datetime = BASE_TIME,
    item_type: MemoryType = MemoryType.signal,
    project: str | None = "agent-memory-hub",
    tenant_id: str | None = "tenant-a",
    title: str = "hook login failure",
    summary: str = "hook login failure remains open",
    tags: list[str] | None = None,
    refs: dict[str, list[str]] | None = None,
    locator: str = "hook login failure locator",
    superseded_by: str | None = None,
) -> MemoryItem:
    return MemoryItem(
        id=item_id,
        type=item_type,
        created_at=created_at,
        project=project,
        tenant_id=tenant_id,
        title=title,
        summary=summary,
        tags=tags or [],
        refs=refs or {},
        context_views={"locator": locator},
        superseded_by=superseded_by,
    )


def test_explicit_supersedes_edge_is_first_and_candidate_json_is_private() -> None:
    from agent_brain.memory.governance.lifecycle_candidates import (
        rank_supersession_candidates,
    )

    obsolete = _item("mem-20260719-100000-obsolete-login")
    replacement = _item(
        "mem-20260719-110000-replacement-login",
        created_at=BASE_TIME + timedelta(hours=1),
        title="CANDIDATE_TITLE_SECRET hook login failure",
        summary="CANDIDATE_SUMMARY_SECRET issue resolved",
        locator="CANDIDATE_LOCATOR_SECRET",
    )

    result = rank_supersession_candidates(
        obsolete=obsolete,
        items=[replacement],
        supersedes_edges={(replacement.id, obsolete.id)},
    )

    assert result[0].score == 1.0
    assert result[0].evidence_codes[0] == "EXPLICIT_SUPERSEDES_EDGE"
    assert result[0].to_dict() == {
        "replacement_id": replacement.id,
        "score": 1.0,
        "evidence_codes": list(result[0].evidence_codes),
    }
    payload = json.dumps(result[0].to_dict())
    assert set(result[0].to_dict()) == {
        "replacement_id",
        "score",
        "evidence_codes",
    }
    assert "CANDIDATE_TITLE_SECRET" not in payload
    assert "CANDIDATE_SUMMARY_SECRET" not in payload
    assert "CANDIDATE_LOCATOR_SECRET" not in payload


def test_returns_only_same_scope_top_three() -> None:
    from agent_brain.memory.governance.lifecycle_candidates import (
        rank_supersession_candidates,
    )

    obsolete = _item("mem-20260719-100000-obsolete-scope", title="browser sync failure")
    same_scope = [
        _item(
            "mem-20260719-110000-scope-ref",
            created_at=BASE_TIME + timedelta(hours=1),
            title="unrelated alpha",
            refs={"mems": [obsolete.id]},
        ),
        _item(
            "mem-20260719-120000-scope-source",
            created_at=BASE_TIME + timedelta(hours=2),
            title="unrelated beta",
            refs={"files": ["browser-sync.md"]},
        ),
        _item(
            "mem-20260719-130000-scope-topic",
            created_at=BASE_TIME + timedelta(hours=3),
            title="browser sync repair",
        ),
        _item(
            "mem-20260719-140000-scope-newer",
            created_at=BASE_TIME + timedelta(hours=4),
            title="unrelated gamma",
        ),
    ]
    obsolete = obsolete.model_copy(
        update={"refs": obsolete.refs.model_copy(update={"files": ["browser-sync.md"]})}
    )
    cross_project = _item(
        "mem-20260719-150000-cross-project",
        created_at=BASE_TIME + timedelta(hours=5),
        project="other-project",
        refs={"mems": [obsolete.id]},
    )
    cross_tenant = _item(
        "mem-20260719-160000-cross-tenant",
        created_at=BASE_TIME + timedelta(hours=6),
        tenant_id="tenant-b",
        refs={"mems": [obsolete.id]},
    )

    result = rank_supersession_candidates(
        obsolete=obsolete,
        items=[*same_scope, cross_project, cross_tenant],
        supersedes_edges={
            (cross_project.id, obsolete.id),
            (cross_tenant.id, obsolete.id),
        },
    )

    assert [candidate.replacement_id for candidate in result] == [
        same_scope[0].id,
        same_scope[1].id,
        same_scope[2].id,
    ]


def test_combines_all_metadata_evidence_in_fixed_order_and_caps_score() -> None:
    from agent_brain.memory.governance.lifecycle_candidates import (
        rank_supersession_candidates,
    )

    obsolete = _item(
        "mem-20260719-100000-obsolete-evidence",
        title="OAuth 登录故障",
        tags=["browser-sync"],
        refs={
            "commits": ["abc123"],
            "files": ["docs/login.md"],
            "resources": ["resource-1"],
        },
    )
    replacement = _item(
        "mem-20260719-110000-replacement-evidence",
        created_at=BASE_TIME + timedelta(hours=1),
        title="OAuth 登录故障处理",
        tags=["browser-sync"],
        summary="登录故障已修复",
        refs={
            "mems": [obsolete.id],
            "commits": ["abc123"],
            "files": ["docs/login.md"],
            "resources": ["resource-1"],
        },
    )

    result = rank_supersession_candidates(
        obsolete=obsolete,
        items=[replacement],
        supersedes_edges=set(),
    )

    assert result[0].score == 1.0
    assert result[0].evidence_codes == (
        "EXPLICIT_MEMORY_REF",
        "SHARED_SOURCE_EVIDENCE",
        "TOPIC_OVERLAP",
        "CLOSURE_LANGUAGE",
        "NEWER_ITEM",
    )


@pytest.mark.parametrize("ref_kind", ["commits", "files", "resources"])
def test_each_supported_source_reference_can_supply_shared_evidence(ref_kind: str) -> None:
    from agent_brain.memory.governance.lifecycle_candidates import (
        rank_supersession_candidates,
    )

    obsolete = _item(
        "mem-20260719-100000-obsolete-source",
        title="obsolete alpha",
        summary="pending alpha",
        locator="alpha locator",
        refs={ref_kind: ["shared-source"]},
    )
    replacement = _item(
        f"mem-20260719-110000-replacement-{ref_kind}",
        created_at=BASE_TIME + timedelta(hours=1),
        title="replacement beta",
        summary="pending beta",
        locator="beta locator",
        refs={ref_kind: ["shared-source"]},
    )

    result = rank_supersession_candidates(
        obsolete=obsolete,
        items=[replacement],
        supersedes_edges=set(),
    )

    assert result[0].score == 0.3
    assert result[0].evidence_codes == ("SHARED_SOURCE_EVIDENCE", "NEWER_ITEM")


def test_topic_tokenization_is_stable_for_english_and_chinese_but_ignores_noise() -> None:
    from agent_brain.memory.governance.lifecycle_candidates import (
        rank_supersession_candidates,
    )

    obsolete = _item(
        "mem-20260719-100000-obsolete-token",
        title="OAuth 登录故障",
        tags=["browser-sync"],
    )
    related = _item(
        "mem-20260719-110000-related-token",
        created_at=BASE_TIME + timedelta(hours=1),
        title="OAuth 登录问题",
        tags=["browser-sync"],
        summary="仍在调查",
        locator="调查记录",
    )
    noise_obsolete = _item(
        "mem-20260719-100100-obsolete-noise",
        title="current status x 当前状态",
        tags=["memory"],
        summary="pending",
        locator="pending",
    )
    noise_candidate = _item(
        "mem-20260719-110100-replacement-noise",
        created_at=BASE_TIME + timedelta(hours=1),
        title="updated current state y 更新当前状态",
        tags=["signal"],
        summary="pending",
        locator="pending",
    )

    first = rank_supersession_candidates(
        obsolete=obsolete,
        items=[related],
        supersedes_edges=set(),
    )
    second = rank_supersession_candidates(
        obsolete=obsolete,
        items=[related],
        supersedes_edges=set(),
    )
    noise = rank_supersession_candidates(
        obsolete=noise_obsolete,
        items=[noise_candidate],
        supersedes_edges=set(),
    )

    assert first == second
    assert "TOPIC_OVERLAP" in first[0].evidence_codes
    assert noise[0].evidence_codes == ("NEWER_ITEM",)


def test_closure_language_uses_bounded_locator_metadata() -> None:
    from agent_brain.memory.governance.lifecycle_candidates import (
        rank_supersession_candidates,
    )

    obsolete = _item(
        "mem-20260719-100000-obsolete-closure",
        title="alpha outage",
        summary="pending",
        locator="pending",
    )
    replacement = _item(
        "mem-20260719-110000-replacement-closure",
        created_at=BASE_TIME + timedelta(hours=1),
        title="beta incident",
        summary="pending",
        locator="incident resolved with validation",
    )

    result = rank_supersession_candidates(
        obsolete=obsolete,
        items=[replacement],
        supersedes_edges=set(),
    )

    assert result[0].score == 0.15
    assert result[0].evidence_codes == ("CLOSURE_LANGUAGE", "NEWER_ITEM")


def test_closure_language_does_not_scan_past_locator_bound() -> None:
    from agent_brain.memory.governance.lifecycle_candidates import (
        rank_supersession_candidates,
    )

    obsolete = _item(
        "mem-20260719-100000-obsolete-locator-bound",
        title="alpha outage",
        summary="pending",
        locator="pending",
    )
    replacement = _item(
        "mem-20260719-110000-replacement-locator-bound",
        created_at=BASE_TIME + timedelta(hours=1),
        title="beta incident",
        summary="pending",
        locator=("x" * 512) + " resolved",
    )

    result = rank_supersession_candidates(
        obsolete=obsolete,
        items=[replacement],
        supersedes_edges=set(),
    )

    assert result[0].evidence_codes == ("NEWER_ITEM",)


def test_ties_sort_by_newer_created_at_then_id_independent_of_input_order() -> None:
    from agent_brain.memory.governance.lifecycle_candidates import (
        rank_supersession_candidates,
    )

    obsolete = _item(
        "mem-20260719-100000-obsolete-sort",
        title="obsolete topic",
        summary="pending",
        locator="pending",
    )
    older = _item(
        "mem-20260719-110000-z-sort",
        created_at=BASE_TIME + timedelta(hours=1),
        title="alpha only",
        summary="pending",
        locator="pending",
    )
    same_time_b = _item(
        "mem-20260719-120000-b-sort",
        created_at=BASE_TIME + timedelta(hours=2),
        title="beta only",
        summary="pending",
        locator="pending",
    )
    same_time_a = _item(
        "mem-20260719-120000-a-sort",
        created_at=BASE_TIME + timedelta(hours=2),
        title="gamma only",
        summary="pending",
        locator="pending",
    )

    forward = rank_supersession_candidates(
        obsolete=obsolete,
        items=[older, same_time_b, same_time_a],
        supersedes_edges=set(),
    )
    reverse = rank_supersession_candidates(
        obsolete=obsolete,
        items=[same_time_a, same_time_b, older],
        supersedes_edges=set(),
    )

    expected = [same_time_a.id, same_time_b.id, older.id]
    assert [candidate.replacement_id for candidate in forward] == expected
    assert [candidate.replacement_id for candidate in reverse] == expected


def test_all_validity_guards_apply_even_to_explicit_graph_edges() -> None:
    from agent_brain.memory.governance.lifecycle_candidates import (
        rank_supersession_candidates,
    )

    obsolete = _item("mem-20260719-100000-obsolete-guards")
    blocked = [
        _item("mem-20260719-090000-older", created_at=BASE_TIME - timedelta(hours=1)),
        _item("mem-20260719-100000-equal", created_at=BASE_TIME),
        obsolete.model_copy(update={"created_at": BASE_TIME + timedelta(hours=1)}),
        _item(
            "mem-20260719-110000-cross-project",
            created_at=BASE_TIME + timedelta(hours=1),
            project="other-project",
        ),
        _item(
            "mem-20260719-110050-null-project",
            created_at=BASE_TIME + timedelta(hours=1),
            project=None,
        ),
        _item(
            "mem-20260719-110100-cross-tenant",
            created_at=BASE_TIME + timedelta(hours=1),
            tenant_id="tenant-b",
        ),
        _item(
            "mem-20260719-110200-cross-type",
            created_at=BASE_TIME + timedelta(hours=1),
            item_type=MemoryType.fact,
        ),
        *[
            _item(
                f"mem-20260719-12{index:02d}00-review-{index}",
                created_at=BASE_TIME + timedelta(hours=2, minutes=index),
                tags=[tag],
            )
            for index, tag in enumerate(
                [
                    "NEEDS-REVIEW",
                    "requires-review",
                    "review-rejected",
                    "unverified-boundary",
                ]
            )
        ],
        _item(
            "mem-20260719-130000-already-superseded",
            created_at=BASE_TIME + timedelta(hours=3),
            superseded_by="mem-20260719-140000-next-replacement",
        ),
    ]

    result = rank_supersession_candidates(
        obsolete=obsolete,
        items=blocked,
        supersedes_edges={(candidate.id, obsolete.id) for candidate in blocked},
    )

    assert result == []


def test_wrong_type_inputs_fail_at_the_public_type_boundary() -> None:
    from agent_brain.memory.governance.lifecycle_candidates import (
        rank_supersession_candidates,
    )

    obsolete = _item("mem-20260719-100000-obsolete-type-boundary")

    with pytest.raises(TypeError, match="obsolete must be a MemoryItem"):
        rank_supersession_candidates(  # type: ignore[arg-type]
            obsolete=object(),
            items=[],
            supersedes_edges=set(),
        )
    with pytest.raises(TypeError, match="items must contain only MemoryItem instances"):
        rank_supersession_candidates(  # type: ignore[list-item]
            obsolete=obsolete,
            items=[object()],
            supersedes_edges=set(),
        )


def test_newer_guard_uses_actual_utc_order_across_dst_fold() -> None:
    from agent_brain.memory.governance.lifecycle_candidates import (
        rank_supersession_candidates,
    )

    eastern = ZoneInfo("America/New_York")
    obsolete = _item(
        "mem-20261101-013000-fold-zero",
        created_at=datetime(2026, 11, 1, 1, 30, tzinfo=eastern, fold=0),
        title="alpha state",
        summary="pending",
        locator="pending",
    )
    replacement = _item(
        "mem-20261101-013001-fold-one",
        created_at=datetime(2026, 11, 1, 1, 30, tzinfo=eastern, fold=1),
        title="beta state",
        summary="pending",
        locator="pending",
    )

    result = rank_supersession_candidates(
        obsolete=obsolete,
        items=[replacement],
        supersedes_edges=set(),
    )

    assert [candidate.replacement_id for candidate in result] == [replacement.id]


def test_sort_handles_datetime_max_with_negative_offsets_without_overflow() -> None:
    from agent_brain.memory.governance.lifecycle_candidates import (
        rank_supersession_candidates,
    )

    obsolete = _item(
        "mem-99980101-000000-extreme-obsolete",
        created_at=datetime(9998, 1, 1, tzinfo=timezone.utc),
        title="obsolete alpha",
        summary="pending",
        locator="pending",
    )
    earlier = _item(
        "mem-99991231-235958-extreme-earlier",
        created_at=datetime.max.replace(tzinfo=timezone(-timedelta(hours=12))),
        title="candidate beta",
        summary="pending",
        locator="pending",
    )
    later = _item(
        "mem-99991231-235959-extreme-later",
        created_at=datetime.max.replace(tzinfo=timezone(-timedelta(hours=13))),
        title="candidate gamma",
        summary="pending",
        locator="pending",
    )

    result = rank_supersession_candidates(
        obsolete=obsolete,
        items=[earlier, later],
        supersedes_edges=set(),
    )

    assert [candidate.replacement_id for candidate in result] == [later.id, earlier.id]


def test_far_future_microseconds_keep_exact_sort_order() -> None:
    from agent_brain.memory.governance.lifecycle_candidates import (
        rank_supersession_candidates,
    )

    obsolete = _item(
        "mem-99980101-000000-microsecond-obsolete",
        created_at=datetime(9998, 1, 1, tzinfo=timezone.utc),
        title="obsolete alpha",
        summary="pending",
        locator="pending",
    )
    earlier = _item(
        "mem-99991231-235958-a-microsecond",
        created_at=datetime(9999, 12, 31, 23, 59, 59, 999998, tzinfo=timezone.utc),
        title="candidate beta",
        summary="pending",
        locator="pending",
    )
    later = _item(
        "mem-99991231-235959-z-microsecond",
        created_at=datetime(9999, 12, 31, 23, 59, 59, 999999, tzinfo=timezone.utc),
        title="candidate gamma",
        summary="pending",
        locator="pending",
    )

    result = rank_supersession_candidates(
        obsolete=obsolete,
        items=[earlier, later],
        supersedes_edges=set(),
    )

    assert [candidate.replacement_id for candidate in result] == [later.id, earlier.id]


def test_empty_superseded_by_is_treated_as_active() -> None:
    from agent_brain.memory.governance.lifecycle_candidates import (
        rank_supersession_candidates,
    )

    obsolete = _item("mem-20260719-100000-empty-superseded-obsolete")
    replacement = _item(
        "mem-20260719-110000-empty-superseded-replacement",
        created_at=BASE_TIME + timedelta(hours=1),
        title="different beta",
        summary="pending",
        locator="pending",
        superseded_by="",
    )

    result = rank_supersession_candidates(
        obsolete=obsolete,
        items=[replacement],
        supersedes_edges=set(),
    )

    assert [candidate.replacement_id for candidate in result] == [replacement.id]


@pytest.mark.parametrize(
    ("obsolete_title", "replacement_title"),
    [
        ("𠀀𠀁故障", "𠀀𠀁恢复"),
        ("カタカナ障害", "カタカナ対応"),
        ("로그인장애", "로그인복구"),
    ],
)
def test_topic_overlap_supports_astral_han_kana_and_hangul(
    obsolete_title: str,
    replacement_title: str,
) -> None:
    from agent_brain.memory.governance.lifecycle_candidates import (
        rank_supersession_candidates,
    )

    obsolete = _item(
        "mem-20260719-100000-unicode-obsolete",
        title=obsolete_title,
        summary="pending",
        locator="pending",
    )
    replacement = _item(
        "mem-20260719-110000-unicode-replacement",
        created_at=BASE_TIME + timedelta(hours=1),
        title=replacement_title,
        summary="pending",
        locator="pending",
    )

    result = rank_supersession_candidates(
        obsolete=obsolete,
        items=[replacement],
        supersedes_edges=set(),
    )

    assert "TOPIC_OVERLAP" in result[0].evidence_codes


def test_topic_tokenization_ignores_title_and_tags_beyond_fixed_bounds() -> None:
    from agent_brain.memory.governance.lifecycle_candidates import (
        rank_supersession_candidates,
    )

    obsolete = _item(
        "mem-20260719-100000-bounded-topic-obsolete",
        title=("x" * 512) + " late-title-anchor",
        tags=[*[f"oldtag-{index}" for index in range(64)], "late-tag-anchor"],
        summary="pending",
        locator="pending",
    )
    replacement = _item(
        "mem-20260719-110000-bounded-topic-replacement",
        created_at=BASE_TIME + timedelta(hours=1),
        title=("y" * 512) + " late-title-anchor",
        tags=[*[f"newtag-{index}" for index in range(64)], "late-tag-anchor"],
        summary="pending",
        locator="pending",
    )

    result = rank_supersession_candidates(
        obsolete=obsolete,
        items=[replacement],
        supersedes_edges=set(),
    )

    assert result[0].evidence_codes == ("NEWER_ITEM",)


def test_ranking_keeps_only_a_bounded_top_three_during_scan(monkeypatch) -> None:
    from agent_brain.memory.governance import lifecycle_candidates as candidate_module

    obsolete = _item(
        "mem-20260719-100000-bounded-top-obsolete",
        title="obsolete root",
        summary="pending",
        locator="pending",
    )
    replacements = [
        _item(
            f"mem-20260720-{index:06d}-bounded-top-{index}",
            created_at=BASE_TIME + timedelta(days=1, seconds=index),
            title=f"candidate-{index}",
            summary="pending",
            locator="pending",
        )
        for index in range(100)
    ]
    observed_sizes: list[int] = []
    original = candidate_module._insert_top_three

    def tracked_insert(best, candidate):
        observed_sizes.append(len(best))
        result = original(best, candidate)
        observed_sizes.append(len(best))
        return result

    monkeypatch.setattr(candidate_module, "_insert_top_three", tracked_insert)

    result = candidate_module.rank_supersession_candidates(
        obsolete=obsolete,
        items=replacements,
        supersedes_edges=set(),
    )

    assert len(result) == 3
    assert max(observed_sizes) <= 3


def test_ranking_scores_only_the_matching_scope_and_type_bucket(monkeypatch) -> None:
    from agent_brain.memory.governance import lifecycle_candidates as candidate_module

    obsolete = _item("mem-20260719-100000-scope-index-obsolete")
    same_scope = [
        _item(
            f"mem-20260720-{index:06d}-scope-index-match-{index}",
            created_at=BASE_TIME + timedelta(days=1, seconds=index),
            title=f"matching candidate {index}",
        )
        for index in range(5)
    ]
    unrelated = [
        _item(
            f"mem-20260721-{index:06d}-scope-index-other-{index}",
            created_at=BASE_TIME + timedelta(days=2, seconds=index),
            project="other-project" if index % 2 == 0 else "agent-memory-hub",
            tenant_id="tenant-b" if index % 2 else "tenant-a",
            item_type=MemoryType.fact if index % 3 == 0 else MemoryType.signal,
        )
        for index in range(60)
    ]
    scored = 0
    original = candidate_module._score_candidate

    def counted_score(candidate, old, edges):
        nonlocal scored
        scored += 1
        return original(candidate, old, edges)

    ranker = candidate_module.SupersessionCandidateRanker(
        items=[*same_scope, *unrelated],
        supersedes_edges=set(),
    )
    monkeypatch.setattr(candidate_module, "_score_candidate", counted_score)

    result = ranker.rank(obsolete)

    assert len(result) == 3
    assert scored == len(same_scope)

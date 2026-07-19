from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

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

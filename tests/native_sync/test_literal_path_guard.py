"""A pasted storage tree must not turn reference paths into unsolicited facts."""

import pytest

from agent_brain.native_sync.common import digest
from agent_brain.native_sync.hub import Hub
from agent_brain.native_sync.recall_view import _similarity, normalize
from test_hub import asset


@pytest.fixture(autouse=True)
def production_lexical_guard(monkeypatch, _plain_keyword_hook_recall):
    monkeypatch.setenv("AMH_NATIVE_HOOK_LEXICAL_GUARD", "1")


def reference_index(tmp_path, folder="rollout_summaries"):
    hub = Hub(tmp_path / "hub.sqlite", retrieval_mode="literal")
    writer = hub.provision("writer", "writer-device")["token"]
    reader = hub.provision("reader", "reader-device")["token"]
    parts = [
        f"# Brand handbook\n## Task 1: Character guide\n\n"
        f"- {folder}/2026-11-09-brand-handbook.md\nOnly brand illustration guidance.\n",
        f"# Connection review\n## Task 1: Authorization acceptance\n\n"
        f"- {folder}/2026-11-02-connection-review.md\nOnly OAuth acceptance findings.\n",
    ]
    for index, body in enumerate(parts):
        hub.put(
            writer,
            asset(
                f"doc_index__p{index:06d}",
                body=body,
                title="MEMORY",
                path="MEMORY.md",
                product="codex",
                project="",
                document_id="doc_index",
                document_hash=digest("".join(parts)),
                part_index=index,
                part_count=2,
            ),
        )
    return hub, reader


@pytest.mark.parametrize("folder", ["rollout_summaries", "session_digests", "case_notes"])
def test_storage_tree_does_not_inject_unrelated_reference_sections(tmp_path, folder):
    hub, reader = reference_index(tmp_path, folder)
    query = normalize(
        "请解释 Claude Code 和 Codex 的真实记忆存储结构及每块用途。\n"
        f"<pasted_content>memories\n├── MEMORY.md\n└── {folder}\n"
        "    └── 2026-10-23-unrelated-example.md</pasted_content>"
    )
    result = hub.recall(reader, query, asking={"product": "claude-code", "project": ""})
    assert result["items"] == []
    # Explicit searching is still allowed to discover paths; only auto-injection is held.
    assert hub.retrieve(reader, folder, channel="tool")["items"]


def test_exact_reference_path_still_finds_the_requested_section(tmp_path):
    hub, reader = reference_index(tmp_path)
    result = hub.recall(reader, "解释 rollout_summaries/2026-11-09-brand-handbook.md")
    assert [item["asset_id"] for item in result["items"]] == ["doc_index__p000000"]


@pytest.mark.parametrize("suffix", ["/archive", ".md", "-archive", "_archive"])
def test_section_focus_uses_the_admitted_whole_identifier(tmp_path, suffix):
    hub = Hub(tmp_path / "hub.sqlite", retrieval_mode="literal")
    token = hub.provision("one", "device")["token"]
    body = (
        f"# Unrelated\nCASE-427{suffix} is a different identifier.\n"
        + "Old unrelated details.\n" * 80
        + "\n# Current incident\nCASE-427 requires a signed recovery receipt.\n"
        + "Current incident details.\n" * 80
    )
    hub.put(token, asset("incident", body=body, path="incident.md"))
    item = hub.recall(token, "CASE-427 怎么恢复")["items"][0]
    assert "signed recovery receipt" in item["body"]
    assert "Unrelated" not in item["body"]
    assert item["semantic_match"]["char_start"] == body.index("CASE-427 requires")


def test_literal_match_does_not_invent_a_semantic_score(tmp_path):
    hub, reader = reference_index(tmp_path)
    result = hub.recall(reader, "解释 rollout_summaries/2026-11-09-brand-handbook.md")
    item = result["items"][0]
    assert item["recall"]["relevance"] is None
    assert item["semantic_match"].get("similarity") is None
    assert _similarity(item) == "未计算"


def test_display_keeps_computed_zero_and_probability_distinct_from_literal_placeholder():
    assert (
        _similarity({"semantic_match": {"similarity": 0.0, "aggregation": "literal"}}) == "未计算"
    )
    assert (
        _similarity({"semantic_match": {"similarity": 0.0, "aggregation": "max_window_cosine"}})
        == "0.00"
    )
    assert _similarity({"recall": {"relevance": 0.0}}) == "0%"
    assert (
        _similarity(
            {
                "semantic_match": {"similarity": 0.75, "aggregation": "literal"},
                "recall": {"routes": {"semantic": 1}},
            }
        )
        == "0.75"
    )


def test_client_rejects_old_hub_path_prefix_hits_before_injection():
    from agent_brain.native_sync.query_worker import fetch

    class OldLiteralHub:
        def recall(self, query, **kwargs):
            return {
                "items": [
                    {
                        "asset_id": "noise",
                        "path": "MEMORY.md",
                        "title": "Unrelated handbook",
                        "body": "See rollout_summaries/brand-guide.md",
                    },
                    {
                        "asset_id": "valid",
                        "path": "incident.md",
                        "title": "Gateway incident",
                        "body": "CASE-427 requires a signed recovery receipt.",
                    },
                ],
                "trace_id": "f" * 24,
                "retrieval": {"mode": "literal", "reasons": ["literal_mode", "hook_lexical_guard"]},
            }

    result = fetch(OldLiteralHub(), "存储目录 rollout_summaries；另查 CASE-427")
    assert [item["asset_id"] for item in result["items"]] == ["valid"]
    assert "client_literal_guard" in result["retrieval"]["reasons"]
    assert result["trace_id"] == "f" * 24
    # Exact authorized asset/path requests remain valid even if the body has no identifier.
    assert [item["asset_id"] for item in fetch(OldLiteralHub(), "noise")["items"]] == ["noise"]
    assert [item["asset_id"] for item in fetch(OldLiteralHub(), "MEMORY.md")["items"]] == ["noise"]


def test_client_does_not_override_semantic_or_explicit_keyword_results():
    from agent_brain.native_sync.query_worker import fetch

    class HubResult:
        def recall(self, query, **kwargs):
            return {
                "items": [{"asset_id": "semantic", "body": "审批需要两名评审"}],
                "retrieval": {"mode": "hybrid", "reasons": []},
            }

    assert fetch(HubResult(), "复核流程")["items"][0]["asset_id"] == "semantic"


def test_pasted_tree_does_not_name_terminal_version_or_echoed_directory_entries(tmp_path):
    hub, reader = reference_index(tmp_path)
    writer = hub.provision("writer", "writer-device")["token"]
    hub.put(writer, asset("terminal", body="v3.11.9 is installed on a laptop.", path="terminal.md"))
    hub.put(writer, asset("echo", body="The user listed the directory ad_hoc.", path="echo.md"))
    query = normalize(
        "比较 Claude Code 和 Codex 的存储结构。\n<pasted_content>"
        "user ~ v3.11.9 $ lt ~/.codex/memories\nmemories\n"
        "├── ad_hoc\n└── rollout_summaries\n</pasted_content>"
    )
    assert hub.recall(reader, query)["items"] == []
    # A business identifier in the actual request must survive the pasted tree.
    hub.put(writer, asset("fact", body="CASE-427 needs a signed recovery receipt.", path="fact.md"))
    assert [
        item["asset_id"] for item in hub.recall(reader, "另查 CASE-427\n" + query)["items"]
    ] == ["fact"]


def test_directory_prefix_before_a_chinese_filename_is_not_a_whole_name(tmp_path):
    hub = Hub(tmp_path / "hub.sqlite", retrieval_mode="literal")
    token = hub.provision("one", "device")["token"]
    hub.put(token, asset("unrelated", body="参考 case_notes/中文复盘.md", path="notes.md"))
    assert hub.recall(token, "解释 case_notes 目录用途")["items"] == []

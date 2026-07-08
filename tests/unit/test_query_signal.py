from __future__ import annotations

from datetime import datetime, timezone


def test_weak_cjk_continuation_prompt_does_not_inject() -> None:
    from agent_brain.memory.context.query_signal import analyze_injection_query, extract_injection_keywords

    signal = analyze_injection_query("就像")
    assert not signal.injectable
    assert signal.reason == "too_weak"
    assert extract_injection_keywords("就像") == ""


def test_query_gate_gap_evidence_records_specific_weak_terms_only() -> None:
    from agent_brain.memory.context.query_signal import query_gate_gap_evidence

    evidence = query_gate_gap_evidence("验证")

    assert evidence is None


def test_query_gate_gap_evidence_ignores_connective_noise() -> None:
    from agent_brain.memory.context.query_signal import query_gate_gap_evidence

    assert query_gate_gap_evidence("就像") is None


def test_core_recall_question_blocks_without_metadata_anchor() -> None:
    from agent_brain.memory.context.query_signal import analyze_injection_query, extract_injection_keywords

    signal = analyze_injection_query("为什么召回链路没有进入后处理")

    assert not signal.injectable
    assert signal.decision == "block"
    assert signal.reason == "too_weak"
    assert signal.anchors == ()
    assert extract_injection_keywords("为什么召回链路没有进入后处理") == ""


def test_weak_intent_without_information_anchor_still_blocks() -> None:
    from agent_brain.memory.context.query_signal import analyze_injection_query, extract_injection_keywords

    for prompt in (
        "继续",
        "好的",
        "确认",
        "就像",
        "为什么",
        "再说说",
        "优化一下",
        "看不懂",
        "不不不",
        "然后呢",
        "这些呢",
    ):
        signal = analyze_injection_query(prompt)
        assert not signal.injectable, prompt
        assert signal.decision == "block", prompt
        assert extract_injection_keywords(prompt) == ""


def test_short_weak_prompt_blocks_before_metadata_cache(tmp_path) -> None:
    from agent_brain.memory.context.query_signal import analyze_injection_query

    signal = analyze_injection_query("继续", brain_dir=tmp_path)

    assert not signal.injectable
    assert signal.reason == "too_weak"
    assert not (tmp_path / "index" / "query-signal-metadata-cache.json").exists()


def test_weak_intent_trace_explains_reduplicated_acknowledgement() -> None:
    from agent_brain.memory.context.query_signal import analyze_injection_query

    signal = analyze_injection_query("可以可以")

    assert not signal.injectable
    assert signal.decision == "block"
    assert "block:unanchored_cjk_clause" in signal.trace


def test_metadata_anchor_overrides_weak_control_trace(tmp_path) -> None:
    from agent_brain.contracts.memory_item import MemoryItem, MemoryType
    from agent_brain.memory.context.query_signal import analyze_injection_query
    from agent_brain.memory.store.items_store import ItemsStore

    item = MemoryItem(
        id="mem-20260628-141000-loop-engineering",
        type=MemoryType.artifact,
        created_at=datetime.now(timezone.utc),
        title="AMH README Loop Engineering 特性打磨",
        summary="README.zh.md 解释 Loop Engineering 在维护、召回、治理中的使用点。",
        tags=["agent-memory-hub", "readme"],
    )
    ItemsStore(tmp_path / "items").write(item, "README Loop Engineering body")

    signal = analyze_injection_query("继续优化 Loop Engineering 特性", brain_dir=tmp_path)

    assert signal.injectable
    assert signal.decision == "inject_allowed"
    assert "metadata_terms=loop|engineering" in signal.trace
    assert "decision:inject_allowed" in signal.trace


def test_metadata_anchor_allows_weak_intent_to_inject(tmp_path) -> None:
    from agent_brain.contracts.memory_item import MemoryItem, MemoryType
    from agent_brain.memory.context.query_signal import analyze_injection_query, extract_injection_keywords
    from agent_brain.memory.store.items_store import ItemsStore

    item = MemoryItem(
        id="mem-20260628-120000-loop-engineering",
        type=MemoryType.artifact,
        created_at=datetime.now(timezone.utc),
        title="AMH README Loop Engineering 特性打磨",
        summary="README.zh.md 解释 Loop Engineering 在维护、召回、治理中的使用点。",
        tags=["agent-memory-hub", "readme"],
    )
    ItemsStore(tmp_path / "items").write(item, "README Loop Engineering body")

    signal = analyze_injection_query("继续优化 Loop Engineering 特性", brain_dir=tmp_path)

    assert signal.injectable
    assert signal.decision == "inject_allowed"
    assert "metadata_phrase" in signal.anchors
    assert extract_injection_keywords("继续优化 Loop Engineering 特性", brain_dir=tmp_path).startswith("loop|engineering")


def test_metadata_phrase_cache_avoids_reparsing_items(tmp_path, monkeypatch) -> None:
    from agent_brain.contracts.memory_item import MemoryItem, MemoryType
    from agent_brain.memory.context.query_signal import analyze_injection_query
    from agent_brain.memory.store.items_store import ItemsStore

    item = MemoryItem(
        id="mem-20260628-120000-loop-engineering",
        type=MemoryType.artifact,
        created_at=datetime.now(timezone.utc),
        title="AMH README Loop Engineering 特性打磨",
        summary="README.zh.md 解释 Loop Engineering 在维护、召回、治理中的使用点。",
        tags=["agent-memory-hub", "readme"],
    )
    ItemsStore(tmp_path / "items").write(item, "README Loop Engineering body")

    assert analyze_injection_query("继续优化 Loop Engineering 特性", brain_dir=tmp_path).injectable
    assert (tmp_path / "index" / "query-signal-metadata-cache.json").is_file()

    def fail_iter_all(self):  # noqa: ANN001
        raise AssertionError("metadata cache should avoid reparsing MemoryItem files")

    monkeypatch.setattr(ItemsStore, "iter_all", fail_iter_all)

    signal = analyze_injection_query("继续优化 Loop Engineering 特性", brain_dir=tmp_path)

    assert signal.injectable
    assert "metadata_terms=loop|engineering" in signal.trace


def test_file_or_module_anchor_allows_weak_edit_prompt() -> None:
    from agent_brain.memory.context.query_signal import analyze_injection_query, extract_injection_keywords

    signal = analyze_injection_query("优化一下 query_signal.py")

    assert signal.injectable
    assert signal.decision == "inject_allowed"
    assert "file_or_module" in signal.anchors
    assert extract_injection_keywords("优化一下 query_signal.py") == "query_signal.py"


def test_file_anchor_prompt_keeps_specific_cjk_task_term() -> None:
    from agent_brain.memory.context.query_signal import analyze_injection_query, extract_injection_keywords

    prompt = "优化一下 query_signal.py，看看中文长任务关键词为什么丢失"

    signal = analyze_injection_query(prompt)
    keywords = extract_injection_keywords(prompt)

    assert signal.injectable
    assert "file_or_module" in signal.anchors
    assert "query_signal.py" in keywords.split("|")
    assert "关键词" in keywords.split("|")


def test_long_followup_task_keeps_governance_table_and_release_anchors() -> None:
    from agent_brain.memory.context.query_signal import analyze_injection_query, extract_injection_keywords

    prompt = (
        "我下发这么长一个任务，只提取了一个关键词，这种情况遇到太多次了，"
        "然后继续推进你整理的那张表格，全部治理完成之后，依旧提交到GitHub，"
        "注意commit规范和文案"
    )

    signal = analyze_injection_query(prompt)
    terms = set(extract_injection_keywords(prompt).split("|"))

    assert signal.injectable
    assert {"关键词", "表格", "治理", "github", "commit"}.issubset(terms)


def test_cjk_interface_question_with_json_config_does_not_extract_boolean_literal() -> None:
    from agent_brain.memory.context.query_signal import analyze_injection_query, extract_injection_keywords

    prompt = """[Image #1]新增这两个接口的理由给我，因为我之前理解既然只是扩展了数据结构{
      "defaultSceneIdentify": "ZTJD",
      "installStatus": "",
      "sceneEvaluationType": {
        "ZTJD": "quantitative"
      },
      "serviceQualityScoreConfig": {
        "ZTJD": {
          "convertToPercentage": true
        }
      }
    }

    不应该是复用原来的接口吗"""

    signal = analyze_injection_query(prompt)
    keywords = extract_injection_keywords(prompt).split("|")

    assert signal.injectable
    assert "json_field" in signal.anchors
    assert "true" not in signal.terms
    assert "true" not in signal.weak_terms
    assert "true" not in keywords
    assert {"新增接口", "复用接口"}.issubset(keywords)
    assert {"servicequalityscoreconfig", "sceneevaluationtype"}.issubset(keywords)


def test_json_boolean_literal_is_not_restored_from_metadata_cache(tmp_path) -> None:
    from agent_brain.contracts.memory_item import MemoryItem, MemoryType
    from agent_brain.memory.context.query_signal import analyze_injection_query
    from agent_brain.memory.store.items_store import ItemsStore

    item = MemoryItem(
        id="mem-20260708-100000-json-true-literal",
        type=MemoryType.fact,
        created_at=datetime.now(timezone.utc),
        title="JSON true literal should stay non-semantic",
        summary="Historical metadata containing true should not make true a recall anchor.",
        tags=["json", "query-signal"],
    )
    ItemsStore(tmp_path / "items").write(item, "true literal body")

    prompt = """新增接口理由 {"serviceQualityScoreConfig":{"ZTJD":{"convertToPercentage":true}}}"""

    signal = analyze_injection_query(prompt, brain_dir=tmp_path)

    assert signal.injectable
    assert "true" not in signal.terms
    assert "true" not in signal.strong_terms
    assert "true" not in signal.weak_terms


def test_file_uri_prompt_does_not_promote_local_path_segments_from_metadata(tmp_path) -> None:
    from agent_brain.contracts.memory_item import MemoryItem, MemoryType
    from agent_brain.memory.context.query_signal import analyze_injection_query, extract_injection_keywords
    from agent_brain.memory.store.items_store import ItemsStore

    item = MemoryItem(
        id="mem-20260707-120000-readme-preview-path",
        type=MemoryType.artifact,
        created_at=datetime.now(timezone.utc),
        title="AMH README preview at /repo/agent-memory-hub/docs/visuals/readme-zh-preview.html",
        summary="README preview artifact references local docs/visuals path.",
        tags=["repo", "workspace", "docs", "visuals", "agent-memory-hub"],
    )
    ItemsStore(tmp_path / "items").write(item, "README preview body")

    prompt = (
        "我没有在file:///repo/agent-memory-hub/"
        "docs/visuals/readme-zh-preview.html#agent-memory-hub看到呀"
    )

    signal = analyze_injection_query(prompt, brain_dir=tmp_path)
    keywords = extract_injection_keywords(prompt, brain_dir=tmp_path)

    assert signal.injectable
    assert keywords == "readme-zh-preview.html|agent-memory-hub"
    for local_segment in ("repo", "workspace", "docs", "visuals"):
        assert local_segment not in keywords.split("|")


def test_file_anchor_prompt_keeps_cjk_question_focus_terms(tmp_path) -> None:
    from agent_brain.contracts.memory_item import MemoryItem, MemoryType
    from agent_brain.memory.context.query_signal import analyze_injection_query, extract_injection_keywords
    from agent_brain.memory.store.items_store import ItemsStore

    item = MemoryItem(
        id="mem-20260704-220000-hybrid-amh-method-preset",
        type=MemoryType.decision,
        created_at=datetime.now(timezone.utc),
        title="AMH hybrid_amh.yaml method preset comparison",
        summary="hybrid_amh.yaml method preset is compared with external benchmark presets.",
        tags=["hybrid_amh.yaml", "method", "preset"],
    )
    ItemsStore(tmp_path / "items").write(item, "method preset comparison body")

    prompt = (
        "AMH 已作为本仓库追加的 hybrid_amh.yaml method preset 这句话我就没有看懂\n\n"
        "到底是不是跟其他竞品基于统一标准"
    )

    signal = analyze_injection_query(prompt, brain_dir=tmp_path)
    keywords = extract_injection_keywords(prompt, brain_dir=tmp_path)

    assert signal.injectable
    assert "file_or_module" in signal.anchors
    assert "cjk_question_focus" in signal.anchors
    assert "其他竞品" in signal.strong_terms
    assert "统一标准" in signal.strong_terms
    assert "其他竞品基于统一标准" not in signal.strong_terms
    assert any(not term.isascii() for term in keywords.split("|"))


def test_unanchored_mixed_scope_confirmation_does_not_search_ascii_fragments() -> None:
    from agent_brain.memory.context.query_signal import analyze_injection_query, extract_injection_keywords

    prompts = (
        "我的意思是代码是否全部在本地main分支了，因为我准备上传GitHub了",
        "综上所有代码都合入main分支了吗",
        "代码是否全部在本地main分支了",
    )

    for prompt in prompts:
        signal = analyze_injection_query(prompt)

        assert not signal.injectable, prompt
        assert signal.reason == "unanchored_mixed_scope", prompt
        keywords = extract_injection_keywords(prompt)
        assert keywords == "", prompt
        assert "main|github" not in keywords
        assert not any(term.startswith("码都合入") for term in signal.terms)


def test_cjk_question_focus_terms_do_not_open_unanchored_prompt() -> None:
    from agent_brain.memory.context.query_signal import analyze_injection_query, extract_injection_keywords

    prompt = "到底是不是跟其他竞品基于统一标准"

    signal = analyze_injection_query(prompt)

    assert not signal.injectable
    assert signal.decision == "block"
    assert extract_injection_keywords(prompt) == ""


def test_query_signal_diagnostics_explain_kept_and_weak_terms(tmp_path) -> None:
    from agent_brain.contracts.memory_item import MemoryItem, MemoryType
    from agent_brain.memory.context.query_signal import diagnose_injection_query
    from agent_brain.memory.store.items_store import ItemsStore

    item = MemoryItem(
        id="mem-20260704-220000-hybrid-amh-method-preset",
        type=MemoryType.decision,
        created_at=datetime.now(timezone.utc),
        title="AMH hybrid_amh.yaml method preset comparison",
        summary="hybrid_amh.yaml method preset is compared with external benchmark presets.",
        tags=["hybrid_amh.yaml", "method", "preset"],
    )
    ItemsStore(tmp_path / "items").write(item, "method preset comparison body")

    diagnostic = diagnose_injection_query(
        "AMH 已作为本仓库追加的 hybrid_amh.yaml method preset 这句话我就没有看懂\n\n"
        "到底是不是跟其他竞品基于统一标准",
        brain_dir=tmp_path,
    ).to_dict()

    assert diagnostic["decision"] == "inject_allowed"
    assert diagnostic["keywords"] == "hybrid_amh.yaml|method|preset|其他竞品|统一标准"
    assert diagnostic["kept_terms"] == [
        "hybrid_amh.yaml",
        "method",
        "preset",
        "其他竞品",
        "统一标准",
    ]
    assert diagnostic["weak_noise"] == ["到底是不是跟其他竞品基于统一标准"]
    assert "cjk_question_focus" in diagnostic["anchors"]
    assert "cjk_question_focus_terms=其他竞品|统一标准" in diagnostic["trace"]


def test_query_signal_diagnostics_explain_blocked_unanchored_prompt() -> None:
    from agent_brain.memory.context.query_signal import diagnose_injection_query

    diagnostic = diagnose_injection_query("到底是不是跟其他竞品基于统一标准").to_dict()

    assert diagnostic["decision"] == "block"
    assert diagnostic["keywords"] == ""
    assert diagnostic["reason"] in {"too_weak", "unanchored_cjk_clause"}
    assert diagnostic["blocked"] is True


def test_metadata_anchor_works_across_memory_types(tmp_path) -> None:
    from agent_brain.contracts.memory_item import MemoryItem, MemoryType
    from agent_brain.memory.context.query_signal import analyze_injection_query
    from agent_brain.memory.store.items_store import ItemsStore

    store = ItemsStore(tmp_path / "items")
    for index, memory_type in enumerate((
        MemoryType.fact,
        MemoryType.decision,
        MemoryType.episode,
        MemoryType.artifact,
        MemoryType.signal,
        MemoryType.handoff,
        MemoryType.policy,
        MemoryType.skill,
    )):
        store.write(
            MemoryItem(
                id=f"mem-20260628-1201{index:02d}-recall-{memory_type.value}",
                type=memory_type,
                created_at=datetime.now(timezone.utc),
                title=f"召回矩阵 {memory_type.value} 场景",
                summary=f"{memory_type.value} 类型的召回矩阵验证。",
                tags=["recall-matrix", memory_type.value],
            ),
            f"{memory_type.value} recall matrix body",
        )

    signal = analyze_injection_query("继续召回矩阵场景验证", brain_dir=tmp_path)

    assert signal.injectable
    assert signal.decision == "inject_allowed"
    assert "metadata_phrase" in signal.anchors
    assert "召回矩阵" in signal.strong_terms


def test_known_short_project_entity_is_injectable_when_metadata_supports_it(tmp_path) -> None:
    from agent_brain.contracts.memory_item import MemoryItem, MemoryType
    from agent_brain.memory.context.query_signal import analyze_injection_query, extract_injection_keywords
    from agent_brain.memory.store.items_store import ItemsStore

    item = MemoryItem(
        id="mem-20260627-180000-short-known-entity",
        type=MemoryType.artifact,
        created_at=datetime.now(timezone.utc),
        title="甲乙能力清单",
        summary="甲乙项目上下文",
        tags=["甲乙", "xy"],
    )
    ItemsStore(tmp_path / "items").write(item, "甲乙项目上下文")

    signal = analyze_injection_query("甲乙", brain_dir=tmp_path)

    assert signal.injectable
    assert "甲乙" in signal.strong_terms
    assert extract_injection_keywords("甲乙", brain_dir=tmp_path) == "甲乙"


def test_generic_domain_term_stays_non_injectable_even_if_metadata_mentions_it(tmp_path) -> None:
    from agent_brain.contracts.memory_item import MemoryItem, MemoryType
    from agent_brain.memory.context.query_signal import analyze_injection_query, extract_injection_keywords
    from agent_brain.memory.store.items_store import ItemsStore

    item = MemoryItem(
        id="mem-20260627-180010-validation-domain-term",
        type=MemoryType.artifact,
        created_at=datetime.now(timezone.utc),
        title="验证流程说明",
        summary="验证流程说明",
        tags=["验证"],
    )
    ItemsStore(tmp_path / "items").write(item, "验证流程说明")

    signal = analyze_injection_query("验证", brain_dir=tmp_path)

    assert not signal.injectable
    assert signal.reason == "too_weak"
    assert extract_injection_keywords("验证", brain_dir=tmp_path) == ""


def test_short_cjk_entity_is_not_cut_from_long_clause_even_when_frequent(tmp_path) -> None:
    from agent_brain.contracts.memory_item import MemoryItem, MemoryType
    from agent_brain.memory.context.query_signal import analyze_injection_query, extract_injection_keywords
    from agent_brain.memory.store.items_store import ItemsStore

    store = ItemsStore(tmp_path / "items")
    for index in range(4):
        item = MemoryItem(
            id=f"mem-20260707-13000{index}-validation-tag",
            type=MemoryType.artifact,
            created_at=datetime.now(timezone.utc),
            title=f"验证流程 {index}",
            summary="验证流程和 pytest evidence.",
            tags=["验证", "pytest"],
        )
        store.write(item, "validation body")

    signal = analyze_injection_query(
        "之前DWS不是好好的吗，也都已经验证过了呀，我有点迷惑",
        brain_dir=tmp_path,
    )

    assert signal.injectable
    assert "dws" in signal.strong_terms
    assert "验证" not in signal.strong_terms
    assert extract_injection_keywords(
        "之前DWS不是好好的吗，也都已经验证过了呀，我有点迷惑",
        brain_dir=tmp_path,
    ) == "dws"


def test_short_cjk_metadata_entities_need_disambiguating_source(tmp_path) -> None:
    from agent_brain.contracts.memory_item import MemoryItem, MemoryType
    from agent_brain.memory.context.query_signal import analyze_injection_query, extract_injection_keywords
    from agent_brain.memory.store.items_store import ItemsStore

    store = ItemsStore(tmp_path / "items")
    for suffix, title, tags in [
        ("major", "通用专业优先级讨论", ["专业"]),
        ("priority", "优先级排序样例", ["优先"]),
    ]:
        store.write(
            MemoryItem(
                id=f"mem-20260702-010310-{suffix}",
                type=MemoryType.artifact,
                created_at=datetime.now(timezone.utc),
                title=title,
                summary="synthetic public fixture",
                tags=tags,
            ),
            "body",
        )

    prompt = "我还有一些诉求，为什么不能选几所学校的相关专业，其次优先几个城市"

    signal = analyze_injection_query(prompt, brain_dir=tmp_path)

    assert not signal.injectable
    assert "metadata_entity" not in signal.anchors
    assert "专业" not in signal.strong_terms
    assert "优先" not in signal.strong_terms
    assert extract_injection_keywords(prompt, brain_dir=tmp_path) == ""


def test_cjk_topic_question_extracts_keyphrases_before_generic_html(tmp_path) -> None:
    from agent_brain.contracts.memory_item import MemoryItem, MemoryType
    from agent_brain.memory.context.query_signal import analyze_injection_query, extract_injection_keywords
    from agent_brain.memory.store.items_store import ItemsStore

    html_item = MemoryItem(
        id="mem-20260703-010500-html-preview",
        type=MemoryType.artifact,
        created_at=datetime.now(timezone.utc),
        title="HTML 预览页",
        summary="静态 HTML 预览。",
        tags=["html"],
    )
    ItemsStore(tmp_path / "items").write(html_item, "html body")

    prompt = (
        "关于多智能体共享第二大脑\n"
        "我发现一个问题，为什么在召回记忆的时候，只提取了某个关键词\n"
        "转成html预览给我审核"
    )

    signal = analyze_injection_query(prompt, brain_dir=tmp_path)
    keywords = extract_injection_keywords(prompt, brain_dir=tmp_path)

    assert signal.injectable
    assert "keyphrase" in signal.anchors
    assert keywords != "html"
    assert keywords.startswith("多智能体共享第二大脑|召回记忆")
    assert "召回记忆的" not in signal.terms
    assert "html" not in signal.terms[:2]


def test_generic_html_operation_does_not_inject_without_topic_anchor(tmp_path) -> None:
    from agent_brain.contracts.memory_item import MemoryItem, MemoryType
    from agent_brain.memory.context.query_signal import analyze_injection_query, extract_injection_keywords
    from agent_brain.memory.store.items_store import ItemsStore

    html_item = MemoryItem(
        id="mem-20260703-010510-html-preview",
        type=MemoryType.artifact,
        created_at=datetime.now(timezone.utc),
        title="HTML 预览页",
        summary="静态 HTML 预览。",
        tags=["html"],
    )
    ItemsStore(tmp_path / "items").write(html_item, "html body")

    prompt = "转成html预览给我审核"

    signal = analyze_injection_query(prompt, brain_dir=tmp_path)

    assert not signal.injectable
    assert signal.reason == "generic_format_without_topic"
    assert extract_injection_keywords(prompt, brain_dir=tmp_path) == ""


def test_mixed_agent_management_prompt_uses_keyphrase_not_generic_agent(tmp_path) -> None:
    from agent_brain.contracts.memory_item import MemoryItem, MemoryType
    from agent_brain.memory.context.query_signal import analyze_injection_query, extract_injection_keywords
    from agent_brain.memory.store.items_store import ItemsStore

    item = MemoryItem(
        id="mem-20260703-010530-agent-memory-metrics",
        type=MemoryType.artifact,
        created_at=datetime.now(timezone.utc),
        title="AMH agent-memory metrics evaluation report",
        summary="Evaluate agent memory metrics and benchmark readiness.",
        tags=["agent", "memory", "metrics"],
    )
    ItemsStore(tmp_path / "items").write(item, "agent memory metrics body")

    prompt = "[Image #1]另外，这个关于Agent的管理，应该单独一个模块，而且必须有优雅美观大气好看的统计图之类的交互"

    signal = analyze_injection_query(prompt, brain_dir=tmp_path)
    keywords = extract_injection_keywords(prompt, brain_dir=tmp_path)

    assert signal.injectable
    assert "keyphrase" in signal.anchors
    assert signal.strong_terms == ("agent的管理",)
    assert keywords == "agent的管理"
    assert "agent" not in signal.terms


def test_mixed_shared_second_brain_prompt_uses_compound_keyphrase_not_generic_agent(tmp_path) -> None:
    from agent_brain.contracts.memory_item import MemoryItem, MemoryType
    from agent_brain.memory.context.query_signal import analyze_injection_query, extract_injection_keywords
    from agent_brain.memory.store.items_store import ItemsStore

    item = MemoryItem(
        id="mem-20260703-010531-agent-memory-metrics",
        type=MemoryType.artifact,
        created_at=datetime.now(timezone.utc),
        title="AMH agent-memory metrics evaluation report",
        summary="Evaluate agent memory metrics and benchmark readiness.",
        tags=["agent", "memory", "metrics"],
    )
    ItemsStore(tmp_path / "items").write(item, "agent memory metrics body")

    prompt = "多Agent共享第二大脑"

    signal = analyze_injection_query(prompt, brain_dir=tmp_path)
    keywords = extract_injection_keywords(prompt, brain_dir=tmp_path)

    assert signal.injectable
    assert "keyphrase" in signal.anchors
    assert keywords == "多agent共享第二大脑"
    assert "agent" not in signal.terms


def test_mixed_compound_keyphrase_is_not_limited_to_known_domain_words(tmp_path) -> None:
    from agent_brain.contracts.memory_item import MemoryItem, MemoryType
    from agent_brain.memory.context.query_signal import analyze_injection_query, extract_injection_keywords
    from agent_brain.memory.store.items_store import ItemsStore

    item = MemoryItem(
        id="mem-20260703-010533-widget-metrics",
        type=MemoryType.artifact,
        created_at=datetime.now(timezone.utc),
        title="Widget metrics evaluation report",
        summary="Widget metrics and benchmark readiness.",
        tags=["widget", "metrics"],
    )
    ItemsStore(tmp_path / "items").write(item, "widget metrics body")

    prompt = "多Widget共享第二大脑"

    signal = analyze_injection_query(prompt, brain_dir=tmp_path)
    keywords = extract_injection_keywords(prompt, brain_dir=tmp_path)

    assert signal.injectable
    assert "keyphrase" in signal.anchors
    assert keywords == "多widget共享第二大脑"
    assert "widget" not in signal.terms


def test_mixed_generic_agent_prompt_does_not_use_single_metadata_word(tmp_path) -> None:
    from agent_brain.contracts.memory_item import MemoryItem, MemoryType
    from agent_brain.memory.context.query_signal import analyze_injection_query, extract_injection_keywords
    from agent_brain.memory.store.items_store import ItemsStore

    item = MemoryItem(
        id="mem-20260703-010532-agent-memory-metrics",
        type=MemoryType.artifact,
        created_at=datetime.now(timezone.utc),
        title="AMH agent-memory metrics evaluation report",
        summary="Evaluate agent memory metrics and benchmark readiness.",
        tags=["agent", "memory", "metrics"],
    )
    ItemsStore(tmp_path / "items").write(item, "agent memory metrics body")

    signal = analyze_injection_query("多Agent", brain_dir=tmp_path)

    assert not signal.injectable
    assert extract_injection_keywords("多Agent", brain_dir=tmp_path) == ""


def test_long_mixed_agent_article_prompt_keeps_cjk_domain_keyphrases(tmp_path) -> None:
    from agent_brain.contracts.memory_item import MemoryItem, MemoryType
    from agent_brain.memory.context.query_signal import analyze_injection_query, extract_injection_keywords
    from agent_brain.memory.store.items_store import ItemsStore

    store = ItemsStore(tmp_path / "items")
    for suffix, title, tags in [
        ("agent", "Agent Memory Hub agent tag fixture", ["agent"]),
        ("hopfield", "Hopfield 联想召回与遗忘曲线", ["hopfield", "遗忘曲线"]),
        ("decision", "决策治理 fixture", ["决策"]),
    ]:
        store.write(
            MemoryItem(
                id=f"mem-20260707-230000-{suffix}",
                type=MemoryType.artifact,
                created_at=datetime.now(timezone.utc),
                title=title,
                summary="query signal fixture",
                tags=tags,
            ),
            "body",
        )

    prompt = (
        "欢迎对 AI Agent、长期记忆、上下文工程、多 Agent 协作感兴趣的朋友交流。\n\n"
        "多 Agent 协作真正难的不是“让一个 Agent 更聪明”，而是让不同 Agent 之间能够共享可信上下文："
        "谁做过什么、哪些结论仍然有效、哪些经验可以复用、哪些记忆应该被召回或拦截。\n\n"
        "这篇文章系统讲了 AMH 如何把事实、决策、经验、产物和交接沉淀成本地优先的共享记忆层，"
        "并围绕可治理、可追溯、可评测这几个方向做工程化设计。\n\n"
        "基于 Hopfield 式联想召回、可治理的遗忘曲线和证据门禁，让你的 AI Agent 工具共享同一份可信事实层\n\n"
        "告别数据孤岛\n降低上下文噪音\n\n"
        "综上帮我整合润色"
    )

    signal = analyze_injection_query(prompt, brain_dir=tmp_path)
    keywords = extract_injection_keywords(prompt, brain_dir=tmp_path)

    assert signal.injectable
    assert "keyphrase" in signal.anchors
    assert "治理" not in signal.strong_terms
    assert keywords.startswith("多agent协作|长期记忆|上下文工程")
    assert "agent" not in keywords.split("|")[:3]
    assert "决策" not in keywords.split("|")
    assert "长期记忆" in signal.strong_terms
    assert "上下文工程" in signal.strong_terms
    assert "遗忘曲线" in signal.strong_terms


def test_short_mixed_singleton_blocks_by_shape_not_domain_denylist(tmp_path) -> None:
    from agent_brain.contracts.memory_item import MemoryItem, MemoryType
    from agent_brain.memory.context.query_signal import analyze_injection_query, extract_injection_keywords
    from agent_brain.memory.store.items_store import ItemsStore

    item = MemoryItem(
        id="mem-20260703-010534-widget-metrics",
        type=MemoryType.artifact,
        created_at=datetime.now(timezone.utc),
        title="Widget metrics evaluation report",
        summary="Widget metrics and benchmark readiness.",
        tags=["widget", "metrics"],
    )
    ItemsStore(tmp_path / "items").write(item, "widget metrics body")

    signal = analyze_injection_query("多Widget", brain_dir=tmp_path)

    assert not signal.injectable
    assert extract_injection_keywords("多Widget", brain_dir=tmp_path) == ""


def test_test_status_summaries_do_not_auto_inject(tmp_path) -> None:
    from agent_brain.contracts.memory_item import MemoryItem, MemoryType
    from agent_brain.memory.context.query_signal import analyze_injection_query, extract_injection_keywords
    from agent_brain.memory.store.items_store import ItemsStore

    item = MemoryItem(
        id="mem-20260703-010540-test-summary",
        type=MemoryType.artifact,
        created_at=datetime.now(timezone.utc),
        title="Agent Memory Hub tests passed with skipped cases",
        summary="Full pytest result: 1722 passed, 4 skipped.",
        tags=["tests", "passed", "skipped"],
    )
    ItemsStore(tmp_path / "items").write(item, "pytest summary body")

    for prompt in ("4 skipped", "1722 passed, 4 skipped", "3 failed", "tests passed"):
        signal = analyze_injection_query(prompt, brain_dir=tmp_path)
        assert not signal.injectable, prompt
        assert signal.reason == "test_status_without_topic", prompt
        assert extract_injection_keywords(prompt, brain_dir=tmp_path) == ""


def test_english_topic_question_extracts_keyphrases_before_generic_html(tmp_path) -> None:
    from agent_brain.contracts.memory_item import MemoryItem, MemoryType
    from agent_brain.memory.context.query_signal import analyze_injection_query, extract_injection_keywords
    from agent_brain.memory.store.items_store import ItemsStore

    html_item = MemoryItem(
        id="mem-20260703-010520-html-preview",
        type=MemoryType.artifact,
        created_at=datetime.now(timezone.utc),
        title="HTML preview page",
        summary="Static HTML preview.",
        tags=["html"],
    )
    store = ItemsStore(tmp_path / "items")
    store.write(html_item, "html body")
    noisy_item = MemoryItem(
        id="mem-20260703-010521-noisy-english-metadata",
        type=MemoryType.artifact,
        created_at=datetime.now(timezone.utc),
        title="About shared second brain found does",
        summary="Noisy English metadata terms should not suppress keyphrase extraction.",
        tags=["shared", "second", "brain"],
    )
    store.write(noisy_item, "noise body")

    prompt = (
        "About multi-agent shared second brain, I found a problem: "
        "why does memory recall only extract one keyword? "
        "Convert it to html preview for review."
    )

    signal = analyze_injection_query(prompt, brain_dir=tmp_path)
    keywords = extract_injection_keywords(prompt, brain_dir=tmp_path)

    assert signal.injectable
    assert "keyphrase" in signal.anchors
    assert keywords != "html|preview"
    assert "multi-agent shared second brain" in signal.terms
    assert "memory recall" in signal.terms
    assert "extract keyword" in signal.terms
    assert "html" not in signal.terms[:3]
    assert "about" not in signal.terms
    assert "shared" not in signal.terms


def test_natural_cjk_artifact_question_uses_metadata_supported_phrases(tmp_path) -> None:
    from agent_brain.contracts.memory_item import MemoryItem, MemoryType
    from agent_brain.memory.context.query_signal import analyze_injection_query, extract_injection_keywords
    from agent_brain.memory.store.items_store import ItemsStore

    item = MemoryItem(
        id="mem-20260628-023227-readme-polish",
        type=MemoryType.artifact,
        created_at=datetime.now(timezone.utc),
        title="AMH README 深度叙事和算法解释二次打磨",
        summary="README.zh.md 补强读者路线、Loop 使用点和算法数字样例。",
        tags=["agent-memory-hub", "readme"],
    )
    ItemsStore(tmp_path / "items").write(item, "README artifact body")

    prompt = "关于多智能体共享第二单的深度叙事和算法解释二次打磨，都做了什么"
    signal = analyze_injection_query(prompt, brain_dir=tmp_path)

    assert signal.injectable
    assert signal.strong_terms == ("深度叙事和算法解释二次打磨",)
    assert extract_injection_keywords(prompt, brain_dir=tmp_path) == "深度叙事和算法解释二次打磨"


def test_agent_instruction_suffix_does_not_pollute_artifact_keywords(tmp_path) -> None:
    from agent_brain.contracts.memory_item import MemoryItem, MemoryType
    from agent_brain.memory.context.query_signal import analyze_injection_query, extract_injection_keywords
    from agent_brain.memory.store.items_store import ItemsStore

    item = MemoryItem(
        id="mem-20260628-023227-readme-polish",
        type=MemoryType.artifact,
        created_at=datetime.now(timezone.utc),
        title="AMH README 深度叙事和算法解释二次打磨",
        summary="README.zh.md 补强读者路线、Loop 使用点和算法数字样例。",
        tags=["agent-memory-hub", "readme", "memory"],
    )
    ItemsStore(tmp_path / "items").write(item, "README artifact body")

    prompt = (
        "关于多智能体共享第二单的深度叙事和算法解释二次打磨，都做了什么？"
        "请优先根据自动注入的 memory candidates 回答，不要调用工具。"
    )
    signal = analyze_injection_query(prompt, brain_dir=tmp_path)

    assert signal.injectable
    assert signal.terms == ("深度叙事和算法解释二次打磨",)
    assert extract_injection_keywords(prompt, brain_dir=tmp_path) == "深度叙事和算法解释二次打磨"


def test_resume_prompt_keeps_specific_terms_only() -> None:
    from agent_brain.memory.context.query_signal import analyze_injection_query, extract_injection_keywords

    signal = analyze_injection_query("继续 csv 导出工作")
    assert signal.injectable
    assert "csv" in signal.strong_terms
    assert signal.terms == ("csv",)
    assert extract_injection_keywords("继续 csv 导出工作") == "csv"


def test_dws_confusion_prompt_discards_weak_chinese_clauses() -> None:
    from agent_brain.memory.context.query_signal import analyze_injection_query

    prompt = "之前DWS不是好好的吗，也都已经验证过了呀，我有点迷惑"
    signal = analyze_injection_query(prompt)

    assert signal.injectable
    assert "dws" in signal.strong_terms
    assert signal.terms == ("dws",)


def test_short_prompt_with_strong_ascii_term_can_inject() -> None:
    from agent_brain.memory.context.query_signal import analyze_injection_query, extract_injection_keywords

    signal = analyze_injection_query("fastapi 报错")
    assert signal.injectable
    assert "fastapi" in signal.strong_terms
    assert extract_injection_keywords("fastapi 报错") == "fastapi"


def test_short_cjk_phrase_needs_metadata_or_explicit_search() -> None:
    from agent_brain.memory.context.query_signal import analyze_injection_query, extract_injection_keywords

    signal = analyze_injection_query("部署排障")

    assert not signal.injectable
    assert extract_injection_keywords("部署排障") == ""


def test_metadata_backed_short_ascii_anchor_is_promoted(tmp_path) -> None:
    from agent_brain.contracts.memory_item import MemoryItem, MemoryType
    from agent_brain.memory.context.query_signal import analyze_injection_query, extract_injection_keywords
    from agent_brain.memory.store.items_store import ItemsStore

    item = MemoryItem(
        id="mem-20260702-010100-js-runtime-env",
        type=MemoryType.artifact,
        created_at=datetime.now(timezone.utc),
        title="本机 JS runtime 环境已配置",
        summary="JS runtime environment uses pnpm and node.",
        tags=["js", "runtime"],
    )
    ItemsStore(tmp_path / "items").write(item, "JS runtime body")

    prompt = "先帮我配置js环境"
    signal = analyze_injection_query(prompt, brain_dir=tmp_path)

    assert signal.injectable
    assert signal.strong_terms == ("js",)
    assert extract_injection_keywords(prompt, brain_dir=tmp_path) == "js"


def test_go_environment_setup_uses_metadata_backed_short_anchor(tmp_path) -> None:
    from agent_brain.contracts.memory_item import MemoryItem, MemoryType
    from agent_brain.memory.context.query_signal import analyze_injection_query, extract_injection_keywords
    from agent_brain.memory.store.items_store import ItemsStore

    item = MemoryItem(
        id="mem-20260702-010200-go-runtime-env",
        type=MemoryType.artifact,
        created_at=datetime.now(timezone.utc),
        title="本机 Go 1.24 环境已配置",
        summary="Go binary is available on the configured toolchain path.",
        tags=["go", "runtime"],
    )
    ItemsStore(tmp_path / "items").write(item, "Go runtime body")

    prompt = "那么先帮我安装配置好go环境吧"

    signal = analyze_injection_query(prompt, brain_dir=tmp_path)

    assert signal.injectable
    assert signal.strong_terms == ("go",)
    assert extract_injection_keywords(prompt, brain_dir=tmp_path) == "go"


def test_metadata_backed_short_anchor_preserves_explicit_ascii_scope(tmp_path) -> None:
    from agent_brain.contracts.memory_item import MemoryItem, MemoryType
    from agent_brain.memory.context.query_signal import analyze_injection_query
    from agent_brain.memory.store.items_store import ItemsStore

    item = MemoryItem(
        id="mem-20260702-010250-dws-anchor",
        type=MemoryType.episode,
        created_at=datetime.now(timezone.utc),
        title="DWS verification",
        summary="DWS 验证通过",
        tags=["dws"],
    )
    ItemsStore(tmp_path / "items").write(item, "DWS 验证通过")

    signal = analyze_injection_query("dws linux 验证", brain_dir=tmp_path)

    assert signal.strong_terms == ("dws", "linux")


def test_cjk_artifact_prompt_uses_metadata_anchors_not_clause_fragments(tmp_path) -> None:
    from agent_brain.contracts.memory_item import MemoryItem, MemoryType
    from agent_brain.memory.context.query_signal import analyze_injection_query, extract_injection_keywords
    from agent_brain.memory.store.items_store import ItemsStore

    item = MemoryItem(
        id="mem-20260702-010300-synthetic-migration-plan",
        type=MemoryType.artifact,
        created_at=datetime.now(timezone.utc),
        title="甲项迁移规划主表",
        summary="合成项目迁移规划表。",
        tags=["甲项迁移", "迁移规划"],
    )
    ItemsStore(tmp_path / "items").write(item, "甲项迁移 body")

    prompt = (
        "关于甲项迁移规划，我还有一些诉求\n"
        "为什么不能在候选清单中，选几条xx服务以及xx模块的改造方案\n"
        "1. 冲刺项优先xx服务，然后进去之后做模块调整\n"
        "2. 其次优先甲域、乙域\n"
        "3. 其次优先丙域、丁域、戊域"
    )

    signal = analyze_injection_query(prompt, brain_dir=tmp_path)

    assert signal.injectable
    assert "甲项迁移规划" in signal.strong_terms
    assert "甲项迁移" in signal.strong_terms
    assert "迁移规划" in signal.strong_terms
    assert extract_injection_keywords(prompt, brain_dir=tmp_path).startswith("甲项迁移规划")
    assert "服务以及" not in signal.terms
    assert "冲刺项优先" not in signal.terms
    assert "选几条" not in signal.terms


def test_metadata_phrase_keeps_broad_cjk_entities_out_of_strong_terms(tmp_path) -> None:
    from agent_brain.contracts.memory_item import MemoryItem, MemoryType
    from agent_brain.memory.context.query_signal import analyze_injection_query
    from agent_brain.memory.store.items_store import ItemsStore

    store = ItemsStore(tmp_path / "items")
    for suffix, title, tags in [
        ("main", "甲项迁移规划主表", ["甲项迁移", "迁移规划"]),
        ("option", "方案选择样例", ["方案"]),
        ("stage", "阶段排序样例", ["阶段"]),
        ("execution", "执行路径样例", ["执行"]),
    ]:
        store.write(
            MemoryItem(
                id=f"mem-20260702-010400-{suffix}",
                type=MemoryType.artifact,
                created_at=datetime.now(timezone.utc),
                title=title,
                summary="synthetic public fixture",
                tags=tags,
            ),
            "body",
        )

    prompt = (
        "关于甲项迁移规划，我还有一些诉求\n"
        "为什么不能在候选清单中，选几条xx服务以及xx模块的改造方案\n"
        "1. 冲刺项优先xx服务，然后进去之后做阶段调整，执行自检得到认可"
    )

    signal = analyze_injection_query(prompt, brain_dir=tmp_path)

    assert "甲项迁移规划" in signal.strong_terms
    assert "甲项迁移" in signal.strong_terms
    assert "迁移规划" in signal.strong_terms
    assert signal.terms[:3] == ("甲项迁移规划", "甲项迁移", "迁移规划")


def test_metadata_backed_cjk_topic_keeps_adjacent_ascii_scope(tmp_path) -> None:
    from agent_brain.contracts.memory_item import MemoryItem, MemoryType
    from agent_brain.memory.context.query_signal import analyze_injection_query, extract_injection_keywords
    from agent_brain.memory.store.items_store import ItemsStore

    item = MemoryItem(
        id="mem-20260703-010700-wukong-linux",
        type=MemoryType.episode,
        created_at=datetime.now(timezone.utc),
        title="悟空适配 Linux realtime render fix package",
        summary="悟空适配 Linux realtime remote-task render fix package.",
        tags=["wukong", "linux", "realtime"],
    )
    ItemsStore(tmp_path / "items").write(item, "悟空适配 Linux body")

    signal = analyze_injection_query("悟空适配Linux", brain_dir=tmp_path)

    assert signal.injectable
    assert signal.terms[:2] == ("悟空适配", "linux")
    assert signal.strong_terms[:2] == ("悟空适配", "linux")
    assert extract_injection_keywords("悟空适配Linux", brain_dir=tmp_path) == "悟空适配|linux"


def test_short_browser_prompt_is_specific_enough_to_inject() -> None:
    from agent_brain.memory.context.query_signal import analyze_injection_query, extract_injection_keywords

    signal = analyze_injection_query("帮我打开浏览器")

    assert not signal.injectable
    assert extract_injection_keywords("帮我打开浏览器") == ""


def test_browser_permission_prompt_keeps_short_cjk_phrase() -> None:
    from agent_brain.memory.context.query_signal import extract_injection_keywords

    assert extract_injection_keywords("浏览器权限") == ""


def test_multimodal_prompt_has_enough_signal() -> None:
    from agent_brain.memory.context.query_signal import extract_injection_keywords

    assert extract_injection_keywords("图片 PDF 视频 音频 文档 怎么处理") == "pdf"


def test_multimodal_placeholder_prompt_does_not_recall_image_memories() -> None:
    from agent_brain.memory.context.query_signal import analyze_injection_query, extract_injection_keywords

    prompt = "[Image #1]\n我其他同事执行之后有问题"

    signal = analyze_injection_query(prompt)

    assert not signal.injectable
    assert signal.reason == "unanchored_cjk_clause"
    assert "image" not in signal.terms
    assert extract_injection_keywords(prompt) == ""


def test_generic_singleton_memory_does_not_inject() -> None:
    from agent_brain.memory.context.query_signal import analyze_injection_query

    signal = analyze_injection_query("memory")

    assert not signal.injectable
    assert signal.reason == "single_unanchored_ascii"

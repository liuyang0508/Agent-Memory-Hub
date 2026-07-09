"""Prompt signal extraction for automatic memory injection."""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

from agent_brain.memory.context.prompt_normalization import normalize_hook_prompt_for_recall
from agent_brain.memory.context.query_intent import (
    file_or_module_terms,
    weak_intent_without_anchor,
)

_TOKEN_RE = re.compile(r"[A-Za-z0-9_+.-]+|[\u4e00-\u9fff]+", re.UNICODE)
_JSONISH_FIELD_RE = re.compile(
    r"""["']([A-Za-z][A-Za-z0-9_+.-]{2,})["']\s*:""",
)
_JS_OBJECT_FIELD_RE = re.compile(
    r"""(?<=[{,])\s*([A-Za-z][A-Za-z0-9_+.-]{2,})\s*:""",
)
_CJK_METADATA_SPLIT_RE = re.compile(
    r"[\s:：,，.。?？!！/\\（）()\[\]【】「」『』、]+"
)
_CJK_TOPIC_RE = re.compile(r"关于([\u4e00-\u9fff]{4,24})(?:[\s:：,，.。?？!！/\\（）()\[\]【】「」『』、]+|$)")
_MIXED_TOPIC_RE = re.compile(
    r"关于\s*((?:[A-Za-z][A-Za-z0-9_+.-]*\s*){1,3}(?:的)?\s*[\u4e00-\u9fff]{2,12})"
)
_MIXED_COMPOUND_RE = re.compile(
    r"(?<![A-Za-z0-9_+.\-\u4e00-\u9fff])"
    r"([\u4e00-\u9fff]{0,4}(?:[A-Za-z][A-Za-z0-9_+.-]*\s*){1,3}"
    r"(?:的[\u4e00-\u9fff]{2,12}|[\u4e00-\u9fff]{2,14}))"
    r"(?![A-Za-z0-9_+.-])",
)
_CJK_CONTEXT_RE = re.compile(r"在([\u4e00-\u9fff]{2,12})的时候")
_CJK_KEYPHRASE_TRAILING_NOISE_RE = re.compile(
    r"(没有.*|不能.*|是否.*|是不是.*|不是.*|我发现.*|都做了什么|做了什么|是什么|为什么|怎么|如何|哪些|多少|"
    r"时候|的问题|问题|相关|诉求|审核|预览|给我|一下|的)$"
)
_CJK_RELATION_FOCUS_RE = re.compile(
    r"(?:跟|和|与|同|对比|比较)"
    r"((?:其他)?[\u4e00-\u9fff]{2,8}?)"
    r"(?=基于|按照|依据|依照|按|对齐|统一|一致|相同|一样|比较|对比|$)"
)
_CJK_BASELINE_FOCUS_RE = re.compile(
    r"(?:基于|按照|依据|依照|按|对齐)"
    r"([\u4e00-\u9fff]{2,12})"
    r"(?=$|[\s:：,，.。?？!！/\\（）()\[\]【】「」『』、])"
)
_GENERIC_ASCII_ANCHORS = {
    "agent",
    "ai",
    "html",
    "question",
    "preview",
}
_GENERIC_METADATA_ENTITY_TERMS = {
    "agent",
    "ai",
    "决策",
    "经验",
    "工具",
    "问题",
}
_DOMAIN_KEYPHRASE_PRIORITY = (
    "多agent协作",
    "多智能体共享第二大脑",
    "长期记忆",
    "上下文工程",
    "共享可信上下文",
    "可信上下文",
    "记忆召回",
    "共享记忆层",
    "联想召回",
    "遗忘曲线",
    "证据门禁",
    "可信事实层",
    "数据孤岛",
    "上下文噪音",
    "记忆维护",
    "记忆治理",
    "记忆注入",
)
_CJK_TASK_ANCHOR_TERMS = (
    "关键词",
    "表格",
    "治理",
    "文案",
)
_ASCII_TASK_ANCHOR_TERMS = (
    "github",
    "commit",
)
_RUNTIME_ASCII_EXACT_TERMS = {
    "agent_brain",
    "hook",
    "hookspecificoutput",
    "mcp",
    "ocr",
    "userpromptsubmit",
}
_RUNTIME_ASCII_CONTEXT_RE = re.compile(
    r"(hook|mcp|ocr|pytest|trace|json|日志|截图|报错|错误)",
    re.IGNORECASE,
)
_CJK_TASK_ANCHOR_CONTEXT_TERMS = (
    "长任务",
    "提取",
    "关键词",
    "表格",
    "治理",
    "提交",
    "规范",
    "文案",
    "丢失",
)
_TEST_STATUS_ASCII_TERMS = {
    "error",
    "errors",
    "failed",
    "failures",
    "passed",
    "skipped",
    "warning",
    "warnings",
    "xfailed",
    "xpassed",
}
_TEST_STATUS_CONTEXT_ASCII_TERMS = {
    "pytest",
    "test",
    "tests",
}
_TEST_STATUS_COMMAND_ASCII_TERMS = {
    "black",
    "check",
    "checks",
    "eslint",
    "jest",
    "mypy",
    "npm",
    "pnpm",
    "prettier",
    "pyright",
    "pytest",
    "ruff",
    "uv",
    "vitest",
    "yarn",
}
_TEST_STATUS_DURATION_RE = re.compile(r"^\d+(?:\.\d+)?s$")
_TEST_STATUS_RESULT_RE = re.compile(
    r"\b\d+\s+(?:passed|failed|skipped|warnings?|errors?|failures?|xfailed|xpassed)\b",
    re.IGNORECASE,
)
_TEST_STATUS_COMMAND_RE = re.compile(
    r"\b(?:black|eslint|jest|mypy|npm|pnpm|prettier|pyright|pytest|ruff|uv|vitest|yarn)\b"
    r".*\b(?:passed|failed|skipped|warnings?|errors?|failures?|xfailed|xpassed)\b",
    re.IGNORECASE | re.DOTALL,
)
_TEST_STATUS_FOLLOWUP_INTENT_RE = re.compile(
    r"\b(?:debug|explain|fix|handle|how|investigate|reason|resolve|triage|what|why)\b",
    re.IGNORECASE,
)
_ASCII_TOPIC_RE = re.compile(
    r"\babout\s+([A-Za-z0-9_+./-]+(?:\s+[A-Za-z0-9_+./-]+){0,7})"
    r"(?=,|:|\.|\?|!|$)",
    re.IGNORECASE,
)
_ASCII_MEMORY_RECALL_RE = re.compile(r"\bmemory\s+recall\b", re.IGNORECASE)
_ASCII_EXTRACT_KEYWORD_RE = re.compile(
    r"\bextract(?:s|ed|ing)?\s+"
    r"(?:(?:only|just)\s+)?(?:(?:one|a|the|some|single)\s+)?"
    r"keywords?\b",
    re.IGNORECASE,
)
_ASCII_KEYPHRASE_TRAILING_NOISE_RE = re.compile(
    r"\b(?:i|we)\s+found\s+a\s+problem.*$|"
    r"\b(?:why|what|how|which|when|where|does|do|did|can|could|should|would)\b.*$|"
    r"\b(?:convert|render|preview|review)\b.*$",
    re.IGNORECASE,
)
_ASCII_KEYPHRASE_STOPWORDS = {
    "a",
    "an",
    "and",
    "for",
    "it",
    "of",
    "only",
    "the",
    "to",
}
_ASCII_LITERAL_STOPWORDS = {
    "false",
    "null",
    "none",
    "true",
}
_GENERIC_COMMAND_TERMS = {
    "change",
    "changes",
    "current",
    "review",
    "run",
    "status",
}
_SLASH_COMMAND_PROMPT_RE = re.compile(
    r"^\s*(?:please\s+)?(?:run\s+)?/[A-Za-z][A-Za-z0-9_-]*"
    r"(?:\s+(?:on\s+)?(?:my\s+)?(?:current\s+)?changes?)?\s*$",
    re.IGNORECASE,
)
_CJK_CONFIRMATION_OPERATOR_RE = re.compile(
    r"(?:是否|是不是|有没有|全部|吗)"
)
_CJK_NON_SUBSTANTIVE_WEAK_TERMS = {
    "了",
    "吗",
    "呢",
    "吧",
    "的",
    "这个",
    "那个",
    "这些",
    "那些",
}

_METADATA_CACHE_VERSION = 4
_METADATA_CACHE_PATH = "index/query-signal-metadata-cache.json"


@dataclass(frozen=True)
class QuerySignal:
    terms: tuple[str, ...]
    strong_terms: tuple[str, ...]
    weak_terms: tuple[str, ...]
    injectable: bool
    reason: str
    specificity: float
    decision: str = "block"
    anchors: tuple[str, ...] = ()
    trace: tuple[str, ...] = ()


@dataclass(frozen=True)
class QueryGateGapEvidence:
    reason: str
    evidence: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "reason": self.reason,
            "evidence": list(self.evidence),
        }


@dataclass(frozen=True)
class QuerySignalDiagnostics:
    """Machine-readable explanation of query-signal admission."""

    prompt: str
    keywords: str
    decision: str
    reason: str
    injectable: bool
    terms: tuple[str, ...]
    kept_terms: tuple[str, ...]
    strong_terms: tuple[str, ...]
    weak_terms: tuple[str, ...]
    weak_noise: tuple[str, ...]
    anchors: tuple[str, ...]
    specificity: float
    trace: tuple[str, ...]

    @property
    def blocked(self) -> bool:
        return not self.injectable

    def to_dict(self) -> dict[str, object]:
        return {
            "prompt": self.prompt,
            "keywords": self.keywords,
            "decision": self.decision,
            "reason": self.reason,
            "injectable": self.injectable,
            "blocked": self.blocked,
            "terms": list(self.terms),
            "kept_terms": list(self.kept_terms),
            "strong_terms": list(self.strong_terms),
            "weak_terms": list(self.weak_terms),
            "weak_noise": list(self.weak_noise),
            "anchors": list(self.anchors),
            "specificity": self.specificity,
            "trace": list(self.trace),
        }


def extract_injection_keywords(
    prompt: str,
    *,
    max_keywords: int = 6,
    brain_dir: Path | None = None,
) -> str:
    """Return pipe-separated query keywords, or empty string when signal is too weak.

    The UserPromptSubmit hook runs on every user turn, including short connective
    phrases such as "就像". For those prompts, searching memory is worse than
    doing nothing because any returned item can pollute the next reasoning turn.
    """
    signal = analyze_injection_query(prompt, brain_dir=brain_dir)
    if not signal.injectable:
        return ""
    return "|".join(signal.terms[:max_keywords])


def diagnose_injection_query(
    prompt: str,
    *,
    max_keywords: int = 6,
    brain_dir: Path | None = None,
) -> QuerySignalDiagnostics:
    signal = analyze_injection_query(prompt, brain_dir=brain_dir)
    keywords = "|".join(signal.terms[:max_keywords]) if signal.injectable else ""
    return QuerySignalDiagnostics(
        prompt=prompt,
        keywords=keywords,
        decision=signal.decision,
        reason=signal.reason,
        injectable=signal.injectable,
        terms=signal.terms,
        kept_terms=signal.terms if signal.injectable else (),
        strong_terms=signal.strong_terms,
        weak_terms=signal.weak_terms,
        weak_noise=_weak_noise_from_trace(signal.trace),
        anchors=signal.anchors,
        specificity=signal.specificity,
        trace=signal.trace,
    )


def query_gate_gap_evidence(
    prompt: str,
    *,
    brain_dir: Path | None = None,
) -> QueryGateGapEvidence | None:
    """Return bounded gap evidence when the query gate blocks specific terms.

    Pure connective prompts such as ``就像`` still produce no gap; otherwise the
    gap stream would be dominated by harmless conversational turns.
    """
    signal = analyze_injection_query(prompt, brain_dir=brain_dir)
    if signal.injectable:
        return None
    if signal.reason != "too_weak" or not signal.terms:
        return None
    return QueryGateGapEvidence(
        reason="query_not_injectable",
        evidence=(
            f"query_signal:{signal.reason}",
            "terms=" + "|".join(signal.terms),
            f"specificity={signal.specificity:.2f}",
        ),
    )


def analyze_injection_query(
    prompt: str,
    *,
    brain_dir: Path | None = None,
) -> QuerySignal:
    prompt = normalize_hook_prompt_for_recall(prompt)
    terms, strong_terms, weak_terms = _candidate_terms(prompt)
    anchors: list[str] = []
    trace: list[str] = []
    if _short_weak_prompt_without_terms(prompt, terms, weak_terms) and not _has_item_metadata(brain_dir):
        specificity = _specificity(terms, strong_terms)
        trace.extend(_weak_intent_traces(weak_terms))
        return QuerySignal(
            tuple(terms), tuple(strong_terms), tuple(weak_terms),
            False, "too_weak", specificity,
            "block", (), tuple([*trace, "block:too_weak"]),
        )
    metadata_terms = _metadata_supported_prompt_terms(prompt, brain_dir)
    for term in metadata_terms:
        _append_unique(terms, term)
        _append_unique(strong_terms, term)
    if metadata_terms:
        anchors.append("metadata_phrase")
        trace.append("metadata_terms=" + "|".join(metadata_terms))
    known_entity_terms = _known_entity_terms(prompt, terms, brain_dir)
    if known_entity_terms:
        for term in known_entity_terms:
            _append_unique(terms, term)
        for term in known_entity_terms:
            _append_unique(strong_terms, term)
        anchors.append("metadata_entity")
        trace.append("metadata_entities=" + "|".join(known_entity_terms))
    file_terms = file_or_module_terms(prompt)
    for term in file_terms:
        _append_unique(terms, term)
        _append_unique(strong_terms, term)
    _remove_file_stem_duplicates(terms, file_terms)
    _remove_file_stem_duplicates(strong_terms, file_terms)
    if file_terms:
        anchors.append("file_or_module")
        trace.append("file_terms=" + "|".join(file_terms))
    json_field_terms = _jsonish_field_terms(prompt)
    for term in json_field_terms:
        _append_unique(terms, term)
        _append_unique(strong_terms, term)
    if json_field_terms:
        anchors.append("json_field")
        trace.append("json_field_terms=" + "|".join(json_field_terms))
    runtime_ascii_terms = _runtime_ascii_terms(prompt)
    for term in runtime_ascii_terms:
        _append_unique(terms, term)
        _append_unique(strong_terms, term)
    if runtime_ascii_terms:
        anchors.append("runtime_ascii")
        trace.append("runtime_ascii_terms=" + "|".join(runtime_ascii_terms))
    keyphrase_terms = _prompt_keyphrase_terms(prompt)
    keyphrase_terms = _metadata_superseded_keyphrase_terms(keyphrase_terms, metadata_terms)
    adjacent_ascii_scope_terms = _adjacent_ascii_scope_terms(
        prompt,
        [*keyphrase_terms, *metadata_terms, *known_entity_terms, *json_field_terms, *runtime_ascii_terms],
    )
    for term in adjacent_ascii_scope_terms:
        _append_unique(terms, term)
        _append_unique(strong_terms, term)
    for term in keyphrase_terms:
        _append_unique(terms, term)
        _append_unique(strong_terms, term)
    if keyphrase_terms:
        anchors.append("keyphrase")
        trace.append("keyphrase_terms=" + "|".join(keyphrase_terms))
    cjk_question_focus_terms = _anchored_cjk_question_focus_terms(
        prompt,
        [
            *keyphrase_terms,
            *metadata_terms,
            *adjacent_ascii_scope_terms,
            *file_terms,
            *known_entity_terms,
            *runtime_ascii_terms,
        ],
    )
    for term in cjk_question_focus_terms:
        _append_unique(terms, term)
        _append_unique(strong_terms, term)
    if cjk_question_focus_terms:
        anchors.append("cjk_question_focus")
        trace.append("cjk_question_focus_terms=" + "|".join(cjk_question_focus_terms))
    structured_cjk_focus_terms = _structured_cjk_focus_terms(
        prompt,
        [
            *json_field_terms,
            *keyphrase_terms,
            *metadata_terms,
            *adjacent_ascii_scope_terms,
            *file_terms,
            *known_entity_terms,
            *runtime_ascii_terms,
        ],
    )
    for term in structured_cjk_focus_terms:
        _append_unique(terms, term)
        _append_unique(strong_terms, term)
    if structured_cjk_focus_terms:
        anchors.append("structured_cjk_focus")
        trace.append("structured_cjk_focus_terms=" + "|".join(structured_cjk_focus_terms))
    task_anchor_terms = _task_anchor_terms(
        prompt,
        [
            *json_field_terms,
            *keyphrase_terms,
            *metadata_terms,
            *adjacent_ascii_scope_terms,
            *file_terms,
            *known_entity_terms,
            *runtime_ascii_terms,
            *cjk_question_focus_terms,
            *structured_cjk_focus_terms,
        ],
    )
    for term in task_anchor_terms:
        _append_unique(terms, term)
        _append_unique(strong_terms, term)
    if task_anchor_terms:
        anchors.append("task_anchor")
        trace.append("task_anchor_terms=" + "|".join(task_anchor_terms))
    leading_task_anchor_terms = _leading_task_anchor_terms(task_anchor_terms, keyphrase_terms)
    trailing_task_anchor_terms = [
        term for term in task_anchor_terms if term not in leading_task_anchor_terms
    ]
    precise_anchor_terms = [
        *structured_cjk_focus_terms,
        *json_field_terms,
        *runtime_ascii_terms,
        *leading_task_anchor_terms,
        *keyphrase_terms,
        *metadata_terms,
        *adjacent_ascii_scope_terms,
        *file_terms,
        *known_entity_terms,
        *cjk_question_focus_terms,
        *trailing_task_anchor_terms,
    ]
    strong_focus_terms = [
        *structured_cjk_focus_terms,
        *json_field_terms,
        *runtime_ascii_terms,
        *leading_task_anchor_terms,
        *keyphrase_terms,
        *metadata_terms,
        *adjacent_ascii_scope_terms,
        *file_terms,
        *cjk_question_focus_terms,
        *trailing_task_anchor_terms,
    ] or known_entity_terms
    _promote_terms(terms, precise_anchor_terms)
    _focus_terms_on_precise_anchor(terms, precise_anchor_terms)
    _focus_strong_terms_on_precise_anchor(strong_terms, strong_focus_terms)
    _focus_unanchored_terms_on_ascii_scope(terms, strong_terms, anchors)
    if keyphrase_terms:
        _prune_ascii_terms_after_keyphrases(terms, keyphrase_terms, protected_terms=task_anchor_terms)
        _prune_ascii_terms_after_keyphrases(strong_terms, keyphrase_terms, protected_terms=task_anchor_terms)
    trace.extend(_weak_intent_traces(weak_terms))
    specificity = _specificity(terms, strong_terms)
    anchors_tuple = tuple(dict.fromkeys(anchors))
    if not terms:
        return QuerySignal(
            (), (), tuple(weak_terms),
            False, "too_weak", specificity,
            "block", anchors_tuple, tuple([*trace, "block:too_weak"]),
        )
    if weak_intent_without_anchor(prompt, list(anchors_tuple)):
        return QuerySignal(
            tuple(terms), tuple(strong_terms), tuple(weak_terms),
            False, "weak_intent_without_anchor", specificity,
            "block", anchors_tuple, tuple([*trace, "block:weak_intent_without_anchor"]),
        )
    if _generic_format_without_topic(terms, keyphrase_terms, file_terms, known_entity_terms):
        return QuerySignal(
            tuple(terms), tuple(strong_terms), tuple(weak_terms),
            False, "generic_format_without_topic", specificity,
            "block", anchors_tuple, tuple([*trace, "block:generic_format_without_topic"]),
        )
    if _test_status_without_topic(prompt, terms, keyphrase_terms, file_terms, known_entity_terms):
        return QuerySignal(
            tuple(terms), tuple(strong_terms), tuple(weak_terms),
            False, "test_status_without_topic", specificity,
            "block", anchors_tuple, tuple([*trace, "block:test_status_without_topic"]),
        )
    if _generic_command_without_topic(prompt, terms, anchors_tuple):
        return QuerySignal(
            tuple(terms), tuple(strong_terms), tuple(weak_terms),
            False, "generic_command_without_topic", specificity,
            "block", anchors_tuple, tuple([*trace, "block:generic_command_without_topic"]),
        )
    if _unanchored_mixed_scope_without_semantic_anchor(prompt, terms, anchors_tuple, weak_terms):
        return QuerySignal(
            tuple(terms), tuple(strong_terms), tuple(weak_terms),
            False, "unanchored_mixed_scope", specificity,
            "block", anchors_tuple, tuple([*trace, "block:unanchored_mixed_scope"]),
        )
    if not anchors_tuple and _looks_like_unanchored_cjk_clause_prompt(prompt, terms):
        return QuerySignal(
            tuple(terms), tuple(strong_terms), tuple(weak_terms),
            False, "unanchored_cjk_clause", specificity,
            "block", anchors_tuple, tuple([*trace, "block:unanchored_cjk_clause"]),
        )
    if _single_unanchored_ascii_term(prompt, terms, anchors_tuple, weak_terms):
        return QuerySignal(
            tuple(terms), tuple(strong_terms), tuple(weak_terms),
            False, "single_unanchored_ascii", specificity,
            "block", anchors_tuple, tuple([*trace, "block:single_unanchored_ascii"]),
        )
    if not strong_terms and specificity < 1.0:
        return QuerySignal(
            tuple(terms), tuple(strong_terms), tuple(weak_terms),
            False, "too_weak", specificity,
            "block", anchors_tuple, tuple([*trace, "block:too_weak"]),
        )
    return QuerySignal(
        tuple(terms), tuple(strong_terms), tuple(weak_terms),
        True, "ok", specificity,
        "inject_allowed", anchors_tuple, tuple([*trace, "decision:inject_allowed"]),
    )


def _candidate_terms(prompt: str) -> tuple[list[str], list[str], list[str]]:
    seen: set[str] = set()
    tokens: list[str] = []
    strong: list[str] = []
    weak: list[str] = []
    for raw in _TOKEN_RE.findall(prompt.lower()):
        token = raw.strip("._-+")
        if _is_weak_intent_token(token):
            if token:
                weak.append(token)
            continue
        expanded = _expand_token(token)
        if not expanded:
            if token and token not in _ASCII_LITERAL_STOPWORDS:
                weak.append(token)
            continue
        for candidate in expanded:
            if candidate in seen:
                continue
            seen.add(candidate)
            tokens.append(candidate)
            if _is_strong_term(candidate):
                strong.append(candidate)
    return tokens, strong, weak


def _metadata_supported_prompt_terms(prompt: str, brain_dir: Path | None) -> list[str]:
    """Recover CJK intent phrases that are proven by existing memory metadata.

    The normal gate deliberately discards long CJK clauses because they are
    often conversational noise.  That becomes too strict for artifact questions
    such as "关于 ... 深度叙事和算法解释二次打磨，都做了什么", where the
    useful phrases are embedded inside one long CJK token.  Only promote
    phrases that also appear in item metadata, so generic long chatter remains
    blocked.
    """
    if brain_dir is None:
        return []
    prompt_lower = prompt.lower()
    matches: dict[str, int] = {}
    cache = _metadata_cache(brain_dir)
    if not cache:
        return []
    for phrase in cache.get("phrases", []):
        if not isinstance(phrase, str):
            continue
        if phrase.strip().lower() in _ASCII_LITERAL_STOPWORDS:
            continue
        if _is_generic_ascii_anchor(phrase) or _ascii_phrase_embedded_in_cjk_compound(prompt_lower, phrase):
            continue
        pos = _metadata_phrase_position(prompt_lower, phrase)
        if pos >= 0:
            matches[phrase] = pos
    return [
        phrase
        for phrase, _pos in sorted(
            matches.items(),
            key=lambda item: (item[1], -len(item[0]), item[0]),
        )
    ]


def _metadata_cache(brain_dir: Path | None) -> dict[str, object] | None:
    if brain_dir is None:
        return None
    brain_dir = Path(brain_dir)
    items_dir = brain_dir / "items"
    fingerprint = _items_fingerprint(items_dir)
    cache_path = brain_dir / _METADATA_CACHE_PATH
    cached = _read_metadata_cache(cache_path, fingerprint)
    if cached is not None:
        return cached
    return _rebuild_metadata_cache(items_dir, cache_path, fingerprint)


def _read_metadata_cache(
    cache_path: Path,
    fingerprint: dict[str, int],
) -> dict[str, object] | None:
    try:
        payload = json.loads(cache_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    if payload.get("version") != _METADATA_CACHE_VERSION:
        return None
    if payload.get("fingerprint") != fingerprint:
        return None
    return payload


def _rebuild_metadata_cache(
    items_dir: Path,
    cache_path: Path,
    fingerprint: dict[str, int],
) -> dict[str, object] | None:
    phrases: set[str] = set()
    entity_scores: dict[str, int] = {}
    entity_sources: dict[str, set[str]] = {}
    try:
        from agent_brain.memory.store.items_store import ItemsStore

        for item, _body in ItemsStore(items_dir).iter_all():
            fields = [
                item.title,
                item.summary,
                str(item.project or ""),
                *[str(tag) for tag in item.tags],
            ]
            has_ascii_alias = any(_is_ascii_alias(str(tag)) for tag in item.tags)
            for field in fields:
                phrases.update(_metadata_search_phrases(str(field).lower()))
            _add_entity_score(
                entity_scores,
                str(item.project or ""),
                2,
                source="project",
                sources=entity_sources,
            )
            for tag in item.tags:
                tag_term = str(tag).strip().lower()
                source = (
                    "tag_with_ascii_alias"
                    if has_ascii_alias and not _is_ascii(tag_term) and len(tag_term) <= 2
                    else "tag"
                )
                _add_entity_score(
                    entity_scores,
                    str(tag),
                    _entity_field_weight(str(tag), base=2, has_ascii_alias=has_ascii_alias),
                    source=source,
                    sources=entity_sources,
                )
            for entity in _metadata_title_entity_candidates(str(item.title)):
                _add_entity_score(
                    entity_scores,
                    entity,
                    1,
                    source="title",
                    sources=entity_sources,
                )
    except Exception:
        return None
    payload: dict[str, object] = {
        "version": _METADATA_CACHE_VERSION,
        "fingerprint": fingerprint,
        "phrases": sorted(phrases),
        "entity_scores": dict(sorted(entity_scores.items())),
        "entity_sources": {
            term: sorted(sources)
            for term, sources in sorted(entity_sources.items())
        },
    }
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    except OSError:
        pass
    return payload


def _items_fingerprint(items_dir: Path) -> dict[str, int]:
    count = 0
    max_mtime_ns = 0
    total_size = 0
    try:
        paths = sorted(items_dir.rglob("*.md"))
    except OSError:
        paths = []
    for path in paths:
        try:
            rel_parts = path.relative_to(items_dir).parts
            if "archived" in rel_parts[:-1]:
                continue
            stat = path.stat()
        except OSError:
            continue
        count += 1
        max_mtime_ns = max(max_mtime_ns, stat.st_mtime_ns)
        total_size += stat.st_size
    return {
        "count": count,
        "max_mtime_ns": max_mtime_ns,
        "total_size": total_size,
    }


def _metadata_search_phrases(text: str) -> list[str]:
    phrases: list[str] = []
    seen: set[str] = set()

    def add(phrase: str) -> None:
        phrase = phrase.strip("._-+ ")
        if not phrase or phrase in seen:
            return
        if _is_ascii(phrase):
            if phrase.lower() in _ASCII_LITERAL_STOPWORDS:
                return
            if len(phrase) < 4:
                return
        elif len(phrase) < 4:
            return
        seen.add(phrase)
        phrases.append(phrase)

    for raw in _TOKEN_RE.findall(text):
        token = raw.strip("._-+")
        if not token:
            continue
        if _is_ascii(token):
            add(token)
            continue
        for part in _CJK_METADATA_SPLIT_RE.split(token):
            part = part.strip()
            if not part:
                continue
            if len(part) <= 24:
                add(part)
                continue
            for index in range(0, len(part) - 3):
                add(part[index:index + 4])
    return phrases


def _metadata_phrase_position(prompt_lower: str, phrase: str) -> int:
    if _is_ascii(phrase):
        pattern = r"(?<![a-z0-9_+.-])" + re.escape(phrase.lower()) + r"(?![a-z0-9_+.-])"
        match = re.search(pattern, prompt_lower)
        return -1 if match is None else match.start()
    return prompt_lower.find(phrase)


def _ascii_phrase_embedded_in_cjk_compound(prompt_lower: str, phrase: str) -> bool:
    if not _is_ascii(phrase):
        return False
    pattern = r"(?<![a-z0-9_+.-])" + re.escape(phrase.lower()) + r"(?![a-z0-9_+.-])"
    for match in re.finditer(pattern, prompt_lower):
        before = prompt_lower[match.start() - 1] if match.start() > 0 else ""
        after = prompt_lower[match.end()] if match.end() < len(prompt_lower) else ""
        if _is_cjk_char(before):
            return True
        if _is_cjk_char(after) and after not in {"呢", "吗", "吧"}:
            return True
    return False


def _prompt_keyphrase_terms(prompt: str) -> list[str]:
    terms: list[str] = []

    for term in _cjk_keyphrase_terms(prompt):
        _append_unique(terms, term)

    for term in _ascii_keyphrase_terms(prompt):
        _append_unique(terms, term)

    return terms


def _jsonish_field_terms(prompt: str) -> list[str]:
    positions: dict[str, int] = {}
    for pattern in (_JSONISH_FIELD_RE, _JS_OBJECT_FIELD_RE):
        for match in pattern.finditer(prompt):
            term = _normalize_jsonish_identifier(match.group(1))
            if not _is_jsonish_field_anchor(term):
                continue
            positions.setdefault(term, match.start())
    return [
        term
        for term, _pos in sorted(
            positions.items(),
            key=lambda item: (-len(item[0]), item[1], item[0]),
        )
    ]


def _normalize_jsonish_identifier(raw: str) -> str:
    return raw.strip("._-+").lower()


def _is_jsonish_field_anchor(term: str) -> bool:
    if not term or not _is_ascii(term):
        return False
    if term in _ASCII_LITERAL_STOPWORDS:
        return False
    if not _is_ascii_candidate(term):
        return False
    return any(ch.isalpha() for ch in term)


def _runtime_ascii_terms(prompt: str) -> list[str]:
    terms: list[str] = []
    has_runtime_context = bool(_RUNTIME_ASCII_CONTEXT_RE.search(prompt))
    for match in re.finditer(r"(?<![A-Za-z0-9_+.-])([A-Za-z][A-Za-z0-9_+.-]{2,})(?![A-Za-z0-9_+.-])", prompt):
        raw = match.group(1).strip("._-+")
        normalized = raw.lower()
        if not _is_runtime_ascii_anchor(raw, has_runtime_context=has_runtime_context):
            continue
        _append_unique(terms, normalized)
    return terms


def _is_runtime_ascii_anchor(raw: str, *, has_runtime_context: bool) -> bool:
    normalized = raw.strip("._-+").lower()
    if not _is_ascii_candidate(normalized):
        return False
    if normalized in _GENERIC_ASCII_ANCHORS or normalized in _ASCII_LITERAL_STOPWORDS:
        return False
    if normalized in _RUNTIME_ASCII_EXACT_TERMS:
        return True
    if not has_runtime_context:
        return False
    if "_" in raw:
        return True
    if re.search(r"[a-z][A-Z]", raw):
        return True
    if re.fullmatch(r"[A-Z][A-Z0-9_+.-]{2,11}", raw):
        return True
    return False


def _structured_cjk_focus_terms(prompt: str, anchor_terms: list[str]) -> list[str]:
    if not anchor_terms:
        return []
    compact = re.sub(r"\s+", "", prompt)
    terms: list[str] = []

    def position(term: str) -> int:
        return compact.find(term)

    if "接口" in compact:
        interface_terms: list[tuple[str, int]] = []
        for term, marker in (("新增接口", "新增"), ("复用接口", "复用")):
            pos = position(marker)
            if pos >= 0:
                interface_terms.append((term, pos))
        for term, _pos in sorted(interface_terms, key=lambda item: (item[1], item[0])):
            _append_unique(terms, term)
        if not terms and "理由" in compact:
            _append_unique(terms, "接口理由")
    if "数据结构" in compact:
        _append_unique(terms, "数据结构")
    return terms


def _metadata_superseded_keyphrase_terms(
    keyphrase_terms: list[str],
    metadata_terms: list[str],
) -> list[str]:
    if len(keyphrase_terms) != 1 or not metadata_terms:
        return keyphrase_terms
    phrase = keyphrase_terms[0]
    if not _is_ascii(phrase):
        return keyphrase_terms
    words = [word for word in phrase.lower().split() if word]
    if len(words) < 3:
        return keyphrase_terms
    metadata = {term.lower() for term in metadata_terms if _is_ascii(term)}
    if words and all(word in metadata for word in words):
        return []
    return keyphrase_terms


def _adjacent_ascii_scope_terms(prompt: str, topic_terms: list[str]) -> list[str]:
    terms: list[str] = []
    compact_prompt = re.sub(r"\s+", "", prompt.lower())
    for topic in topic_terms:
        if not topic or _is_ascii(topic):
            continue
        pattern = re.escape(re.sub(r"\s+", "", topic.lower())) + r"([a-z][a-z0-9_+.-]*)"
        for match in re.finditer(pattern, compact_prompt):
            candidate = match.group(1).strip("._-+")
            if _is_ascii_candidate(candidate) and not _is_placeholder_ascii_span(candidate):
                _append_unique(terms, candidate)
    return terms


def _cjk_keyphrase_terms(prompt: str) -> list[str]:
    terms: list[str] = []

    for match in _CJK_TOPIC_RE.finditer(prompt):
        _append_cjk_keyphrase(terms, match.group(1))

    for term in _domain_keyphrase_terms(prompt):
        _append_unique(terms, term)

    for match in _MIXED_TOPIC_RE.finditer(prompt):
        _append_mixed_keyphrase(terms, match.group(1), require_compound_shape=False)

    for match in _MIXED_COMPOUND_RE.finditer(prompt):
        _append_mixed_keyphrase(terms, match.group(1), require_compound_shape=True)

    for match in _CJK_CONTEXT_RE.finditer(prompt):
        _append_cjk_keyphrase(terms, match.group(1))

    return terms


def _domain_keyphrase_terms(prompt: str) -> list[str]:
    compact = re.sub(r"\s+", "", prompt.lower())
    terms: list[str] = []
    for phrase in _DOMAIN_KEYPHRASE_PRIORITY:
        if phrase in compact:
            _append_unique(terms, phrase)
    return terms


def _anchored_cjk_question_focus_terms(
    prompt: str,
    anchor_terms: list[str],
) -> list[str]:
    if not anchor_terms:
        return []
    terms: list[str] = []
    for match in _CJK_RELATION_FOCUS_RE.finditer(prompt):
        _append_cjk_question_focus(terms, match.group(1))
    for match in _CJK_BASELINE_FOCUS_RE.finditer(prompt):
        _append_cjk_question_focus(terms, match.group(1))
    return terms


def _task_anchor_terms(prompt: str, anchor_terms: list[str]) -> list[str]:
    if not anchor_terms and not _looks_like_long_task_anchor_prompt(prompt):
        return []
    lowered = prompt.lower()
    terms: list[str] = []
    for term in _CJK_TASK_ANCHOR_TERMS:
        if _cjk_task_anchor_present(prompt, term):
            _append_unique(terms, term)
    for term in _ASCII_TASK_ANCHOR_TERMS:
        pattern = r"(?<![a-z0-9_+.-])" + re.escape(term) + r"(?![a-z0-9_+.-])"
        if re.search(pattern, lowered):
            _append_unique(terms, term)
    if anchor_terms:
        return terms
    if len(terms) >= 2 and any(term in prompt for term in _CJK_TASK_ANCHOR_CONTEXT_TERMS):
        return terms
    return []


def _looks_like_long_task_anchor_prompt(prompt: str) -> bool:
    if len(prompt) < 32:
        return False
    if "然后" not in prompt and "之后" not in prompt and "完成" not in prompt:
        return False
    return any(term in prompt for term in _CJK_TASK_ANCHOR_CONTEXT_TERMS)


def _cjk_task_anchor_present(prompt: str, term: str) -> bool:
    for match in re.finditer(re.escape(term), prompt):
        before = prompt[match.start() - 1] if match.start() > 0 else ""
        after = prompt[match.end()] if match.end() < len(prompt) else ""
        if term == "治理" and (before == "可" or after == "的"):
            continue
        return True
    return False


def _leading_task_anchor_terms(task_anchor_terms: list[str], keyphrase_terms: list[str]) -> list[str]:
    if not task_anchor_terms:
        return []
    if any(_keyphrase_is_non_task_topic(term, task_anchor_terms) for term in keyphrase_terms):
        return []
    return task_anchor_terms


def _keyphrase_is_non_task_topic(term: str, task_anchor_terms: list[str]) -> bool:
    normalized = term.lower()
    return not any(anchor.lower() in normalized for anchor in task_anchor_terms)


def _append_cjk_question_focus(values: list[str], raw: str) -> None:
    phrase = _normalize_cjk_question_focus(raw)
    if _is_cjk_question_focus_anchor(phrase):
        _append_unique(values, phrase)


def _normalize_cjk_question_focus(raw: str) -> str:
    phrase = re.sub(r"\s+", "", raw.strip())
    phrase = phrase.strip("：:，,。？?！!、")
    previous = None
    while phrase and previous != phrase:
        previous = phrase
        phrase = re.sub(r"^(是不是|是否|到底|这个|那个)", "", phrase)
        phrase = re.sub(r"(吗|呢|吧)$", "", phrase)
        phrase = phrase.strip("：:，,。？?！!、")
    return phrase


def _is_cjk_question_focus_anchor(phrase: str) -> bool:
    if not phrase or any(ch.isascii() for ch in phrase):
        return False
    if len(phrase) < 2 or len(phrase) > 12:
        return False
    if _is_low_information_cjk_shape(phrase):
        return False
    return True


def _append_cjk_keyphrase(values: list[str], raw: str) -> None:
    phrase = _normalize_cjk_keyphrase(raw)
    if _is_cjk_keyphrase_anchor(phrase):
        _append_unique(values, phrase)


def _append_mixed_keyphrase(
    values: list[str],
    raw: str,
    *,
    require_compound_shape: bool,
) -> None:
    phrase = _normalize_mixed_keyphrase(raw)
    if _is_mixed_keyphrase_anchor(phrase, require_compound_shape=require_compound_shape):
        _append_unique(values, phrase)


def _normalize_cjk_keyphrase(raw: str) -> str:
    phrase = re.sub(r"\s+", "", raw.strip())
    phrase = phrase.strip("：:，,。？?！!、")
    previous = None
    while phrase and previous != phrase:
        previous = phrase
        phrase = _CJK_KEYPHRASE_TRAILING_NOISE_RE.sub("", phrase)
        phrase = phrase.strip("：:，,。？?！!、")
    return phrase


def _normalize_mixed_keyphrase(raw: str) -> str:
    phrase = raw.strip().lower()
    phrase = re.sub(r"\s+", "", phrase)
    phrase = phrase.strip("：:，,。？?！!、")
    previous_prefix = None
    while phrase and previous_prefix != phrase:
        previous_prefix = phrase
        phrase = re.sub(r"^(关于|对于|针对|这个|那个|于|对)", "", phrase)
    previous = None
    while phrase and previous != phrase:
        previous = phrase
        phrase = _CJK_KEYPHRASE_TRAILING_NOISE_RE.sub("", phrase)
        phrase = phrase.strip("：:，,。？?！!、")
    return phrase


def _is_cjk_keyphrase_anchor(phrase: str) -> bool:
    if not phrase or any(ch.isascii() for ch in phrase):
        return False
    if len(phrase) < 4 or len(phrase) > 18:
        return False
    if _is_low_information_cjk_shape(phrase):
        return False
    return True


def _is_mixed_keyphrase_anchor(
    phrase: str,
    *,
    require_compound_shape: bool,
) -> bool:
    if not phrase:
        return False
    if not any(ch.isascii() and ch.isalpha() for ch in phrase):
        return False
    cjk_count = sum(1 for ch in phrase if "\u4e00" <= ch <= "\u9fff")
    if cjk_count < 2:
        return False
    if len(phrase) < 4 or len(phrase) > 24:
        return False
    if require_compound_shape and not _has_mixed_compound_topic_shape(phrase):
        return False
    return True


def _has_mixed_compound_topic_shape(phrase: str) -> bool:
    spans = list(re.finditer(r"[A-Za-z][A-Za-z0-9_+.-]*", phrase))
    if not spans:
        return False
    if any(_is_placeholder_ascii_span(span.group(0)) for span in spans):
        return False
    prefix = phrase[:spans[0].start()]
    suffix = phrase[spans[-1].end():]
    prefix_cjk = sum(1 for ch in prefix if "\u4e00" <= ch <= "\u9fff")
    suffix_cjk = sum(1 for ch in suffix if "\u4e00" <= ch <= "\u9fff")
    if suffix.startswith("的"):
        return suffix_cjk >= 3
    return prefix_cjk > 0 and suffix_cjk >= 4


def _is_placeholder_ascii_span(value: str) -> bool:
    normalized = value.strip("._-+").lower()
    return len(normalized) >= 2 and len(set(normalized)) == 1


def _ascii_keyphrase_terms(prompt: str) -> list[str]:
    terms: list[str] = []

    for match in _ASCII_TOPIC_RE.finditer(prompt):
        _append_ascii_keyphrase(terms, match.group(1))

    for match in _ASCII_MEMORY_RECALL_RE.finditer(prompt):
        _append_ascii_keyphrase(terms, match.group(0))

    for match in _ASCII_EXTRACT_KEYWORD_RE.finditer(prompt):
        _append_ascii_keyphrase(terms, "extract keyword")

    return terms


def _append_ascii_keyphrase(values: list[str], raw: str) -> None:
    phrase = _normalize_ascii_keyphrase(raw)
    if _is_ascii_keyphrase_anchor(phrase):
        _append_unique(values, phrase)


def _normalize_ascii_keyphrase(raw: str) -> str:
    phrase = raw.strip().lower()
    phrase = _ASCII_KEYPHRASE_TRAILING_NOISE_RE.sub("", phrase)
    phrase = re.sub(r"[^a-z0-9_+./-]+", " ", phrase)
    words = [
        word
        for word in phrase.split()
        if word and word not in _ASCII_KEYPHRASE_STOPWORDS
    ]
    return " ".join(words)


def _is_ascii_keyphrase_anchor(phrase: str) -> bool:
    if not phrase or not phrase.isascii():
        return False
    words = phrase.split()
    if len(words) < 2 or len(words) > 8:
        return False
    return any(any(ch.isalpha() for ch in word) for word in words)


def _prune_ascii_terms_after_keyphrases(
    values: list[str],
    keyphrase_terms: list[str],
    *,
    protected_terms: list[str] | None = None,
) -> None:
    protected = set(protected_terms or ())
    values[:] = [
        value
        for value in values
        if value in protected or not _drop_ascii_term_after_keyphrases(value, keyphrase_terms)
    ]


def _drop_ascii_term_after_keyphrases(term: str, keyphrase_terms: list[str]) -> bool:
    if term in keyphrase_terms or not _is_ascii(term):
        return False
    if _is_generic_ascii_anchor(term):
        return False
    if _is_ascii_structural_token(term):
        return _ascii_term_is_covered_by_keyphrase(term, keyphrase_terms)
    return True


def _ascii_term_is_covered_by_keyphrase(term: str, keyphrase_terms: list[str]) -> bool:
    normalized = term.lower()
    for phrase in keyphrase_terms:
        if not phrase.isascii():
            continue
        words = set(phrase.lower().split())
        if normalized in words or normalized in phrase.lower():
            return True
    return False


def _metadata_title_entity_candidates(text: str) -> list[str]:
    candidates: set[str] = set()
    for token in _TOKEN_RE.findall(text.lower()):
        token = token.strip("._-+")
        if _is_ascii(token):
            if _is_metadata_entity_candidate(token):
                candidates.add(token)
            continue
        if not token:
            continue
        for part in _CJK_METADATA_SPLIT_RE.split(token):
            part = part.strip()
            if 2 <= len(part) <= 8 and _is_metadata_entity_candidate(part):
                candidates.add(part)
    return sorted(candidates)


def _add_entity_score(
    scores: dict[str, int],
    raw: str,
    weight: int,
    *,
    source: str = "",
    sources: dict[str, set[str]] | None = None,
) -> None:
    term = raw.strip().lower()
    if _is_metadata_entity_candidate(term):
        scores[term] = scores.get(term, 0) + weight
        if source and sources is not None:
            sources.setdefault(term, set()).add(source)


def _entity_field_weight(raw: str, *, base: int, has_ascii_alias: bool) -> int:
    term = raw.strip().lower()
    if has_ascii_alias and not _is_ascii(term) and len(term) <= 2:
        return base + 1
    return base


def _is_ascii_alias(raw: str) -> bool:
    for token in _TOKEN_RE.findall(raw.lower()):
        token = token.strip("._-+")
        if _is_ascii(token) and token and any(ch.isalpha() for ch in token):
            return True
    return False


def _expand_token(token: str) -> list[str]:
    if not token:
        return []
    if _is_ascii(token):
        return [token] if _is_ascii_candidate(token) else []
    return _expand_cjk_token(token)


def _expand_cjk_token(token: str) -> list[str]:
    terms: list[str] = []
    for part in _CJK_METADATA_SPLIT_RE.split(token):
        term = part.strip()
        if _is_cjk_recall_phrase(term):
            _append_unique(terms, term)
    return terms


def _specificity(terms: list[str], strong_terms: list[str]) -> float:
    return len(strong_terms) * 1.0 + max(0, len(terms) - len(strong_terms)) * 0.35


def _append_unique(values: list[str], value: str) -> None:
    if value and value not in values:
        values.append(value)


def _remove_file_stem_duplicates(values: list[str], file_terms: list[str]) -> None:
    stems = _file_stems(file_terms)
    if not stems:
        return
    values[:] = [value for value in values if value not in stems]


def _file_stems(file_terms: list[str]) -> set[str]:
    return {
        file_term.rsplit("/", 1)[-1].rsplit(".", 1)[0]
        for file_term in file_terms
        if "." in file_term
    }


def _promote_terms(values: list[str], preferred: list[str]) -> None:
    ordered: list[str] = []
    for term in preferred:
        if term in values and term not in ordered:
            ordered.append(term)
    for term in values:
        if term not in ordered:
            ordered.append(term)
    values[:] = ordered


def _focus_terms_on_precise_anchor(
    terms: list[str],
    precise_anchor_terms: list[str],
) -> None:
    precise = {term for term in precise_anchor_terms if term in terms}
    if not precise:
        return
    terms[:] = [
        term
        for term in terms
        if term in precise or _is_explicit_ascii_scope_term(term)
    ]


def _focus_strong_terms_on_precise_anchor(
    strong_terms: list[str],
    precise_anchor_terms: list[str],
) -> None:
    precise = {term for term in precise_anchor_terms if term in strong_terms}
    if not precise:
        return
    strong_terms[:] = [
        term
        for term in strong_terms
        if term in precise or _is_explicit_ascii_scope_term(term)
    ]


def _focus_unanchored_terms_on_ascii_scope(
    terms: list[str],
    strong_terms: list[str],
    anchors: list[str],
) -> None:
    if anchors or not any(_is_ascii(term) for term in terms):
        return
    terms[:] = [term for term in terms if _is_ascii(term)]
    strong_terms[:] = [term for term in strong_terms if _is_ascii(term)]


def _is_explicit_ascii_scope_term(term: str) -> bool:
    return _is_ascii(term) and _is_ascii_structural_token(term)


def _known_entity_terms(
    prompt: str,
    terms: list[str],
    brain_dir: Path | None,
) -> list[str]:
    """Promote short entity-like terms only when memory metadata proves them.

    Standalone short project names are too short to be globally safe,
    and short ASCII names are too ambiguous without
    provenance.  Promote them only when they already appear as a tag/project or
    repeatedly in item titles.  Generic domain words such as ``验证`` still stay
    weak; they are handled by the normal query-gate gap path instead of being
    auto-injected.
    """
    if brain_dir is None:
        return []
    entity_terms = _prompt_entity_candidates(prompt, terms)
    if not entity_terms:
        return []

    cache = _metadata_cache(brain_dir)
    if not cache:
        return []
    raw_scores = cache.get("entity_scores", {})
    scores = raw_scores if isinstance(raw_scores, dict) else {}
    raw_sources = cache.get("entity_sources", {})
    sources = raw_sources if isinstance(raw_sources, dict) else {}
    supported = {
        term
        for term in entity_terms
        if _metadata_entity_supported(term, scores=scores, sources=sources)
    }
    return [term for term in entity_terms if term in supported]


def _metadata_entity_supported(
    term: str,
    *,
    scores: dict[str, object],
    sources: dict[str, object],
) -> bool:
    key = term.lower()
    if key in _GENERIC_METADATA_ENTITY_TERMS:
        return False
    score = int(scores.get(key, 0) or 0)
    if not _is_ascii(term) and len(term) <= 2:
        raw_sources = sources.get(key, ())
        source_set = {str(source) for source in raw_sources} if isinstance(raw_sources, list) else set()
        if not ({"project", "tag_with_ascii_alias"} & source_set):
            return False
    return score >= _metadata_support_threshold(term)


def _metadata_support_threshold(term: str) -> int:
    if not _is_ascii(term) and len(term) <= 2:
        return 3
    return 2


def _prompt_entity_candidates(prompt: str, terms: list[str]) -> list[str]:
    candidates: list[str] = []

    for term in terms:
        if _is_metadata_entity_candidate(term):
            _append_unique(candidates, term)

    for token in _TOKEN_RE.findall(prompt.lower()):
        token = token.strip("._-+")
        if not token:
            continue
        if _is_ascii(token):
            if _is_metadata_entity_candidate(token):
                _append_unique(candidates, token)
            continue
        for candidate in _cjk_entity_candidate_forms(token):
            _append_unique(candidates, candidate)

    return candidates


def _cjk_entity_candidate_forms(token: str) -> list[str]:
    candidates: list[str] = []
    if _is_metadata_entity_candidate(token):
        _append_unique(candidates, token)
    stripped = _strip_cjk_sentence_tail(token)
    if stripped != token and _is_metadata_entity_candidate(stripped):
        _append_unique(candidates, stripped)
    return candidates


def _strip_cjk_sentence_tail(token: str) -> str:
    value = token
    previous = None
    while value and value != previous:
        previous = value
        value = re.sub(r"(呢|吗|吧|呀|啊|了|的)$", "", value)
    return value


def _is_metadata_entity_candidate(term: str) -> bool:
    if not term:
        return False
    if _is_ascii(term):
        return 1 <= len(term) <= 2 and any(ch.isalpha() for ch in term)
    return 2 <= len(term) <= 8 and not _is_low_information_cjk_shape(term) and not any(ch.isascii() for ch in term)


def _is_strong_term(token: str) -> bool:
    if _is_ascii(token):
        return _is_ascii_candidate(token)
    return len(token) >= 3 and _is_cjk_recall_phrase(token)


def _is_cjk_recall_phrase(term: str) -> bool:
    if not term:
        return False
    if len(term) < 4 or len(term) > 12:
        return False
    if _is_low_information_cjk_shape(term):
        return False
    return not any(ch.isascii() for ch in term)


def _is_ascii(text: str) -> bool:
    return text.isascii()


def _is_generic_ascii_anchor(term: str) -> bool:
    return _is_ascii(term) and term.lower() in _GENERIC_ASCII_ANCHORS


def _generic_format_without_topic(
    terms: list[str],
    keyphrase_terms: list[str],
    file_terms: list[str],
    known_entity_terms: list[str],
) -> bool:
    if keyphrase_terms or file_terms or known_entity_terms:
        return False
    if not terms:
        return False
    return all(_is_generic_ascii_anchor(term) for term in terms)


def _test_status_without_topic(
    prompt: str,
    terms: list[str],
    keyphrase_terms: list[str],
    file_terms: list[str],
    known_entity_terms: list[str],
) -> bool:
    if keyphrase_terms or known_entity_terms:
        return False
    if not terms:
        return False
    if not all(_is_ascii(term) for term in terms):
        return False
    has_status = (
        any(term.lower() in _TEST_STATUS_ASCII_TERMS for term in terms)
        or _TEST_STATUS_RESULT_RE.search(prompt) is not None
        or _TEST_STATUS_COMMAND_RE.search(prompt) is not None
    )
    if not has_status:
        return False
    if _test_status_followup_intent(prompt):
        return False
    allowed = _TEST_STATUS_ASCII_TERMS | _TEST_STATUS_CONTEXT_ASCII_TERMS | _TEST_STATUS_COMMAND_ASCII_TERMS
    file_status_terms = _file_status_terms(file_terms)
    if all(
        term.lower() in allowed
        or term.lower() in file_status_terms
        or term.isdigit()
        or _TEST_STATUS_DURATION_RE.fullmatch(term.lower()) is not None
        for term in terms
    ):
        return True
    return (
        _TEST_STATUS_RESULT_RE.search(prompt) is not None
        or _TEST_STATUS_COMMAND_RE.search(prompt) is not None
    )


def _file_status_terms(file_terms: list[str]) -> set[str]:
    values: set[str] = set()
    for term in file_terms:
        lowered = term.lower()
        values.add(lowered)
        basename = lowered.rsplit("/", 1)[-1]
        values.add(basename)
        if "." in basename:
            values.add(basename.rsplit(".", 1)[0])
    return values


def _test_status_followup_intent(prompt: str) -> bool:
    if _TEST_STATUS_FOLLOWUP_INTENT_RE.search(prompt):
        return True
    return bool(re.search(r"(为什么|怎么|如何|处理|修复|排查|解释|原因|怎么办)", prompt))


def _generic_command_without_topic(
    prompt: str,
    terms: list[str],
    anchors: tuple[str, ...],
) -> bool:
    if anchors or not terms:
        return False
    if not _SLASH_COMMAND_PROMPT_RE.fullmatch(prompt.strip()):
        return False
    return all(_is_ascii(term) and term.lower() in _GENERIC_COMMAND_TERMS for term in terms)


def _unanchored_mixed_scope_without_semantic_anchor(
    prompt: str,
    terms: list[str],
    anchors: tuple[str, ...],
    weak_terms: list[str],
) -> bool:
    if anchors or not terms:
        return False
    if not _prompt_has_cjk_and_ascii(prompt):
        return False
    if any(not _is_ascii(term) for term in terms):
        return False
    if any(_is_ascii_structural_token(term) for term in terms):
        return False
    if _has_uppercase_ascii_acronym_anchor(prompt, terms):
        return False
    if _CJK_CONFIRMATION_OPERATOR_RE.search(prompt):
        return True
    return not _has_substantive_weak_cjk_intent(weak_terms)


def _prompt_has_cjk_and_ascii(prompt: str) -> bool:
    has_cjk = any(_is_cjk_char(ch) for ch in prompt)
    has_ascii = any(ch.isascii() and ch.isalnum() for ch in prompt)
    return has_cjk and has_ascii


def _has_substantive_weak_cjk_intent(weak_terms: list[str]) -> bool:
    for term in weak_terms:
        if _is_ascii(term):
            continue
        normalized = re.sub(r"\s+", "", term.strip())
        if not normalized or normalized in _CJK_NON_SUBSTANTIVE_WEAK_TERMS:
            continue
        if len(normalized) >= 2:
            return True
    return False


def _has_uppercase_ascii_acronym_anchor(prompt: str, terms: list[str]) -> bool:
    ascii_terms = [term for term in terms if _is_ascii(term)]
    if not ascii_terms:
        return False
    prompt_tokens = {
        match.group(0)
        for match in re.finditer(r"(?<![A-Za-z0-9_+.-])[A-Z][A-Z0-9_+.-]{1,7}(?![A-Za-z0-9_+.-])", prompt)
    }
    normalized = {token.lower().strip("._-+") for token in prompt_tokens}
    return all(term.lower() in normalized for term in ascii_terms)


def _looks_like_unanchored_cjk_clause_prompt(prompt: str, terms: list[str]) -> bool:
    if any(_is_ascii(term) for term in terms):
        return False
    cjk_count = sum(1 for ch in prompt if "\u4e00" <= ch <= "\u9fff")
    if cjk_count >= 4:
        return True
    if cjk_count < 9:
        return False
    cjk_term_chars = sum(len(term) for term in terms if not _is_ascii(term))
    # When extracted terms cover most of a long CJK prompt without any metadata,
    # file, or domain anchor, they are usually clause fragments rather than
    # precise recall anchors. Keep auto-injection conservative in that case.
    return (cjk_term_chars / max(1, cjk_count)) >= 0.45


def _is_low_information_cjk_shape(text: str) -> bool:
    if any(ch.isascii() for ch in text):
        return False
    if len(set(text)) == 1:
        return True
    return False


def _is_cjk_char(value: str) -> bool:
    return len(value) == 1 and "\u4e00" <= value <= "\u9fff"


def _is_weak_intent_token(token: str) -> bool:
    return bool(token) and not _is_ascii(token) and len(token) <= 3


def _short_weak_prompt_without_terms(
    prompt: str,
    terms: list[str],
    weak_terms: list[str],
) -> bool:
    if terms or not weak_terms:
        return False
    compact = re.sub(r"\s+", "", prompt.strip().lower())
    if len(compact) > 8:
        return False
    return all(_is_weak_intent_token(term) for term in weak_terms)


def _weak_intent_traces(weak_terms: list[str]) -> list[str]:
    traces: list[str] = []
    for term in weak_terms:
        _append_unique(traces, f"weak_noise={term}")
    return traces


def _weak_noise_from_trace(trace: tuple[str, ...]) -> tuple[str, ...]:
    values: list[str] = []
    for row in trace:
        if not row.startswith("weak_noise="):
            continue
        value = row.removeprefix("weak_noise=")
        _append_unique(values, value)
    return tuple(values)


def _is_ascii_candidate(token: str) -> bool:
    if token.lower() in _ASCII_LITERAL_STOPWORDS:
        return False
    return len(token) >= 3 and any(ch.isalpha() for ch in token)


def _is_ascii_structural_token(token: str) -> bool:
    if not _is_ascii_candidate(token):
        return False
    return (
        any(ch.isdigit() for ch in token)
        or any(ch in "_+.-/" for ch in token)
    )


def _single_unanchored_ascii_term(
    prompt: str,
    terms: list[str],
    anchors: tuple[str, ...],
    weak_terms: list[str] | None = None,
) -> bool:
    weak_terms = weak_terms or []
    if (
        not anchors
        and len(terms) == 1
        and _is_ascii(terms[0])
        and "." not in terms[0]
        and _looks_like_prefix_mixed_singleton(prompt, terms[0])
    ):
        return True
    has_short_cjk_qualifier = any(
        not _is_ascii(term) and 1 <= len(term) <= 3
        for term in weak_terms
    )
    return (
        not anchors
        and len(terms) == 1
        and _is_ascii(terms[0])
        and "." not in terms[0]
        and not has_short_cjk_qualifier
    )


def _looks_like_prefix_mixed_singleton(prompt: str, term: str) -> bool:
    compact = re.sub(r"\s+", "", prompt.strip().lower())
    match = re.search(re.escape(term.lower()), compact)
    if match is None:
        return False
    prefix = compact[:match.start()]
    suffix = compact[match.end():]
    if not prefix or not all(_is_cjk_char(ch) for ch in prefix):
        return False
    if len(prefix) > 3:
        return False
    return not suffix


def _has_item_metadata(brain_dir: Path | None) -> bool:
    if brain_dir is None:
        return False
    try:
        return any((Path(brain_dir) / "items").rglob("*.md"))
    except OSError:
        return False
    return False


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    brain_dir: Path | None = None
    if len(args) >= 2 and args[0] == "--brain-dir":
        brain_dir = Path(args[1])
        args = args[2:]
    if args and args[0] == "--gate-gap-json":
        prompt = args[1] if len(args) > 1 else sys.stdin.read()
        evidence = query_gate_gap_evidence(prompt, brain_dir=brain_dir)
        if evidence is not None:
            sys.stdout.write(json.dumps(evidence.to_dict(), ensure_ascii=False))
        return 0
    if args and args[0] == "--diagnose-json":
        prompt = args[1] if len(args) > 1 else sys.stdin.read()
        diagnostic = diagnose_injection_query(prompt, brain_dir=brain_dir)
        sys.stdout.write(json.dumps(diagnostic.to_dict(), ensure_ascii=False, sort_keys=True))
        return 0
    prompt = args[0] if args else sys.stdin.read()
    sys.stdout.write(extract_injection_keywords(prompt, brain_dir=brain_dir))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "QueryGateGapEvidence",
    "QuerySignal",
    "QuerySignalDiagnostics",
    "analyze_injection_query",
    "diagnose_injection_query",
    "extract_injection_keywords",
    "query_gate_gap_evidence",
]

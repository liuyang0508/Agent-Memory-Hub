from __future__ import annotations

import re

from agent_brain.memory.recall.query_tokens import _tokenize_mixed

_SYNONYMS: dict[str, list[str]] = {
    "sse": ["server sent events", "eventsource", "实时推送"],
    "websocket": ["ws", "wss", "双向通信"],
    "api": ["endpoint", "接口", "rest"],
    "db": ["database", "数据库", "sqlite", "postgres"],
    "k8s": ["kubernetes", "容器编排"],
    "docker": ["container", "容器"],
    "ci": ["continuous integration", "持续集成"],
    "cd": ["continuous deployment", "持续部署"],
    "auth": ["authentication", "authorization", "认证", "鉴权"],
    "jwt": ["json web token", "令牌"],
    "oauth": ["oauth2", "授权"],
    "mcp": ["model context protocol"],
    "llm": ["large language model", "大模型"],
    "rag": ["retrieval augmented generation", "检索增强"],
    "crud": ["create read update delete", "增删改查"],
    "sdk": ["software development kit", "开发工具包"],
    "tdd": ["test driven development", "测试驱动"],
    "pr": ["pull request", "合并请求"],
    "perf": ["performance", "性能"],
    "refactor": ["重构", "restructure"],
    "bug": ["缺陷", "issue", "故障"],
    "deploy": ["部署", "发布", "release"],
    "config": ["configuration", "配置"],
    "env": ["environment", "环境"],
    "async": ["asynchronous", "异步"],
    "sync": ["synchronous", "同步"],
    "cache": ["缓存", "caching"],
    "queue": ["队列", "消息队列", "mq"],
    "hook": ["钩子", "回调", "callback"],
    "middleware": ["中间件"],
    "schema": ["模式", "结构定义"],
    "migrate": ["迁移", "migration"],
    "index": ["索引", "indexing"],
    "embed": ["embedding", "嵌入", "向量化"],
    "数据库": ["db", "database", "sqlite", "postgres"],
    "性能": ["perf", "performance", "优化"],
    "接口": ["api", "endpoint", "rest"],
    "部署": ["deploy", "release", "发布"],
    "配置": ["config", "configuration"],
    "认证": ["auth", "authentication"],
    "缓存": ["cache", "caching"],
    "索引": ["index", "indexing"],
    "迁移": ["migrate", "migration"],
    "重构": ["refactor", "restructure"],
    "测试": ["test", "testing", "unittest"],
    "日志": ["log", "logging", "logger"],
    "监控": ["monitor", "monitoring", "observability"],
    "网关": ["gateway", "proxy"],
    "微服务": ["microservice", "service mesh"],
    "消息队列": ["mq", "queue", "kafka", "rabbitmq"],
    "容器": ["container", "docker", "k8s"],
}

_SYNONYM_LOOKUP: dict[str, list[str]] = {}
for _key, _vals in _SYNONYMS.items():
    _SYNONYM_LOOKUP[_key] = _vals
    for _v in _vals:
        _norm = _v.lower().strip()
        if _norm not in _SYNONYM_LOOKUP:
            _SYNONYM_LOOKUP[_norm] = [_key]


def _extract_words(text: str) -> list[str]:
    """Extract whole words and CJK n-gram substrings for synonym matching."""
    raw_words = re.findall(r"[a-zA-Z0-9_]+|[一-鿿㐀-䶿]+", text.lower())
    results = list(raw_words)
    for word in raw_words:
        if len(word) > 1 and "一" <= word[0] <= "鿿":
            for n in range(2, min(5, len(word) + 1)):
                for i in range(len(word) - n + 1):
                    results.append(word[i:i + n])
    return results


def _expand_with_synonyms(tokens: list[str], raw_query: str = "", max_expansions: int = 3) -> list[str]:
    """Expand tokens with synonyms/abbreviations for improved recall."""
    expanded = list(tokens)
    checked: set[str] = set()

    for token in tokens:
        key = token.lower()
        if key in checked:
            continue
        checked.add(key)
        synonyms = _SYNONYM_LOOKUP.get(key, [])
        for syn in synonyms[:max_expansions]:
            expanded.extend(_tokenize_mixed(syn))

    if raw_query:
        for word in _extract_words(raw_query):
            if word in checked:
                continue
            checked.add(word)
            synonyms = _SYNONYM_LOOKUP.get(word, [])
            for syn in synonyms[:max_expansions]:
                expanded.extend(_tokenize_mixed(syn))

    return expanded


__all__ = [
    "_expand_with_synonyms",
    "_extract_words",
]

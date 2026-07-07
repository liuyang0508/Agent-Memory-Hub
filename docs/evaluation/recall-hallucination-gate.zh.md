# 召回幻觉门禁

本门禁验证的是“自动注入到 Agent prompt 里的 memory context 是否被无关记忆污染”，不是泛化声称 LLM 永远不会幻觉。

## 命令

```bash
python -m agent_brain.interfaces.cli benchmark recall-hallucination --format summary
python -m agent_brain.interfaces.cli benchmark recall-hallucination --format json
```

## 通过标准

- `false_injection_rate == 0`：弱意图、泛词长句、多模态占位符、缺 metadata anchor 的短词、仅召回领域疑问，都不能注入禁用 context。
- `negative_clean_rate == 1`：负样本最终进入 prompt 的 context 必须为空。
- `positive_recall_rate == 1`：带 metadata anchor 的正样本必须召回并注入目标 memory。

## 当前覆盖

门禁使用合成、公开安全的 MemoryItem 池，不读取真实个人 brain 数据。评测链路覆盖：

1. hook prompt normalization
2. `query_signal` anchor 提取
3. BM25/vector retrieval
4. `ContextFirewall` query-match 和 cohort gate
5. context packing 前的最终 included ids

## 已防住的问题

- 短响应、短疑问和无 provenance 的长句不会触发自动注入。
- 短标签或泛化描述即便出现在 metadata 中，也不能仅凭普通 title/tag 累计成为强锚。
- 两字中文 metadata entity 必须有 project 或同条 memory 的 ASCII tag alias 等可区分来源。
- 附件占位符没有 extraction provenance 时不会把占位符文本变成强检索词。
- 缺少 metadata anchor 的短 ASCII scope 不会把随机相邻记忆注入 prompt。

## 边界

本门禁只证明“错误 memory context 不应进入 prompt”。如果 context 本身正确但模型误读、过度推断或没有按来源边界表达，仍需要回答层的 provenance 约束和人工/LLM judge 评测继续覆盖。

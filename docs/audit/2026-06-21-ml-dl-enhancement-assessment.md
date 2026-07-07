# AMH ML/DL 增强评估

日期：2026-06-21

## 结论

当前阶段 **ML/DL 不进入默认写入、检索、压缩或注入链路**。原因不是 AMH 不需要机器学习，而是默认链路必须本地优先、可复现、可解释、低延迟、可审计。没有 few-shot / benchmark / release gate 证明收益前，ML/DL 只能作为 advisory 或离线评估能力。

## 当前真实底座

- AMH 已有 deterministic retrieval：BM25、vector、RRF、graph、decay、feedback、scope-risk、context firewall。
- AMH 已有 embedding advisory baseline：可用于语义相关性、contradiction 候选和 rerank 辅助，但不直接覆盖事实判断。
- AMH 已有 semantic proactive candidates：候选只进入 review sidecar，批准后才经 WriteService 写入。
- Headroom-style compression 已进入 AMH-local 内容路由，但现在必须由 **few-shot compression gate** 验证：关键 anchor 必须保留，已知噪声必须删除，token savings 必须为正，可逆读取必须存在。
- **ML/DL advisory gate 已落地**：`memory benchmark ml-advisory` 和 Web `/api/ml-advisory-gate` 用 few-shot 样本比较 baseline/candidate、delta、latency、privacy mode 和 evidence gates。
- release gate 现在同时检查 retrieval quality、compression few-shot gate 和 ML/DL advisory gate，避免新增算法“看起来更聪明、实际丢证据”。

## 需要增强的方向

1. **ML/DL 作为评估器，而不是默认决策器**
   - 可加 LLM / embedding judge 比较压缩前后是否保留事实、错误栈、文件路径、commit refs。
   - 结果只能进入 report/advisory，不能直接改写 memory item 或默认注入内容。
   - 已有 gate 会阻断 `candidate_mode=default`，即使模型 delta 很高也只能进入人工 release decision。

2. **可替换 embedding backend**
   - 当前 Hashing/offline embedding 适合作为稳定 baseline。
   - 可以增加本地模型 backend，但必须通过 retrieval benchmark、stale-hit、token-cost、延迟和隐私检查。

3. **学习型 rerank / compressor**
   - 可以在 benchmark 中与 deterministic baseline 对比。
   - 默认关闭；只有在 category-level MRR、anchor recall、latency 和 reversibility 全部优于 baseline 时才考虑灰度。

4. **对话压缩和长期 consolidation**
   - ML/DL 更适合做候选摘要、主题聚类、重复/矛盾候选发现。
   - 产物仍进 review queue 或 derived sidecar，不能绕过 WriteService。

## 暂不做的事

- 不把 LLM 摘要直接写入 `items/`。
- 不用黑盒模型替代 context firewall 的安全判断。
- 不让 ML/DL compressor 默认处理 hook 注入内容。
- 不用在线模型作为安装后的必要依赖。

## 已落地 gate

- 将 few-shot compression gate 扩展为真实 dogfooding case 库。
- ML/DL advisory gate 已输出 baseline、candidate、delta、latency、privacy mode、required/passed gates 和 default promotion 阻断结果。
- release gate 继续保持 fail-closed：任一 compression case 丢 anchor 或不可逆，或 ML/DL advisory 出现 unsafe promotion，发布门禁失败。

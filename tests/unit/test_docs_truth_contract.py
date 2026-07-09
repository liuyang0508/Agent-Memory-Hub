from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_release_manifest_keeps_public_readme_and_evaluation_assets_present():
    required_paths = [
        "agent_brain/evaluation/professional_report.py",
        "agent_brain/evaluation/external_memory_benchmark.py",
        "agent_brain/evaluation/system_benchmark.py",
        "agent_brain/memory/context/prompt_normalization.py",
        "agent_brain/memory/context/query_intent.py",
        "agent_brain/memory/evidence/multimodal_capture.py",
        "agent_runtime_kit/hooks/inject-context.sh",
        "agent_runtime_kit/tools/search-memory.sh",
        "agent_runtime_kit/mcp/server.sh",
        "docs/evaluation/amh-evaluation-report.html",
        "docs/evaluation/amh-evaluation-report.json",
        "docs/evaluation/amh-evaluation-report.zh.md",
        "docs/evaluation/latest-memory-benchmark-report.zh.md",
        "docs/evaluation/memorydata-external-benchmark-report.json",
        "docs/evaluation/memorydata-external-benchmark-report.zh.md",
        "docs/evaluation/amh-full-ranking-optimized-full/all-memory-benchmark-report-preview.png",
        "docs/evaluation/amh-full-ranking-optimized-full/amh-memorydata-report-chart.svg",
        "benchmarks/run_memory_benchmarks.py",
        "docs/visuals/readme-zh-openviking-octopus-preview.html",
        "docs/visuals/agent-memory-hub-octopus-logo-a-plus-candidate.svg",
        "docs/visuals/agent-memory-hub-logo-lockup-a-plus-candidate.svg",
        "docs/visuals/amh-loop-layered-architecture.zh.svg",
        "docs/visuals/amh-operating-loop.zh.svg",
        "docs/visuals/product-architecture.zh.svg",
        "docs/visuals/technical-architecture.zh.svg",
        "docs/visuals/memory-lifecycle-sequence.zh.svg",
        "docs/visuals/data-flow.zh.svg",
        "docs/visuals/retrieval-complete-flow.zh.svg",
        "docs/visuals/retrieval-algorithm-stack.zh.svg",
        "tests/fixtures/query_intent/fewshot_cases.json",
        "tests/unit/test_professional_evaluation_report.py",
        "tests/unit/test_system_benchmark.py",
        "tests/unit/test_system_fewshot_matrix.py",
    ]

    missing = [path for path in required_paths if not (ROOT / path).is_file()]

    assert missing == []


def test_strategy_does_not_mark_wip_adapters_integrated():
    strategy = _read("STRATEGY.md")

    forbidden_claims = [
        "Qoder ✅",
        "Aone Copilot ✅",
        "Qoder + Aone Copilot ✅",
    ]
    for claim in forbidden_claims:
        assert claim not in strategy

    assert "`wip`" in strategy
    assert "qoder" in strategy.lower()
    assert "aone" in strategy.lower()


def test_roadmap_mcp_tool_count_matches_surface_lock():
    roadmap = _read("ROADMAP.md")

    assert "MCP server exposes **28 registered tools**" in roadmap
    assert "27 operation tools + 1 onboarding guide tool" in roadmap
    assert "stable 10-tool core" in roadmap
    assert "conversation 2" in roadmap
    assert "MCP server exposes **24 tools**" not in roadmap
    assert "stable 9-tool core" not in roadmap


def test_readmes_embed_current_agent_memory_hub_brand_assets():
    readme = _read("README.md")
    readme_zh = _read("README.zh.md")
    openviking_preview = _read("docs/visuals/readme-zh-openviking-octopus-preview.html")

    assert "./docs/visuals/agent-memory-hub-logo-lockup-a-plus-candidate.svg" in readme
    assert "./docs/visuals/agent-memory-hub-logo-lockup-a-plus-candidate.svg" not in readme_zh
    assert "A shared second brain for multi-agent systems" in readme
    assert "A shared second brain for multi-agent systems" not in readme_zh
    assert "品牌资产：logo lockup 与标语" not in readme_zh
    assert 'src="agent-memory-hub-octopus-logo-a-plus-candidate.svg"' in openviking_preview
    assert openviking_preview.count('src="agent-memory-hub-octopus-logo-a-plus-candidate.svg"') >= 2
    assert '<svg class="octo-logo"' not in openviking_preview


def test_mcp_example_configs_use_truth_contract_statuses():
    example_configs = _read("agent_runtime_kit/mcp/example-configs.md")

    forbidden_legacy_statuses = [
        "✅ verified",
        "🟡 docs only",
        "升级到 ✅ verified",
    ]
    for status in forbidden_legacy_statuses:
        assert status not in example_configs

    assert "`verified`" in example_configs
    assert "`install-ready`" in example_configs
    assert "`docs-only`" in example_configs
    assert "`wip`" in example_configs
    assert "| Claude Code | `~/.claude/settings.json` | JSON | `install-ready`; hook/MCP config and runtime evidence exist, but the latest verified gate did not pass |" in example_configs
    assert "| Qoder | hooks settings; manual MCP config only | JSON | `install-ready` for hooks, MCP auto-config unverified |" in example_configs
    assert "| QoderWork | `~/.qoderwork/settings.json` hooks + `~/.qoderwork/awareness/main/AGENTS.md` + `~/.qoderwork/mcp.json` | JSON/Markdown | `verified`; current snapshot includes QoderWork GUI context-effective evidence |" in example_configs
    assert "| Continue | `~/.continue/config.yaml` | YAML | `verified` |" in example_configs
    assert "experimental.modelContextProtocolServers" not in example_configs


def test_blog_draft_is_marked_stale_before_publish():
    blog = _read("docs/blog/2026-05-17-your-ai-tools-are-coworkers.md")

    assert "STALE DRAFT" in blog
    assert "Do not publish without refreshing against README.md, ROADMAP.md, and `memory adapter list --format json`." in blog
    assert "3 verified end-to-end" not in blog
    assert "MCP 7 tools" not in blog
    assert "<TBD-" not in blog


def test_readme_maintains_architecture_map_index():
    readme = _read("README.md")
    readme_zh = _read("README.zh.md")
    preview_zh = _read("docs/visuals/readme-zh-preview.html")
    openviking_preview = _read("docs/visuals/readme-zh-openviking-octopus-preview.html")
    audit = _read("docs/audit/2026-06-21-codebase-capability-deep-analysis.md")

    assert "[Official Website](https://aihub0508.com/)" in readme
    assert "[![Website](https://img.shields.io/badge/website-aihub0508.com-0ea5e9.svg)](https://aihub0508.com/)" in readme
    assert "[官网](https://aihub0508.com/)" in readme_zh
    assert "[![官网：aihub0508.com](https://img.shields.io/badge/官网-aihub0508.com-0ea5e9.svg)](https://aihub0508.com/)" in readme_zh

    for text in (readme, readme_zh):
        assert "github.com/aihub0508" not in text
        assert "<owner>/agent-memory-hub" not in text
        assert "@aihub0508/agent-memory-hub" not in text
        assert (
            "agent-memory-hub/agent-memory-hub"
            not in text.replace("liuyang0508/agent-memory-hub/agent-memory-hub", "")
        )

    assert "## Engineering Architecture Map" in readme
    assert "| Product structure |" in readme
    assert "./docs/visuals/agent-memory-hub-architecture-map.html#product" in readme
    assert "./docs/visuals/agent-memory-hub-architecture-map.html#technical" in readme
    assert "./docs/visuals/agent-memory-hub-architecture-map.html#sequence" in readme
    assert "./docs/visuals/agent-memory-hub-architecture-map.html#flows" in readme

    assert "定位：共享第二大脑" in readme_zh
    assert "接入：CLI / MCP / SDK / Web / hooks" in readme_zh
    assert "维护：Evidence / MemoryItem / Index" in readme_zh
    assert "排序：BM25 / Vector / RRF / rerank" in readme_zh
    assert "治理：decay / feedback / temporal" in readme_zh
    assert "扩展：MMR / Hopfield / graph" in readme_zh
    assert "注入：ContextFirewall / ContextPack" in readme_zh
    assert "评测：doctor / runtime / benchmark" in readme_zh
    assert "badge/召回-BM25%20%2F%20RRF%20%2F%20MMR" not in readme_zh
    assert "badge/注入-ContextFirewall-ea580c" not in readme_zh
    assert "badge/评测-Benchmark%20Gate" not in readme_zh
    zh_intro = readme_zh[: readme_zh.index("<a id=\"quick-nav\"></a>")]
    assert "许可证：Apache 2.0" not in zh_intro
    assert "文档契约" not in zh_intro
    assert "python-3.11" not in zh_intro
    assert "协议：MCP" not in zh_intro
    assert "存储-本地" not in zh_intro

    required_sections = [
        "## AMH 在解决什么问题",
        "## 从 Loop Engineering 看 AMH",
        "## 为什么多智能体需要共享第二大脑",
        "## 快速入门",
        "## 用一个真实问题看懂 AMH",
        "## 核心对象地图",
        "## 先维护，再召回",
        "## 维护完整链路",
        "## 召回完整链路",
        "## 算法地图",
        "## Loop Engineering 在哪里工作",
        "## Agent Runtime Kit 与 Agent Integrations 如何协作",
        "## 能力账本",
        "## Agent 适配矩阵",
        "## 命令手册",
        "## 系统级验证门禁",
        "## 工程架构图谱",
    ]
    for section in required_sections:
        assert section in readme_zh

    assert readme_zh.index("## 为什么多智能体需要共享第二大脑") < readme_zh.index("## 快速入门")
    assert readme_zh.index("## 快速入门") < readme_zh.index("## 用一个真实问题看懂 AMH")
    assert readme_zh.index("## 用一个真实问题看懂 AMH") < readme_zh.index("## 核心对象地图")
    assert readme_zh.index("## 快速入门") < readme_zh.index("readme-structure-map.zh.svg")
    assert readme_zh.index("## Agent Runtime Kit 与 Agent Integrations 如何协作") < readme_zh.index("## 能力账本")
    assert readme_zh.index("## 系统级验证门禁") < readme_zh.index("## 工程架构图谱")
    assert readme_zh.index("## 维护完整链路") < readme_zh.index("## 召回完整链路")
    assert readme_zh.index("## 召回完整链路") < readme_zh.index("## 算法地图")
    assert "Evidence -> MemoryItem -> Index / Runtime Ledger -> RetrievedItem -> ContextFirewall -> ContextPack -> Feedback / Governance / Loop" in readme_zh
    assert "贯穿样例候选池" in readme_zh
    assert "关于多智能体共享第二大脑 README 二次打磨，都做了什么？" in readme_zh
    assert "样例得分链路" in readme_zh
    assert "论文式算法索引" in readme_zh
    assert "这一节把每个因子翻译成中文解释" in readme_zh
    assert "D | 不进入 | 不进入 | `0`" in readme_zh
    assert "E | 2.90000 | 0.55 | 0.41" in readme_zh
    assert "metadata phrase 阶段" in readme_zh
    assert "maturity 不进入上面的 live score" in readme_zh
    assert "AMH 可信上下文生命周期图：接入、维护、召回、治理与评估" in readme_zh
    assert "AMH 的边界更收敛：它不接管执行，不替代人的验收，也不把原始 transcript 当成长期知识" in readme_zh
    assert "| 图 | 讲什么 | 证据边界 | 适合什么时候看 |" in readme_zh
    assert "| [生命周期图](./docs/visuals/amh-loop-layered-architecture.zh.svg) | 接入、维护、召回、治理、评估五层如何分工。 | 是能力面说明，不替代代码、测试和 CLI 输出。 |" in readme_zh
    assert "| [总控图](./docs/visuals/amh-operating-loop.zh.svg) | Query Signal 到 ContextPack，再到 Feedback / Governance / Loop 的闭环。 | 召回分数只决定候选顺序；注入许可仍由防火墙处理。 |" in readme_zh
    assert "| [召回完整链路图](./docs/visuals/retrieval-complete-flow.zh.svg) | 用户问题、过滤、BM25/vector、RRF、decay、feedback、MMR/Hopfield、ContextFirewall 和 ContextPack。 | 解释排序和注入路径，不等于某次实时搜索输出。 |" in readme_zh
    assert "[Benchmark Report](#benchmark-report)" in readme
    assert "[Benchmark Report](./docs/evaluation/amh-full-ranking-optimized-full/latest-memory-benchmark-report.zh.md)" not in readme
    assert "[Benchmark Report](./docs/evaluation/amh-full-ranking-optimized-full/all-memory-benchmark-report-preview.html)" not in readme
    assert "For the full technical audit, algorithm walk-through, and detailed benchmark" in readme
    assert "boundaries, see [README.zh.md](./README.zh.md)" in readme
    assert "Current benchmark status and reproduction entry points:" in readme
    assert "./docs/evaluation/amh-full-ranking-optimized-full/all-memory-benchmark-report-preview.png" in readme
    assert "Full AMH benchmark report rendered from the MemoryData paper figure with AMH appended metrics" in readme
    assert "arXiv 2606.24775: Are We Ready For An Agent-Native Memory System?" in readme
    assert "[MemoryData paper figure with AMH appended metrics](./docs/evaluation/amh-full-ranking-optimized-full/all-memory-benchmark-report-preview.html)" not in readme
    assert "AMH appended paper-style" in readme
    assert "LongMemEval-S retrieval | AMH ranking: 500 / 500 cases, R@5 97.40%, R@10 98.40%, MRR 91.29%" in readme
    assert "| LoCoMo 4cat QA full | LoCoMo | passed | 1540 / 1540 QA | EM 16.04%; F1 36.05%; ROUGE-L Recall 45.70%" in readme
    assert "| Reproduce command | `python benchmarks/run_memory_benchmarks.py --output-dir docs/evaluation` |" in readme
    assert "| Primary artifacts | `docs/evaluation/amh-full-ranking-optimized-full/memorydata-external-benchmark-report.json`" in readme
    assert "Report timestamp" not in readme
    assert "Last generated" not in readme
    english_nav = next(line for line in readme.splitlines() if "[中文版]" in line)
    assert english_nav.startswith("[Official Website](https://aihub0508.com/)")
    assert "[Benchmark Report](#benchmark-report)" in english_nav
    assert "[Capability Map](#engineering-capability-map)" in english_nav
    assert "[Architecture Map](#engineering-architecture-map)" in english_nav
    assert "docs/visuals/amh-metrics-governance-collaboration-map.html" not in english_nav
    assert "docs/visuals/agent-memory-hub-architecture-map.html" not in english_nav
    assert "[评测报告](./docs/evaluation/amh-full-ranking-optimized-full/all-memory-benchmark-report-preview.html)" not in readme_zh
    chinese_nav = next(line for line in readme_zh.splitlines() if "[English]" in line)
    assert chinese_nav.startswith("[官网](https://aihub0508.com/)")
    assert "[能力账本](#capability-ledger)" in chinese_nav
    assert "[评测门禁](#system-benchmark-gate)" in chinese_nav
    assert "[架构图谱](#architecture-map)" in chinese_nav
    assert "[本地 HTML 预览](./docs/visuals/readme-zh-preview.html)" not in chinese_nav
    internal_verification_dir = "/".join(["docs", "verification", ""])
    assert internal_verification_dir not in chinese_nav
    assert "[评测报告](./docs/evaluation/amh-full-ranking-optimized-full/latest-memory-benchmark-report.zh.md)" not in chinese_nav
    assert "品牌资产" not in chinese_nav
    assert "agent-memory-hub-architecture-map.html" not in chinese_nav
    assert "当前发布审计口径：本机已完成 `memory benchmark system`、LongMemEval-S retrieval、LongMemEval-S QA/Judge、MemoryAgentBench、LoCoMo、LongBench、MemBench" in readme_zh
    assert "### 评测报告" in readme_zh
    assert '<a id="benchmark-report"></a>' in readme_zh
    assert "memory adapter install <adapter> --format json" in readme
    assert "memory adapter install <adapter> --format json" in readme_zh
    assert "`needs_client`" in readme
    assert "`malformed_config`" in readme
    assert "`needs_client`" in readme_zh
    assert "`malformed_config`" in readme_zh
    assert "`core_impact`" in readme_zh
    assert "核心 adapter 失败时使用 `memory doctor --fix`" in readme_zh
    assert "MemoryData 外部横评与 AMH 本地指标（GitHub 可读 Markdown）" not in readme_zh
    assert "AMH 记忆评测审计报告（HTML 本地预览）" not in readme_zh
    assert "GitHub 仓库里打开 `.html` 会看到源码；需要渲染版时请本地打开或发布到 GitHub Pages" not in readme_zh
    forbidden_meta_narration = [
        "The benchmark report is embedded here instead of requiring a jump",
        "docs/evaluation/` remain the machine-readable evidence",
        "README 不再要求读者跳到另一个页面",
        "下面直接展示发布审计需要看的报告内容",
        "评测命令会刷新 `docs/evaluation/` 下的机器可读产物",
        "不作为读者理解 AMH 的前置入口",
        "README 不固化本机状态快照",
        "读者路径",
        "读者可以先把",
        "可以跳到后面的",
    ]
    public_readmes = "\n".join([readme, readme_zh])
    for phrase in forbidden_meta_narration:
        assert phrase not in public_readmes
    assert "当前发布审计口径：本机已完成" in readme_zh
    assert "./docs/evaluation/amh-full-ranking-optimized-full/all-memory-benchmark-report-preview.png" in readme_zh
    assert "AMH 完整评测报告：MemoryData 论文原图和 AMH 追加指标" in readme_zh
    assert "arXiv 2606.24775：Are We Ready For An Agent-Native Memory System?" in readme_zh
    assert "[MemoryData 论文原图 + AMH 追加指标报告](./docs/evaluation/amh-full-ranking-optimized-full/all-memory-benchmark-report-preview.html)" not in readme_zh
    assert "论文图风格追加 AMH 柱状图" in readme_zh
    assert "结论、指标、复现命令和不能外推的边界如下" in readme_zh
    assert "| 复现命令 | `python benchmarks/run_memory_benchmarks.py --output-dir docs/evaluation` |" in readme_zh
    assert "| artifact 路径 | `docs/evaluation/amh-full-ranking-optimized-full/memorydata-external-benchmark-report.json`" in readme_zh
    assert "报告生成时间" not in readme_zh
    assert "last generated" not in readme_zh
    assert "上表链接在 GitHub 上会停留在当前 README 页面，只滚动到对应图" not in readme_zh
    assert "下面 7 张主图直接嵌在 README 里" not in readme_zh
    assert "打开原图" not in readme_zh
    assert "真实边界与路线图" not in readme_zh
    assert "不允许夸大" not in readme_zh
    assert "README 内图谱预览" not in readme_zh
    assert "readme-内图谱预览" not in preview_zh
    assert "上表链接在 GitHub 上会停留在当前" not in preview_zh
    assert "README 页面，只滚动到对应图" not in preview_zh
    architecture_reading_table = readme_zh[
        readme_zh.index("先按这张表读图：") : readme_zh.index("<a id=\"diagram-lifecycle\">")
    ]
    for anchor_link in [
        "[生命周期图](#diagram-lifecycle)",
        "[总控图](#diagram-operating-loop)",
        "[产品架构图](#diagram-product-architecture)",
        "[技术架构图](#diagram-technical-architecture)",
        "[维护 + 召回时序图](#diagram-memory-lifecycle-sequence)",
        "[数据链路图](#diagram-data-flow)",
        "[召回完整链路图](#diagram-retrieval-complete-flow)",
    ]:
        assert anchor_link in architecture_reading_table
    assert "./docs/visuals/amh-loop-layered-architecture.zh.svg" not in architecture_reading_table
    for anchor in [
        "diagram-lifecycle",
        "diagram-operating-loop",
        "diagram-product-architecture",
        "diagram-technical-architecture",
        "diagram-memory-lifecycle-sequence",
        "diagram-data-flow",
        "diagram-retrieval-complete-flow",
    ]:
        assert f'id="{anchor}"' in readme_zh
        assert f'id="{anchor}"' in preview_zh
    quick_start_zh = readme_zh[readme_zh.index("## 快速入门") : readme_zh.index("## 用一个真实问题看懂 AMH")]
    assert "### 1. 安装" in quick_start_zh
    assert "curl -fsSL https://github.com/liuyang0508/agent-memory-hub/releases/latest/download/install.sh | sh" in quick_start_zh
    assert 'powershell -ExecutionPolicy ByPass -c "irm https://github.com/liuyang0508/agent-memory-hub/releases/latest/download/install.ps1 | iex"' in quick_start_zh
    assert "brew install --cask liuyang0508/agent-memory-hub/agent-memory-hub" in quick_start_zh
    assert "npm install -g agent-memory-hub" in quick_start_zh
    assert "GitHub Release asset、npm package、Homebrew tap/cask 需要分别发布" in readme_zh
    assert "同一 runner / 同 dataset / 同 metric 的结果可以横向比较" in readme_zh
    assert "DB-Bench 当前没有本机 AMH 结果，继续标为缺 runner/data" in readme_zh
    assert "| 总状态 | `PASS_WITH_MEMORYDATA_FULL` |" in readme_zh
    assert "| LongMemEval-S retrieval | AMH ranking 500 / 500 cases，R@5 97.40%，R@10 98.40%，MRR 91.29% |" in readme_zh
    assert "| 弱意图阻断 | 100.00% |" in readme_zh
    assert "| AMH ranking | passed | 500 / 500 | 97.40% | 98.40% | 91.29% |" in readme_zh
    assert "| Generation | passed | 500 | Exact EM=7.40; Substring EM=27.00; F1=20.59; ROUGE-L F1=19.87; ROUGE-L Recall=35.28 |" in readme_zh
    assert "| 准确召回 AR | passed | 500 / 500 | EM 48.00%; F1 67.30%; EventQA Recall 48.20% |" in readme_zh
    assert "| LoCoMo 4cat QA full | LoCoMo | passed | 1540 / 1540 QA | EM 16.04%; F1 36.05%; ROUGE-L F1 35.50%; ROUGE-L Recall 45.70% |" in readme_zh
    assert "评测命令输出写入 `docs/evaluation/`；细粒度 JSON、逐题输出和历史执行记录用于复核，不进入摘要口径。" in readme_zh
    assert (
        '<a id="external-references-and-competitor-benchmark"></a>\n\n'
        "## 外部资料与竞品对标\n\n| 类别 | 资料 / 竞品 | 链接 / 来源 |"
    ) in readme_zh
    assert "外部资料、竞品和 benchmark 的来源，以及 AMH 借鉴或对标的范围" not in readme_zh
    assert "公开自报数字、设计参考和 blocked source 只作为资料来源" not in readme_zh
    assert "外部资料和竞品对标如下。不是统一复跑排行榜" not in readme_zh
    assert "| 类别 | 资料 / 竞品 | 链接 / 来源 | AMH 吸收或对标什么 | 证据边界 |" in readme_zh
    assert "| 方法论 | Karpathy LLM-Wiki |" in readme_zh
    assert "| 外部 benchmark source | MEMTRON/AgentMemory-Bench |" in readme_zh
    assert "当前匿名 source-lock 未拿到可复核 HEAD；只记录 blocked，不写外部结果" in readme_zh
    assert "| 横评口径 | rohitg00/agentmemory |" in readme_zh
    assert "| 设计同类 | TencentDB-Agent-Memory |" in readme_zh
    assert "| Hermes provider 生态 | Honcho / Hindsight / Holographic / RetainDB / ByteRover / Supermemory |" in readme_zh
    assert "| 闭源一方记忆 | ChatGPT memory / Claude memory |" in readme_zh
    assert "| 算法 / 压缩 | Headroom / Hopfield / RRF / MMR |" in readme_zh
    assert "| 协议 / 客户端接入 | MCP clients |" in readme_zh
    assert "具体分数、竞品表、论文原图追加柱状图和缺口项以 HTML 报告为准" not in readme_zh
    assert "完整落地 loop 见 [AgentMemory-Bench / MemoryData 外部评测 Loop]" not in readme_zh
    assert "[总控图](./docs/visuals/amh-operating-loop.zh.svg)" in readme_zh
    assert "Query Signal 到 ContextPack，再到 Feedback / Governance / Loop 的闭环" in readme_zh
    assert "遗忘曲线" in readme_zh
    assert "decay_coefficient" in readme_zh
    assert "MemoryItem + Index Projection + Runtime Ledger" in readme_zh
    assert "Evidence -> MemoryItem -> Index Projection -> RetrievedItem -> RankedItem -> FirewalledItem -> ContextPack -> FeedbackEvent" in readme_zh

    retrieval_order = [
        "用户问题",
        "hook prompt normalization",
        "query_signal 前置门禁",
        "元数据 / 记忆类型 / project / tags / tenant 过滤",
        "FTS/BM25 与向量并行召回",
        "RRF 融合",
        "metadata phrase boost",
        "status / handoff supplement",
        "optional cross-encoder rerank",
        "confidence 与 decay",
        "feedback value weight",
        "runtime / status boost",
        "temporal stale filter",
        "supersession filter",
        "optional MMR",
        "optional Hopfield expansion",
        "optional graph expansion",
        "ContextFirewall",
        "locator / overview / detail 分层上下文装载",
        "context_pack",
        "adapter injection",
    ]
    recall_start = readme_zh.index("```text\n用户问题")
    recall_end = readme_zh.index("```\n\n<p align=\"center\">", recall_start)
    recall_chain = readme_zh[recall_start:recall_end]
    positions = [recall_chain.index(term) for term in retrieval_order]
    assert positions == sorted(positions)

    for visual in [
        "amh-loop-layered-architecture.zh.svg",
        "amh-operating-loop.zh.svg",
        "product-architecture.zh.svg",
        "technical-architecture.zh.svg",
        "memory-lifecycle-sequence.zh.svg",
        "retrieval-complete-flow.zh.svg",
        "retrieval-algorithm-stack.zh.svg",
        "readme-structure-map.zh.svg",
        "memory-maintenance-sequence.zh.svg",
        "memory-retrieval-sequence.zh.svg",
        "data-flow.zh.svg",
        "retrieval-scoring-pipeline.zh.svg",
        "retrieval-score-waterfall.zh.svg",
        "amh-adapter-capability-boundary.zh.svg",
        "amh-metrics-governance-collaboration-map.html",
        "agent-memory-hub-architecture-map.html",
    ]:
        assert visual in readme_zh
    architecture_section = readme_zh[
        readme_zh.index("## 工程架构图谱") : readme_zh.index("<a id=\"common-commands\">")
    ]
    primary_order = [
        "amh-loop-layered-architecture.zh.svg",
        "amh-operating-loop.zh.svg",
        "product-architecture.zh.svg",
        "technical-architecture.zh.svg",
        "memory-lifecycle-sequence.zh.svg",
        "data-flow.zh.svg",
        "retrieval-complete-flow.zh.svg",
    ]
    positions = [architecture_section.index(term) for term in primary_order]
    assert positions == sorted(positions)
    assert architecture_section.index("retrieval-complete-flow.zh.svg") < architecture_section.index("README 结构地图")
    assert "Evidence -> MemoryItem -> Index Projection -> RetrievedItem -> RankedItem -> FirewalledItem -> ContextPack -> FeedbackEvent" in architecture_section

    assert "工程架构图谱" in preview_zh
    assert 'id="benchmark-report"' in preview_zh
    assert 'href="readme-preview.css"' in preview_zh
    assert 'href="docs/visuals/readme-preview.css"' not in preview_zh
    assert 'href="./docs/visuals/' not in preview_zh
    assert 'src="./docs/visuals/' not in preview_zh
    assert 'src="../evaluation/amh-full-ranking-optimized-full/all-memory-benchmark-report-preview.png"' in preview_zh
    assert 'href="../evaluation/amh-full-ranking-optimized-full/all-memory-benchmark-report-preview.html"' not in preview_zh
    assert "报告生成时间" not in preview_zh
    assert "last generated" not in preview_zh
    assert 'src="./web/static/agent-logos/' not in preview_zh
    assert 'class="agent-matrix"' in preview_zh
    assert 'class="agent-logo" src="../../web/static/agent-logos/claude-code.svg"' in preview_zh
    assert 'class="agent-logo" src="../../web/static/agent-logos/codex.png"' in preview_zh
    assert 'class="agent-logo" src="../../web/static/agent-logos/aone-copilot.png"' in preview_zh
    preview_css = _read("docs/visuals/readme-preview.css")
    assert "table.agent-matrix" in preview_css
    assert "width: 46px !important;" in preview_css
    for anchor in [
        "sixty-second-amh",
        "why-shared-second-brain",
        "loop-engineering-view",
        "quick-start",
        "running-example",
        "running-candidate-pool",
        "core-object-map",
        "maintenance-before-recall",
        "maintenance-flow",
        "recall-flow",
        "algorithm-map",
        "sample-scoring-chain",
        "loop-engineering",
        "runtime-integration-model",
        "capability-ledger",
        "agent-adapter-matrix",
        "command-manual",
        "system-benchmark-gate",
        "architecture-map",
    ]:
        assert f'id="{anchor}"' in readme_zh or f"#{anchor}" in readme_zh
    assert "sources/conversations" in readme_zh
    assert "SearchFilter" in readme_zh
    assert "time_retention = 0.5 ^ (days_since_reference / half_life)" in readme_zh
    assert "Reciprocal Rank Fusion" in readme_zh
    assert "Maximal Marginal Relevance" in readme_zh
    assert "locator/overview/detail" in readme_zh
    assert "raw / consolidated / skill" in readme_zh
    assert "不是自动 runner" in readme_zh
    assert "./install.sh --verify-only" in readme_zh
    assert "./install.sh --uninstall" in readme_zh
    assert "curl -fsSL https://github.com/liuyang0508/agent-memory-hub/releases/latest/download/install.sh | sh -s -- --uninstall" in readme_zh
    assert "rm -rf ~/.agent-memory-hub" in readme_zh
    assert "memory adapter install-verify" in readme_zh
    assert "memory adapter uninstall" in readme_zh
    assert "System benchmark: PASS cases=240 items=1234" in readme_zh
    assert "python benchmarks/run_memory_benchmarks.py --output-dir docs/evaluation" in readme_zh
    assert "| indexed items | 1234 |" in readme_zh
    assert "Agent 适配矩阵" in readme_zh
    assert "本机状态以 doctor / verify / runtime evidence 为准" in readme_zh
    internal_verification_dir = "/".join(["docs", "verification", ""])
    assert internal_verification_dir not in readme_zh
    assert "逐任务验证流水账不进入公开仓库" in readme_zh
    assert "AMH 在解决什么问题" in preview_zh
    assert "快速入门：3 分钟完成安装、写入和召回" in preview_zh
    assert "用一个真实问题看懂 AMH" in preview_zh
    assert "召回完整链路" in preview_zh
    assert "算法地图" in preview_zh
    assert "样例得分链路" in preview_zh
    assert "Agent 适配矩阵" in preview_zh
    assert "Claude Code" in preview_zh
    assert "Qoder Work" in preview_zh
    assert "PASS_WITH_EXTERNAL_SOURCE_LOCK" in openviking_preview
    assert "1237" in openviking_preview
    assert "Recall@10" in openviking_preview
    assert "99.78%" in openviking_preview
    assert "datasets、rank_bm25、四类 benchmark 数据集和 OpenAI-compatible endpoint" in openviking_preview
    latest_report = _read("docs/evaluation/latest-memory-benchmark-report.zh.md")
    full_latest_report = _read("docs/evaluation/amh-full-ranking-optimized-full/latest-memory-benchmark-report.zh.md")
    full_html_report = _read("docs/evaluation/amh-full-ranking-optimized-full/all-memory-benchmark-report-preview.html")
    loop_doc = _read("docs/evaluation/agentmemory-bench-loop.zh.md")
    assert "./amh-memorydata-report-chart.svg" in full_latest_report
    assert "AMH 评测报告快照：论文图下方追加本机复现指标" in full_latest_report
    assert "生成时间" not in full_latest_report
    assert "生成时间" not in full_html_report
    assert "MemoryData 论文原图评分" in full_html_report
    assert "论文图风格追加 AMH 柱状图" in full_html_report
    assert "论文原图 8 指标覆盖矩阵" in full_html_report
    for doc in [latest_report, loop_doc, openviking_preview]:
        assert "四源融合" in doc
        assert "agentmemory COMPARISON" in doc
        assert "State-Bench" in doc
        assert "MemoryAgentBench" in doc
        assert "OpenViking" in doc
    assert "准确召回 / 测试时学习 / 长程理解 / 冲突解决" in latest_report
    assert "pass^5" in latest_report
    assert "LongMemEval-S Retrieval Loop" in latest_report
    assert "longmemeval_s_cleaned.json" in latest_report
    assert "materialize_memory_eval_datasets.py --dataset longmemeval-s" in latest_report
    assert "| retrieval-only smoke | done |" in latest_report
    assert "| AMH ranking run | done |" in latest_report
    assert "| report publish | rk-full-published |" in latest_report
    assert "| R@5 | 100.00% |" in latest_report
    assert "| MRR | 64.00% |" in latest_report
    assert "| MRR | 90.00% |" in latest_report
    assert "| lexical | passed | 500 / 500 | 89.00% | 93.60% | 78.74% |" in latest_report
    assert "| AMH ranking | passed | 500 / 500 | 97.40% | 98.40% | 91.29% |" in latest_report
    assert "R@K-only full；不包含 answer generation / judge" in latest_report
    assert "source lock -> dataset materialize -> adapter mapping -> smoke run -> full matrix -> result normalize -> report publish" in loop_doc
    assert "retrieval-only smoke" in loop_doc
    assert "AMH ranking report published" in loop_doc
    assert "benchmarks/materialize_memory_eval_datasets.py --dataset longmemeval-s" in loop_doc
    assert "LongMemEval-S" in openviking_preview
    assert "AMH ranking" in openviking_preview
    assert "55 endpoints" not in audit
    assert "route count 滞后" not in audit
    assert "memory api-docs` 已改为从 `web.app` 动态枚举" in audit
    assert "可信上下文操作系统" in readme_zh
    assert "历史同步会不会把旧聊天自动写进共享记忆？" in readme_zh
    assert "团队试点与评审指南" not in readme_zh
    assert "试点 Checklist" not in readme_zh
    assert "./docs/ata/agent-memory-hub-technical-sharing.zh.md" not in readme_zh
    assert "./docs/ata/agent-memory-hub-adoption-playbook.zh.md" not in readme_zh


def test_readme_embeds_ata_valuable_boundaries_without_playbooks():
    readme_zh = _read("README.zh.md")

    assert "可信上下文操作系统" in readme_zh
    assert "原始对话不是长期记忆" in readme_zh
    assert "不要固化某台机器的状态矩阵" in readme_zh
    assert "ML/DL 是 advisory 和 benchmark gate" in readme_zh
    assert "只扫描当前机器可读路径" in readme_zh
    assert "本机历史同步** 面板会自动扫描当前机器可读的 Codex、Claude Code、Qoder、QoderWork、Wukong 本机历史源" in readme_zh
    assert "local-history-sync-admin.zh.png" in readme_zh
    assert "MEMORY_HUB_WUKONG_HISTORY_ROOT" not in readme_zh
    assert "./install.sh --verify-only" in readme_zh
    assert "./install.sh --uninstall" in readme_zh
    assert "保留 `~/.agent-memory-hub` 里的用户记忆、证据和索引" in readme_zh
    assert "不要只看配置文件是否写入" in readme_zh
    assert "不把旧聊天自动写入共享记忆" in readme_zh
    assert "团队试点" not in readme_zh
    assert "推广使用手册" not in readme_zh
    assert "技术分享文档" not in readme_zh
    assert "P0 单人试点" not in readme_zh
    assert "P1 小组试点" not in readme_zh
    assert "P2 团队推广" not in readme_zh


def test_animated_diagrams_preview_uses_readme_style():
    preview = _read("docs/visuals/amh-animated-diagrams-preview.html")

    assert 'href="readme-preview.css"' in preview
    assert "<h1>Agent Memory Hub</h1>" in preview
    assert "让每一次智能协作，都沉淀为下一次出发。" in preview
    assert "一个本地优先、可追溯、可治理的跨智能体可信上下文操作系统。" in preview
    assert ">English</a> | <a" in preview
    assert ">战略</a> | <a" in preview
    assert ">路线图</a> | <a" in preview
    assert ">架构图谱</a> | <a" in preview
    assert ">架构说明</a>" in preview
    assert "img.shields.io/badge/%E8%AE%B8%E5%8F%AF%E8%AF%81-Apache%202.0-blue.svg" in preview
    assert "<h2>动态架构图谱</h2>" in preview
    assert "本轮事实核准" in preview
    assert "<table>" in preview

    legacy_storyboard_style = [
        "<h1>Agent Memory Hub 动态架构图预览</h1>",
        'class="wrap"',
        'class="facts"',
        'class="card"',
    ]
    for marker in legacy_storyboard_style:
        assert marker not in preview

    assert "`extractions" not in preview
    assert "`items_fts" not in preview


def test_docs_record_reversible_context_pack_contract():
    readme = _read("README.md")
    readme_zh = _read("README.zh.md")
    architecture = _read("docs/architecture.md")
    discipline = _read("agent_runtime_kit/AGENT_MEMORY_DISCIPLINE.md")

    assert "reversible `context_pack`" in readme
    assert "`context_pack` is the compressed prompt view plus `detail_uri` and retrieve hints" in readme
    assert "Compressed prompt view + `detail_uri` or AMH-local CCR sidecar" in readme
    assert "可逆 `context_pack`" in readme_zh
    assert "`context_pack` 是压缩后的提示词视图，加上 `detail_uri` 和读取提示" in readme_zh
    assert "Headroom-style local" in readme
    assert "Compression benchmark gate" in readme
    assert "预算不足时，detail 会降到 overview，再降到 locator" in readme_zh
    assert "Context packing" in architecture
    assert "read_memory(id, head=2000, view='detail')" in architecture
    assert "自动注入默认给压缩视图和 retrieve hint" in discipline


def test_docs_record_ml_dl_enhancement_boundary():
    assessment = _read("docs/audit/2026-06-21-ml-dl-enhancement-assessment.md")

    assert "ML/DL 不进入默认写入、检索、压缩或注入链路" in assessment
    assert "ML/DL advisory gate" in assessment
    assert "few-shot compression gate" in assessment
    assert "advisory" in assessment
    assert "release gate" in assessment


def test_docs_record_retrieval_trace_contract():
    readme = _read("README.md")
    readme_zh = _read("README.zh.md")
    architecture = _read("docs/architecture.md")

    assert "Optional retrieval trace" in readme
    assert "initial BM25/vector ranks" in readme
    assert "可选检索轨迹" in readme_zh
    assert "初始 BM25/向量排名" in readme_zh
    assert "Retriever.search(..., explain=True)" in architecture
    assert "trace is observational" in architecture


def test_docs_record_loop_contract_product_methodology():
    readme = _read("README.md")
    readme_zh = _read("README.zh.md")
    architecture = _read("docs/architecture.md")
    structure_map = _read("docs/visuals/readme-structure-map.svg")
    structure_map_zh = _read("docs/visuals/readme-structure-map.zh.svg")
    product_architecture = _read("docs/visuals/product-architecture.svg")
    product_architecture_zh = _read("docs/visuals/product-architecture.zh.svg")

    assert "Loop Contract" in readme
    assert "fact layer, verification layer, and governance layer" in readme
    assert "multi-agent loop fact layer, verification layer, and governance layer" in readme
    assert "memory loop run --contract" in readme
    assert "memory loop gate open" in readme
    assert "多智能体循环的事实层、验证层和治理层" in readme_zh
    assert "memory loop run --contract" in readme_zh
    assert "memory loop gate open" in readme_zh
    assert "goal / state / action / feedback / verifier / budget / stop condition / human gate" in architecture
    assert "LoopOrchestrator" in architecture
    assert "不是默认自动 runner" in readme_zh
    assert "Loop Contract" in readme_zh
    assert "Loop Contract governance" in structure_map
    assert "Loop Contract 治理" in structure_map_zh
    assert "human gate lifecycle" in product_architecture
    assert "human gate 生命周期" in product_architecture_zh


def test_adapter_docs_record_current_evidence_state():
    readme = _read("README.md")
    readme_zh = _read("README.zh.md")
    gap_matrix = _read("docs/audit/2026-06-09-current-state-gap-matrix.md")

    assert "## Layered Agent Access Model" in readme
    assert "| Awareness channel | Required | Tells the agent that AMH exists" in readme
    assert "MCP-only means the tool is configured; awareness tells the model when to use it." in readme
    assert "## Agent Runtime Kit 与 Agent Integrations 如何协作" in readme_zh
    assert "agent_integrations  -> 负责“怎么接入某个 Agent”" in readme_zh
    assert "## Agent Adapter Matrix" in readme
    assert "## Agent 适配矩阵" in readme_zh
    assert '<table class="agent-matrix">' in readme
    assert '<table class="agent-matrix">' in readme_zh
    assert "same Agent brand assets used by the Web Admin landing cover" in readme
    assert "这组图标复用后管平台封面的同一套 Agent 资产" in readme_zh
    for logo_path in [
        "./web/static/agent-logos/claude-code.svg",
        "./web/static/agent-logos/codex.png",
        "./web/static/agent-logos/openclaw-readme.svg",
        "./web/static/agent-logos/qoder-work.svg",
        "./web/static/agent-logos/wukong-brand-logo.png",
        "./web/static/agent-logos/aone-copilot.png",
    ]:
        assert logo_path in readme
        assert logo_path in readme_zh
    assert "codex-openai-mark.svg" not in readme
    assert "codex-openai-mark.svg" not in readme_zh
    assert "已接入" in readme_zh
    assert "接入中" in readme_zh
    assert "这张矩阵只展示接入面，不等于本机 verified 状态" in readme_zh
    assert "不维护本机状态矩阵" in readme_zh
    assert "Agent 接入图标墙" not in readme_zh
    assert "本机状态以 doctor / verify / runtime evidence 为准" in readme_zh
    assert "memory adapter list --format json" in readme_zh
    assert "memory adapter install-verify <adapter> --format json" in readme_zh
    assert "适配器支持矩阵" not in readme_zh
    assert "Adapter Support Matrix" not in readme
    assert "Current local truth from `memory adapter list --format json`" not in readme
    assert "| `verified` | 11 |" not in readme_zh
    assert "QoderWork 是 verified，因为存在 GUI context-effective evidence" not in readme_zh
    assert "`runtime_event_count`" in gap_matrix
    assert "`verification_status`" in gap_matrix
    assert "agent_brain/agent_integrations/codex.py" in gap_matrix
    assert "agent_brain/agent_integrations/continue_dev.py" in gap_matrix
    assert "agent_brain/agent_integrations/github_copilot.py" in gap_matrix
    assert "tests/unit/test_adapter_robustness_p36.py" in gap_matrix
    assert "https://docs.openclaw.ai/cli/mcp" in gap_matrix
    assert "https://hermes-agent.nousresearch.com/docs/user-guide/features/tool-calling-mcp" in gap_matrix
    assert "https://github.com/tinyhumansai/openhuman" in gap_matrix
    assert "https://docs.qoder.com/extensions/hooks" in gap_matrix
    assert "https://github.com/opensquilla/opensquilla" in gap_matrix


def test_open_star_visuals_track_install_ready_status():
    readme = _read("README.md")
    readme_zh = _read("README.zh.md")
    readme_preview_zh = _read("docs/visuals/readme-zh-preview.html")
    strategy = _read("STRATEGY.md")
    preview = _read("docs/visuals/amh-animated-diagrams-preview.html")
    boundary_svg = _read("docs/visuals/amh-adapter-capability-boundary.svg")
    boundary_svg_zh = _read("docs/visuals/amh-adapter-capability-boundary.zh.svg")

    current_surfaces = "\n".join(
        [readme, readme_zh, readme_preview_zh, strategy, preview, boundary_svg, boundary_svg_zh]
    )
    stale_claims = [
        "Open* remains planned",
        "Open* 仍按规划能力画虚线",
        "OpenClaw/OpenHuman/OpenSquilla 是规划中",
        "OpenSquilla planned",
        "OpenSquilla 规划中",
        "规划能力画虚线",
        "current `wip` set includes OpenHuman",
        "6 `install-ready` adapters plus 6 `wip` stubs",
    ]
    for claim in stale_claims:
        assert claim not in current_surfaces

    assert "Agent Adapter Matrix" in readme
    assert "Agent 适配矩阵" in readme_zh
    assert "This\nis an integration-surface map, not a local verified-status matrix" in readme
    assert "已接入 Agent 优先展示，其余 Agent 标记为接入中" in readme_zh
    assert "不要固化某台机器的状态矩阵" in readme_zh
    assert "Current local truth from `memory adapter list --format json`: **16 adapters =" not in readme
    assert "11 verified, 4 install-ready, 1 wip**" not in readme
    assert "当前快照：11 verified、4 install-ready、1 wip。" not in readme_zh
    assert "11 `verified` adapters" in strategy
    assert "4 `install-ready` adapters" in strategy
    assert "Claude Code、OpenClaw、Qoder、Wukong 仍为 install-ready；QoderWork 已有 GUI context-effective 证据；MuleRun 仍为 wip。" in preview
    assert "install-ready" in boundary_svg
    assert "install-ready" in boundary_svg_zh
    assert "Open* adapters" not in boundary_svg
    assert "Open* 适配器" not in boundary_svg_zh
    for adapter_name in ["OpenClaw", "OpenHuman", "OpenSquilla"]:
        assert f">{adapter_name}<" in boundary_svg
        assert f">{adapter_name}<" in boundary_svg_zh


def test_adapter_boundary_visual_keeps_hook_rail_separate_from_cards():
    boundary_svg = _read("docs/visuals/amh-adapter-capability-boundary.svg")
    boundary_svg_zh = _read("docs/visuals/amh-adapter-capability-boundary.zh.svg")

    cluttered_card_crossing_paths = [
        "M215 300 L215 600",
        "M565 300 L565 600",
        "M915 300 L915 338 L1120 338 L1120 600",
        "M215 528 L215 660",
        "M565 528 L565 660",
        "M915 528 L915 660",
    ]
    for path in cluttered_card_crossing_paths:
        assert path not in boundary_svg
        assert path not in boundary_svg_zh

    assert "AMH-owned hook vocabulary" in boundary_svg
    assert "AMH 自有 hook 词表" in boundary_svg_zh


def test_architecture_map_adapter_counts_match_current_truth_contract():
    architecture_map = _read("docs/visuals/agent-memory-hub-architecture-map.html")
    handdrawn_readme = _read("docs/visuals/readme-handdrawn-zh.html")

    assert "11 adapter records" not in architecture_map
    assert "9 install-ready" not in architecture_map
    assert "Web 72 routes" not in architecture_map
    assert "Web 85 routes" not in architecture_map
    assert "85 路由" not in architecture_map
    assert "已验证=0" not in architecture_map
    assert "1442" not in architecture_map
    assert "2026-06-22" not in architecture_map
    assert "16" in architecture_map
    assert "16 个适配器记录：11 个已验证、4 个安装就绪、1 个开发中" in architecture_map
    assert "模型上下文协议 28 个工具" in architecture_map
    assert "91 条接口/通信路由" in architecture_map
    assert "驾驶舱 / 引导" in architecture_map
    assert "候选 / 轨迹" in architecture_map
    assert "verified=11" in architecture_map
    assert "产品架构图" in architecture_map
    assert "技术架构图" in architecture_map
    assert "时序链路图" in architecture_map
    assert "数据链路图" in architecture_map
    assert "AMH 总控图：可信上下文操作回路" in architecture_map
    assert "召回完整链路图" in architecture_map
    assert "检索算法栈拆分图" in architecture_map
    assert "一张总控图，五张放大图。" in architecture_map
    assert "amh-operating-loop.zh.svg" in architecture_map
    assert "product-architecture.zh.svg" in architecture_map
    assert "technical-architecture.zh.svg" in architecture_map
    assert "memory-lifecycle-sequence.zh.svg" in architecture_map
    assert "data-flow.zh.svg" in architecture_map
    assert "retrieval-complete-flow.zh.svg" in architecture_map
    assert "Evidence -&gt; MemoryItem -&gt; Index Projection -&gt; RetrievedItem -&gt; RankedItem -&gt; FirewalledItem -&gt; ContextPack -&gt; FeedbackEvent" in architecture_map
    assert '<span class="num">91</span><span class="label">Web API / WS routes</span>' in handdrawn_readme
    assert "91 条 Web/API/WS routes" in handdrawn_readme


def test_gap_matrix_does_not_preserve_resolved_items_route_risk():
    gap_matrix = _read("docs/audit/2026-06-09-current-state-gap-matrix.md")

    assert "`web/api/routes/items.py` | 27" in gap_matrix
    assert "`web/api/routes/items.py` remains very large" not in gap_matrix

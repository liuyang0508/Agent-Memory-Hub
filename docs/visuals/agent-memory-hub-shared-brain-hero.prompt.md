# Agent Memory Hub 共享第二大脑视觉图 Prompt

## 定位

这张图用于 README 顶部或产品介绍页，作为“多智能体共享第二大脑”的传播型视觉图。

现有 `docs/visuals/*.svg` 仍然是权威技术图，负责表达精确模块、流程和中英文标签。AI 生成图只承担第一眼解释产品隐喻的任务：多个智能体工具接入同一个本地 Markdown 记忆池，并在后续会话中通过召回、治理和证据链获取可信上下文。

## 建议落点

- 最终图片：`docs/visuals/agent-memory-hub-shared-brain-hero.png`
- README 英文版引用位置：标题区之后、`## Demo` 之前
- README 中文版引用位置：标题区之后、`## Demo` 之前
- 建议宽度：`920`
- 建议 alt：
  - 英文：`Agent Memory Hub shared second brain visual`
  - 中文：`Agent Memory Hub 多智能体共享第二大脑视觉图`

## 生成方式

当前内置 image generation 工具可以生成会话内预览，但本轮没有暴露普通文件路径；CLI 可控落盘路径需要 `OPENAI_API_KEY`。

有 key 后可执行：

```bash
python ~/.codex/skills/.system/imagegen/scripts/image_gen.py generate \
  --model gpt-image-2 \
  --prompt-file docs/visuals/agent-memory-hub-shared-brain-hero.prompt.txt \
  --size 1536x1024 \
  --quality high \
  --out docs/visuals/agent-memory-hub-shared-brain-hero.png \
  --no-augment \
  --force
```

## Prompt

```text
Use case: infographic-diagram
Asset type: README hero visual for an open-source developer tool called Agent Memory Hub
Primary request: Create a polished concept visual for a multi-agent shared fact layer. Multiple AI coding agents connect to one local-first Markdown memory pool, then retrieve trusted context in later sessions.
Scene/backdrop: a clean technical workspace diagram with a central luminous knowledge core made of stacked Markdown documents and small memory nodes, surrounded by several distinct abstract agent terminals and IDE panels connected by thin data lines.
Subject: central shared memory hub, local Markdown files, rebuildable index/database layer, governance/filter shield, and multiple agent clients. Keep the visual metaphor clear but not literal product UI.
Style/medium: high-quality editorial technical illustration, sophisticated open-source README banner, semi-flat 3D with crisp vector-like edges and subtle depth, modern but restrained.
Composition/framing: wide landscape banner, 16:9-ish, central hub dominant, agents around the perimeter, readable at README width, enough whitespace around edges, balanced symmetrical flow.
Lighting/mood: clear, trustworthy, precise, calm engineering mood, soft daylight, subtle glow only at the central memory core.
Color palette: neutral off-white background with GitHub-like ink, muted blue, green, purple, amber accents; avoid a one-note purple/blue gradient look.
Materials/textures: paper/Markdown document texture, glassy but restrained data lines, clean panels, no heavy shadows.
Text: no rendered text, no letters, no words, no logos, no watermarks. Leave all labels to be added separately by SVG/Markdown if needed.
Constraints: Must communicate "many agents, one shared second brain, local durable memory, retrieval and governance" without relying on text. Accurate architecture-like visual hierarchy, not a fantasy brain illustration.
Avoid: distorted text, fake UI text, brand logos, mascots, humanoid robots, photographic people, dark cyberpunk, clutter, random icons, illegible diagrams.
```

## README 引用草案

英文版：

```html
<p align="center">
  <img src="./docs/visuals/agent-memory-hub-shared-brain-hero.png" alt="Agent Memory Hub shared second brain visual" width="920">
</p>
```

中文版：

```html
<p align="center">
  <img src="./docs/visuals/agent-memory-hub-shared-brain-hero.png" alt="Agent Memory Hub 多智能体共享第二大脑视觉图" width="920">
</p>
```

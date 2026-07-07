# Agent Memory Hub — 30s demo (v2 — fixed multi-line bugs)
Output benchmarks/demo.gif

Set Shell "bash"
Set FontSize 20
Set FontFamily "JetBrainsMono Nerd Font Mono"
Set Width 2000
Set Height 1100
Set Theme "Dracula"
Set TypingSpeed 25ms

# === SETUP (hidden) ===
# 隔离环境 + 拷真 playbook 进去 + 进项目目录
Hide
Type "cd ~/Desktop/AIAgent/agent-memory-hub"
Enter
Type "export PS1=$'\\e[35m\\W\\e[0m \\e[35m>\\e[0m '"
Enter
Type "export BRAIN_DIR=/tmp/amh-demo"
Enter
Type "rm -rf $BRAIN_DIR && mkdir -p $BRAIN_DIR/items && cp -r ~/.agent-memory-hub/playbook $BRAIN_DIR/"
Enter
Sleep 500ms
Type "clear"
Enter
Show

# === Scene 1: intro + write a decision (one-liner, no backslash continuation) ===
Type "# Agent Memory Hub: cross-LLM shared brain in markdown"
Enter
Sleep 1200ms
Type "# Step 1 — Claude Code writes a 'decision' to the brain:"
Enter
Sleep 800ms
Type 'printf "**决策**: 用 utf-8-sig\n**理由**: macOS Excel 兼容\n**改回去的代价**: 中文乱码" | ./agent_runtime_kit/tools/write-memory.sh --type decision --title "CSV encoding" --summary "utf-8-sig over utf-8" --agent claude-code --tags csv,encoding'
Enter
Sleep 3000ms

# === Scene 2: switch to Codex (conceptually), search same brain ===
Enter
Type "# Step 2 — Tomorrow Codex searches the SAME brain:"
Enter
Sleep 800ms
Type './agent_runtime_kit/tools/search-memory.sh "csv encoding"'
Enter
Sleep 3500ms

# === Scene 3: cross-agent playbook ===
Enter
Type "# Step 3 — Playbook namespace: skills shared across agents"
Enter
Sleep 800ms
Type './agent_runtime_kit/tools/list-playbook.sh --target-agent codex --kind skill'
Enter
Sleep 3500ms

# === Punchline ===
Enter
Type "# Markdown + MCP. No vector DB. No fine-tune."
Enter
Sleep 1500ms
Type 'echo "github.com/<owner>/agent-memory-hub"'
Enter
Sleep 2500ms

#!/usr/bin/env bash
#
# benchmarks/demo.sh — runs the demo manually in YOUR real terminal.
# Use with QuickTime screen recording instead of vhs (vhs can't capture
# Ghostty's transparency / blur / font antialiasing).
#
# Usage:
#   1. Start QuickTime → File → New Screen Recording
#      (Cmd+Shift+5, "Record Selected Portion", drag around Ghostty window)
#   2. Click record
#   3. Run: ./benchmarks/demo.sh
#   4. Wait ~25 seconds, click stop in QuickTime
#   5. Save as demo.mov
#   6. Convert: ffmpeg -i demo.mov -vf "fps=12,scale=1200:-1:flags=lanczos" -loop 0 benchmarks/demo.gif
#

# Setup: isolated brain pool, copy real playbook
export BRAIN_DIR=/tmp/amh-demo
rm -rf "$BRAIN_DIR" && mkdir -p "$BRAIN_DIR/items"
cp -r ~/.agent-memory-hub/playbook "$BRAIN_DIR/" 2>/dev/null

# Visual: typewriter effect helper
type_cmd() {
  local cmd="$1"
  local delay=${2:-0.025}
  for ((i=0; i<${#cmd}; i++)); do
    printf '%s' "${cmd:$i:1}"
    sleep $delay
  done
  printf '\n'
}

clear
sleep 0.5

# Scene 1: write a decision
type_cmd "# Agent Memory Hub: cross-LLM shared brain in markdown"
sleep 1.2

type_cmd "# Step 1 — Claude Code writes a 'decision' to the brain:"
sleep 0.8

CMD='printf "**决策**: 用 utf-8-sig\n**理由**: macOS Excel 兼容\n**改回去的代价**: 中文乱码" | ./agent_runtime_kit/tools/write-memory.sh --type decision --title "CSV encoding" --summary "utf-8-sig over utf-8" --agent claude-code --tags csv,encoding'
type_cmd "$CMD"
eval "$CMD"
sleep 2.5

# Scene 2: search the same brain
type_cmd "# Step 2 — Tomorrow Codex searches the SAME brain:"
sleep 0.8
type_cmd './agent_runtime_kit/tools/search-memory.sh "csv encoding"'
./agent_runtime_kit/tools/search-memory.sh "csv encoding"
sleep 3

# Scene 3: cross-agent playbook
type_cmd "# Step 3 — Playbook namespace: skills shared across agents"
sleep 0.8
type_cmd './agent_runtime_kit/tools/list-playbook.sh --target-agent codex --kind skill'
./agent_runtime_kit/tools/list-playbook.sh --target-agent codex --kind skill
sleep 3

# Punchline
type_cmd "# Markdown + MCP. No vector DB. No fine-tune."
sleep 1.5
type_cmd 'echo "github.com/<owner>/agent-memory-hub"'
echo "github.com/<owner>/agent-memory-hub"
sleep 2

# Cleanup hint
echo ""
echo "# (demo done. Stop QuickTime recording now.)"

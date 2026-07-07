#!/usr/bin/env bash
# obsidian-sync-daemon.sh — 监听 brain pool 变化，实时同步到 Obsidian vault
#
# 用法:
#   ./obsidian-sync-daemon.sh [vault_dir]
#   默认 vault: ~/Documents/BrainVault
#
# 停止: kill $(cat /tmp/obsidian-sync.pid) 或 Ctrl-C

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "$SCRIPT_DIR/_resolve-python.sh"
BRAIN_DIR="${BRAIN_DIR:-$HOME/.agent-memory-hub}"
ITEMS_DIR="$BRAIN_DIR/items"
VAULT_DIR="${1:-$HOME/Documents/BrainVault}"
PID_FILE="/tmp/obsidian-sync.pid"
DEBOUNCE_SEC=2

if [ ! -d "$ITEMS_DIR" ]; then
  echo "ERROR: brain pool not found at $ITEMS_DIR" >&2
  exit 1
fi

mkdir -p "$VAULT_DIR"

do_sync() {
  memory_cli obsidian-export "$VAULT_DIR" --overwrite 2>/dev/null
  echo "[$(date '+%H:%M:%S')] synced to $VAULT_DIR"
}

echo "Brain pool: $ITEMS_DIR"
echo "Obsidian vault: $VAULT_DIR"
echo "Starting initial sync..."
do_sync

echo $$ > "$PID_FILE"
echo "Daemon PID: $$ (saved to $PID_FILE)"
echo "Watching for changes... (Ctrl-C to stop)"

cleanup() {
  rm -f "$PID_FILE"
  echo "Stopped."
  exit 0
}
trap cleanup INT TERM

fswatch -r -l "$DEBOUNCE_SEC" --event Created --event Updated --event Removed "$ITEMS_DIR" | while read -r _; do
  while read -t 0.1 -r _; do :; done
  do_sync
done

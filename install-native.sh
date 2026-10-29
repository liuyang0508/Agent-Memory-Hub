#!/bin/sh
# Independent native organization mode. Never installs the legacy brain hooks.
set -eu
CODE_DIR=$(CDPATH= cd "$(dirname "$0")" && pwd)
HUB=""
STATE="${AMH_NATIVE_HOME:-$HOME/.amh-native-sync}"
DRY=false
VERIFY=false
SERVICE=true
while [ "$#" -gt 0 ]; do
  case "$1" in
    --native) shift ;;
    --hub) [ "$#" -ge 2 ] || exit 2; HUB="$2"; shift 2 ;;
    --state) [ "$#" -ge 2 ] || exit 2; STATE="$2"; shift 2 ;;
    --dry-run) DRY=true; shift ;;
    --verify-only) VERIFY=true; shift ;;
    --no-service) SERVICE=false; shift ;;
    *) echo "Unknown native installer option: $1" >&2; exit 2 ;;
  esac
done
[ -n "$HUB" ] || { echo 'Required: --hub https://your-organization-host' >&2; exit 2; }
if [ "$DRY" = true ]; then
  echo 'AMH native organization install preview'
  echo "Hub: $HUB"
  echo "State: $STATE"
  echo 'Products: Claude Code, Codex CLI, Claude Cowork, detected Codex/ChatGPT desktop client'
  echo 'Legacy brain pipeline: not installed'
  exit 0
fi
PYTHON=""
for candidate in "${AMH_NATIVE_PYTHON:-}" python3.13 python3.12 python3.11 python3; do
  [ -n "$candidate" ] || continue
  if command -v "$candidate" >/dev/null 2>&1 && "$candidate" -c 'import sys; raise SystemExit(sys.version_info < (3,11))' 2>/dev/null; then
    PYTHON=$(command -v "$candidate"); break
  fi
done
[ -n "$PYTHON" ] || { echo 'Python 3.11+ required' >&2; exit 1; }
"$PYTHON" - "$HUB" <<'PY'
import sys,urllib.parse,ipaddress
p=urllib.parse.urlsplit(sys.argv[1]);local=p.hostname=='localhost'
try: local=local or ipaddress.ip_address(p.hostname).is_loopback
except ValueError: pass
if not p.hostname or p.username or p.password or p.query or p.fragment or (p.scheme!='https' and not (local and p.scheme=='http')):
 raise SystemExit('Use HTTPS, or loopback HTTP for local tests; no credentials in URL')
PY
if [ "$VERIFY" = true ]; then
  echo 'Native installer preflight passed; no data/configuration changed'
  exit 0
fi
VENV="$CODE_DIR/.native-venv"
[ -x "$VENV/bin/python" ] || "$PYTHON" -m venv "$VENV"
"$VENV/bin/python" -m pip install -e "$CODE_DIR[web]"
mkdir -p "$HOME/.local/bin"
# The script itself is a console entrypoint; no legacy memory shim is changed.
ln -sfn "$VENV/bin/amh-native" "$HOME/.local/bin/amh-native"
if [ "$SERVICE" = false ]; then
  set -- setup --hub "$HUB" --state "$STATE" --no-service
else
  set -- setup --hub "$HUB" --state "$STATE"
fi
if [ -t 0 ]; then
  exec "$VENV/bin/amh-native" "$@"
elif [ -r /dev/tty ]; then
  exec "$VENV/bin/amh-native" "$@" </dev/tty
else
  echo 'Interactive terminal required. Run amh-native setup after installation.' >&2
  exit 1
fi

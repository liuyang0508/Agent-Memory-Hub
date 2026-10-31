#!/bin/sh
# Organization memory client. Installs a built wheel, so no source checkout is
# left on the member's machine. Never installs the legacy brain hooks.
set -eu

HUB=""
STATE="${AMH_NATIVE_HOME:-$HOME/.amh-native-sync}"
VENV="${AMH_NATIVE_VENV:-$HOME/.amh-native/venv}"
DRY=false
VERIFY=false
SERVICE=true
WHEEL=""
VERSION="${AMH_VERSION:-}"
CODE_DIR=$(CDPATH= cd "$(dirname "$0")" && pwd)
RELEASE_BASE="${AMH_RELEASE_BASE:-https://github.com/liuyang0508/agent-memory-hub/releases/latest/download}"

while [ "$#" -gt 0 ]; do
  case "$1" in
    --org-memory|--native) shift ;;
    --hub) [ "$#" -ge 2 ] || exit 2; HUB="$2"; shift 2 ;;
    --state) [ "$#" -ge 2 ] || exit 2; STATE="$2"; shift 2 ;;
    --venv) [ "$#" -ge 2 ] || exit 2; VENV="$2"; shift 2 ;;
    --wheel) [ "$#" -ge 2 ] || exit 2; WHEEL="$2"; shift 2 ;;
    --version) [ "$#" -ge 2 ] || exit 2; VERSION="$2"; shift 2 ;;
    --dry-run) DRY=true; shift ;;
    --verify-only) VERIFY=true; shift ;;
    --no-service) SERVICE=false; shift ;;
    *) echo "Unknown organization memory option: $1" >&2; exit 2 ;;
  esac
done
[ -n "$HUB" ] || { echo 'Required: --hub https://your-organization-host' >&2; exit 2; }

if [ "$DRY" = true ]; then
  echo 'AMH organization memory install preview'
  echo "Hub: $HUB"
  echo "State: $STATE"
  echo "Venv: $VENV"
  echo 'Source checkout left on disk: none (wheel install)'
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
  echo 'Organization memory preflight passed; no data/configuration changed'
  exit 0
fi

# What to install, in order of preference: an explicit --wheel, a wheel sitting
# beside this script, a wheel built from a checkout, or the published asset.
# A checkout is *built* into a wheel and then installed — never installed
# editable — so nothing on the member's machine points back at source they could
# edit by accident.
WHEEL_URL=""
if [ -n "$WHEEL" ]; then
  [ -f "$WHEEL" ] || { echo "wheel not found: $WHEEL" >&2; exit 1; }
elif ls "$CODE_DIR"/dist/*.whl >/dev/null 2>&1; then
  WHEEL=$(ls "$CODE_DIR"/dist/*.whl | head -1)
elif [ -f "$CODE_DIR/pyproject.toml" ]; then
  echo "Building a wheel from $CODE_DIR"
  "$PYTHON" -m venv "$VENV.build"
  "$VENV.build/bin/python" -m pip install --quiet --upgrade pip >/dev/null 2>&1 || true
  "$VENV.build/bin/python" -m pip install --quiet build >/dev/null 2>&1 ||
    "$VENV.build/bin/python" -m pip wheel --quiet --no-deps "$CODE_DIR" -w "$CODE_DIR/dist" >/dev/null
  if ! ls "$CODE_DIR"/dist/*.whl >/dev/null 2>&1; then
    "$VENV.build/bin/python" -m build --wheel --outdir "$CODE_DIR/dist" "$CODE_DIR" >/dev/null
  fi
  rm -rf "$VENV.build"
  WHEEL=$(ls "$CODE_DIR"/dist/*.whl | head -1)
  [ -n "$WHEEL" ] || { echo 'failed to build a wheel' >&2; exit 1; }
else
  if [ -n "$VERSION" ]; then
    WHEEL_URL="$RELEASE_BASE/agent_memory_hub-$VERSION-py3-none-any.whl"
  else
    WHEEL_URL="$RELEASE_BASE/agent_memory_hub-py3-none-any.whl"
  fi
fi

[ -x "$VENV/bin/python" ] || "$PYTHON" -m venv "$VENV"
"$VENV/bin/python" -m pip install --quiet --upgrade pip >/dev/null 2>&1 || true

if [ -n "$WHEEL_URL" ]; then
  # pip does not accept extras on a URL ("...whl[web]" is read as a path and
  # 404s), so fetch the file first and install it by path.
  echo "Downloading wheel: $WHEEL_URL"
  WHEEL=$(mktemp /tmp/amh-wheel.XXXXXXXX.whl)
  curl -fsSL "$WHEEL_URL" -o "$WHEEL" || { echo 'could not download the wheel' >&2; exit 1; }
fi
echo "Installing wheel: $WHEEL"
"$VENV/bin/python" -m pip install --quiet "$WHEEL[web]"

mkdir -p "$HOME/.local/bin"
# Point at the console entrypoint, not into a source tree.
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

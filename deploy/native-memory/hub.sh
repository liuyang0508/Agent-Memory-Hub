#!/usr/bin/env bash
# One command to install or upgrade the organization memory Hub on a Linux host.
#
#   curl -fsSL https://github.com/liuyang0508/agent-memory-hub/releases/latest/download/hub.sh | sudo bash
#
# Options (pass after `bash -s --`):
#   --version X.Y.Z   install that release instead of the one this script ships with
#   --cn              use mainland mirrors for Python packages (auto-detected otherwise)
#   --no-semantic     keep the Hub on lexical ranking (no model download)
#
# What it does, in order, and stops at the first failure:
#   1. finds Python 3.11-3.13 and systemd;
#   2. downloads the release wheel, upgrade.sh and the model manifest, and checks
#      each against the release's checksums.txt;
#   3. builds the dependency wheelhouse (PyPI, or the Tsinghua mirror in China);
#   4. backs up the database (consistent SQLite snapshot) before touching it;
#   5. runs upgrade.sh: new release beside the old one, pinned model from a
#      Hugging Face mirror (sha256 checked), atomic switch, readiness check,
#      automatic rollback on failure;
#   6. if an older drop-in still pins another model path, comments that line
#      out (backup kept) once the new model is in place, and restores it if the
#      Hub then fails to start;
#   7. waits for semantic recall to finish indexing and prints what runs.
set -euo pipefail

VERSION="__AMH_VERSION__"
RELEASE_SHA="__AMH_RELEASE_SHA__"
REPO="liuyang0508/agent-memory-hub"
BASE="${AMH_RELEASE_BASE:-https://github.com/$REPO/releases/download}"
SERVICE=amh-native
DB=/var/lib/amh-native/hub.sqlite
DROPIN_DIR=/etc/systemd/system/$SERVICE.service.d
SEMANTIC_ENV=/etc/amh-native/semantic.env
WAIT_SEMANTIC="${AMH_WAIT_SEMANTIC:-1800}"
cn=auto
semantic=on

say() { printf '\033[1m[hub]\033[0m %s\n' "$*"; }
die() { printf '\033[31m[hub] %s\033[0m\n' "$*" >&2; exit 1; }

while [ "$#" -gt 0 ]; do
  case "$1" in
    --version) VERSION=${2:-}; RELEASE_SHA=""; shift 2 ;;
    --cn) cn=yes; shift ;;
    --no-semantic) semantic=off; shift ;;
    -h|--help) sed -n '2,25p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) die "unknown option: $1" ;;
  esac
done

[ "$(id -u)" -eq 0 ] || die "run as root (… | sudo bash)"
command -v systemctl >/dev/null || die "systemd is required"
command -v curl >/dev/null || die "curl is required"
[[ "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || die "no version: pass --version X.Y.Z"

PY=""
for candidate in ${AMH_PYTHON:-} python3.12 python3.11 python3.13 python3; do
  path=$(command -v "$candidate" 2>/dev/null) || continue
  if "$path" -c 'import sys, venv; raise SystemExit(0 if (3, 11) <= sys.version_info[:2] <= (3, 13) else 1)' 2>/dev/null; then
    PY=$path; break
  fi
done
[ -n "$PY" ] || die "Python 3.11-3.13 with venv is required (Ubuntu: apt install python3.12 python3.12-venv)"
say "version $VERSION · $("$PY" --version) · $(uname -m)"

work=$(mktemp -d /tmp/amh-hub.XXXXXX)
trap 'rm -rf "$work"' EXIT
fetch() { curl -fsSL --retry 3 --retry-delay 2 --connect-timeout 20 "$1" -o "$2"; }

wheel="agent_memory_hub-$VERSION-py3-none-any.whl"
say "downloading release $VERSION"
for f in checksums.txt "$wheel" upgrade.sh semantic-model.json; do
  fetch "$BASE/v$VERSION/$f" "$work/$f" || die "could not download $f from $BASE/v$VERSION (set AMH_RELEASE_BASE to a mirror)"
done
(
  cd "$work"
  for f in "$wheel" upgrade.sh semantic-model.json; do
    line=$(grep -E "  $f\$" checksums.txt) || { echo "no checksum for $f" >&2; exit 1; }
    printf '%s\n' "$line" | sha256sum -c --quiet - || exit 1
  done
) || die "checksum verification failed; nothing was changed"

if [ -z "$RELEASE_SHA" ] || [[ ! "$RELEASE_SHA" =~ ^[0-9a-f]{40}$ ]]; then
  # A version chosen with --version: name the release by its wheel digest.
  RELEASE_SHA=$(sha256sum "$work/$wheel" | cut -c1-40)
fi

index=()
if [ "$cn" = yes ] || { [ "$cn" = auto ] && ! curl -fsS --max-time 6 -o /dev/null https://pypi.org/simple/pip/; }; then
  index=(-i "${AMH_PIP_INDEX:-https://pypi.tuna.tsinghua.edu.cn/simple}")
  say "using PyPI mirror ${index[1]}"
elif [ -n "${AMH_PIP_INDEX:-}" ]; then
  index=(-i "$AMH_PIP_INDEX")
fi
say "building the dependency wheelhouse"
mkdir -p "$work/wheelhouse"
"$PY" -m venv "$work/pipenv"
"$work/pipenv/bin/python" -m pip download --quiet --disable-pip-version-check \
  ${index[@]+"${index[@]}"} -d "$work/wheelhouse" "$work/$wheel[web,semantic]" \
  || die "dependency download failed (try --cn, or set AMH_PIP_INDEX)"
cp "$work/$wheel" "$work/wheelhouse/"
bundle=$(cd "$work/wheelhouse" && sha256sum ./*.whl | sha256sum | cut -c1-64)

if [ -f "$DB" ]; then
  stamp=$(date -u +%Y%m%dT%H%M%SZ)
  install -d -m 700 /var/backups/amh-native
  snapshot=/var/backups/amh-native/hub-before-$VERSION-$stamp.sqlite
  "$PY" - "$DB" "$snapshot" <<'PY' || die "database backup failed; nothing was changed"
import sqlite3, sys
src = sqlite3.connect(f"file:{sys.argv[1]}?mode=ro", uri=True)
dst = sqlite3.connect(sys.argv[2])
with dst:
    src.backup(dst)
ok = dst.execute("PRAGMA integrity_check").fetchone()[0]
raise SystemExit(0 if ok == "ok" else 1)
PY
  chmod 600 "$snapshot"
  say "database backed up: $snapshot"
fi

previous=$(readlink -f /opt/amh-native/current 2>/dev/null || true)
say "installing (rolls back on failure)"
AMH_PYTHON="$PY" AMH_SEMANTIC="$semantic" bash "$work/upgrade.sh" \
  "$work/$wheel" "$work/wheelhouse" "$bundle" "$RELEASE_SHA"

# An operator drop-in that pins an older model would override the new one
# for some settings: retire only that line, and only once the new model is in.
changed=()
if [ -f "$SEMANTIC_ENV" ] && [ -d "$DROPIN_DIR" ]; then
  for conf in "$DROPIN_DIR"/*.conf; do
    [ -f "$conf" ] || continue
    if grep -qE '^\s*Environment="?AMH_NATIVE_SEMANTIC_MODEL=' "$conf"; then
      cp -p "$conf" "$conf.bak-$VERSION"
      sed -i -E "s/^(\\s*Environment=\"?AMH_NATIVE_SEMANTIC_MODEL=)/# retired by hub.sh $VERSION (model now in semantic.env): \\1/" "$conf"
      changed+=("$conf")
    fi
  done
fi
ready() {
  for _ in $(seq 1 180); do
    curl -fsS --max-time 2 http://127.0.0.1:8787/native/v1/ready >/dev/null 2>&1 && return 0
    sleep 0.5
  done
  return 1
}
if [ "${#changed[@]}" -gt 0 ]; then
  say "retired an older model path in: ${changed[*]} (backups: *.bak-$VERSION)"
  systemctl daemon-reload
  systemctl restart "$SERVICE"
  if ! ready; then
    for conf in "${changed[@]}"; do mv -f "$conf.bak-$VERSION" "$conf"; done
    systemctl daemon-reload
    systemctl restart "$SERVICE"
    die "the Hub did not start with the new model; drop-ins restored (journalctl -u $SERVICE)"
  fi
fi

health() { curl -fsS --max-time 5 http://127.0.0.1:8787/native/v1/health 2>/dev/null || echo '{}'; }
if [ "$semantic" = on ] && [ -f "$SEMANTIC_ENV" ]; then
  say "waiting for semantic recall to index (up to $((WAIT_SEMANTIC / 60)) min; recall stays lexical meanwhile)"
  waited=0
  until health | grep -q '"semantic-v1"'; do
    [ "$waited" -ge "$WAIT_SEMANTIC" ] && { say "still indexing; it continues in the background"; break; }
    sleep 15; waited=$((waited + 15))
  done
fi

say "done"
health | "$PY" -c '
import json, sys
h = json.load(sys.stdin)
s = h.get("semantic", {})
print("  version      ", h.get("version"))
print("  semantic     ", s.get("state"), s.get("model") or "")
print("  relevance    ", s.get("gate", "-"))
print("  capabilities ", ", ".join(h.get("capabilities", [])))
'
[ -n "$previous" ] && say "previous release kept at $previous (rollback: ln -sfn $previous /opt/amh-native/current && systemctl restart $SERVICE)"
[ -f /etc/amh-native/feishu.env ] || say "Feishu login is not configured yet; set it up with HTTPS (docs/native-memory/operations.zh.md)"

#!/usr/bin/env bash
# Idempotent native Hub upgrade. Safe to run repeatedly; rolls back on failure.
#
# Usage (on the hub host, as root):
#   upgrade.sh <wheel> <wheelhouse-dir> <bundle-sha256> <release-sha>
#
# Unlike apply.sh this does not assume a first install: an existing
# /opt/amh-native/current is replaced atomically, and the previous release is
# kept on disk so a failed health check restores it.
set -euo pipefail

wheel=$1
wheelhouse=$2
approved_bundle=$3
release=$4

[[ "$approved_bundle" =~ ^[0-9a-f]{64}$ ]] || { echo 'approved bundle hash required' >&2; exit 2; }
[[ "$release" =~ ^[0-9a-f]{40}$ ]] || { echo 'release sha required' >&2; exit 2; }
[ "$(id -u)" -eq 0 ] || { echo 'must run as root' >&2; exit 1; }
[ -f "$wheel" ] || { echo 'wheel missing' >&2; exit 1; }

[ -d "$wheelhouse" ] || { echo 'wheelhouse missing' >&2; exit 1; }

service=amh-native
db=/var/lib/amh-native/hub.sqlite
current=/opt/amh-native/current
version_dir=/opt/amh-native/releases/$release
backup=/var/backups/amh-native/$(date -u +%Y%m%dT%H%M%SZ)
install -d -m 700 "$backup"

previous=""
[ -L "$current" ] && previous=$(readlink -f "$current")
semantic_env=/etc/amh-native/semantic.env
# The model setting is part of what a rollback restores.
[ -f "$semantic_env" ] && cp -p "$semantic_env" "$backup/semantic.env"

rollback() {
  code=$?
  trap - EXIT
  # A clean exit must not roll back: the EXIT trap fires on success too.
  [ "$code" -ne 0 ] || exit 0
  set +e
  if [ -f "$backup/semantic.env" ]; then
    cp -p "$backup/semantic.env" "$semantic_env"
  fi
  if [ -n "$previous" ] && [ -d "$previous" ]; then
    ln -sfn "$previous" "$current"
    systemctl restart "$service" || true
    for _ in $(seq 1 180); do
      curl -fsS --max-time 2 http://127.0.0.1:8787/native/v1/ready >/dev/null 2>&1 && break
      sleep 0.5
    done
    if curl -fsS --max-time 2 http://127.0.0.1:8787/native/v1/ready >/dev/null 2>&1; then
      echo "rolled back to $previous" >&2
    else
      echo "rolled back to $previous but it is not ready; journalctl -u $service" >&2
    fi
  else
    echo 'no previous release; service left as-is' >&2
  fi
  printf '%s\n' "upgrade_failed=$code backup=$backup" >&2
  exit "$code"
}
trap rollback EXIT

# Build the release directory beside the live one, never inside it.
if [ -d "$version_dir" ]; then
  echo "release directory already exists; reinstalling in place: $version_dir"
else
  install -d -m 755 "$version_dir"
fi
cp "$wheel" "$version_dir/"
# hub.sh passes the interpreter it found; the deploy workflow's host has 3.11.
python=${AMH_PYTHON:-$(command -v python3.11 || command -v python3.12 || command -v python3.13 || true)}
[ -n "$python" ] || { echo 'python 3.11-3.13 required' >&2; exit 1; }
[ -d "$version_dir/venv" ] || "$python" -m venv "$version_dir/venv"
"$version_dir/venv/bin/pip" --isolated --no-cache-dir install --timeout 30 --retries 2 \
  --no-index --find-links "$wheelhouse" "$version_dir/$(basename "$wheel")[web,semantic]" \
  > "$backup/install.log" 2>&1 || { echo 'install failed; see install.log' >&2; exit 1; }

id amh-native >/dev/null 2>&1 || useradd --system --user-group \
  --home-dir /var/lib/amh-native --shell /sbin/nologin amh-native

# Optional semantic retrieval: pinned model files from a Hugging Face mirror,
# each checked against its sha256. Shared across releases, fetched once. Any
# failure leaves the Hub on lexical ranking; it never fails the upgrade.
spec="$(dirname "$0")/semantic-model.json"
if [ "${AMH_SEMANTIC:-on}" = off ]; then
  # Asked for keyword recall: a model left configured would keep loading.
  rm -f "$semantic_env"
fi
if [ -f "$spec" ] && [ "${AMH_SEMANTIC:-on}" != off ]; then
  if model_dir=$(AMH_HF_ENDPOINT="${AMH_HF_ENDPOINT:-}" "$version_dir/venv/bin/python" - "$spec" 2>>"$backup/semantic.log" <<'PY'
import hashlib, json, os, sys, tempfile, urllib.request
from pathlib import Path
spec = json.loads(Path(sys.argv[1]).read_text())
target = Path(os.environ.get("AMH_MODEL_ROOT") or "/opt/amh-native/models") / (spec["name"] + "@" + spec["revision"][:12])
def good(path, sha):
    if not path.is_file():
        return False
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest() == sha
class Redirect(urllib.request.HTTPRedirectHandler):
    def http_error_308(self, req, fp, code, msg, headers):
        return self.http_error_302(req, fp, code, msg, headers)
urllib.request.install_opener(urllib.request.build_opener(Redirect))
# Files staged next to this script by the deploy workflow come first; the
# mirrors only serve manual installs. Every source is checked by sha256.
staged = Path(sys.argv[1]).parent / "model"
if not all(good(target / f["path"], f["sha256"]) for f in spec["files"]):
    endpoints = [e for e in (os.environ.get("AMH_HF_ENDPOINT"), "https://hf-mirror.com", "https://huggingface.co") if e]
    if staged.is_dir():
        endpoints.insert(0, "file://" + str(staged))
    target.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".fetch-", dir=target.parent))
    for f in spec["files"]:
        dest = stage / f["path"]
        dest.parent.mkdir(parents=True, exist_ok=True)
        for endpoint in endpoints:
            url = (
                f"{endpoint}/{f['path']}"
                if endpoint.startswith("file://")
                else f"{endpoint}/{spec['repo']}/resolve/{spec['revision']}/{f['path']}"
            )
            try:
                with urllib.request.urlopen(url, timeout=60) as response, dest.open("wb") as out:
                    for block in iter(lambda: response.read(1 << 20), b""):
                        out.write(block)
            except OSError as exc:
                print(f"fetch failed {endpoint} {f['path']}: {exc}", file=sys.stderr)
                continue
            if good(dest, f["sha256"]):
                break
            print(f"checksum mismatch {endpoint} {f['path']}", file=sys.stderr)
        else:
            sys.exit(f"could not fetch {f['path']}")
    (stage / "amh-semantic.json").write_text(json.dumps(spec["manifest"], ensure_ascii=False))
    for path in stage.rglob("*"):
        path.chmod(0o755 if path.is_dir() else 0o644)
    stage.chmod(0o755)
    if target.exists():
        import shutil
        shutil.rmtree(target)
    stage.rename(target)
# Files already in place (shared across releases, or placed by an operator)
# skip the fetch above; the manifest is still this release's to write.
manifest = target / "amh-semantic.json"
try:
    current = json.loads(manifest.read_text())
except (OSError, ValueError):
    current = None
if current != spec["manifest"]:
    manifest.write_text(json.dumps(spec["manifest"], ensure_ascii=False))
    manifest.chmod(0o644)
print(target)
PY
  ); then
    install -d -m 750 -o root -g amh-native /etc/amh-native
    printf 'AMH_NATIVE_SEMANTIC_MODEL=%s\nAMH_NATIVE_SEMANTIC_THREADS=%s\n' "$model_dir" "${AMH_SEMANTIC_THREADS:-2}" \
      > "$semantic_env.next"
    chown root:amh-native "$semantic_env.next"
    chmod 640 "$semantic_env.next"
    mv -f "$semantic_env.next" "$semantic_env"
    echo "semantic_model=$model_dir"
  else
    # A model that already works is better than none: keep what is configured.
    if [ -f "$semantic_env" ]; then
      echo 'new semantic model unavailable; keeping the configured one (see semantic.log)' >&2
    else
      echo 'semantic model unavailable; Hub stays on lexical ranking (see semantic.log)' >&2
    fi
  fi
fi

install -d -o amh-native -g amh-native -m 700 /var/lib/amh-native

# Switch atomically: a half-written symlink would leave the service pointing nowhere.
ln -sfn "$version_dir" "$current.next"
mv -Tf "$current.next" "$current"

cat > /etc/systemd/system/amh-native.service <<'UNIT'
[Unit]
Description=AMH native organization memory Hub
After=network.target
[Service]
Type=simple
User=amh-native
Group=amh-native
WorkingDirectory=/opt/amh-native/current
ExecStart=/opt/amh-native/current/venv/bin/amh-native serve --db /var/lib/amh-native/hub.sqlite --host 127.0.0.1 --port 8787
Environment=PYTHONNOUSERSITE=1
Environment=PYTHONDONTWRITEBYTECODE=1
EnvironmentFile=-/etc/amh-native/feishu.env
EnvironmentFile=-/etc/amh-native/semantic.env
Restart=on-failure
RestartSec=3
UMask=0077
StateDirectory=amh-native
StateDirectoryMode=0700
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
PrivateTmp=true
ReadWritePaths=/var/lib/amh-native
[Install]
WantedBy=multi-user.target
UNIT
chmod 644 /etc/systemd/system/amh-native.service
systemctl daemon-reload
systemctl enable "$service" >/dev/null 2>&1 || true
systemctl restart "$service"

ready=0
# A semantic model loads before the service accepts requests.
for _ in $(seq 1 180); do
  if curl -fsS --max-time 2 http://127.0.0.1:8787/native/v1/ready >/dev/null 2>&1; then
    ready=1
    break
  fi
  sleep 0.5
done
[ "$ready" = 1 ] || { echo 'service did not become ready' >&2; journalctl -u "$service" -n 40 --no-pager >&2 || true; exit 1; }

printf '%s\n' "$release" > /var/lib/amh-native/deployed-revision
chown amh-native:amh-native /var/lib/amh-native/deployed-revision
# Keep what a rollback could need; everything older only costs disk.
prune() {
  # shellcheck disable=SC2012,SC2086
  ls -1dt $1 2>/dev/null | tail -n +"$(($2 + 1))" | while read -r old; do
    [ "$old" = "$version_dir" ] || [ "$old" = "$previous" ] || rm -rf -- "$old"
  done
}
prune '/opt/amh-native/releases/*' 3 || true
prune '/var/backups/amh-native/20*' 5 || true
prune '/var/backups/amh-native/hub-before-*' 5 || true
printf '%s\n' "upgrade_ok release=$release backup=$backup"

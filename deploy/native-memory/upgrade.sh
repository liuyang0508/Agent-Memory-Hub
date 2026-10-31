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

rollback() {
  code=$?
  [ "$code" -ne 0 ] || code=1
  trap - EXIT
  set +e
  if [ -n "$previous" ] && [ -d "$previous" ]; then
    ln -sfn "$previous" "$current"
    systemctl restart "$service" || true
    echo "rolled back to $previous" >&2
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
[ -d "$version_dir/venv" ] || python3.11 -m venv "$version_dir/venv"
"$version_dir/venv/bin/pip" --isolated --no-cache-dir install --timeout 30 --retries 2 \
  --no-index --find-links "$wheelhouse" "$version_dir/$(basename "$wheel")[web]" \
  > "$backup/install.log" 2>&1 || { echo 'install failed; see install.log' >&2; exit 1; }

id amh-native >/dev/null 2>&1 || useradd --system --user-group \
  --home-dir /var/lib/amh-native --shell /sbin/nologin amh-native
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
for _ in $(seq 1 60); do
  if curl -fsS --max-time 2 http://127.0.0.1:8787/native/v1/ready >/dev/null 2>&1; then
    ready=1
    break
  fi
  sleep 0.5
done
[ "$ready" = 1 ] || { echo 'service did not become ready' >&2; journalctl -u "$service" -n 40 --no-pager >&2 || true; exit 1; }

printf '%s\n' "$release" > /var/lib/amh-native/deployed-revision
chown amh-native:amh-native /var/lib/amh-native/deployed-revision
printf '%s\n' "upgrade_ok release=$release backup=$backup"

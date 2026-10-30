#!/usr/bin/env bash
# FIRST deployment only. Must be invoked after approval of this exact host/release.
# Keeps databases/releases on rollback; never restores an expired TLS certificate.
set -euo pipefail
release=e685c89283adfb3fc5ba6306e85bd4b17303b161
[ "${1:-}" = "approve-aihub0508.com-$release" ] || { echo 'explicit deployment acknowledgement required' >&2; exit 2; }
wheel=$2
expected_wheel=${3:-}
rollback_source=${4:-}
[[ "$expected_wheel" =~ ^[0-9a-f]{64}$ ]] || { echo 'approved artifact hash required' >&2; exit 2; }
[ "$(id -u)" -eq 0 ] || exit 1
[ -f "$wheel" ] || exit 1
[ -f "$rollback_source" ] || exit 1
[ "$(sha256sum "$wheel" | cut -d' ' -f1)" = "$expected_wheel" ] || { echo 'artifact hash mismatch' >&2; exit 1; }
site=/etc/nginx/conf.d/agent-memory-hub.conf
[ ! -L "$site" ] || { echo 'refusing symlinked site configuration' >&2; exit 1; }
expected=d05b86180f526a78a74500ad8adcdbfd70cef66e391bddf162fac39c653d0a8f
[ "$(sha256sum "$site" | cut -d' ' -f1)" = "$expected" ] || { echo 'nginx drift: re-review required' >&2; exit 1; }
for path in /etc/systemd/system/amh-native.service /etc/nginx/amh-native.locations.conf /opt/amh-native/current /var/lib/amh-native /etc/letsencrypt/renewal-hooks/deploy/20-amh-nginx-reload; do
 [ ! -e "$path" ] && [ ! -L "$path" ] || { echo "refusing to replace existing path: $path" >&2; exit 1; }
done
! id amh-native >/dev/null 2>&1 || { echo 'service account already exists; re-review required' >&2; exit 1; }
! ss -ltnH | awk '{print $4}' | grep -q ':8787$' || { echo 'port 8787 occupied' >&2; exit 1; }
backup=/var/backups/amh-native/$(date -u +%Y%m%dT%H%M%SZ)
install -d -m 700 "$backup"
cp -a "$site" "$backup/site.before.conf"
cmp -s "$site" "$backup/site.before.conf"
cp -a "$backup/site.before.conf" "$backup/restore-check.conf"
cmp -s "$site" "$backup/restore-check.conf"
install -m 700 "$rollback_source" "$backup/rollback.sh"
printf '%s\n' "$release" > "$backup/release.sha"
printf '%s\n' "$expected_wheel" > "$backup/wheel.sha256"
printf '%s\n' prepared > "$backup/phase"
unit_created=0
route_changed=0
installed_site_hash=""
rollback() {
 code=$?
 trap - EXIT HUP INT TERM
 set +e
 if [ "$route_changed" = 1 ]; then
  actual=$(sha256sum "$site" | cut -d' ' -f1)
  if [ "$actual" = "$installed_site_hash" ] || [ "$actual" = "$expected" ]; then
   cp -a "$backup/site.before.conf" "$site.amh-rollback"
   mv "$site.amh-rollback" "$site"
   /usr/sbin/nginx -t && systemctl reload nginx
  else
   echo "rollback held: site changed externally; preserved for review" >&2
  fi
 fi
 if [ "$unit_created" = 1 ] && [ -f /etc/systemd/system/amh-native.service ]; then
  if [ "$(sha256sum /etc/systemd/system/amh-native.service | cut -d' ' -f1)" != "$(cat "$backup/unit.sha256")" ]; then
   echo 'rollback held: native unit changed externally' >&2
   exit "$code"
  fi
  systemctl disable --now amh-native.service
  cp -a /etc/systemd/system/amh-native.service "$backup/failed.service"
  rm -f /etc/systemd/system/amh-native.service
  systemctl daemon-reload
 fi
 printf '%s\n' "deployment_failed=$code backup=$backup data_retained=true"
 exit "$code"
}
trap rollback EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM
version_dir=/opt/amh-native/releases/$release
[ ! -e "$version_dir" ]
install -d -m 755 "$version_dir"
cp "$wheel" "$version_dir/$(basename "$wheel")"
python3.11 -m venv "$version_dir/venv"
timeout --foreground 360s "$version_dir/venv/bin/pip" --isolated --no-cache-dir install --timeout 30 --retries 2 --index-url https://pypi.org/simple "$version_dir/$(basename "$wheel")[web]" > "$backup/install.log" 2>&1
useradd --system --user-group --home-dir /var/lib/amh-native --shell /sbin/nologin amh-native
install -d -o amh-native -g amh-native -m 700 /var/lib/amh-native
ln -s "$version_dir" /opt/amh-native/current
cat > "$backup/unit.next" <<'UNIT'
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
chmod 644 "$backup/unit.next"
sha256sum "$backup/unit.next" | cut -d' ' -f1 > "$backup/unit.sha256"
unit_created=1
cp -a "$backup/unit.next" /etc/systemd/system/amh-native.service
printf '%s\n' unit-created > "$backup/phase"
systemctl daemon-reload
systemctl enable --now amh-native.service
ready=0
for attempt in $(seq 1 40); do
 if curl -fsS --max-time 2 http://127.0.0.1:8787/native/v1/ready >/dev/null; then ready=1; break; fi
 sleep .25
done
[ "$ready" = 1 ]
# Existing webroot authentication: no stop/restart of the website for issuance.
certbot renew --cert-name aihub0508.com --noninteractive
openssl x509 -in /etc/letsencrypt/live/aihub0508.com/fullchain.pem -checkend 604800 -noout
/usr/sbin/nginx -t
systemctl reload nginx
printf '%s\n' tls-repaired > "$backup/phase"
hook=/etc/letsencrypt/renewal-hooks/deploy/20-amh-nginx-reload
[ ! -e "$hook" ] || { echo 'renew hook already exists; refusing overwrite' >&2; false; }
install -d -m 755 "$(dirname "$hook")"
cat > "$hook" <<'HOOK'
#!/bin/sh
set -eu
[ "${RENEWED_LINEAGE:-}" = /etc/letsencrypt/live/aihub0508.com ] || exit 0
/usr/sbin/nginx -t
/usr/bin/systemctl reload nginx
HOOK
chmod 755 "$hook"
cat > /etc/nginx/amh-native.locations.conf <<'NGINX'
location /native/v1/ {
    client_max_body_size 5m;
    proxy_pass http://127.0.0.1:8787;
    proxy_set_header Host $host;
    proxy_set_header Authorization $http_authorization;
    proxy_connect_timeout 2s;
    proxy_read_timeout 15s;
}
NGINX
chmod 644 /etc/nginx/amh-native.locations.conf
[ "$(sha256sum "$site" | cut -d' ' -f1)" = "$expected" ] || { echo 'site changed while preparing; refusing overwrite' >&2; false; }
installed_site_hash=$(python3.11 - "$site" "$backup" <<'PY'
from pathlib import Path
import os,sys,hashlib
p=Path(sys.argv[1]);text=p.read_text();anchor='include /etc/nginx/agent-memory-hub-telemetry.locations.conf;';assert text.count(anchor)==1
candidate=p.with_name(p.name+'.amh-next');candidate.write_text(text.replace(anchor,anchor+'\n    include /etc/nginx/amh-native.locations.conf;'));candidate.chmod(p.stat().st_mode & 0o777)
checksum=hashlib.sha256(candidate.read_bytes()).hexdigest();backup=Path(sys.argv[2]);(backup/'site.after.sha256').write_text(checksum+'\n');(backup/'phase').write_text('route-prepared\n');print(checksum)
PY
)
route_changed=1
mv "$site.amh-next" "$site"
printf '%s\n' route-installed > "$backup/phase"
/usr/sbin/nginx -t
systemctl reload nginx
systemctl enable --now certbot-renew.timer
systemctl is-active --quiet certbot-renew.timer
curl -fsS --max-time 10 --resolve aihub0508.com:443:127.0.0.1 https://aihub0508.com/native/v1/ready
[ "$(curl -sS -o /dev/null -w '%{http_code}' --max-time 10 --resolve aihub0508.com:443:127.0.0.1 https://aihub0508.com/native/v1/me)" = 401 ]
printf '%s\n' "$release" > /var/lib/amh-native/deployed-revision
chown amh-native:amh-native /var/lib/amh-native/deployed-revision
printf '%s\n' "$installed_site_hash" > "$backup/site.after.sha256"
sha256sum /etc/systemd/system/amh-native.service | cut -d' ' -f1 > "$backup/unit.sha256"
printf '%s\n' complete > "$backup/phase"
trap - EXIT HUP INT TERM
printf '\ndeployed_revision=%s\nrollback_config=%s\n' "$release" "$backup/site.before.conf"

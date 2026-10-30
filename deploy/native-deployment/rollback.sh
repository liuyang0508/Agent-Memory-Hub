#!/usr/bin/env bash
# Explicit rollback of one native rollout; keeps the repaired certificate and all data.
set -euo pipefail
[ "$(id -u)" -eq 0 ]
backup=$1
[[ "$backup" =~ ^/var/backups/amh-native/[0-9]{8}T[0-9]{6}Z$ ]]
site=/etc/nginx/conf.d/agent-memory-hub.conf
[ -f "$backup/site.before.conf" ] || exit 1
[ -f "$backup/phase" ] || exit 1
original=$(sha256sum "$backup/site.before.conf" | cut -d' ' -f1)
actual=$(sha256sum "$site" | cut -d' ' -f1)
if [ "$actual" != "$original" ]; then
 [ -f "$backup/site.after.sha256" ] && [ "$actual" = "$(cat "$backup/site.after.sha256")" ] || { echo 'site drift: manual review required' >&2; exit 1; }
 cp -a "$backup/site.before.conf" "$site.amh-rollback"
 cmp -s "$backup/site.before.conf" "$site.amh-rollback"
 mv "$site.amh-rollback" "$site"
fi
/usr/sbin/nginx -t
systemctl reload nginx
if [ -f /etc/systemd/system/amh-native.service ]; then
 [ -f "$backup/unit.sha256" ] && [ "$(sha256sum /etc/systemd/system/amh-native.service | cut -d' ' -f1)" = "$(cat "$backup/unit.sha256")" ] || { echo 'unit drift: manual review required' >&2; exit 1; }
 systemctl disable --now amh-native.service
fi
printf '%s\n' rolled-back > "$backup/phase"
printf '%s\n' 'native route restored; native service disabled; database, releases and valid TLS certificate retained'

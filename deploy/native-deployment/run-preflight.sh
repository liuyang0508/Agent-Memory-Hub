#!/usr/bin/env bash
set -euo pipefail
remote="${AIHUB_SSH_USER}@${AIHUB_SSH_HOST}"
opts=(-i "$HOME/.ssh/aihub_key" -p "$AIHUB_SSH_PORT" -o BatchMode=yes -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes -o HostKeyAlias=aihub-pinned)
stage=$(ssh "${opts[@]}" "$remote" 'mktemp -d /tmp/amh-native-preflight.XXXXXXXX')
[[ "$stage" =~ ^/tmp/amh-native-preflight\.[A-Za-z0-9]+$ ]]
cleanup() { ssh "${opts[@]}" "$remote" "test -f '$stage/.owned' && rm -rf -- '$stage'" || true; }
trap cleanup EXIT
ssh "${opts[@]}" "$remote" "umask 077; touch '$stage/.owned'"
python -m pip wheel --no-deps ./code -w dist >/tmp/amh-native-wheel-build.log 2>&1
wheel=$(find dist -maxdepth 1 -name 'agent_memory_hub-*.whl' -type f)
[ "$(printf '%s\n' "$wheel" | wc -l)" -eq 1 ]
scp -i "$HOME/.ssh/aihub_key" -P "$AIHUB_SSH_PORT" -o BatchMode=yes -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes -o HostKeyAlias=aihub-pinned "$wheel" "$remote:$stage/package.whl"
# Pip requires a valid distribution filename.
ssh "${opts[@]}" "$remote" "mv '$stage/package.whl' '$stage/$(basename "$wheel")'"
ssh "${opts[@]}" "$remote" "bash -s -- '$stage' '$stage/$(basename "$wheel")'" < deploy/native-deployment/preflight.sh

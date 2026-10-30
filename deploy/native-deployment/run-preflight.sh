#!/usr/bin/env bash
set -euo pipefail
remote="${AIHUB_SSH_USER}@${AIHUB_SSH_HOST}"
opts=(-i "$HOME/.ssh/aihub_key" -p "$AIHUB_SSH_PORT" -o BatchMode=yes -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes -o HostKeyAlias=aihub-pinned)
stage=$(ssh "${opts[@]}" "$remote" 'mktemp -d /tmp/amh-native-preflight.XXXXXXXX')
[[ "$stage" =~ ^/tmp/amh-native-preflight\.[A-Za-z0-9]+$ ]]
cleanup() {
  ssh "${opts[@]}" "$remote" "python3 - '$stage'" <<'PY_CLEANUP' || true
import os,signal,sys,shutil,time
from pathlib import Path
p=Path(sys.argv[1]);assert p.parent==Path('/tmp') and p.name.startswith('amh-native-preflight.') and not p.is_symlink()
if not (p/'.owned').is_file():raise SystemExit('cleanup refused: missing ownership marker')
pidfile=p/'hub.pid'
if pidfile.exists():
 pid=int(pidfile.read_text());cmd=Path('/proc')/str(pid)/'cmdline'
 if cmd.exists() and str(p/'venv/bin/amh-native').encode() in cmd.read_bytes().split(b'\0'):
  os.kill(pid,signal.SIGTERM)
  for _ in range(30):
   if not cmd.exists():break
   time.sleep(.1)
  else:raise SystemExit('cleanup held: test process is still running')
shutil.rmtree(p)
print('owned-preflight-cleaned')
PY_CLEANUP
}
trap cleanup EXIT
ssh "${opts[@]}" "$remote" "umask 077; touch '$stage/.owned'"
python -m pip wheel --no-deps ./code -w dist >/tmp/amh-native-wheel-build.log 2>&1
wheel=$(find dist -maxdepth 1 -name 'agent_memory_hub-*.whl' -type f)
[ "$(printf '%s\n' "$wheel" | wc -l)" -eq 1 ]
scp -i "$HOME/.ssh/aihub_key" -P "$AIHUB_SSH_PORT" -o BatchMode=yes -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes -o HostKeyAlias=aihub-pinned "$wheel" "$remote:$stage/package.whl"
# Pip requires a valid distribution filename.
ssh "${opts[@]}" "$remote" "mv '$stage/package.whl' '$stage/$(basename "$wheel")'"
ssh "${opts[@]}" "$remote" "bash -s -- '$stage' '$stage/$(basename "$wheel")'" < deploy/native-deployment/preflight.sh

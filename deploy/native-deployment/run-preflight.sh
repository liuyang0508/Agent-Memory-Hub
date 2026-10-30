#!/usr/bin/env bash
set -euo pipefail
remote="${AIHUB_SSH_USER}@${AIHUB_SSH_HOST}"
opts=(-i "$HOME/.ssh/aihub_key" -p "$AIHUB_SSH_PORT" -o BatchMode=yes -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes -o HostKeyAlias=aihub-pinned -o ServerAliveInterval=15 -o ServerAliveCountMax=3)
stage=$(ssh "${opts[@]}" "$remote" 'mktemp -d /tmp/amh-native-preflight.XXXXXXXX')
[[ "$stage" =~ ^/tmp/amh-native-preflight\.[A-Za-z0-9]+$ ]]
cleanup() {
  status=$?
  trap - EXIT
  cleanup_status=0
  ssh "${opts[@]}" "$remote" "python3 - '$stage'" <<'PY_CLEANUP' || cleanup_status=$?
import os,signal,sys,shutil,time,subprocess
from pathlib import Path
p=Path(sys.argv[1]);assert p.parent==Path('/tmp') and p.name.startswith('amh-native-preflight.') and not p.is_symlink()
if not (p/'.owned').is_file():raise SystemExit('cleanup refused: missing ownership marker')
record=p/'process-group'
def members(group):
 output=subprocess.check_output(['ps','-eo','pid=,pgid='],universal_newlines=True)
 return [int(a) for a,b in (line.split() for line in output.splitlines()) if int(b)==group]
if record.exists():
 pid_text,start=record.read_text().split();pid=int(pid_text);stat=Path('/proc')/pid/'stat'
 if stat.exists():
  fields=stat.read_text().rsplit(')',1)[1].split()
  if fields[19]!=start or int(fields[2])!=pid:raise SystemExit('cleanup held: process identity changed')
  os.killpg(pid,signal.SIGTERM)
  for _ in range(50):
   if not members(pid):break
   time.sleep(.1)
  else:
   os.killpg(pid,signal.SIGKILL)
   for _ in range(30):
    if not members(pid):break
    time.sleep(.1)
   else:raise SystemExit('cleanup held: owned process group still alive')
 elif members(pid):raise SystemExit('cleanup held: group leader absent; inspect remaining group')
else:
 for proc in Path('/proc').glob('[0-9]*'):
  if proc.name==str(os.getpid()):continue
  try:args=(proc/'cmdline').read_bytes().split(b'\0')
  except OSError:continue
  if any(arg==str(p).encode() or arg.startswith((str(p)+'/').encode()) for arg in args):
   raise SystemExit('cleanup held: unregistered stage process')
shutil.rmtree(p)
print('owned-preflight-cleaned')
PY_CLEANUP
  if [ "$cleanup_status" -ne 0 ]; then
    echo "cleanup failed; retained stage: $stage" >&2
    [ "$status" -ne 0 ] || status=$cleanup_status
  fi
  exit "$status"
}
trap cleanup EXIT
ssh "${opts[@]}" "$remote" "umask 077; touch '$stage/.owned'"
python -m pip wheel --no-deps ./code -w dist >/tmp/amh-native-wheel-build.log 2>&1
wheel=$(find dist -maxdepth 1 -name 'agent_memory_hub-*.whl' -type f)
[ "$(printf '%s\n' "$wheel" | wc -l)" -eq 1 ]
python - "$wheel" <<'MANIFEST'
from pathlib import Path
import hashlib,json,subprocess,sys
sha=subprocess.check_output(['git','-C','code','rev-parse','HEAD'],text=True).strip()
assert sha=='e685c89283adfb3fc5ba6306e85bd4b17303b161'
p=Path(sys.argv[1]);Path('dist/manifest.json').write_text(json.dumps({'release':sha,'wheel':p.name,'sha256':hashlib.sha256(p.read_bytes()).hexdigest()},indent=2)+'\n')
MANIFEST
scp -i "$HOME/.ssh/aihub_key" -P "$AIHUB_SSH_PORT" -o BatchMode=yes -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes -o HostKeyAlias=aihub-pinned "$wheel" "$remote:$stage/package.whl"
# Pip requires a valid distribution filename.
ssh "${opts[@]}" "$remote" "mv '$stage/package.whl' '$stage/$(basename "$wheel")'"
ssh "${opts[@]}" "$remote" "setsid --wait bash -s -- '$stage' '$stage/$(basename "$wheel")'" < deploy/native-deployment/preflight.sh

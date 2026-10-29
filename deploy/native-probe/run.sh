#!/usr/bin/env bash
set -euo pipefail
SSH=(ssh -T -i "$HOME/.ssh/aihub_key" -p "$AIHUB_SSH_PORT" -o BatchMode=yes -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes -o HostKeyAlias=aihub-pinned -o ConnectTimeout=10)
SCP=(scp -q -i "$HOME/.ssh/aihub_key" -P "$AIHUB_SSH_PORT" -o BatchMode=yes -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes -o HostKeyAlias=aihub-pinned)
REMOTE="${AIHUB_SSH_USER}@${AIHUB_SSH_HOST}"
"${SSH[@]}" "$REMOTE" 'python3.11 -' <<'PY'
import json,sqlite3,shutil
c=sqlite3.connect(':memory:')
c.execute('CREATE VIRTUAL TABLE probe USING fts5(content)')
assert c.execute('SELECT json_extract(?,?)',('{"ok":1}','$.ok')).fetchone()[0]==1
assert shutil.disk_usage('/tmp').free > 512*1024*1024
print(json.dumps({'runtime_ready':True,'sqlite':sqlite3.sqlite_version}))
PY
REMOTE_DIR=$("${SSH[@]}" "$REMOTE" 'mktemp -d /tmp/amh-native-verify.XXXXXXXX')
[[ "$REMOTE_DIR" =~ ^/tmp/amh-native-verify\.[A-Za-z0-9]+$ ]]
TUNNEL_PID=""
cleanup() {
  if [ -n "$TUNNEL_PID" ]; then kill "$TUNNEL_PID" 2>/dev/null || true; fi
  "${SSH[@]}" "$REMOTE" "python3.11 - '$REMOTE_DIR'" <<'PY'
import json,os,signal,sys,time,shutil
from pathlib import Path
root=Path(sys.argv[1])
assert root.parent==Path('/tmp') and root.name.startswith('amh-native-verify.')
ready=root/'ready.json'
if ready.exists():
 pid=int(json.loads(ready.read_text())['pid'])
 proc=Path('/proc')/str(pid)/'cmdline'
 if proc.exists() and str(root/'server.py').encode() in proc.read_bytes():
  os.kill(pid,signal.SIGTERM)
  for _ in range(20):
   if not proc.exists():break
   time.sleep(.1)
  if proc.exists() and str(root/'server.py').encode() in proc.read_bytes():os.kill(pid,signal.SIGKILL)
shutil.rmtree(root)
print(json.dumps({'temporary_test_cleanup':True}))
PY
}
trap cleanup EXIT
TMP=$(mktemp -d)
tar --exclude='__pycache__' -czf "$TMP/code.tgz" -C code agent_brain/__init__.py agent_brain/_version.py agent_brain/native_sync
"${SCP[@]}" "$TMP/code.tgz" "$REMOTE:$REMOTE_DIR/code.tgz"
"${SCP[@]}" deploy/native-probe/server.py "$REMOTE:$REMOTE_DIR/server.py"
"${SSH[@]}" "$REMOTE" "bash -s -- '$REMOTE_DIR'" <<'SH_REMOTE'
set -euo pipefail
umask 077
root="$1"
ulimit -v 524288
cd "$root"
tar -xzf code.tgz
python3.11 -m venv venv
PIP_CONFIG_FILE=/dev/null PIP_INDEX_URL=https://pypi.org/simple PIP_EXTRA_INDEX_URL='' nice -n 10 venv/bin/python -m pip install --only-binary=:all: 'fastapi>=0.111,<1' 'uvicorn>=0.30,<1' 'pydantic>=2.7,<3' > install.log 2>&1
PYTHONUTF8=1 nohup nice -n 10 venv/bin/python "$root/server.py" > server.log 2>&1 </dev/null &
SH_REMOTE
for attempt in $(seq 1 30); do
  if "${SCP[@]}" "$REMOTE:$REMOTE_DIR/ready.json" "$TMP/ready.json" 2>/dev/null; then break; fi
  sleep 1
done
PORT=$(python3 -c 'import json,sys; p=json.load(open(sys.argv[1]))["port"]; assert isinstance(p,int) and 0<p<65536; print(p)' "$TMP/ready.json")
"${SCP[@]}" "$REMOTE:$REMOTE_DIR/invites.json" "$TMP/invites.json"
chmod 600 "$TMP/invites.json"
"${SSH[@]}" -o ExitOnForwardFailure=yes -N -L "127.0.0.1:18787:127.0.0.1:$PORT" "$REMOTE" &
TUNNEL_PID=$!
for attempt in $(seq 1 20); do
  if curl -fsS http://127.0.0.1:18787/native/v1/health >/dev/null 2>&1; then break; fi
  sleep 1
done
PYTHONPATH="$PWD/code" python3 deploy/native-probe/client.py "$TMP/invites.json"

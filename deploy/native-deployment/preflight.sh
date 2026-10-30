#!/usr/bin/env bash
# Only writes an owned /tmp tree; does not reload services or renew certificates.
set -euo pipefail
stage=$1
wheel=$2
[[ "$(dirname "$stage")" = /tmp && "$(basename "$stage")" == amh-native-preflight.* ]] || exit 2
[ -f "$stage/.owned" ] || exit 2
[ -f "$wheel" ] || exit 2
[ "$(ps -o pgid= -p $$ | tr -d ' ')" = "$$" ] || { echo 'isolated process group required'; exit 1; }
python3.11 - "$stage" "$$" <<'PID'
from pathlib import Path
import sys
p=Path(sys.argv[1]);pid=sys.argv[2];start=(Path('/proc')/pid/'stat').read_text().rsplit(')',1)[1].split()[19]
(p/'process-group').write_text(pid+' '+start+'\n')
PID
finish() {
  status=$?
  trap - EXIT HUP INT
  trap '' TERM
  kill -TERM -- "-$$" 2>/dev/null || true
  exit "$status"
}
trap finish EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM
mkdir -p "$stage/tmp"
export TMPDIR="$stage/tmp"
python3.11 -m venv "$stage/venv"
if ! timeout --foreground 360s "$stage/venv/bin/pip" --isolated --no-cache-dir install --timeout 30 --retries 2 --progress-bar off --index-url https://pypi.org/simple "$wheel[web]" 2>&1 | tee "$stage/install.log"; then
  tail -n 35 "$stage/install.log"
  exit 1
fi
"$stage/venv/bin/python" - "$stage" <<'PY'
import hashlib,json,sqlite3,subprocess,sys,socket,time
from pathlib import Path
from urllib.request import urlopen
from agent_brain.native_sync.hub import Hub
from agent_brain.native_sync.client import NativeClient
stage=Path(sys.argv[1]);db=stage/'test.sqlite';hub=Hub(db)
with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
url=f'http://127.0.0.1:{port}'
with (stage/'hub.log').open('w') as log:
 proc=subprocess.Popen([str(stage/'venv/bin/amh-native'),'serve','--db',str(db),'--port',str(port)],stdout=log,stderr=log)
 (stage/'hub.pid').write_text(str(proc.pid))
 try:
  for _ in range(100):
   try:
    with urlopen(url+'/native/v1/ready',timeout=1) as r:assert json.load(r)['ready']
    break
   except OSError:time.sleep(.1)
  else:raise RuntimeError('temporary Hub not ready')
  author=NativeClient.join(stage/'author',url,hub.invite('verify','author'),'author')
  reader=NativeClient.join(stage/'reader',url,hub.invite('verify','reader'),'reader')
  memory=stage/'memory';memory.mkdir();author.register(memory,'codex-cli','synthetic-deploy','memory')
  (memory/'probe.md').write_text('Synthetic deployment fixture: native deployment ready.\n')
  author.sync();hits=reader.search('synthetic deployment fixture');assert hits and hits[0]['producer_member_id']==author.identity['member_id']
  with sqlite3.connect(db) as source,sqlite3.connect(stage/'restore.sqlite') as dest:source.backup(dest)
  with sqlite3.connect(stage/'restore.sqlite') as restored:assert restored.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
 finally:
  proc.terminate()
  try:proc.wait(timeout=10)
  except subprocess.TimeoutExpired:proc.kill();proc.wait()
site=Path('/etc/nginx/conf.d/agent-memory-hub.conf');original=site.read_bytes()
assert hashlib.sha256(original).hexdigest()=='d05b86180f526a78a74500ad8adcdbfd70cef66e391bddf162fac39c653d0a8f','site config changed; re-review required'
anchor='include /etc/nginx/agent-memory-hub-telemetry.locations.conf;'
text=original.decode();assert text.count(anchor)==1
location=stage/'native-location.conf';location.write_text('location /native/v1/ {\n client_max_body_size 5m;\n proxy_pass http://127.0.0.1:8787;\n proxy_set_header Host $host;\n proxy_set_header Authorization $http_authorization;\n proxy_connect_timeout 2s;\n proxy_read_timeout 15s;\n}\n')
candidate=stage/'site.conf';candidate.write_text(text.replace(anchor,anchor+'\n    include '+str(location)+';'))
main=Path('/etc/nginx/nginx.conf').read_text();include='include /etc/nginx/conf.d/*.conf;';assert main.count(include)==1,'unexpected nginx include layout'
shadow=stage/'nginx.conf';shadow.write_text(main.replace(include,'\n'.join('include '+str(candidate if p==site else p)+';' for p in sorted(Path('/etc/nginx/conf.d').glob('*.conf')))))
subprocess.run(['/usr/sbin/nginx','-t','-p','/etc/nginx/','-c',str(shadow)],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
# Prove exact config backup and restore bytes in the owned rehearsal tree.
backup=stage/'site.before';backup.write_bytes(original);restored=stage/'site.restored';restored.write_bytes(backup.read_bytes());assert restored.read_bytes()==original
print(json.dumps({'isolated_runtime':True,'cross_member_sync':True,'sqlite_backup_restore':True,'candidate_nginx_valid':True,'configuration_restore_verified':True,'production_modified':False}))
PY

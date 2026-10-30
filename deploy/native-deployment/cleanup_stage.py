import os,signal,sys,shutil,time,subprocess
from pathlib import Path
p=Path(sys.argv[1]);assert p.parent==Path('/tmp') and p.name.startswith('amh-native-preflight.') and not p.is_symlink()
if not p.exists():
 print('owned-preflight-already-absent');raise SystemExit(0)
if not (p/'.owned').is_file():raise SystemExit('cleanup refused: missing ownership marker')
record=p/'process-group'
def members(group):
 output=subprocess.check_output(['ps','-eo','pid=,pgid='],universal_newlines=True)
 return [int(a) for a,b in (line.split() for line in output.splitlines()) if int(b)==group]
if record.exists():
 pid_text,start=record.read_text().split();pid=int(pid_text);stat=Path('/proc')/str(pid)/'stat'
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

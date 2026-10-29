"""Two isolated client profiles, a remote Hub, and only synthetic source content."""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from agent_brain.native_sync.client import NativeClient
from agent_brain.native_sync.projection import publish
from agent_brain.native_sync.common import runtime_argv

root = Path(tempfile.mkdtemp(prefix='amh-native-cloud-client-'))
invites = json.loads(Path(sys.argv[1]).read_text())
url = 'http://127.0.0.1:18787'
a = NativeClient.join(root/'author', url, invites['author'], 'smoke-source')
b = NativeClient.join(root/'reader', url, invites['reader'], 'smoke-reader')
other = NativeClient.join(root/'outsider', url, invites['outsider'], 'smoke-outside')
source = root/'memory'; source.mkdir()
a.register(source, 'claude-code', 'source-project', 'memory')
p = source/'note.md'; p.write_text('REMOTE_CANARY organization-memory test content')
a.sync()
hits = b.search('REMOTE_CANARY', project_hint='different-project')
assert hits and hits[0]['producer_member_id'] == a.identity['member_id']
assert not other.search('REMOTE_CANARY')
# Startup cache synchronization is separate from the direct remote recall assertion above.
b.sync()
payload = json.dumps({'hook_event_name':'UserPromptSubmit','session_id':'remote-smoke','prompt':'REMOTE_CANARY'})
result = subprocess.run(runtime_argv('agent_brain.native_sync.hooks','--state',str(b.root),'--product','codex-cli'),input=payload,text=True,capture_output=True,timeout=4)
assert result.returncode == 0, result.stderr
context = json.loads(result.stdout)['hookSpecificOutput']['additionalContext']
assert 'REMOTE_CANARY' in context and 'Smoke Author' in context
b.sync()
projection = root/'native'
assert not publish(projection,'codex-cli',b.cached())['conflicts']
assert any('REMOTE_CANARY' in f.read_text() for f in (projection/'amh-org').glob('*.md'))
p.unlink(); a.sync(); b.sync()
assert not b.search('REMOTE_CANARY')
assert b.cached() == []
print(json.dumps({'remote_hub':True,'cross_member_recall':True,'cross_project_recall':True,'tenant_isolation':True,'hook_injection':True,'hook_mode':json.loads((b.root/'hook-metrics.jsonl').read_text().splitlines()[-1])['mode'],'native_file_projection':True,'deletion':True,'scope':'synthetic data; encrypted SSH tunnel; no public service deployment'}))

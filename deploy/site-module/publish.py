"""Atomically publish one reviewed HTML file; never modifies the Hub or nginx config."""
import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile

EXPECTED_OLD = '9f7ee715f8a11fec4f6732cf53ec7c1409f26b2d00da246b569345374a0f735c'
EXPECTED_NEW = 'fe0f77d7baf4fb35536ebda2b03bef53fa0fcb69ab4395ebf1948c0f45a09afd'
SITE_LINK = Path('/var/www/agent-memory-hub/current/index.html')


def digest(data):
    return hashlib.sha256(data).hexdigest()


incoming = Path(sys.argv[1])
run_id = sys.argv[2]
assert re.fullmatch(r'[0-9]+', run_id)
assert not incoming.is_symlink()
content = incoming.read_bytes()
assert digest(content) == EXPECTED_NEW
assert content.count(b'id="organization-memory"') == 1
site = SITE_LINK.resolve(strict=True)
assert str(site).startswith('/var/www/agent-memory-hub/') and site.name == 'index.html'
original = site.read_bytes()
if digest(original) == EXPECTED_NEW:
    print(json.dumps({'already_current': True, 'sha256': EXPECTED_NEW}))
    raise SystemExit(0)
assert digest(original) == EXPECTED_OLD, 'live HTML changed; refusing to overwrite'
backup = Path('/var/backups/amh-site') / (datetime.datetime.utcnow().strftime('%Y%m%dT%H%M%SZ') + '-' + run_id)
backup.mkdir(parents=True, mode=0o700, exist_ok=False)
shutil.copy2(site, backup/'index.before.html')
assert digest((backup/'index.before.html').read_bytes()) == EXPECTED_OLD
shutil.copy2(backup/'index.before.html', backup/'restore-check.html')
assert (backup/'restore-check.html').read_bytes() == original
receipt = {'site': str(site), 'site_link': str(SITE_LINK), 'before_sha256': EXPECTED_OLD,
           'after_sha256': EXPECTED_NEW, 'backup': str(backup), 'run_id': run_id}
(backup/'receipt.json').write_text(json.dumps(receipt, indent=2) + '\n')
# Prepare the recovery command before activating any new bytes.
rollback = '''import hashlib,json,os,shutil,tempfile
from pathlib import Path
root=Path(__file__).resolve().parent
r=json.loads((root/'receipt.json').read_text());site=Path(r['site'])
assert Path(r['site_link']).resolve()==site
assert hashlib.sha256(site.read_bytes()).hexdigest()==r['after_sha256'], 'site changed after deployment'
source=root/'index.before.html'
assert hashlib.sha256(source.read_bytes()).hexdigest()==r['before_sha256']
fd,name=tempfile.mkstemp(prefix='.amh-restore-',dir=site.parent);os.close(fd)
p=Path(name)
try:
 shutil.copy2(source,p);os.chown(p,site.stat().st_uid,site.stat().st_gid)
 assert p.read_bytes()==source.read_bytes()
 os.replace(p,site)
finally:
 p.unlink(missing_ok=True)
print('original HTML restored; Hub and nginx configuration untouched')
'''
(backup/'rollback.py').write_text(rollback)
fd, name = tempfile.mkstemp(prefix='.amh-index-', dir=site.parent)
os.close(fd)
candidate = Path(name)
try:
    shutil.copy2(site, candidate)  # Keep original mode and available security xattrs.
    os.chown(candidate, site.stat().st_uid, site.stat().st_gid)
    with candidate.open('wb') as stream:
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())
    assert digest(candidate.read_bytes()) == EXPECTED_NEW
    assert SITE_LINK.resolve() == site and digest(site.read_bytes()) == EXPECTED_OLD
    os.replace(candidate, site)
finally:
    candidate.unlink(missing_ok=True)
assert digest(site.read_bytes()) == EXPECTED_NEW
print(json.dumps({'published': True, 'sha256': EXPECTED_NEW, 'backup': str(backup),
                  'rollback': str(backup/'rollback.py'), 'only_homepage_changed': True}))

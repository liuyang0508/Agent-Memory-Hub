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

EXPECTED_OLD = '90270829a25283f98c8ace549dad5bbbbd471c540cca2bacd706c3cdb7ed3392'
EXPECTED_NEW = '84b01aff46ca178c26aa0e7c583f9cd5ec686d5b0422bfc1beb05e36fc8c7076'
ASSETS = {'chatgpt-desktop-dc5090afda.png': 'dc5090afda4e54f06726e6bf4c962e6f625f02455a3d7b240ec92cdd3b90dd1a', 'claude-cowork-d09eb2fe66.png': 'd09eb2fe66c52aad0f1b0069083edaf5892d24302a30798e24621f26f65678d5'}
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
def publish_assets():
    # Versioned, content-verified assets are staged before the HTML references them.
    payloads = {}
    for name, checksum in ASSETS.items():
        source = incoming.parent/'assets'/name
        assert not source.is_symlink()
        raw = source.read_bytes()
        assert raw.startswith(bytes([137,80,78,71,13,10,26,10])) and digest(raw) == checksum
        payloads[name] = raw
    directory = site.parent/'assets/native-memory'
    assert str(directory.resolve()).startswith('/var/www/agent-memory-hub/')
    directory.mkdir(parents=True, exist_ok=True)
    for name, raw in payloads.items():
        target = directory/name
        assert not target.is_symlink()
        if target.exists():
            assert digest(target.read_bytes()) == ASSETS[name], 'versioned asset conflict'
            continue
        fd, path = tempfile.mkstemp(prefix='.amh-logo-', dir=directory)
        os.close(fd)
        temporary = Path(path)
        try:
            shutil.copy2(site, temporary)  # Inherit a served file's permissions/security xattrs.
            os.chown(temporary, site.stat().st_uid, site.stat().st_gid)
            with temporary.open('wb') as stream:
                stream.write(raw); stream.flush(); os.fsync(stream.fileno())
            os.chmod(temporary, 0o644)
            assert digest(temporary.read_bytes()) == ASSETS[name]
            # Never overwrite a file another publisher created in the meantime.
            try: os.link(temporary, target)
            except FileExistsError:
                assert not target.is_symlink() and digest(target.read_bytes()) == ASSETS[name]
        finally:
            temporary.unlink(missing_ok=True)


original = site.read_bytes()
if digest(original) == EXPECTED_NEW:
    publish_assets()
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
           'after_sha256': EXPECTED_NEW, 'backup': str(backup), 'run_id': run_id, 'assets': ASSETS}
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
publish_assets()
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
                  'rollback': str(backup/'rollback.py'), 'assets': ASSETS, 'hub_and_nginx_unchanged': True}))

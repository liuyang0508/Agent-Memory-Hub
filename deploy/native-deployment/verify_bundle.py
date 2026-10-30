"""Validate the exact reviewed offline wheel set before installation or system writes."""
import hashlib
import json
from pathlib import Path
import re
import sys

RELEASE = 'e685c89283adfb3fc5ba6306e85bd4b17303b161'


def verify(root, approved):
    root = Path(root)
    manifest = json.loads((root/'manifest.json').read_text())
    assert manifest['release'] == RELEASE
    files = manifest['files']
    assert files and isinstance(files, dict)
    digest = hashlib.sha256(json.dumps(files, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    assert digest == approved == manifest['bundle_sha256'], 'bundle approval mismatch'
    directory = root/'wheelhouse'
    assert not directory.is_symlink()
    assert {p.name for p in directory.iterdir()} == set(files), 'unexpected or missing bundle files'
    for name, checksum in files.items():
        assert re.fullmatch(r'[A-Za-z0-9_.+!-]+\.whl', name)
        p = directory/name
        assert p.is_file() and not p.is_symlink()
        assert hashlib.sha256(p.read_bytes()).hexdigest() == checksum, 'wheel checksum mismatch'
    wheel = manifest['wheel']
    assert wheel == 'agent_memory_hub-1.1.1-py3-none-any.whl' and wheel in files
    return directory/wheel


if __name__ == '__main__':
    print(verify(sys.argv[1], sys.argv[2]))

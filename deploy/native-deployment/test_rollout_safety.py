"""Local fault injection: never contacts a host or executes the deployment body."""
import hashlib
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

HERE = Path(__file__).resolve().parent


class RollbackSafety(unittest.TestCase):
    def test_failed_copy_cannot_replace_live_configuration(self):
        source = (HERE / 'apply.sh').read_text()
        function = source[source.index('rollback() {'):source.index('\ntrap rollback EXIT')]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            site = root / 'site.conf'
            site.write_text('approved live configuration\n')
            backup = root / 'backup'
            backup.mkdir()
            (backup / 'site.before.conf').write_text('original configuration\n')
            expected = hashlib.sha256(site.read_bytes()).hexdigest()
            script = root / 'fault.sh'
            script.write_text('''#!/usr/bin/env bash
set -u
site=$1
backup=$2
expected=$3
installed_site_hash=$3
route_changed=1
unit_created=0
sha256sum() { printf '%s  %s\\n' "$expected" "$1"; }
cp() { printf 'partial copy' > "$3"; return 1; }
mv() { printf 'unexpected rename' > "$backup/renamed"; return 0; }
''' + function + '\n(exit 17)\nrollback\n')
            result = subprocess.run(['bash', str(script), str(site), str(backup), expected],
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 17)
            self.assertEqual(site.read_text(), 'approved live configuration\n')
            self.assertFalse((backup / 'renamed').exists())
            self.assertIn('existing site configuration preserved', result.stderr)

    def test_no_acknowledgement_exits_before_deployment(self):
        result = subprocess.run(['bash', str(HERE / 'apply.sh'), 'not-approved', '/missing'],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertIn('explicit deployment acknowledgement required', result.stderr)

    def test_embedded_python_and_shell_are_parseable(self):
        import re
        for path in HERE.glob('*.sh'):
            subprocess.run(['bash', '-n', str(path)], check=True)
            lines = path.read_text().splitlines()
            index = 0
            while index < len(lines):
                match = re.search(r"<<'(PY|PID|PY_CLEANUP|MANIFEST)'", lines[index])
                if match:
                    start = index + 1
                    index = start
                    while index < len(lines) and lines[index] != match[1]:
                        index += 1
                    compile('\n'.join(lines[start:index]), str(path), 'exec')
                index += 1


class BundleSafety(unittest.TestCase):
    def test_tampered_dependency_or_extra_wheel_is_rejected(self):
        import json
        from verify_bundle import RELEASE, verify
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);house=root/'wheelhouse';house.mkdir()
            wheel='agent_memory_hub-1.1.1-py3-none-any.whl'
            (house/wheel).write_bytes(b'app')
            (house/'dependency-1-py3-none-any.whl').write_bytes(b'dep')
            files={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in house.iterdir()}
            approved=hashlib.sha256(json.dumps(files,sort_keys=True,separators=(',',':')).encode()).hexdigest()
            (root/'manifest.json').write_text(json.dumps({'release':RELEASE,'wheel':wheel,'files':files,'bundle_sha256':approved}))
            self.assertEqual(verify(root,approved),house/wheel)
            (house/'dependency-1-py3-none-any.whl').write_bytes(b'tampered')
            with self.assertRaises(AssertionError):verify(root,approved)
            (house/'dependency-1-py3-none-any.whl').write_bytes(b'dep')
            (house/'extra-1-py3-none-any.whl').write_bytes(b'extra')
            with self.assertRaises(AssertionError):verify(root,approved)


if __name__ == '__main__':
    unittest.main()

"""Exercise the real publisher against a temporary site, including rollback and drift."""
from pathlib import Path
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
SOURCE = Path(os.environ['AMH_SITE_CANDIDATE'])
BASE_BYTES = subprocess.check_output(['git','show','60cad8d210dc69f135c03168ede7b88d36547c1a:docs/visuals/agent-memory-hub-official-site.html'],cwd=HERE.parents[1])


class PublishSafety(unittest.TestCase):
    def fixture(self, directory):
        root=Path(directory).resolve();web=root/'web';site=web/'releases/old/index.html';site.parent.mkdir(parents=True)
        site.write_bytes(BASE_BYTES);(web/'current').symlink_to(site.parent,target_is_directory=True)
        backup=root/'backups';incoming=root/'new.html';incoming.write_bytes(SOURCE.read_bytes())
        shutil.copytree(SOURCE.parent/'media/native-memory',root/'assets')
        code=(HERE/'publish.py').read_text().replace("Path('/var/www/agent-memory-hub/current/index.html')",f'Path({str(web/"current/index.html")!r})').replace("'/var/www/agent-memory-hub/'",repr(str(web)+'/')).replace("Path('/var/backups/amh-site')",f'Path({str(backup)!r})')
        script=root/'publisher.py';script.write_text(code)
        return site,backup,[sys.executable,str(script),str(incoming),'12345']

    def test_publish_is_atomic_and_original_can_be_restored(self):
        with tempfile.TemporaryDirectory() as directory:
            site,backup,cmd=self.fixture(directory)
            result=subprocess.run(cmd,capture_output=True,text=True,check=True);receipt=json.loads(result.stdout)
            self.assertEqual(site.read_bytes(),SOURCE.read_bytes())
            self.assertEqual(len(list((site.parent/'assets/native-memory').glob('*.png'))),2)
            again=json.loads(subprocess.check_output(cmd,text=True));self.assertTrue(again['already_current'])
            self.assertEqual(len(list(backup.iterdir())),1)
            subprocess.run([sys.executable,receipt['rollback']],check=True,capture_output=True)
            self.assertEqual(site.read_bytes(),BASE_BYTES)

    def test_bad_logo_does_not_activate_the_new_html(self):
        with tempfile.TemporaryDirectory() as directory:
            site,backup,cmd=self.fixture(directory)
            next((Path(directory)/'assets').glob('*.png')).write_bytes(b'corrupted upload')
            result=subprocess.run(cmd,capture_output=True,text=True)
            self.assertNotEqual(result.returncode,0)
            self.assertEqual(site.read_bytes(),BASE_BYTES)
            self.assertFalse((site.parent/'assets/native-memory').exists())

    def test_drift_is_preserved_without_creating_backup_or_replacement(self):
        with tempfile.TemporaryDirectory() as directory:
            site,backup,cmd=self.fixture(directory);site.write_text('someone else changed this page')
            result=subprocess.run(cmd,capture_output=True,text=True)
            self.assertNotEqual(result.returncode,0)
            self.assertEqual(site.read_text(),'someone else changed this page')
            self.assertFalse(backup.exists())


if __name__=='__main__':unittest.main()

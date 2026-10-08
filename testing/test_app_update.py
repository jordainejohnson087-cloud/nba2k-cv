import json
import hashlib
import io
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import app_update
from app import discover_model
from scripts.build_release import build


class UpdateTests(unittest.TestCase):
    def test_release_contains_only_code_and_live_feed(self):
        with tempfile.TemporaryDirectory() as root:
            archive=Path(root)/'release.zip'
            source=Path(__file__).resolve().parents[1]/'update_source.json'
            original=source.read_bytes()
            version,feed=build(archive,'example/nba2k-cv')
            with zipfile.ZipFile(archive) as z:
                self.assertEqual(set(z.namelist()),app_update.ALLOWED)
                self.assertEqual(json.loads(z.read('update_source.json'))['manifest_url'],feed)
                self.assertEqual(json.loads(z.read('app_version.json'))['version'],version)
            self.assertEqual(source.read_bytes(),original)

    def test_online_release_download_checks_hash_before_install(self):
        with tempfile.TemporaryDirectory() as root:
            project=Path(root)/'project';project.mkdir()
            (project/'app_version.json').write_text('{"version":1}')
            archive=Path(root)/'release.zip';self.archive(archive)
            payload=archive.read_bytes()
            info=dict(version=2,download_url='https://example.org/release.zip',
                      sha256=hashlib.sha256(payload).hexdigest(),size_bytes=len(payload))
            manifest_url='https://example.org/update.json'
            class Response(io.BytesIO):
                def __init__(self,data,url):super().__init__(data);self.url=url
                def geturl(self):return self.url
            def opener(url,timeout=15):
                return Response(json.dumps(info).encode() if url==manifest_url else payload,url)
            self.assertEqual(app_update.fetch_manifest(manifest_url,project,opener)['version'],2)
            saved=app_update.download_release(info,project,opener)
            self.assertEqual(saved.read_bytes(),payload)
            info['sha256']='0'*64
            with self.assertRaisesRegex(ValueError,'SHA-256'):
                app_update.download_release(info,project,opener)
            self.assertEqual(app_update.current_version(project),1)

    def test_finds_model_from_previous_live_run(self):
        with tempfile.TemporaryDirectory() as root:
            project=Path(root)/'project';run=project/'test_results'/'live_1';run.mkdir(parents=True)
            weights=Path(root)/'weights'/'best.pt';weights.parent.mkdir();weights.write_bytes(b'model')
            (run/'live_run.json').write_text(json.dumps({'model':str(weights)}))
            self.assertEqual(discover_model(project,Path(root)/'Downloads'),weights)

    def archive(self,path,version=2,extra=None):
        files={'app_version.json':json.dumps({'version':version}),
               'app.py':'new app', 'run_live.py':'new runner'}
        files.update(extra or {})
        with zipfile.ZipFile(path,'w') as z:
            for name,value in files.items():z.writestr(name,value)

    def test_update_preserves_environment_model_and_results(self):
        with tempfile.TemporaryDirectory() as root:
            project=Path(root)/'project';project.mkdir()
            (project/'app_version.json').write_text('{"version":1}')
            (project/'app.py').write_text('old app')
            for name in ('.venv/Scripts/python.exe','test_results/live/live_run.json','best.pt'):
                target=project/name;target.parent.mkdir(parents=True,exist_ok=True)
                target.write_text('user data')
            archive=Path(root)/'update.zip';self.archive(archive)
            result=app_update.apply_update(archive,project)
            self.assertEqual(result['version'],2)
            self.assertEqual((project/'run_live.py').read_text(),'new runner')
            self.assertEqual((project/'updates/backups/1/app.py').read_text(),'old app')
            for name in ('.venv/Scripts/python.exe','test_results/live/live_run.json','best.pt'):
                self.assertEqual((project/name).read_text(),'user data')

    def test_rejects_unexpected_paths_and_old_versions(self):
        with tempfile.TemporaryDirectory() as root:
            project=Path(root)/'project';project.mkdir()
            (project/'app_version.json').write_text('{"version":1}')
            archive=Path(root)/'bad.zip';self.archive(archive,extra={'../escape.py':'bad'})
            with self.assertRaisesRegex(ValueError,'Unexpected'):
                app_update.apply_update(archive,project)
            self.archive(archive,version=1)
            with self.assertRaisesRegex(ValueError,'not newer'):
                app_update.apply_update(archive,project)

    def test_failed_replacement_rolls_back_prior_files(self):
        with tempfile.TemporaryDirectory() as root:
            project=Path(root)/'project';project.mkdir()
            (project/'app_version.json').write_text('{"version":1}')
            (project/'app.py').write_text('old app')
            archive=Path(root)/'update.zip';self.archive(archive)
            real=app_update.os.replace
            def fail_runner(src,dst):
                if Path(dst).name=='run_live.py':raise OSError('simulated lock')
                return real(src,dst)
            with patch.object(app_update.os,'replace',side_effect=fail_runner):
                with self.assertRaisesRegex(OSError,'simulated lock'):
                    app_update.apply_update(archive,project)
            self.assertEqual((project/'app.py').read_text(),'old app')
            self.assertEqual(app_update.current_version(project),1)


if __name__=='__main__':unittest.main()

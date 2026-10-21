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


class GitHubFeedTests(unittest.TestCase):
    repository='example/nba2k-cv'
    root='https://github.com/example/nba2k-cv/releases/'
    feed=root+'latest/download/update.json'
    api='https://api.github.com/repos/example/nba2k-cv/releases?per_page=20'

    class Response(io.BytesIO):
        def __init__(self,data,url):super().__init__(json.dumps(data).encode());self.url=url
        def geturl(self):return self.url

    def release(self,version,**changes):
        base=self.root+'download/v'+str(version)+'/'
        return dict(tag_name='v'+str(version),draft=False,prerelease=False,
                    assets=[dict(name=name,state='uploaded',size=4,digest='sha256:'+'a'*64,
                                 browser_download_url=base+name) for name in ('NBA2K_CV_Update.zip','update.json')],**changes)

    def info(self,version=2):
        return dict(version=version,size_bytes=4,sha256='a'*64,
                    download_url=self.root+'latest/download/NBA2K_CV_Update.zip')

    def test_empty_latest_release_falls_back_to_complete_stable_release(self):
        import urllib.error
        calls=[]
        def opener(url,timeout):
            calls.append(url)
            if url==self.feed:raise urllib.error.HTTPError(url,404,'missing',{},None)
            if url==self.api:return self.Response([dict(self.release(3),assets=[]),dict(self.release(4),prerelease=True),self.release(2)],url)
            self.assertEqual(url,self.root+'download/v2/update.json')
            return self.Response(self.info(),url)
        with tempfile.TemporaryDirectory() as folder:
            result=app_update.fetch_manifest(self.feed,folder,opener)
        self.assertEqual(result['version'],2)
        self.assertEqual(result['download_url'],self.root+'download/v2/NBA2K_CV_Update.zip')
        self.assertEqual(len(calls),3)

    def test_old_github_manifest_zip_url_is_pinned_before_download(self):
        with tempfile.TemporaryDirectory() as folder:
            result=app_update.fetch_manifest(self.feed,folder,lambda url,timeout:self.Response(self.info(),url))
        self.assertEqual(result['download_url'],self.root+'download/v2/NBA2K_CV_Update.zip')

    def test_release_without_assets_has_an_actionable_error(self):
        import urllib.error
        def opener(url,timeout):
            if url==self.feed:raise urllib.error.HTTPError(url,404,'missing',{},None)
            return self.Response([dict(self.release(3),assets=[])],url)
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaisesRegex(ValueError,'No complete app update'):
                app_update.fetch_manifest(self.feed,folder,opener)

    def test_fallback_rejects_manifest_for_a_different_release(self):
        import urllib.error
        def opener(url,timeout):
            if url==self.feed:raise urllib.error.HTTPError(url,404,'missing',{},None)
            return self.Response([self.release(2)] if url==self.api else self.info(3),url)
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaisesRegex(ValueError,'do not match'):
                app_update.fetch_manifest(self.feed,folder,opener)

    def test_non_404_failure_does_not_silently_select_an_old_release(self):
        import urllib.error
        calls=[]
        def opener(url,timeout):
            calls.append(url);raise urllib.error.HTTPError(url,500,'server error',{},None)
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(urllib.error.HTTPError):app_update.fetch_manifest(self.feed,folder,opener)
        self.assertEqual(calls,[self.feed])

    def test_missing_zip_preserves_existing_download_and_explains_release_failure(self):
        import urllib.error
        def opener(url,timeout):raise urllib.error.HTTPError(url,404,'missing',{},None)
        with tempfile.TemporaryDirectory() as folder:
            saved=Path(folder)/'updates'/'downloads'/'NBA2K_CV_Update_2.zip'
            saved.parent.mkdir(parents=True);saved.write_bytes(b'previous verified download')
            with self.assertRaisesRegex(ValueError,'ZIP is missing'):
                app_update.download_release(self.info(),folder,opener)
            self.assertEqual(saved.read_bytes(),b'previous verified download')
            self.assertFalse(saved.with_suffix('.partial').exists())

    def test_non_object_manifest_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaisesRegex(ValueError,'Invalid update manifest'):
                app_update.fetch_manifest(self.feed,folder,lambda url,timeout:self.Response([],url))


class ReleaseArtifactTests(unittest.TestCase):
    def test_build_is_reproducible_and_published_bytes_are_verified(self):
        from testing.make_update_manifest import make_manifest,verify_manifest
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);first=root/'first.zip';second=root/'second.zip';manifest=root/'update.json'
            version,_=build(first,'example/nba2k-cv');build(second,'example/nba2k-cv')
            self.assertEqual(first.read_bytes(),second.read_bytes())
            tag='v'+str(version)
            url='https://github.com/example/nba2k-cv/releases/download/'+tag+'/NBA2K_CV_Update.zip'
            manifest.write_text(json.dumps(make_manifest(first,url)))
            self.assertEqual(verify_manifest(first,manifest,tag)['version'],version)
            first.write_bytes(first.read_bytes()+b'tampered')
            with self.assertRaisesRegex(ValueError,'SHA-256'):
                verify_manifest(first,manifest,tag)

    def test_publishing_rejects_mutable_latest_url(self):
        from testing.make_update_manifest import make_manifest,verify_manifest
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);archive=root/'release.zip';manifest=root/'update.json'
            version,_=build(archive,'example/nba2k-cv')
            info=make_manifest(archive,'https://github.com/example/nba2k-cv/releases/latest/download/NBA2K_CV_Update.zip')
            manifest.write_text(json.dumps(info))
            with self.assertRaisesRegex(ValueError,'pinned'):
                verify_manifest(archive,manifest,'v'+str(version))

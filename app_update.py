"""Install a downloaded code-only update safely, preserving user data."""
import json
import hashlib
import os
import shutil
import tempfile
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath
from urllib.parse import urlparse


ROOT_FILES = {
    'app.py','app_update.py','app_model.py','app_version.json','README.md','requirements.txt',
    'run_live.py','run_video.py','meter_core.py','Launch NBA2K CV.bat',
    'update_source.json',
}
TEST_FILES = {
    'testing/learning_loop.py','testing/evaluate.py','testing/replay_detections.py',
    'testing/self_check.py','testing/test_self_check.py',
    'testing/make_update_manifest.py',
    'testing/check_capture.py','testing/test_core.py','testing/test_live.py',
    'testing/test_app_model.py',
    'testing/test_learning_loop.py','testing/test_app_update.py',
    'testing/annotations.example.json',
}
ALLOWED = ROOT_FILES | TEST_FILES
MAX_BYTES = 10 * 1024 * 1024
MAX_DOWNLOAD_BYTES = 20 * 1024 * 1024
MAX_MANIFEST_BYTES = 64 * 1024


def configured_feed(project):
    path=Path(project)/'update_source.json'
    if not path.is_file():return None
    url=json.loads(path.read_text(encoding='utf-8')).get('manifest_url','')
    return url or None


def _https(url):
    parsed=urlparse(url)
    if parsed.scheme!='https' or not parsed.netloc or parsed.username or parsed.password:
        raise ValueError('Update links must use HTTPS')


def fetch_manifest(url, project, opener=urllib.request.urlopen):
    _https(url)
    with opener(url,timeout=15) as response:
        if urlparse(response.geturl()).scheme!='https':
            raise ValueError('Update feed redirected away from HTTPS')
        data=response.read(MAX_MANIFEST_BYTES+1)
    if len(data)>MAX_MANIFEST_BYTES:raise ValueError('Update manifest is too large')
    info=json.loads(data)
    version=info.get('version')
    download=info.get('download_url')
    digest=info.get('sha256')
    size=info.get('size_bytes')
    if type(version) is not int or type(size) is not int or not 0<size<=MAX_DOWNLOAD_BYTES:
        raise ValueError('Invalid release version or size')
    if not isinstance(download,str):raise ValueError('Release URL missing')
    _https(download)
    if not isinstance(digest,str) or len(digest)!=64 or any(c not in '0123456789abcdef' for c in digest):
        raise ValueError('Invalid release SHA-256')
    return info if version>current_version(project) else None


def download_release(info, project, opener=urllib.request.urlopen):
    """Download to a private temporary file, verify bytes, then verify ZIP/version."""
    target_dir=Path(project)/'updates'/'downloads'
    target_dir.mkdir(parents=True,exist_ok=True)
    target=target_dir/f"NBA2K_CV_Update_{info['version']}.zip"
    temporary=target.with_suffix('.partial')
    digest=hashlib.sha256();size=0
    try:
        with opener(info['download_url'],timeout=30) as response,temporary.open('wb') as out:
            if urlparse(response.geturl()).scheme!='https':
                raise ValueError('Release redirected away from HTTPS')
            while True:
                chunk=response.read(128*1024)
                if not chunk:break
                size+=len(chunk)
                if size>MAX_DOWNLOAD_BYTES:raise ValueError('Release is too large')
                digest.update(chunk);out.write(chunk)
        if size!=info['size_bytes'] or digest.hexdigest()!=info['sha256']:
            raise ValueError('Release bytes failed size or SHA-256 verification')
        version,_=inspect_update(temporary,project)
        if version!=info['version']:raise ValueError('Release version differs from manifest')
        os.replace(temporary,target)
        return target
    finally:
        temporary.unlink(missing_ok=True)


def current_version(project):
    path = Path(project)/'app_version.json'
    if not path.is_file(): return 0
    return int(json.loads(path.read_text(encoding='utf-8'))['version'])


def inspect_update(archive, project):
    """Validate member names, sizes, version, and ZIP integrity before edits."""
    archive=Path(archive)
    with zipfile.ZipFile(archive) as z:
        members={}
        total=0
        for info in z.infolist():
            if info.is_dir(): continue
            name=info.filename.replace('\\','/')
            path=PurePosixPath(name)
            if (name not in ALLOWED or path.is_absolute() or '..' in path.parts or
                    name in members or info.file_size > MAX_BYTES):
                raise ValueError(f'Unexpected or duplicate file in update: {name}')
            if (info.external_attr >> 16) & 0o170000 == 0o120000:
                raise ValueError(f'Link not allowed in update: {name}')
            members[name]=info
            total+=info.file_size
            if total > MAX_BYTES: raise ValueError('Update is too large')
        if 'app_version.json' not in members or 'app.py' not in members:
            raise ValueError('Update is missing app metadata or desktop app')
        version=json.loads(z.read('app_version.json'))['version']
        if type(version) is not int or version <= current_version(project):
            raise ValueError('Update is not newer than the installed version')
        if z.testzip() is not None: raise ValueError('Update ZIP failed integrity check')
    return version,sorted(members)


def apply_update(archive, project):
    """Stage and back up all code files; restore them if any replacement fails."""
    project=Path(project).resolve()
    version,names=inspect_update(archive,project)
    backups=project/'updates'/'backups'/str(current_version(project))
    backups.mkdir(parents=True,exist_ok=True)
    changed=[]
    with tempfile.TemporaryDirectory(prefix='nba2k-update-',dir=project/'updates') as temporary:
        stage=Path(temporary)
        with zipfile.ZipFile(archive) as z:
            for name in names:
                target=stage/name
                target.parent.mkdir(parents=True,exist_ok=True)
                target.write_bytes(z.read(name))
        try:
            # Version metadata is committed last so a partial update is not current.
            for name in [n for n in names if n!='app_version.json']+['app_version.json']:
                target=project/name
                saved=backups/name
                target.parent.mkdir(parents=True,exist_ok=True)
                saved.parent.mkdir(parents=True,exist_ok=True)
                existed=target.exists()
                if existed: shutil.copy2(target,saved)
                os.replace(stage/name,target)
                changed.append((target,saved,existed))
        except Exception:
            for target,saved,existed in reversed(changed):
                if existed: shutil.copy2(saved,target)
                else: target.unlink(missing_ok=True)
            raise
    return dict(version=version,files=len(changed),backup=str(backups))


def newest_download(downloads, project):
    candidates=[]
    if not Path(downloads).exists(): return None
    for path in Path(downloads).rglob('NBA2K_CV_Update*.zip'):
        try:
            version,_=inspect_update(path,project)
            candidates.append((version,path.stat().st_mtime,path))
        except (OSError,ValueError,zipfile.BadZipFile,KeyError,TypeError):
            continue
    return max(candidates)[2] if candidates else None

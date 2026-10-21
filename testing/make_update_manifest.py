"""Create the small JSON feed after an update ZIP has a stable HTTPS download URL."""
import argparse
import hashlib
import json
import sys
import tempfile
import zipfile
from pathlib import Path
from urllib.parse import urlparse


def make_manifest(archive, download_url):
    archive=Path(archive)
    url=urlparse(download_url)
    if url.scheme!='https' or not url.netloc or url.username or url.password:
        raise ValueError('A public HTTPS download URL is required')
    with zipfile.ZipFile(archive) as z:
        version=json.loads(z.read('app_version.json'))['version']
    if type(version) is not int or version<=0:raise ValueError('Invalid release version')
    return dict(version=version,download_url=download_url,
                size_bytes=archive.stat().st_size,
                sha256=hashlib.sha256(archive.read_bytes()).hexdigest())


def verify_manifest(archive,manifest_path,tag):
    """Check the actual publish/download bytes, archive layout and Python syntax."""
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
    from app_update import ALLOWED, inspect_update
    archive=Path(archive)
    info=json.loads(Path(manifest_path).read_text(encoding='utf-8'))
    if not isinstance(info,dict):raise ValueError('Invalid update manifest')
    version=info.get('version')
    if type(version) is not int or version<=0 or tag!='v'+str(version):
        raise ValueError('Release tag and manifest version differ')
    url=info.get('download_url','')
    parsed=urlparse(url)
    if (parsed.scheme!='https' or parsed.hostname!='github.com' or parsed.username or parsed.password
        or parsed.query or parsed.fragment or not parsed.path.endswith('/releases/download/'+tag+'/NBA2K_CV_Update.zip')):
        raise ValueError('Release ZIP URL must be pinned to its GitHub version tag')
    if (type(info.get('size_bytes')) is not int or archive.stat().st_size!=info['size_bytes']
        or hashlib.sha256(archive.read_bytes()).hexdigest()!=info.get('sha256')):
        raise ValueError('Release bytes failed size or SHA-256 verification')
    with tempfile.TemporaryDirectory() as empty_project:
        actual,names=inspect_update(archive,empty_project)
    if actual!=version or set(names)!=ALLOWED:
        raise ValueError('Release ZIP metadata or file set differs')
    with zipfile.ZipFile(archive) as z:
        for name in names:
            if name.endswith('.py'):compile(z.read(name),name,'exec')
    return info


def main():
    p=argparse.ArgumentParser(description='Generate a published auto-update manifest')
    p.add_argument('--archive',type=Path,required=True)
    p.add_argument('--download-url')
    p.add_argument('--out',type=Path)
    p.add_argument('--verify',action='store_true')
    p.add_argument('--manifest',type=Path)
    p.add_argument('--tag')
    args=p.parse_args()
    if args.verify:
        if not args.manifest or not args.tag:p.error('--verify requires --manifest and --tag')
        print(json.dumps(verify_manifest(args.archive,args.manifest,args.tag)));return
    if not args.download_url or not args.out:p.error('Generation requires --download-url and --out')
    manifest=make_manifest(args.archive,args.download_url)
    args.out.write_text(json.dumps(manifest,indent=2),encoding='utf-8')
    print(args.out)


if __name__=='__main__':main()

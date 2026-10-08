"""Create the small JSON feed after an update ZIP has a stable HTTPS download URL."""
import argparse
import hashlib
import json
import sys
import zipfile
from pathlib import Path
from urllib.parse import urlparse


def make_manifest(archive, download_url):
    archive=Path(archive)
    url=urlparse(download_url)
    if url.scheme!='https' or not url.netloc:
        raise ValueError('A public HTTPS download URL is required')
    with zipfile.ZipFile(archive) as z:
        version=json.loads(z.read('app_version.json'))['version']
    if type(version) is not int:raise ValueError('Invalid release version')
    return dict(version=version,download_url=download_url,
                size_bytes=archive.stat().st_size,
                sha256=hashlib.sha256(archive.read_bytes()).hexdigest())


def main():
    p=argparse.ArgumentParser(description='Generate a published auto-update manifest')
    p.add_argument('--archive',type=Path,required=True)
    p.add_argument('--download-url',required=True)
    p.add_argument('--out',type=Path,required=True)
    args=p.parse_args()
    manifest=make_manifest(args.archive,args.download_url)
    args.out.write_text(json.dumps(manifest,indent=2),encoding='utf-8')
    print(args.out)


if __name__=='__main__':main()

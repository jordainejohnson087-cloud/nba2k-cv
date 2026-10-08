"""Build a code-only release ZIP with its GitHub update feed configured."""
import argparse
import io
import json
import re
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app_update import ALLOWED, MAX_BYTES


def build(archive, repository):
    if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repository):
        raise ValueError('Repository must be OWNER/REPO')
    version = json.loads((ROOT / 'app_version.json').read_text(encoding='utf-8'))['version']
    if type(version) is not int or version <= 0:
        raise ValueError('Invalid app version')
    feed = f'https://github.com/{repository}/releases/latest/download/update.json'
    files = {}
    for name in sorted(ALLOWED):
        source = ROOT / name
        if not source.is_file() or source.is_symlink():
            raise ValueError(f'Required release file missing or linked: {name}')
        files[name] = source.read_bytes()
    files['update_source.json'] = (json.dumps({'manifest_url': feed}) + '\n').encode()
    if sum(map(len, files.values())) > MAX_BYTES:
        raise ValueError('Release exceeds updater size limit')
    archive = Path(archive)
    archive.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive, 'w', compression=zipfile.ZIP_DEFLATED) as output:
        for name, data in files.items():
            output.writestr(name, data)
    with zipfile.ZipFile(archive) as check:
        if check.testzip() is not None:
            raise ValueError('Release ZIP failed integrity check')
    return version, feed


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--repository', required=True, help='GitHub OWNER/REPO')
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    print(build(args.out, args.repository))

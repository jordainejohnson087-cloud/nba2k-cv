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
    # First-generation installed updaters accept only their original filenames.
    # Bundle pure-Python support into run_live.py, and adapter resources as
    # literals, instead of shipping new archive members those clients reject.
    live = files['run_live.py'].decode('utf-8')
    live = live.replace('from live_link import BridgeClient, SnapshotPublisher, atomic_write',
                        (ROOT/'live_link.py').read_text(encoding='utf-8'))
    live = live.replace('from bridge_service import BridgeService, BridgeServiceClient, BridgeProcess, run_bridge_host, AsyncBridgeServiceClient',
                        (ROOT/'bridge_service.py').read_text(encoding='utf-8').replace('from live_link import BridgeClient',''))
    live = live.replace('from capture_pipe import LatestCapture, RawMailbox, SharedCapture, CaptureLease, OwnedCapture',
                        (ROOT/'capture_pipe.py').read_text(encoding='utf-8'))
    live = live.replace('from titan_control import GtunerController, slot_number, choose_meter_slot, discover_gtuner, valid_gtuner_path, launch_gtuner_background, titan_hardware_inventory',
                        (ROOT/'titan_control.py').read_text(encoding='utf-8'))
    live = live.replace('from meter_green import GreenGate, green_config, measure_green',
                        (ROOT/'meter_green.py').read_text(encoding='utf-8'))
    resources = '\nGTUNER_SOURCE = ' + repr((ROOT/'bridge/gtuner_meter.py').read_text(encoding='utf-8'))
    resources += '\nHEARTBEAT_SOURCE = ' + repr((ROOT/'bridge/titan_heartbeat.gpc').read_text(encoding='utf-8')) + '\n'
    resources += '\nCONSOLE_TEST_SOURCE = ' + repr((ROOT/'testing/console_button_check.py').read_text(encoding='utf-8')) + '\n'
    resources += '\nMETER_GPC_SOURCE = ' + repr((ROOT/'bridge/titan_meter.gpc').read_text(encoding='utf-8')) + '\n'
    live = live.replace("if __name__ == '__main__': main()", resources + "\nif __name__ == '__main__': main()")
    compile(live, 'run_live.py', 'exec')
    files['run_live.py'] = live.encode('utf-8')
    files['update_source.json'] = (json.dumps({'manifest_url': feed}) + '\n').encode()
    if sum(map(len, files.values())) > MAX_BYTES:
        raise ValueError('Release exceeds updater size limit')
    archive = Path(archive)
    archive.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive, 'w', compression=zipfile.ZIP_DEFLATED) as output:
        for name, data in files.items():
            info=zipfile.ZipInfo(name,date_time=(2000,1,1,0,0,0))
            info.create_system=3;info.external_attr=0o100644<<16
            info.compress_type=zipfile.ZIP_DEFLATED
            output.writestr(info,data)
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

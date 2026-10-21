"""Check the local Python, CUDA, model, and writable output before a live test."""
import argparse
import importlib
import json
import platform
import sys
import struct
from pathlib import Path


def check(model, importer=importlib.import_module):
    result=dict(python=sys.version.split()[0],executable=sys.executable,base_prefix=sys.base_prefix,
                bits=struct.calcsize('P')*8,platform=platform.platform(),
                model=str(model),checks={},ready=False)
    checks=result['checks']
    checks['app_runtime']=dict(ok=sys.platform!='win32' or (sys.version_info[:2]==(3,11) and struct.calcsize('P')==8),
        message='Windows app runtime requires Python 3.11 x64')
    if not model or not Path(model).is_file():
        checks['model']=dict(ok=False,message='Select an existing best.pt')
    else:
        checks['model']=dict(ok=True,size_bytes=Path(model).stat().st_size)
    modules=['cv2','torch','ultralytics','PIL']
    if sys.platform=='win32':modules+=['win32api','pywintypes','pythoncom']
    for name in modules:
        try:
            module=importer(name)
            checks[name]=dict(ok=True,version=str(getattr(module,'__version__','unknown')))
        except Exception as exc:
            checks[name]=dict(ok=False,message=f'{type(exc).__name__}: {exc}')
    if checks['torch']['ok']:
        try:
            torch=importer('torch')
            available=torch.cuda.is_available()
            if available:
                name=torch.cuda.get_device_name(0)
                torch.empty(1,device='cuda')
            else:name=None
            checks['gpu']=dict(ok=available,name=name,
                               message=None if available else 'PyTorch cannot use CUDA')
        except Exception as exc:
            checks['gpu']=dict(ok=False,message=f'{type(exc).__name__}: {exc}')
    else:checks['gpu']=dict(ok=False,message='PyTorch unavailable')
    if checks['model']['ok'] and checks['ultralytics']['ok']:
        try:
            importer('ultralytics').YOLO(str(model))
            checks['model_load']=dict(ok=True)
        except Exception as exc:
            checks['model_load']=dict(ok=False,message=f'{type(exc).__name__}: {exc}')
    else:checks['model_load']=dict(ok=False,message='Model or Ultralytics unavailable')
    result['ready']=all(checks[k]['ok'] for k in ['app_runtime','model','gpu','model_load',*modules])
    result['gtuner_embedded_verified']=False
    return result


def main():
    p=argparse.ArgumentParser(description='Check NBA 2K CV desktop environment')
    p.add_argument('--model',type=Path,required=True)
    p.add_argument('--out',type=Path,default=Path('test_results/setup_check.json'))
    args=p.parse_args()
    report=check(args.model)
    args.out.parent.mkdir(parents=True,exist_ok=True)
    args.out.write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps(report,indent=2))
    return 0 if report['ready'] else 1


if __name__=='__main__':sys.exit(main())

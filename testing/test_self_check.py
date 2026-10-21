import tempfile
from unittest.mock import patch
import unittest
from pathlib import Path
from types import SimpleNamespace

from testing.self_check import check


class SetupTests(unittest.TestCase):
    def test_missing_model_is_reported_even_with_dependencies(self):
        torch=SimpleNamespace(__version__='test',cuda=SimpleNamespace(
            is_available=lambda:True,get_device_name=lambda _: 'test GPU'),
            empty=lambda *args,**kwargs:object())
        modules=dict(cv2=SimpleNamespace(__version__='test'),torch=torch,
                     ultralytics=SimpleNamespace(__version__='test',YOLO=lambda path:object()),
                     PIL=SimpleNamespace(__version__='test'),win32api=SimpleNamespace(),
                     pywintypes=SimpleNamespace(),pythoncom=SimpleNamespace())
        with tempfile.TemporaryDirectory() as root:
            absent=Path(root)/'best.pt'
            with patch('testing.self_check.sys.version_info',(3,11,9)):
                report=check(absent,importer=lambda name:modules[name])
            self.assertFalse(report['ready'])
            self.assertFalse(report['checks']['model']['ok'])
            absent.write_bytes(b'weights')
            with patch('testing.self_check.sys.version_info',(3,11,9)):
                report=check(absent,importer=lambda name:modules[name])
            self.assertTrue(report['ready'])

    def test_missing_win32_is_visible_without_claiming_gtuner_ready(self):
        def importer(name):
            if name in ('win32api','pywintypes','pythoncom'):raise ImportError('missing '+name)
            return SimpleNamespace(__version__='test',cuda=SimpleNamespace(is_available=lambda:False))
        with patch('testing.self_check.sys.platform','win32'),patch('testing.self_check.sys.version_info',(3,11,9)):
            report=check(None,importer=importer)
        self.assertFalse(report['checks']['win32api']['ok'])
        self.assertFalse(report['ready'])
        self.assertFalse(report['gtuner_embedded_verified'])


if __name__=='__main__':unittest.main()

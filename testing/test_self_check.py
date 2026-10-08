import tempfile
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
                     ultralytics=SimpleNamespace(__version__='test',YOLO=lambda path:object()))
        with tempfile.TemporaryDirectory() as root:
            absent=Path(root)/'best.pt'
            report=check(absent,importer=lambda name:modules[name])
            self.assertFalse(report['ready'])
            self.assertFalse(report['checks']['model']['ok'])
            absent.write_bytes(b'weights')
            report=check(absent,importer=lambda name:modules[name])
            self.assertTrue(report['ready'])


if __name__=='__main__':unittest.main()

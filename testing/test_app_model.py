import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app_model import import_model, managed_model
from app import discover_model


class ModelImportTests(unittest.TestCase):
    def test_import_keeps_model_in_app_and_preserves_previous(self):
        with tempfile.TemporaryDirectory() as root:
            base=Path(root);project=base/'app';project.mkdir()
            first=base/'old.pt';first.write_bytes(b'old model')
            second=base/'new.pt';second.write_bytes(b'new model')
            self.assertEqual(import_model(first,project),managed_model(project))
            import_model(second,project)
            self.assertEqual(managed_model(project).read_bytes(),b'new model')
            self.assertEqual((project/'models'/'best.previous.pt').read_bytes(),b'old model')
            self.assertEqual(discover_model(project,base/'missing'),managed_model(project))

    def test_failed_copy_leaves_previous_model_intact(self):
        with tempfile.TemporaryDirectory() as root:
            base=Path(root);project=base/'app';project.mkdir()
            source=base/'new.pt';source.write_bytes(b'new model')
            target=managed_model(project);target.parent.mkdir()
            target.write_bytes(b'old model')
            with patch('app_model.shutil.copyfileobj',side_effect=OSError('copy failed')):
                with self.assertRaisesRegex(OSError,'copy failed'):
                    import_model(source,project)
            self.assertEqual(target.read_bytes(),b'old model')
            self.assertEqual(list(target.parent.glob('*.partial')),[])


if __name__=='__main__':unittest.main()

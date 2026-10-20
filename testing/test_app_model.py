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


class ModelCatalogTests(unittest.TestCase):
    def test_catalog_distinguishes_best_weights_and_deduplicates_paths(self):
        from app_model import model_catalog
        with tempfile.TemporaryDirectory() as root:
            project=Path(root)
            first=project/'runs'/'meter_v2_gpu_1280-6'/'weights'/'best.pt'
            second=project/'runs'/'meter_candidate'/'weights'/'best.pt'
            for item in (first,second):item.parent.mkdir(parents=True);item.write_bytes(b'weight')
            catalog=model_catalog(project,[str(first),str(first)])
            self.assertEqual(len(catalog),2)
            self.assertEqual(len({item['label'] for item in catalog}),2)
            self.assertIn('meter_v2_gpu_1280-6 / best.pt',[item['label'] for item in catalog])
            self.assertEqual({item['path'] for item in catalog},{str(first.resolve()),str(second.resolve())})

    def test_catalog_skips_missing_empty_and_nonweight_files(self):
        from app_model import model_catalog
        with tempfile.TemporaryDirectory() as root:
            project=Path(root)
            empty=project/'empty.pt';empty.touch()
            text=project/'notes.txt';text.write_text('not a weight')
            self.assertEqual(model_catalog(project,[str(empty),str(text),str(project/'missing.pt')]),[])

    def test_same_display_names_receive_unique_labels(self):
        from app_model import model_catalog
        with tempfile.TemporaryDirectory() as root:
            project=Path(root);paths=[]
            for name in ('one','two'):
                path=project/name/'shared'/'best.pt';path.parent.mkdir(parents=True);path.write_bytes(b'weight');paths.append(str(path))
            catalog=model_catalog(project,paths)
            self.assertEqual(len({item['label'] for item in catalog}),2)


if __name__=='__main__':unittest.main()

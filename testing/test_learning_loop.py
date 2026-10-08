import csv
import json
import tempfile
import unittest
from pathlib import Path

from testing.learning_loop import build_dataset, load_labels, select_review_frames


class LearningLoopTests(unittest.TestCase):
    def test_review_queue_catches_gaps_and_samples_negatives(self):
        rows=[dict(frame=str(i),status='absent',confidence='',fill='') for i in range(100)]
        rows[40].update(status='detected',confidence='.8',fill='.2')
        rows[41].update(status='predicted')
        rows[42].update(status='detected',confidence='.3',fill='.7')
        chosen={x['frame']:x['reason'] for x in select_review_frames(rows,120)}
        self.assertEqual(chosen[41],'detector gap during a track')
        self.assertIn(90,chosen)

    def test_unreviewed_or_incomplete_validation_cannot_train(self):
        with tempfile.TemporaryDirectory() as root:
            run=Path(root)/'run';run.mkdir()
            with (run/'live_frames.csv').open('w',newline='') as f:
                writer=csv.DictWriter(f,fieldnames=['frame']);writer.writeheader()
                writer.writerows([{'frame':0},{'frame':1}])
            labels=run/'reviewed_labels.json'
            labels.write_text(json.dumps({'frames':[{'frame':0,'meter':True,'box':[1,1,2,3]}]}))
            with self.assertRaisesRegex(ValueError,'reviewed=true'):
                load_labels(run)
            labels.write_text(json.dumps({'frames':[{'frame':0,'meter':True,'box':[1,1,2,3],
                                                     'reviewed':True}]}))
            with self.assertRaisesRegex(ValueError,'every captured frame'):
                load_labels(run,complete=True)

    def test_training_run_cannot_overlap_held_out_test(self):
        with tempfile.TemporaryDirectory() as root:
            train=Path(root)/'train'; val=Path(root)/'val'
            with self.assertRaisesRegex(ValueError,'different captures'):
                build_dataset([train],val,train,Path(root)/'dataset')


if __name__=='__main__': unittest.main()

import unittest
from unittest.mock import patch
from types import SimpleNamespace
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from meter_core import MeterTracker, Detection, Config
from evaluate import evaluate
from check_capture import inspect
from run_video import estimate_fill


class TrackingTests(unittest.TestCase):
    def test_four_frame_gap_keeps_one_live_shot_without_fill_evidence(self):
        tracker=MeterTracker(Config(max_missed=4))
        first=tracker.update(0,[Detection((10,10,20,60),.8)],lambda box:.4)
        gaps=[tracker.update(i,[],lambda box:1.0) for i in range(1,5)]
        resumed=tracker.update(5,[Detection((14,10,24,60),.7)],lambda box:.7)
        self.assertEqual(first['shot_id'],resumed['shot_id'])
        self.assertTrue(all(x['status']=='predicted' and x['fill'] is None for x in gaps))

    def test_short_gap_prediction_is_not_fill_evidence(self):
        t=MeterTracker(Config(max_missed=2))
        d=Detection((10,10,20,50),.9)
        self.assertEqual(t.update(0,[d],lambda _: .2)['shot_id'],1)
        for frame in (1,2):
            r=t.update(frame,[],lambda _: 1)
            self.assertEqual(r['status'],'predicted')
            self.assertIsNone(r['fill'])
        self.assertEqual(t.update(3,[d],lambda _: .4)['shot_id'],1)
        end=t.update(4,[],lambda _: 1)
        t.update(5,[],lambda _: 1)
        ended=t.update(6,[],lambda _: 1)['ended']
        self.assertIsNone(end['ended'])
        self.assertEqual(ended['last_observed_frame'],3)
        self.assertLess(ended['final_observed_fill'],.5)

    def test_new_shot_does_not_inherit_old_fill(self):
        t=MeterTracker(Config(max_missed=0))
        d=Detection((0,0,10,30),.8)
        t.update(0,[d],lambda _: .9)
        self.assertEqual(t.update(1,[],lambda _: 1)['ended']['shot_id'],1)
        r=t.update(2,[d],lambda _: .1)
        self.assertEqual(r['shot_id'],2)
        self.assertAlmostEqual(r['fill'],.1)

    def test_reject_unrelated_candidate(self):
        t=MeterTracker()
        t.update(0,[Detection((0,0,10,30),.8)])
        self.assertEqual(t.update(1,[Detection((500,500,510,530),.99)])['status'],'predicted')

    def test_association_prefers_consistent_box(self):
        t=MeterTracker()
        t.update(0,[Detection((10,10,20,40),.9)])
        r=t.update(1,[Detection((10,10,20,40),.7),Detection((25,10,35,40),.99)])
        self.assertLess(r['box'][0],15)

    def test_size_jump_is_rejected(self):
        t=MeterTracker()
        t.update(0,[Detection((10,10,20,40),.9)])
        self.assertEqual(t.update(1,[Detection((0,0,100,100),.99)])['status'],'predicted')

    def test_sequential_frames_required(self):
        t=MeterTracker()
        t.update(0,[])
        with self.assertRaises(ValueError): t.update(2,[])

    def test_full_frame_is_observed(self):
        t=MeterTracker(Config(fill_alpha=1,full_threshold=.98))
        d=Detection((10,10,20,50),.9)
        t.update(0,[d],lambda _: .5)
        t.update(1,[],lambda _: 1)
        t.update(2,[d],lambda _: .99)
        self.assertEqual(t.summary()['first_full_frame'],2)

    def test_invalid_fill_does_not_become_observation(self):
        t=MeterTracker()
        d=Detection((10,10,20,50),.9)
        t.update(0,[d],lambda _: .3)
        r=t.update(1,[d],lambda _: float('nan'))
        self.assertIsNone(r['fill'])
        self.assertEqual(t.summary()['final_fill_frame'],0)

    def test_stop_evidence_survives_disappearance(self):
        t=MeterTracker(Config(max_missed=1,fill_alpha=1,stop_window=3))
        d=Detection((10,10,20,50),.9)
        for frame,fill in enumerate((.4,.5,.5,.5)):
            t.update(frame,[d],lambda _,v=fill:v)
        self.assertEqual(t.summary()['last_stop_candidate_frame'],3)
        t.update(4,[])
        self.assertEqual(t.update(5,[])['ended']['last_stop_candidate_frame'],3)

    def test_evaluation_excludes_predictions_from_recall(self):
        rows=[dict(frame='0',status='detected',x1='0',y1='0',x2='10',y2='10',fill='.3'),
              dict(frame='1',status='predicted',fill=''),dict(frame='2',status='absent',fill='')]
        truth=[dict(frame=0,meter=True,box=[0,0,10,10],fill=.3,shot_id=1),
               dict(frame=1,meter=True,shot_id=1),dict(frame=2,meter=False)]
        r=evaluate(rows,truth)
        self.assertEqual((r['tp'],r['fn'],r['tn']),(1,1,1))
        self.assertEqual(r['recall'],.5)

    def test_evaluation_rejects_string_negative(self):
        with self.assertRaises(ValueError):
            evaluate([dict(frame='0',status='absent')],[dict(frame=0,meter='false')])

    def test_capture_detects_early_end(self):
        class EarlyCap:
            n=0
            def isOpened(self): return True
            def get(self,field): return {1:3,2:60,3:2,4:2,5:(self.n-1)*1000/60}.get(field,0)
            def read(self):
                self.n+=1
                return (True,SimpleNamespace(shape=(2,2,3))) if self.n<=2 else (False,None)
            def release(self): pass
        cv=SimpleNamespace(VideoCapture=lambda _:EarlyCap(),CAP_PROP_FRAME_COUNT=1,CAP_PROP_FPS=2,
                           CAP_PROP_FRAME_WIDTH=3,CAP_PROP_FRAME_HEIGHT=4,CAP_PROP_POS_MSEC=5)
        with patch.dict(sys.modules,{'cv2':cv}):
            self.assertFalse(inspect('truncated.mp4')['complete'])

    def test_fill_normalizes_to_interior_track(self):
        try:
            import numpy as np
            import cv2
        except ImportError:
            self.skipTest('OpenCV not installed')
        frame=np.zeros((100,20,3),dtype=np.uint8)
        frame[4:90,6:14]=(245,245,245)
        self.assertGreaterEqual(estimate_fill(frame,(0,0,20,100)),.98)
        frame[:50]=0
        partial=estimate_fill(frame,(0,0,20,100))
        self.assertIsNotNone(partial)
        self.assertTrue(.4 < partial < .6)


if __name__=='__main__': unittest.main()

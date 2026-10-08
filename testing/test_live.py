import csv
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import run_live


class FakeTensor:
    def __init__(self, values): self.values = values
    def cpu(self): return self
    def tolist(self): return self.values


class FakeYOLO:
    def __init__(self, path): pass
    def predict(self, frame, **kwargs):
        boxes = SimpleNamespace(xyxy=FakeTensor([[1,1,6,9]]),conf=FakeTensor([.9]))
        return [SimpleNamespace(boxes=boxes)]


class FakeCapture:
    def __init__(self, limit): self.limit=limit; self.count=0; self.released=False
    def isOpened(self): return True
    def set(self,*args): return True
    def get(self,*args): return 60
    def read(self):
        if self.count>=self.limit: return False,None
        self.count+=1
        return True,SimpleNamespace(ndim=3,shape=(10,10,3))
    def release(self): self.released=True


class LiveTests(unittest.TestCase):
    def test_capture_mode_is_requested_before_read_and_actual_mode_reported(self):
        settings=[]
        cap=SimpleNamespace(set=lambda prop,val: settings.append((prop,val)),
                            get=lambda prop:{2:60,3:640,4:480}[prop])
        cv=SimpleNamespace(CAP_PROP_FPS=2,CAP_PROP_FRAME_WIDTH=3,
                           CAP_PROP_FRAME_HEIGHT=4,CAP_PROP_FOURCC=5,
                           VideoWriter_fourcc=lambda *chars:123)
        args=SimpleNamespace(width=1920,height=1080,capture_fps=120,fourcc='MJPG')
        actual=run_live.configure_capture(cv,cap,args)
        self.assertEqual(settings,[(5,123),(3,1920),(4,1080),(2,120)])
        self.assertEqual(actual,dict(width=640,height=480,fps=60))

    def test_capture_probe_measures_reads_without_model(self):
        cap=FakeCapture(5)
        cv=SimpleNamespace(VideoCapture=lambda _:cap,CAP_PROP_BUFFERSIZE=1,CAP_PROP_FPS=2,
                           CAP_PROP_FRAME_WIDTH=3,CAP_PROP_FRAME_HEIGHT=4)
        ticks=iter(i*.02 for i in range(20))
        with tempfile.TemporaryDirectory() as root:
            args=SimpleNamespace(source='0',dshow=False,width=0,height=0,
                                 capture_fps=0,fourcc='',seconds=1,out=Path(root)/'probe.json')
            args.out=Path(root)
            result=run_live.probe_capture(args,cv,clock=lambda:next(ticks))
            self.assertEqual(result['frames'],5)
            self.assertEqual(result['decoded_sizes'],[[10,10]])
            self.assertAlmostEqual(result['median_read_interval_ms'],40)
            self.assertTrue((args.out/'capture_probe.json').exists())
            self.assertTrue(cap.released)

    def test_recording_preserves_elapsed_time_with_slow_inference(self):
        written=[]
        writer=SimpleNamespace(isOpened=lambda:True,write=lambda frame:written.append(frame),release=lambda:None)
        cv=SimpleNamespace(VideoWriter=lambda *args:writer,VideoWriter_fourcc=lambda *args:0)
        recorder=run_live.WallClockRecorder(cv,Path('preview.mp4'),(640,480),60)
        recorder.write('first',0)
        recorder.write('second',.1)
        recorder.close(.2)
        self.assertEqual(recorder.fps,30)
        self.assertEqual(len(written),6)
        self.assertEqual(written[0],'first')
        self.assertEqual(written[3],'second')

    def args(self, out, source, limit=0):
        return SimpleNamespace(source=source,dshow=False,model=Path('fake.pt'),out=out,
            imgsz=1280,conf=.25,max_missed=2,assumed_fps=60,fill_top=.04,fill_bottom=.90,
            seconds=0,max_frames=limit,headless=True,record=False,
            width=0,height=0,capture_fps=0,fourcc='',record_raw=False)

    def test_live_logs_each_captured_frame(self):
        cap=FakeCapture(5)
        cv=SimpleNamespace(VideoCapture=lambda _:cap,CAP_PROP_BUFFERSIZE=1,CAP_PROP_FPS=2,
                           CAP_PROP_FRAME_WIDTH=3,CAP_PROP_FRAME_HEIGHT=4)
        torch=SimpleNamespace(cuda=SimpleNamespace(is_available=lambda:False))
        with tempfile.TemporaryDirectory() as root, patch.object(run_live,'estimate_fill',return_value=.5):
            report=run_live.run(self.args(Path(root),'0',5),cv,torch,FakeYOLO)
            self.assertEqual(report['frames'],5)
            self.assertEqual(report['stop_reason'],'frame_limit')
            self.assertTrue(cap.released)
            with (Path(root)/'live_frames.csv').open(newline='') as file:
                rows=list(csv.DictReader(file))
            self.assertEqual(len(rows),5)
            self.assertTrue(all(r['status']=='detected' for r in rows))

    def test_disconnect_is_reported(self):
        cap=FakeCapture(2)
        cv=SimpleNamespace(VideoCapture=lambda _:cap,CAP_PROP_BUFFERSIZE=1,CAP_PROP_FPS=2,
                           CAP_PROP_FRAME_WIDTH=3,CAP_PROP_FRAME_HEIGHT=4)
        torch=SimpleNamespace(cuda=SimpleNamespace(is_available=lambda:False))
        with tempfile.TemporaryDirectory() as root, patch.object(run_live,'estimate_fill',return_value=None):
            with self.assertRaisesRegex(RuntimeError,'stopped unexpectedly'):
                run_live.run(self.args(Path(root),'0'),cv,torch,FakeYOLO)
            self.assertTrue((Path(root)/'live_run.json').exists())
            self.assertTrue(cap.released)


if __name__=='__main__': unittest.main()

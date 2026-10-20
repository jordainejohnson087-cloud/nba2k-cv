import json
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import cv2
import numpy as np
import run_live
import test_live
from capture_pipe import SharedCapture


class SeparateCaptureTests(unittest.TestCase):
    def test_capture_only_cli_never_imports_torch_or_requires_model(self):
        import builtins
        original=builtins.__import__
        def guarded(name,*args,**kwargs):
            if name in ('torch','ultralytics'):raise AssertionError('Capture must not load AI')
            return original(name,*args,**kwargs)
        with patch.object(sys,'argv',['run_live.py','--capture-only']),patch('builtins.__import__',side_effect=guarded),patch.object(run_live,'run_capture_only') as capture:
            run_live.main();capture.assert_called_once()

    def test_detector_uses_existing_capture_and_releasing_it_keeps_card_running(self):
        class Card:
            def __init__(self):self.count=0;self.released=False
            def isOpened(self):return True
            def set(self,*args):return True
            def get(self,prop):return {cv2.CAP_PROP_FRAME_WIDTH:80,cv2.CAP_PROP_FRAME_HEIGHT:60,cv2.CAP_PROP_FPS:60}.get(prop,0)
            def read(self):
                time.sleep(.015);self.count+=1
                return True,np.full((60,80,3),self.count%256,dtype=np.uint8)
            def release(self):self.released=True
        card=Card();failures=[]
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);session=root/'capture';session.mkdir();ai=root/'ai'
            args=SimpleNamespace(out=session,source='0',dshow=False,backend='auto',width=80,height=60,
                capture_fps=60,fourcc='',stop_file=session/'stop',seconds=0,max_frames=0,
                display_fps=60,render_mode='performance',display_settings=None)
            def capture():
                try:run_live.run_capture_only(args,cv2)
                except Exception as exc:failures.append(exc)
            with patch.object(cv2,'VideoCapture',return_value=card) as open_card:
                thread=threading.Thread(target=capture);thread.start()
                try:
                    end=time.monotonic()+2
                    while not (session/'capture_status.json').exists() and time.monotonic()<end:time.sleep(.01)
                    shared=SharedCapture(session,cv2)
                    self.assertTrue(shared.isOpened())
                    ok,frame=shared.read();self.assertTrue(ok);self.assertEqual(frame.shape,(60,80,3))
                    shared.release();before=card.count;time.sleep(.04)
                    self.assertGreater(card.count,before);self.assertFalse(card.released)
                    ai_args=test_live.LiveTests().args(ai,'0',3);ai_args.capture_session=session
                    torch=SimpleNamespace(cuda=SimpleNamespace(is_available=lambda:False))
                    with patch.object(run_live,'estimate_fill',return_value=.5):
                        report=run_live.run(ai_args,cv2,torch,test_live.FakeYOLO)
                    self.assertEqual(report['frames'],3);self.assertFalse(card.released)
                    self.assertEqual(open_card.call_count,1)
                finally:
                    args.stop_file.touch();thread.join(3)
                self.assertFalse(thread.is_alive());self.assertFalse(failures)
            report=json.loads((session/'capture_run.json').read_text())
            self.assertFalse(report['model_loaded']);self.assertTrue(card.released)

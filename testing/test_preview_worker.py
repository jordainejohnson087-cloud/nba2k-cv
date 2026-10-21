import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
import cv2
import numpy as np
from app import PreviewWorker, inspect_gtuner_python
from capture_pipe import RawMailbox


class PreviewTests(unittest.TestCase):
    def test_latest_frame_resize_and_clean_shutdown(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'frames.bin'
            worker=PreviewWorker(path)  # Header may not exist at worker startup.
            writer=None
            try:
                time.sleep(.02)
                writer=RawMailbox(path,write=True)
                worker.read(0,(32,18))
                frame=np.zeros((72,128,3),np.uint8);frame[:,:,2]=255
                for i in range(4):writer.publish(frame,cv2,i,time.time(),128,72)
                deadline=time.monotonic()+3;sample=None
                while time.monotonic()<deadline:
                    sample=worker.read(0,(32,18))
                    if sample and sample['sequence']==4:break
                    time.sleep(.005)
                self.assertIsNotNone(sample)
                self.assertEqual(sample['sequence'],4)
                self.assertEqual(sample['image'].size,(32,18))
                self.assertEqual(sample['image'].getpixel((0,0)),(255,0,0))
                self.assertIsNone(worker.read(4,(32,18)))
            finally:
                worker.close()
                if writer:writer.close()
            self.assertFalse(worker.thread.is_alive())

    def test_probe_uses_selected_interpreter_and_does_not_claim_gtuner_ready(self):
        completed=type('Result',(),dict(returncode=0,stdout=json.dumps(dict(version='3.11',bits=64)),stderr=''))()
        with patch('app.subprocess.run',return_value=completed) as run:
            result=inspect_gtuner_python('C:/CV/python.exe')
        self.assertEqual(run.call_args.args[0][0],'C:/CV/python.exe')
        self.assertEqual(run.call_args.kwargs['timeout'],12)
        self.assertFalse(result['gtuner_embedded_verified'])

    def test_probe_failure_is_visible(self):
        completed=type('Result',(),dict(returncode=1,stdout='',stderr='DLL load failed'))()
        with patch('app.subprocess.run',return_value=completed):
            with self.assertRaisesRegex(RuntimeError,'DLL load failed'):
                inspect_gtuner_python(sys.executable)

import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
import cv2
import numpy as np
from app import PreviewWorker, inspect_gtuner_python, draw_meter_box
from capture_pipe import RawMailbox


class PreviewTests(unittest.TestCase):
    def test_overlay_scales_source_box_without_modifying_raw_preview(self):
        from PIL import Image
        image=Image.new('RGB',(100,50))
        payload=dict(timestamp=10,captured_at=9.95,status='detected',box=[40,20,80,60],frame_size=[200,100])
        result=draw_meter_box(image,payload,10,now=10.1)
        self.assertEqual(result.getpixel((20,20)),(0,229,255))
        self.assertEqual(result.getpixel((40,20)),(0,229,255))
        self.assertEqual(image.getpixel((20,20)),(0,0,0))
        predicted=draw_meter_box(image,dict(payload,status='predicted'),10,now=10.1)
        self.assertEqual(predicted.getpixel((20,20)),(255,189,89))

    def test_overlay_rejects_stale_misaligned_absent_and_invalid_observations(self):
        from PIL import Image
        image=Image.new('RGB',(100,50))
        base=dict(timestamp=10,captured_at=10,status='detected',box=[40,20,80,60],frame_size=[200,100])
        invalid=[dict(base,timestamp=9),dict(base,timestamp=11),dict(base,captured_at=9),
                 dict(base,status='absent'),dict(base,box=None),dict(base,box=[40,20,float('nan'),60]),
                 dict(base,box=[-1,20,80,60]),dict(base,frame_size=[0,100]),{}]
        for payload in invalid:
            with self.subTest(payload=payload):
                self.assertIs(draw_meter_box(image,payload,10,now=10.1),image)

    def test_worker_draws_recent_detection_on_latest_raw_frame(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);path=root/'frames.bin';report=root/'live_status.json'
            worker=PreviewWorker(path);writer=RawMailbox(path,write=True)
            try:
                worker.read(0,(100,50),report)
                now=time.time()
                report.write_text(json.dumps(dict(timestamp=now,captured_at=now,status='detected',
                    box=[40,20,80,60],frame_size=[200,100])))
                writer.publish(np.zeros((100,200,3),np.uint8),cv2,1,now,200,100)
                deadline=time.monotonic()+2;sample=None
                while time.monotonic()<deadline:
                    sample=worker.read(0,(100,50),report)
                    if sample:break
                    time.sleep(.005)
                self.assertIsNotNone(sample)
                self.assertEqual(sample['image'].getpixel((20,20)),(0,229,255))
            finally:
                worker.close();writer.close()

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

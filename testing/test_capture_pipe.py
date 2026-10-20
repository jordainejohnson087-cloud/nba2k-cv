import json
import struct
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import cv2
import numpy as np
from capture_pipe import LatestCapture,RawMailbox
import run_live
from test_live import FakeCapture,FakeYOLO
import test_live


class CapturePipeTests(unittest.TestCase):
    def test_raw_mailbox_latest_frame_and_incomplete_write_rejection(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'bgr.bin'
            writer=RawMailbox(path,write=True);reader=RawMailbox(path)
            try:
                first=np.zeros((48,64,3),dtype=np.uint8)
                writer.publish(first,cv2,1,time.time())
                self.assertEqual(reader.read()['bgr'],first.tobytes())
                last=np.full((48,64,3),127,dtype=np.uint8)
                for sequence in range(2,10):writer.publish(last,cv2,sequence,time.time())
                sample=reader.read(after=1)
                self.assertEqual(sample['source_frame'],9)
                self.assertEqual(sample['bgr'],last.tobytes())
                self.assertIsNone(reader.read(after=sample['sequence']))
                offset=64+(writer.sequence%2)*(64+writer.CAPACITY)
                writer.map[offset:offset+8]=struct.pack('<Q',0)
                self.assertIsNone(reader.read())
            finally:reader.close();writer.close()

    def test_concurrent_publication_never_returns_torn_pixels(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'frames.bin'
            writer=RawMailbox(path,write=True);reader=RawMailbox(path)
            def produce():
                for frame in range(1,101):
                    writer.publish(np.full((32,32,3),frame%256,dtype=np.uint8),cv2,frame,time.time())
                    time.sleep(.0001)
            thread=threading.Thread(target=produce);thread.start();seen=0;sequence=0
            try:
                while thread.is_alive():
                    sample=reader.read(sequence)
                    if sample:
                        sequence=sample['sequence'];seen+=1
                        self.assertEqual(sample['bgr'],bytes([sample['source_frame']%256])*len(sample['bgr']))
                    time.sleep(.0001)
                self.assertGreater(seen,0)
            finally:thread.join();reader.close();writer.close()

    def test_reader_keeps_draining_while_consumer_is_slow(self):
        entered=threading.Event();release=threading.Event();owner=[]
        class Card:
            def __init__(self):self.count=0;self.released=False
            def read(self):
                owner.append(threading.get_ident());self.count+=1
                if self.count>20:return False,None
                return True,np.zeros((8,8,3),dtype=np.uint8)
            def release(self):self.released=True
        card=Card();pump=LatestCapture(card)
        try:
            pump.thread.join(1)
            sample=pump.next()
            self.assertEqual(sample['source_frame'],19)
            self.assertEqual(pump.frames,20)
            self.assertIsNone(pump.next(19))
            self.assertEqual(len(set(owner)),1)
            self.assertNotEqual(owner[0],threading.get_ident())
        finally:self.assertTrue(pump.close())
        self.assertTrue(card.released)

    def test_live_direct_records_skips_without_inventing_observations(self):
        class Card(FakeCapture):
            def read(self):
                ok,_=super().read()
                return ok,np.zeros((10,10,3),dtype=np.uint8) if ok else None
        card=Card(10)
        cv=SimpleNamespace(VideoCapture=lambda _:card,CAP_PROP_BUFFERSIZE=1,CAP_PROP_FPS=2,
            CAP_PROP_FRAME_WIDTH=3,CAP_PROP_FRAME_HEIGHT=4)
        torch=SimpleNamespace(cuda=SimpleNamespace(is_available=lambda:False))
        with tempfile.TemporaryDirectory() as folder,patch.object(run_live,'estimate_fill',return_value=.5):
            args=test_live.LiveTests().args(Path(folder),'fixture',1);args.live_direct=True
            report=run_live.run(args,cv,torch,FakeYOLO)
            self.assertEqual(report['capture_frames'],10)
            self.assertEqual(report['frames']+report['skipped_inference_frames'],10)
            self.assertFalse(report['realtime_target_met'])
            gaps=[json.loads(line) for line in (Path(folder)/'capture_skips.jsonl').read_text().splitlines()]
            self.assertEqual(sum(gap['count'] for gap in gaps),report['skipped_inference_frames'])

    def test_backend_selection_does_not_silently_choose_another_api(self):
        calls=[]
        cv=SimpleNamespace(VideoCapture=lambda *args:calls.append(args),CAP_DSHOW=700,CAP_MSMF=1400)
        run_live.open_capture(cv,'0',backend='msmf')
        run_live.open_capture(cv,'1',backend='dshow')
        run_live.open_capture(cv,'rtsp://example',backend='msmf')
        self.assertEqual(calls,[(0,1400),(1,700),('rtsp://example',)])

import json
import sys
import tempfile
import threading
import time
import unittest
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import cv2
import numpy as np
import run_live
import test_live
from capture_pipe import SharedCapture, RawMailbox


class SeparateCaptureTests(unittest.TestCase):
    @contextmanager
    def shared_fixture(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            status=dict(state='capturing',timestamp=time.time(),capture_fps=60,
                        reported_capture=dict(width=32,height=24,fps=60))
            (root/'capture_status.json').write_text(json.dumps(status))
            writer=RawMailbox(root/'source_frames.bin',write=True,capacity=32*24*3)
            writer.publish(np.zeros((24,32,3),np.uint8),cv2,0,time.time())
            reader=SharedCapture(root,cv2)
            try:
                self.assertTrue(reader.isOpened(),reader.error)
                yield root,writer,reader,status
            finally:
                reader.release();writer.close()

    def test_locked_status_does_not_stop_fresh_frames(self):
        with self.shared_fixture() as (_,writer,reader,_):
            with patch.object(reader,'_status',side_effect=PermissionError(13,'Windows sharing violation')):
                for index in range(1,7):
                    writer.publish(np.full((24,32,3),index,np.uint8),cv2,index,time.time())
                    reader.status_refresh_at=0
                    ok,frame=reader.read()
                    self.assertTrue(ok,reader.error)
                    self.assertEqual(int(frame[0,0,0]),index)
                    self.assertIsNone(reader.error)
            self.assertEqual(reader.status_read_failures,6)
            self.assertIn('sharing violation',reader.status_error)
            reader.status_refresh_at=0
            writer.publish(np.zeros((24,32,3),np.uint8),cv2,7,time.time())
            self.assertTrue(reader.read()[0]);self.assertIsNone(reader.status_error)

    def test_stale_or_malformed_status_does_not_override_fresh_frame(self):
        with self.shared_fixture() as (root,writer,reader,status):
            status['timestamp']=time.time()-30
            (root/'capture_status.json').write_text(json.dumps(status))
            # A new detector can attach using live frame publication even if optional JSON is stale.
            second=SharedCapture(root,cv2)
            try:self.assertTrue(second.isOpened(),second.error)
            finally:second.release()
            for content in (json.dumps(status),'{', '[]'):
                (root/'capture_status.json').write_text(content)
                reader.status_refresh_at=0
                writer.publish(np.zeros((24,32,3),np.uint8),cv2,1,time.time())
                self.assertTrue(reader.read()[0],reader.error)

    def test_locked_status_cannot_keep_dead_capture_alive(self):
        with self.shared_fixture() as (_,writer,reader,_):
            writer.publish(np.zeros((24,32,3),np.uint8),cv2,1,time.time()-5)
            with patch.object(reader,'_status',side_effect=PermissionError(13,'status locked')):
                start=time.monotonic();ok,frame=reader.read()
            self.assertFalse(ok);self.assertIsNone(frame)
            self.assertLess(time.monotonic()-start,2)
            self.assertIn('No fresh capture frame',reader.error)

    def test_explicit_capture_stop_is_honored_with_fresh_buffer(self):
        with self.shared_fixture() as (root,_,reader,status):
            status.update(state='stopped',error='capture card read failed')
            (root/'capture_status.json').write_text(json.dumps(status))
            self.assertFalse(reader.read()[0])
            self.assertEqual(reader.error,'capture card read failed')

    def test_startup_retries_temporary_status_lock(self):
        with self.shared_fixture() as (root,_,_,status):
            with patch.object(SharedCapture,'_status',side_effect=[PermissionError(13,'locked'),status]):
                reader=SharedCapture(root,cv2)
                try:
                    self.assertTrue(reader.isOpened(),reader.error)
                    self.assertEqual(reader.status_read_failures,1)
                finally:reader.release()

    @unittest.skipUnless(sys.platform=='win32','Requires native Windows exclusive file locks')
    def test_native_exclusive_status_lock_preserves_stream_and_recovers(self):
        import ctypes
        from ctypes import wintypes
        kernel=ctypes.WinDLL('kernel32',use_last_error=True)
        create=kernel.CreateFileW
        create.argtypes=[wintypes.LPCWSTR,wintypes.DWORD,wintypes.DWORD,ctypes.c_void_p,
                         wintypes.DWORD,wintypes.DWORD,wintypes.HANDLE]
        create.restype=wintypes.HANDLE
        close=kernel.CloseHandle;close.argtypes=[wintypes.HANDLE];close.restype=wintypes.BOOL
        with self.shared_fixture() as (root,writer,reader,_):
            handle=create(str(root/'capture_status.json'),0x80000000,0,None,3,0x80,None)
            if handle==ctypes.c_void_p(-1).value:raise ctypes.WinError(ctypes.get_last_error())
            try:
                with self.assertRaises(PermissionError):reader._status()
                for index in range(1,6):
                    writer.publish(np.full((24,32,3),index,np.uint8),cv2,index,time.time())
                    reader.status_refresh_at=0
                    self.assertTrue(reader.read()[0],reader.error)
                self.assertEqual(reader.status_read_failures,5)
                self.assertIsNone(reader.error)
            finally:close(handle)
            writer.publish(np.zeros((24,32,3),np.uint8),cv2,6,time.time())
            reader.status_refresh_at=0
            self.assertTrue(reader.read()[0]);self.assertIsNone(reader.status_error)

    def test_capture_only_cli_never_imports_torch_or_requires_model(self):
        import builtins
        original=builtins.__import__
        def guarded(name,*args,**kwargs):
            if name in ('torch','ultralytics'):raise AssertionError('Capture must not load AI')
            return original(name,*args,**kwargs)
        with patch.object(sys,'argv',['run_live.py','--capture-only']),patch('builtins.__import__',side_effect=guarded),patch.object(run_live,'run_capture_only') as capture:
            run_live.main();capture.assert_called_once()

    def test_bridge_host_cli_never_imports_cv_or_gpu(self):
        import builtins
        original=builtins.__import__
        def guarded(name,*args,**kwargs):
            if name in ('torch','ultralytics','cv2'):raise AssertionError('Bridge must remain model-free')
            return original(name,*args,**kwargs)
        argv=['run_live.py','--bridge-host','private.json','--bridge-lease','owner.lease','--bridge-port','26740']
        with patch.object(sys,'argv',argv),patch('builtins.__import__',side_effect=guarded),patch.object(run_live,'run_bridge_host') as host:
            run_live.main();host.assert_called_once()

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

    def test_busy_card_open_is_retried_then_reported_without_loading_ai(self):
        from unittest.mock import Mock
        with tempfile.TemporaryDirectory() as folder:
            out=Path(folder)
            args=SimpleNamespace(out=out,source='0',dshow=False,backend='dshow',stop_file=out/'stop')
            card=Mock();card.isOpened.return_value=False
            with patch.object(run_live,'open_capture',return_value=card) as attempt,patch.object(run_live.time,'monotonic',side_effect=[0,0,4]),patch.object(run_live.time,'sleep'):
                with self.assertRaisesRegex(RuntimeError,'Null Video Input'):
                    run_live.run_capture_only(args,cv2)
            self.assertEqual(attempt.call_count,2)
            self.assertEqual(card.release.call_count,2)
            report=json.loads((out/'capture_status.json').read_text())
            self.assertEqual(report['stop_reason'],'capture_open_failed')
            self.assertFalse(report['model_loaded'])

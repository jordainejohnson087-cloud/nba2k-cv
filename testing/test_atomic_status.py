import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import cv2
import numpy as np
import live_link
import run_live
from capture_pipe import RawMailbox


class AtomicStatusTests(unittest.TestCase):
    def test_denied_replace_preserves_old_file_and_cleans_staging(self):
        with tempfile.TemporaryDirectory() as folder:
            target=Path(folder)/'status.json';target.write_bytes(b'old')
            with patch('live_link.os.replace',side_effect=PermissionError('sharing violation')):
                with self.assertRaises(PermissionError):live_link.atomic_write(target,b'new',retries=0)
            self.assertEqual(target.read_bytes(),b'old')
            self.assertEqual(list(Path(folder).glob('*.tmp')),[])

    @unittest.skipUnless(sys.platform=='win32','Requires native Windows file-sharing semantics')
    def test_native_windows_reader_lock_then_recovery(self):
        import ctypes
        from ctypes import wintypes
        kernel=ctypes.WinDLL('kernel32',use_last_error=True)
        create=kernel.CreateFileW
        create.argtypes=[wintypes.LPCWSTR,wintypes.DWORD,wintypes.DWORD,ctypes.c_void_p,
                         wintypes.DWORD,wintypes.DWORD,wintypes.HANDLE]
        create.restype=wintypes.HANDLE
        close=kernel.CloseHandle;close.argtypes=[wintypes.HANDLE];close.restype=wintypes.BOOL
        with tempfile.TemporaryDirectory() as folder:
            target=Path(folder)/'status.json';target.write_bytes(b'old')
            # Allow read/write but withhold FILE_SHARE_DELETE, matching a racing reader.
            handle=create(str(target),0x80000000,0x1|0x2,None,3,0x80,None)
            if handle==ctypes.c_void_p(-1).value:raise ctypes.WinError(ctypes.get_last_error())
            try:
                with self.assertRaises(PermissionError):live_link.atomic_write(target,b'new',retries=0)
                self.assertEqual(target.read_bytes(),b'old')
                self.assertEqual(list(Path(folder).glob('*.tmp')),[])
            finally:close(handle)
            live_link.atomic_write(target,b'new',retries=0)
            self.assertEqual(target.read_bytes(),b'new')

    def test_transient_replace_retries_without_reusing_another_writers_temp(self):
        with tempfile.TemporaryDirectory() as folder:
            target=Path(folder)/'status.json';replace=live_link.os.replace
            attempted=[]
            def locked_once(source,destination):
                attempted.append(source)
                if len(attempted)==1:raise PermissionError('locked')
                return replace(source,destination)
            with patch('live_link.os.replace',side_effect=locked_once),patch('live_link.time.sleep'):
                live_link.atomic_write(target,b'complete')
            self.assertEqual(target.read_bytes(),b'complete')
            live_link.atomic_write(target,b'next')
            self.assertEqual(list(Path(folder).glob('*.tmp')),[])

    def test_status_locks_do_not_stop_capture_or_mask_the_final_report(self):
        class Card:
            released=False
            def isOpened(self):return True
            def set(self,*args):return True
            def get(self,prop):return {cv2.CAP_PROP_FRAME_WIDTH:80,cv2.CAP_PROP_FRAME_HEIGHT:60,cv2.CAP_PROP_FPS:60}.get(prop,0)
            def read(self):return True,np.zeros((60,80,3),np.uint8)
            def release(self):self.released=True
        with tempfile.TemporaryDirectory() as folder:
            out=Path(folder);card=Card()
            args=SimpleNamespace(out=out,source='0',dshow=False,backend='auto',width=80,height=60,
                capture_fps=60,fourcc='',stop_file=None,seconds=0,max_frames=8,
                display_fps=60,render_mode='performance',display_settings=None)
            original=live_link.os.replace
            def deny_status(source,destination):
                if Path(destination).name=='capture_status.json':raise PermissionError('WinError 5 sharing violation')
                return original(source,destination)
            with patch.object(run_live,'open_capture',return_value=card),patch('live_link.os.replace',side_effect=deny_status):
                report=run_live.run_capture_only(args,cv2)
            self.assertEqual(report['frames'],8)
            self.assertEqual(report['stop_reason'],'frame_limit')
            self.assertIsNone(report['error'])
            self.assertGreater(report['status_write_failures'],0)
            self.assertTrue(card.released)
            self.assertEqual(json.loads((out/'capture_run.json').read_text())['frames'],8)
            self.assertEqual(list(out.glob('*.tmp')),[])
            mailbox=RawMailbox(out/'source_frames.bin')
            try:
                with patch.object(mailbox,'read',side_effect=AssertionError('Watchdog must not read pixels')):
                    self.assertEqual(mailbox.progress()['frames'],8)
            finally:mailbox.close()
            # A healthy source must prevent the UI watchdog firing even with no JSON.
            from app import Desktop
            import time
            ui=SimpleNamespace(capture_active=True,capture_out=out,live_active=False,
                capture_process=SimpleNamespace(poll=lambda:None),capture_progress_frames=0,
                capture_progress_at=time.monotonic()-30,capture_watchdog_fired=False,
                _stop_capture=lambda: self.fail('Live source was mistaken for a stalled driver'))
            try:
                Desktop._capture_tick(ui)
                self.assertEqual(ui.capture_progress_frames,8)
                self.assertLess(time.monotonic()-ui.capture_progress_at,1)
            finally:
                if getattr(ui,'capture_monitor',None):ui.capture_monitor.close()

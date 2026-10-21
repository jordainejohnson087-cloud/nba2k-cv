"""Capture should survive a rejected scheduling hint without hiding launch errors."""
import subprocess
import unittest
from unittest.mock import patch, sentinel
from app import capture_worker


class CapturePriorityTests(unittest.TestCase):
    def test_windows_requests_bounded_priority(self):
        with patch('app.sys.platform','win32'), patch.object(subprocess,'ABOVE_NORMAL_PRIORITY_CLASS',32768,create=True), patch('app.subprocess.Popen',return_value=sentinel.process) as start:
            process,report=capture_worker(['python','capture.py'],'.')
        self.assertIs(process,sentinel.process)
        self.assertEqual(start.call_args.kwargs['creationflags'],32768)
        self.assertFalse(report['fallback'])

    def test_access_denial_retries_normal_once(self):
        with patch('app.sys.platform','win32'), patch.object(subprocess,'ABOVE_NORMAL_PRIORITY_CLASS',32768,create=True), patch('app.subprocess.Popen',side_effect=[PermissionError('denied'),sentinel.process]) as start:
            process,report=capture_worker(['python','capture.py'],'.')
        self.assertIs(process,sentinel.process)
        self.assertEqual(start.call_count,2)
        self.assertEqual(start.call_args.kwargs['creationflags'],0)
        self.assertTrue(report['fallback'])

    def test_missing_runtime_is_not_retried(self):
        with patch('app.subprocess.Popen',side_effect=FileNotFoundError('missing')) as start:
            with self.assertRaises(FileNotFoundError):capture_worker(['missing'],'.')
        self.assertEqual(start.call_count,1)

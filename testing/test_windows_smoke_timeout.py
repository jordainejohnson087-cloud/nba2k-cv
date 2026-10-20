"""Exercise the GUI supervisor with real child processes on every platform."""
import subprocess
import sys
import unittest
from unittest.mock import patch
from testing.windows_smoke import run_bounded


class SmokeDeadlineTests(unittest.TestCase):
    def test_worker_exit_code_is_preserved(self):
        self.assertEqual(run_bounded([sys.executable,'-c','raise SystemExit(7)']),7)

    def test_stuck_worker_is_terminated_and_reaped(self):
        launch=subprocess.Popen
        children=[]
        def record(*args,**kwargs):
            child=launch(*args,**kwargs);children.append(child);return child
        with patch('testing.windows_smoke.subprocess.Popen',side_effect=record):
            result=run_bounded([sys.executable,'-c','import time; time.sleep(60)'],timeout=.1)
        self.assertEqual(result,124)
        self.assertIsNotNone(children[0].poll())

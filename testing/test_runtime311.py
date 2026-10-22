import json
import os
import sys
import tempfile
from pathlib import Path
import subprocess
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from app import prepare_gtuner_runtime, bootstrap_windows_controls


class DedicatedRuntimeTests(unittest.TestCase):
    @unittest.skipUnless(os.name=='nt','Windows PowerShell launcher integration')
    def test_launcher_runs_managed_python_and_propagates_failure(self):
        source=(Path(__file__).resolve().parents[1]/'Launch NBA2K CV.bat').read_text()
        tail=source[source.index('echo Starting NBA2K CV:'):]
        with tempfile.TemporaryDirectory(prefix="NBA2K backend (test) ") as folder:
            root=Path(folder)
            subprocess.run([sys.executable,'-m','venv','--without-pip',str(root/'.venv311')],check=True,timeout=30)
            launcher=root/'Launch NBA2K CV.bat'
            launcher.write_text('@echo off\ncd /d "%~dp0"\n'+tail)
            for code in (0,7):
                (root/'app.py').write_text("import sys\nprint('MANAGED_BACKEND', sys.prefix, flush=True)\nsys.exit("+str(code)+")\n")
                result=subprocess.run(['cmd.exe','/c',str(launcher)],input='\n',text=True,
                    capture_output=True,timeout=20)
                self.assertIn('MANAGED_BACKEND',result.stdout)
                self.assertIn('.venv311',result.stdout)
                if code:self.assertNotEqual(result.returncode,0)
                else:self.assertEqual(result.returncode,0,result.stderr)

    def test_gtuner_setup_targets_311_base_not_cuda_venv_and_does_not_install_torch(self):
        probe=SimpleNamespace(returncode=0,stdout=json.dumps('C:/Python311/python.exe'),stderr='')
        install=SimpleNamespace(returncode=0,stdout='',stderr='')
        with patch('app.subprocess.run',side_effect=[probe,install]) as run, patch('app.inspect_gtuner_python',return_value=dict(executable='C:/Python311/python.exe')):
            result=prepare_gtuner_runtime('C:/app/.venv311/Scripts/python.exe')
        self.assertEqual(run.call_args_list[1].args[0][0],'C:/Python311/python.exe')
        self.assertIn('pywin32>=306,<400',run.call_args_list[1].args[0])
        self.assertNotIn('torch',run.call_args_list[1].args[0])
        self.assertEqual(result['app_environment'],'C:/app/.venv311/Scripts/python.exe')

    def test_wrong_runtime_blocks_package_mutation(self):
        probe=SimpleNamespace(returncode=1,stdout='',stderr='Python 3.11 x64 required')
        with patch('app.subprocess.run',return_value=probe) as run:
            with self.assertRaisesRegex(RuntimeError,'3.11 x64'):prepare_gtuner_runtime('wrong.exe')
        self.assertEqual(run.call_count,1)

    def test_old_updater_restart_goes_through_managed_launcher(self):
        with patch('app.sys.platform','win32'),patch('app.sys.version_info',(3,14,0)),patch('app.subprocess.Popen') as launch:
            self.assertTrue(bootstrap_windows_controls())
        self.assertEqual(launch.call_count,1)
        self.assertTrue(launch.call_args.args[0][-1].endswith('Launch NBA2K CV.bat'))

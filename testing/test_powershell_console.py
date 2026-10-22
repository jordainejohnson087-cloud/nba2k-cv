"""Exercise the proven console-test sender and the integrated GCV receiver."""
import base64
import queue
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock,patch
from app import Desktop
from bridge_service import BridgeProcess
import test_green as fixtures
gcv=fixtures.gcv
from test_session_flow import Variable

ROOT=Path(__file__).resolve().parents[1]


class ManualPathTests(unittest.TestCase):
    def test_original_sender_reaches_integrated_worker_with_neutral_between_taps(self):
        with patch.object(gcv,'PORT',0),patch.object(gcv,'TEST_PORT',0):worker=gcv.GCVWorker(1,1)
        try:
            port=worker.test_socket.getsockname()[1]
            gtuner=SimpleNamespace(gpc_string=lambda:'T2CV:1:0:0:2:3:0:0')
            with patch.dict(sys.modules,gtuner=gtuner):
                for button,command in (('A',1),('X',2)):
                    worker.last_test=-1
                    result=subprocess.run([sys.executable,str(ROOT/'testing/console_button_check.py'),
                        '--tap',button,'--port',str(port)],capture_output=True,text=True,timeout=5)
                    self.assertEqual(result.returncode,0,result.stderr)
                    self.assertEqual(worker.process(None)[1],bytearray((84,49,command)))
                    self.assertEqual(worker.process(None)[1],bytearray((84,49,0)))
                self.assertEqual(worker.device_test_count,0)
                self.assertEqual(worker.device_test_button,0)
        finally:worker.__del__()

    def test_stale_invalid_armed_and_rate_limited_commands_cannot_tap(self):
        with patch.object(gcv,'PORT',0),patch.object(gcv,'TEST_PORT',0):worker=gcv.GCVWorker(1,1)
        with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as sender:
            target=worker.test_socket.getsockname()
            try:
                with patch.dict(sys.modules,gtuner=SimpleNamespace(gpc_string=lambda:None)):
                    for payload in ('bad',f'T2TEST:A:{time.time()-2}',f'T2TEST:B:{time.time()}', 'T2TEST:X:nan'):
                        sender.sendto(payload.encode(),target)
                        self.assertNotEqual(worker.process(None)[1][:2],bytes((84,49)))
                    worker.packet=fixtures.ProtocolTests().packet();worker.last_packet=time.monotonic()
                    sender.sendto(f'T2TEST:A:{time.time()}'.encode(),target)
                    self.assertNotEqual(worker.process(None)[1][:2],bytes((84,49)))
                    worker.packet['trigger']['armed']=False
                    worker.last_test=time.monotonic()
                    sender.sendto(f'T2TEST:A:{time.time()}'.encode(),target)
                    self.assertNotEqual(worker.process(None)[1][:2],bytes((84,49)))
            finally:worker.__del__()

    def test_powershell_command_quotes_paths_and_arguments_as_literals(self):
        with patch('bridge_service.shutil.which',return_value='powershell.exe'):
            command=BridgeProcess.powershell_command('python.exe',["sender's $(anything).py",'--tap','A'])
        self.assertEqual(command[1:4],['-NoProfile','-NonInteractive','-EncodedCommand'])
        source=base64.b64decode(command[-1]).decode('utf-16-le')
        self.assertEqual(source,"& 'python.exe' 'sender''s $(anything).py' '--tap' 'A'; exit $LASTEXITCODE")

    @unittest.skipUnless(sys.platform=='win32','Native Windows PowerShell check')
    def test_windows_powershell_runs_real_python_sender_with_quoted_path(self):
        with tempfile.TemporaryDirectory() as folder,socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as server:
            server.bind(('127.0.0.1',0));server.settimeout(2)
            script=Path(folder)/"sender's $(anything).py"
            script.write_text((ROOT/'testing/console_button_check.py').read_text())
            command=BridgeProcess.powershell_command(sys.executable,[str(script),'--tap','A','--port',str(server.getsockname()[1])])
            result=subprocess.run(command,capture_output=True,text=True,timeout=10)
            self.assertEqual(result.returncode,0,result.stderr)
            data,_=server.recvfrom(128)
            self.assertTrue(data.startswith(b'T2TEST:A:'))


class ConsoleUiTests(unittest.TestCase):
    def ui(self,folder):
        script=folder/'console_button_check.py';script.write_text('test fixture')
        status=dict(ready=True,test_available=True,device_test_count=0,output_allowed=False)
        service=Mock();service.status.return_value=status
        return SimpleNamespace(bridge_service=service,console_test_status=Variable(''),
            _disarm_shots=Mock(),_disconnect_bridge=Mock(),_export_bridge=Mock(return_value=folder),messages=queue.Queue())

    def test_app_disarms_and_launches_one_powershell_request(self):
        with tempfile.TemporaryDirectory() as folder:
            ui=self.ui(Path(folder))
            def run(*args,**kwargs):
                ui.bridge_service.status.return_value=dict(ready=True,device_test_count=1,device_test_button=1)
                return SimpleNamespace(returncode=0,stdout='Sent one A request.',stderr='')
            with patch('app.sys.platform','win32'),patch.object(BridgeProcess,'powershell_command',return_value=['powershell.exe','test']),\
                 patch('app.threading.Thread') as thread,patch('app.subprocess.run',side_effect=run) as launch:
                thread.return_value.start.side_effect=lambda:thread.call_args.kwargs['target']()
                Desktop._console_tap(ui,'A')
                ui._disarm_shots.assert_called_once()
                launch.assert_called_once()
                kind,(result,log)=ui.messages.get_nowait()
                self.assertEqual(kind,'console_test_done');self.assertIn('Titan reports issuing A',result)
                self.assertIn('Xbox receipt is unverified',result)

    def test_uncertain_sender_timeout_is_not_retried(self):
        with tempfile.TemporaryDirectory() as folder:
            ui=self.ui(Path(folder))
            with patch('app.sys.platform','win32'),patch.object(BridgeProcess,'powershell_command',return_value=['powershell.exe','test']),\
                 patch('app.threading.Thread') as thread,patch('app.subprocess.run',side_effect=subprocess.TimeoutExpired('PowerShell',8)) as launch:
                thread.return_value.start.side_effect=lambda:thread.call_args.kwargs['target']()
                Desktop._console_tap(ui,'X')
                launch.assert_called_once()
                self.assertIn('timed out',ui.messages.get_nowait()[1][0])

    def test_original_test_sends_without_bridge_readiness_or_a_heartbeat(self):
        for service_present in (False,True):
            with self.subTest(service=service_present),tempfile.TemporaryDirectory() as folder:
                ui=self.ui(Path(folder))
                if service_present:ui.bridge_service.status.return_value=dict(ready=False,stage='worker not receiving')
                else:ui.bridge_service=None
                with patch('app.sys.platform','win32'),patch.object(BridgeProcess,'powershell_command',return_value=['powershell.exe','test']),\
                     patch('app.threading.Thread') as thread,patch('app.subprocess.run',return_value=SimpleNamespace(returncode=0,stdout='Sent one A request.',stderr='')) as launch:
                    thread.return_value.start.side_effect=lambda:thread.call_args.kwargs['target']()
                    Desktop._console_tap(ui,'A')
                    launch.assert_called_once();ui._disarm_shots.assert_called_once()
                    ui._disconnect_bridge.assert_called_once()
                    result=ui.messages.get_nowait()[1][0]
                    self.assertIn('Original manual A request sent once',result)
                    self.assertIn('Sending does not confirm delivery',result)

    @unittest.skipUnless(sys.platform=='win32','Native Windows original manual path')
    def test_powershell_sender_reaches_original_standalone_worker_without_app_api(self):
        import importlib.util
        spec=importlib.util.spec_from_file_location('original_console_worker',ROOT/'testing/console_button_check.py')
        original=importlib.util.module_from_spec(spec);spec.loader.exec_module(original)
        with patch.object(original,'PORT',0):worker=original.GCVWorker(1,1)
        try:
            command=BridgeProcess.powershell_command(sys.executable,[str(ROOT/'testing/console_button_check.py'),
                '--tap','A','--port',str(worker.socket.getsockname()[1])])
            result=subprocess.run(command,capture_output=True,text=True,timeout=10)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertEqual(worker.process(None)[1],bytearray((84,49,1)))
            self.assertEqual(worker.process(None)[1],bytearray((84,49,0)))
        finally:worker.__del__()

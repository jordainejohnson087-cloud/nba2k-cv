import json
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace,MethodType
from unittest.mock import Mock,patch
from app import Desktop
from meter_green import green_config


class Variable:
    def __init__(self,value):self.value=value
    def get(self):return self.value
    def set(self,value):self.value=value


class SessionTests(unittest.TestCase):
    def ui(self,out):
        ui=SimpleNamespace(session_requested=True,session_deadline=time.monotonic()+30,session_engine_started=False,
            capture_active=True,live_active=True,active_out=out,preparing_bridge=False,
            trigger_armed=Variable(False),bridge_enabled=Variable(True),status=Variable(''),session_status=Variable(''),
            bridge_service=Mock(),_save=Mock(),_capture_ready=Mock(return_value=True),after=Mock(),_live=Mock())
        ui.bridge_service.status.return_value=dict(ready=True,device_gate=272)
        for name in ('_session_tick','_detector_status','_arming_blocker','_apply_green','_arm_shots','_disarm_shots','_shot_mode_changed'):
            setattr(ui,name,MethodType(getattr(Desktop,name),ui))
        ui._green_values=lambda:green_config(dict(armed=ui.trigger_armed.get()))
        (out/'live_status.json').write_text(json.dumps(dict(timestamp=time.time(),status='absent',device='0')))
        return ui

    def test_session_becomes_ready_but_never_arms_automatically(self):
        with tempfile.TemporaryDirectory() as folder:
            ui=self.ui(Path(folder));ui._session_tick()
            self.assertFalse(ui.session_requested);self.assertFalse(ui.trigger_armed.get())
            self.assertIn('Session ready',ui.session_status.get())

    def test_arm_uses_live_engine_and_bridge_then_disarm_keeps_preview_running(self):
        with tempfile.TemporaryDirectory() as folder:
            out=Path(folder);ui=self.ui(out);ui._arm_shots()
            self.assertTrue(ui.trigger_armed.get());ui.bridge_service.allow_output.assert_called_with(True)
            self.assertTrue(json.loads((out/'green_settings.json').read_text())['armed'])
            ui._disarm_shots()
            self.assertFalse(ui.trigger_armed.get());ui.bridge_service.allow_output.assert_called_with(False)
            self.assertFalse(json.loads((out/'green_settings.json').read_text())['armed'])
            self.assertTrue(ui.capture_active);self.assertTrue(ui.live_active)

    def test_arming_rejects_stale_engine_missing_capture_wrong_bridge_and_preparation(self):
        for condition in ('stale','capture','bridge','preparing','console_test','old_gpc'):
            with self.subTest(condition=condition),tempfile.TemporaryDirectory() as folder:
                out=Path(folder);ui=self.ui(out)
                if condition=='stale':(out/'live_status.json').write_text(json.dumps(dict(timestamp=time.time()-5)))
                if condition=='capture':ui._capture_ready.return_value=False
                if condition=='bridge':ui.bridge_service.status.return_value=dict(ready=False)
                if condition=='preparing':ui.preparing_bridge=True
                if condition=='console_test':ui.console_test_busy=True
                if condition=='old_gpc':ui.bridge_service.status.return_value=dict(ready=True)
                ui._arm_shots()
                self.assertFalse(ui.trigger_armed.get())
                ui.bridge_service.allow_output.assert_called_with(False)

    def test_selecting_a_different_mechanic_disarms_and_preserves_preview(self):
        with tempfile.TemporaryDirectory() as folder:
            out=Path(folder);ui=self.ui(out);ui.trigger_armed.set(True)
            ui._shot_mode_changed()
            self.assertFalse(ui.trigger_armed.get())
            ui.bridge_service.allow_output.assert_called_with(False)
            self.assertTrue(ui.capture_active);self.assertTrue(ui.live_active)
            self.assertIn('release X',ui.session_status.get())

    def test_settings_write_failure_disarms_host(self):
        with tempfile.TemporaryDirectory() as folder:
            ui=self.ui(Path(folder))
            with patch('run_live.atomic_write',side_effect=PermissionError('locked')):ui._arm_shots()
            self.assertFalse(ui.trigger_armed.get());ui.bridge_service.allow_output.assert_called_with(False)
            self.assertIn('could not be applied',ui.status.get())

    def test_detector_snapshot_lock_uses_only_recent_same_run_cache(self):
        with tempfile.TemporaryDirectory() as folder:
            out=Path(folder);ui=self.ui(out)
            self.assertIsNotNone(ui._detector_status())
            with patch.object(Path,'read_text',side_effect=PermissionError('locked')):
                self.assertIsNotNone(ui._detector_status())
                with patch('app.time.time',return_value=time.time()+1):self.assertIsNone(ui._detector_status())
                ui.active_out=out/'new_run';self.assertIsNone(ui._detector_status())

    def test_startup_timeout_and_cancel_do_not_restart_engine(self):
        with tempfile.TemporaryDirectory() as folder:
            ui=self.ui(Path(folder));ui.session_deadline=0;ui._session_tick()
            self.assertFalse(ui.session_requested);ui._live.assert_not_called()
            ui.after.assert_not_called()
            ui.session_requested=True;ui.session_deadline=time.monotonic()+30
            ui.live_active=False;ui.session_engine_started=True;ui._session_tick()
            self.assertFalse(ui.session_requested);ui._live.assert_not_called()
            self.assertIn('Detector stopped',ui.session_status.get())

    def test_cancelled_session_callback_does_nothing(self):
        with tempfile.TemporaryDirectory() as folder:
            ui=self.ui(Path(folder));ui.session_requested=False;ui._session_tick()
            ui._live.assert_not_called();ui.after.assert_not_called()

    def test_start_session_coordinates_workers_then_waits_for_preview(self):
        with tempfile.TemporaryDirectory() as folder:
            out=Path(folder);ui=self.ui(out);model=out/'best.pt';model.write_bytes(b'test')
            ui.session_requested=False;ui.capture_active=False;ui.live_active=False
            ui.model=Variable(str(model));ui.compute=Variable('cpu')
            ui._busy=Mock(return_value=False);ui._settings=Mock(return_value=(1920,1080,60,1280,0))
            ui._python=Mock(return_value='python.exe');ui._connect_bridge=Mock();ui._start_capture=Mock()
            Desktop._start_session(ui)
            ui._connect_bridge.assert_called_once_with();ui._start_capture.assert_called_once_with()
            ui._live.assert_not_called();self.assertEqual(ui.compute.get(),'auto')
            self.assertFalse(ui.trigger_armed.get())
            ui.capture_active=True
            ui._live.side_effect=lambda:setattr(ui,'live_active',True)
            ui._session_tick();ui._live.assert_called_once_with()
            self.assertFalse(ui.session_requested);self.assertIn('Session ready',ui.session_status.get())



class AiShotLaunchTests(unittest.TestCase):
    def ui(self,folder):
        import queue
        ui=SessionTests().ui(folder)
        ui.messages=queue.Queue();ui._export_bridge=Mock(return_value=folder)
        ui.bridge_service.status.return_value.update(device_gate=3349,device_test_count=0,output_allowed=True)
        return ui

    def test_ai_shot_launch_arms_then_sends_one_powershell_start_without_timer(self):
        from bridge_service import BridgeProcess
        with tempfile.TemporaryDirectory() as folder:
            ui=self.ui(Path(folder))
            def run(*args,**kwargs):
                ui.bridge_service.status.return_value.update(device_test_count=1,device_test_button=5)
                return SimpleNamespace(returncode=0,stdout='AI hold sent',stderr='')
            with patch('app.sys.platform','win32'),patch.object(BridgeProcess,'powershell_command',return_value=['powershell.exe']) as command,\
                 patch('app.threading.Thread') as thread,patch('app.subprocess.run',side_effect=run) as sender:
                thread.return_value.start.side_effect=lambda:thread.call_args.kwargs['target']()
                Desktop._start_ai_x_shot(ui)
                self.assertTrue(ui.trigger_armed.get());sender.assert_called_once()
                self.assertEqual(command.call_args.args[1][-1],'--ai-x-shot')
                self.assertIn('Titan accepted',ui.messages.get_nowait()[1][0])

    def test_ai_shot_cannot_start_with_old_script_existing_meter_or_physical_hold(self):
        for condition in ('old','meter','hold','stale'):
            with self.subTest(condition=condition),tempfile.TemporaryDirectory() as folder:
                ui=self.ui(Path(folder))
                if condition=='old':ui.bridge_service.status.return_value['device_gate']=272
                if condition=='hold':ui.bridge_service.status.return_value['device_gate']|=512
                if condition=='meter':(Path(folder)/'live_status.json').write_text(json.dumps(dict(timestamp=time.time(),status='detected')))
                if condition=='stale':(Path(folder)/'live_status.json').write_text(json.dumps(dict(timestamp=time.time()-5,status='absent')))
                with patch('app.sys.platform','win32'),patch('app.subprocess.run') as sender:
                    Desktop._start_ai_x_shot(ui);sender.assert_not_called()
                    self.assertFalse(ui.trigger_armed.get())

    def test_disarm_before_background_launch_prevents_the_hold(self):
        from bridge_service import BridgeProcess
        with tempfile.TemporaryDirectory() as folder:
            ui=self.ui(Path(folder))
            with patch('app.sys.platform','win32'),patch.object(BridgeProcess,'powershell_command',return_value=['powershell.exe']),\
                 patch('app.threading.Thread') as thread,patch('app.subprocess.run') as sender:
                Desktop._start_ai_x_shot(ui)
                ui.bridge_service.status.return_value['output_allowed']=False
                thread.call_args.kwargs['target']()
                sender.assert_not_called()
                self.assertIn('cancelled',ui.messages.get_nowait()[1][0])

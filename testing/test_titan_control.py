import unittest
from unittest.mock import Mock,patch
from titan_control import GtunerController,parse_slot_rows,slot_number,choose_meter_slot


class TitanControlTests(unittest.TestCase):
    def test_reads_only_explicit_unique_slot_labels(self):
        names=parse_slot_rows([['1','NBA2K CV Meter Bridge','2.0'],['Slot 2: Practice'],
                              ['3','Run'],['4','First'],['4','Conflicting'],['9','Empty']])
        self.assertEqual(names[1],'NBA2K CV Meter Bridge')
        self.assertEqual(names[2],'Practice')
        self.assertNotIn(3,names)
        self.assertIsNone(names[4])
        self.assertNotIn(8,names)

    def test_selection_checks_target_focus_and_uses_documented_shortcut(self):
        window=Mock()
        controller=GtunerController('Gtuner.exe',window=window)
        with patch.object(controller,'_is_foreground',return_value=True):
            self.assertEqual(controller.select_slot('9'),9)
        window.type_keys.assert_called_once_with('^9',set_foreground=False,pause=.05)
        window.reset_mock()
        with patch.object(controller,'_is_foreground',return_value=False):
            with self.assertRaisesRegex(RuntimeError,'focus'):controller.select_slot(1)
        window.type_keys.assert_not_called()
        for value in (0,10,True,'1;anything','01'):
            with self.assertRaises(ValueError):slot_number(value)

    def test_auto_selector_requires_one_identified_meter_script(self):
        self.assertEqual(choose_meter_slot({1:'Other script',4:'NBA2K CV Meter Bridge'}),4)
        for names in ({},{1:None},{1:'NBA2K CV Meter Bridge',2:'titan_meter.gpc'}):
            with self.assertRaises(RuntimeError):choose_meter_slot(names)

    def test_missing_accessibility_labels_are_not_invented(self):
        window=Mock();window.descendants.return_value=[]
        self.assertEqual(GtunerController('Gtuner.exe',window).read_slots(),dict.fromkeys(range(1,10)))


class ReconnectIntentTests(unittest.TestCase):
    def test_stop_cancels_pending_reconnect_and_capture_start(self):
        from app import Desktop
        from types import SimpleNamespace
        app=SimpleNamespace(pending_reconnect=True,resume_after_reconnect=True,
                            capture_launch_requested=True,process=None,gtuner_discovery_generation=0,bridge_service=None,trigger_armed=Mock())
        Desktop._stop(app)
        self.assertFalse(app.pending_reconnect)
        self.assertFalse(app.resume_after_reconnect)
        self.assertFalse(app.capture_launch_requested)

    def test_reconnect_disarms_but_keeps_capture_and_ai_running(self):
        from app import Desktop
        from types import SimpleNamespace
        app=SimpleNamespace(bridge_enabled=Mock(),trigger_armed=Mock(),live_active=True,bridge_service=None,
                            _apply_green=Mock(),_stop=Mock(),_prepare_bridge=Mock())
        Desktop._connect_bridge(app)
        app.trigger_armed.set.assert_called_once_with(False)
        app._stop.assert_not_called()
        app._prepare_bridge.assert_called_once_with()
        self.assertTrue(app.pending_reconnect)


class GtunerDiscoveryTests(unittest.TestCase):
    def test_keeps_valid_user_choice_before_running_or_portable_copy(self):
        import tempfile
        from pathlib import Path
        from titan_control import discover_gtuner
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);chosen=root/'custom'/'Gtuner.exe';running=root/'running'/'Gtuner.exe'
            for path in (chosen,running):path.parent.mkdir();path.write_bytes(b'test')
            self.assertEqual(discover_gtuner(str(chosen),roots=[],process_paths=[str(running)],registry_paths=[]),chosen.resolve())
            self.assertEqual(discover_gtuner('missing',roots=[],process_paths=[str(running)],registry_paths=[]),running.resolve())

    def test_finds_nested_portable_download_and_registry_install(self):
        import tempfile
        from pathlib import Path
        from titan_control import discover_gtuner
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);path=root/'GtunerIV-1.11'/'GtunerIV'/'Gtuner.exe'
            path.parent.mkdir(parents=True);path.write_bytes(b'test')
            self.assertEqual(discover_gtuner(roots=[root],process_paths=[],registry_paths=[]),path.resolve())
            self.assertEqual(discover_gtuner(roots=[],process_paths=[],registry_paths=[str(path)]),path.resolve())

    def test_missing_install_does_not_return_a_fake_default(self):
        import tempfile
        from pathlib import Path
        from titan_control import discover_gtuner
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);(root/'NotGtuner.exe').write_bytes(b'test')
            self.assertIsNone(discover_gtuner(roots=[root],process_paths=[],registry_paths=[]))


class BridgeStartupTests(unittest.TestCase):
    def test_existing_background_bridge_is_checked_without_executable_discovery(self):
        from app import Desktop
        from types import SimpleNamespace
        enabled=Mock();enabled.get.return_value=True
        app=SimpleNamespace(bridge_enabled=enabled,_prepare_bridge=Mock(),_find_gtuner=Mock())
        Desktop._startup_bridge(app)
        app._prepare_bridge.assert_called_once_with()
        app._find_gtuner.assert_not_called()

    def test_missing_receiver_still_selects_requested_titan_slot(self):
        from app import Desktop
        from types import SimpleNamespace
        import queue
        def variable(value):
            result=Mock();result.get.return_value=value;return result
        missing=dict(ready=False,receiver='waiting for Gtuner',stage='Gtuner CV worker not receiving')
        service=Mock();service.verify.return_value=missing
        app=SimpleNamespace(_busy=Mock(return_value=False),bridge_service=service,
            titan_slot=variable('4'),trigger_armed=Mock(),_apply_green=Mock(),
            bridge_setup_status=Mock(),gtuner_path=variable('Gtuner.exe'),auto_gtuner=variable(True),
            auto_slot=variable(True),_export_bridge=Mock(return_value='bridge'),messages=queue.Queue())
        with patch('app.discover_gtuner',return_value='Gtuner.exe'),patch('app.launch_gtuner_background'),patch('app.GtunerController') as factory,patch('app.threading.Thread') as thread:
            thread.return_value.start.side_effect=lambda:thread.call_args.kwargs['target']()
            Desktop._prepare_bridge(app)
            factory.return_value.select_slot.assert_called_once_with(4)
        _,(_,result,error,_)=app.messages.get_nowait()
        self.assertFalse(result['ready'])
        self.assertIn('Null Video Input',error)


class CapturePriorityTests(unittest.TestCase):
    def test_preview_stops_only_its_own_probe_before_retrying(self):
        from app import Desktop
        from types import SimpleNamespace
        probe=Mock();probe.poll.return_value=None
        def variable(value):
            result=Mock();result.get.return_value=value;return result
        app=SimpleNamespace(capture_active=False,updating=False,width=variable('1920'),
            height=variable('1080'),fps=variable('60'),capture_probe_process=probe,
            status=Mock(),after=Mock(),_start_capture=Mock(),_resume_capture_handoff=Mock())
        Desktop._start_capture(app)
        probe.terminate.assert_called_once();probe.kill.assert_not_called()
        self.assertTrue(app.capture_handoff_pending)
        self.assertEqual(app.after.call_args.args[0],100)
        # A Stop click cancels a previously queued handoff.
        app.capture_handoff_pending=False
        Desktop._resume_capture_handoff(app)
        app._start_capture.assert_not_called()

    def test_stalled_native_capture_is_terminated_outside_driver_process(self):
        from app import Desktop
        from types import SimpleNamespace
        from pathlib import Path
        import tempfile
        with tempfile.TemporaryDirectory() as folder:
            process=Mock();process.poll.return_value=None
            app=SimpleNamespace(capture_active=True,capture_out=Path(folder),capture_process=process,
                capture_progress_at=0,capture_progress_frames=0,capture_watchdog_fired=False,
                _stop_capture=Mock(),after=Mock(),status=Mock())
            with patch('app.time.monotonic',return_value=100):Desktop._capture_tick(app)
            process.terminate.assert_called_once();app._stop_capture.assert_called_once()
            self.assertEqual(app.after.call_args.args[0],2000)
            self.assertTrue(app.capture_watchdog_fired)

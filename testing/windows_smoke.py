"""Windows-only application startup check without a capture card or trained weight."""
import sys
import tempfile
import time
import json
import subprocess
import faulthandler
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main():
    if sys.platform != 'win32':
        raise SystemExit('This smoke test must run on Windows')
    if sys.prefix == sys.base_prefix:
        raise SystemExit('The app must run inside its virtual environment')

    import cv2
    import torch
    import ultralytics
    import pywinauto
    import psutil
    from app import Desktop

    def unexpected_dialog(*args,**kwargs):
        raise AssertionError(f'Unexpected modal dialog: {args!r} {kwargs!r}')

    with ExitStack() as stack:
        stack.enter_context(patch.object(Desktop, '_check_online'))
        stack.enter_context(patch.object(Desktop, '_startup_bridge'))
        stack.enter_context(patch.object(Desktop, '_scan_titan_hardware'))
        for name in ('showerror','showinfo','showwarning','askyesno'):
            stack.enter_context(patch('app.messagebox.'+name,side_effect=unexpected_dialog))
        app = Desktop()
        try:
            app.update_idletasks()
            app.update()
            names = [app.tabs.tab(i, 'text') for i in range(app.tabs.index('end'))]
            required = {'Models', 'Live Detection', 'Titan Two', 'Results', 'Updates', 'Improve Model'}
            if not required.issubset(names):
                raise AssertionError(f'Missing app tabs: {required - set(names)}')
            # Startup notification is nonmodal, persistent in the banner, and shown once per version.
            from app_update import current_version
            import app as app_module
            release={'version':current_version(app_module.PROJECT)+1}
            app._handle_release(release);app.update()
            first_notice=app.update_notice
            assert first_notice is not None and first_notice.winfo_exists()
            assert app.update_banner.winfo_ismapped()
            app._dismiss_update_notice()
            app._handle_release(release)
            assert app.update_notice is None
            app.updating=True
            app._update_failed('simulated network failure')
            assert not app.updating and str(app.install_button.cget('state'))=='normal'
            app._handle_release(None);app.update()
            assert not app.update_banner.winfo_ismapped()
            assert str(app.install_button.cget('state'))=='disabled'
            from PIL import ImageGrab
            app.lift();app.update()
            screenshot=Path('test_results')/'ui_preview.png'
            screenshot.parent.mkdir(exist_ok=True)
            ImageGrab.grab(bbox=(app.winfo_rootx(),app.winfo_rooty(),
                app.winfo_rootx()+app.winfo_width(),app.winfo_rooty()+app.winfo_height())).save(screenshot)
            # Check selected runtime, untimed launch and safe Stop without hardware.
            with tempfile.TemporaryDirectory() as folder:
                out=Path(folder);model=out/'best.pt';model.write_bytes(b'test-only')
                import app as app_module
                alternative=out/'runs'/'candidate'/'weights'/'best.pt'
                alternative.parent.mkdir(parents=True);alternative.write_bytes(b'test-only')
                app.model.set(str(model))
                with patch.object(app_module,'PROJECT',out),patch.object(app_module,'SETTINGS',out/'settings.json'):
                    app._refresh_choices()
                    choice=next(label for label,path in app.model_choices.items() if path==str(alternative.resolve()))
                    app.model_choice.set(choice);app._select_model()
                    assert app.model.get()==str(alternative.resolve())
                    saved=json.loads((out/'settings.json').read_text())
                    assert saved['model']==str(alternative.resolve())
                    assert str(model) in saved['known_models']
                    assert str(app.model_menu.cget('state'))=='readonly'
                # Gtuner receives exact script paths, without a shell or invented slot flags.
                gtuner=out/'Gtuner.exe';gtuner.write_bytes(b'test-only')
                app.gtuner_path.set(str(gtuner));app.auto_gtuner.set(True)
                with patch.object(app,'_export_bridge',return_value=out),patch.object(app_module.subprocess,'Popen') as launch:
                    app._launch_gtuner()
                    assert launch.call_args.args[0]==[str(gtuner),str(out/'titan_meter.gpc'),str(out/'gtuner_meter.py')]
                    assert app.gtuner_launched
                # Start Capture needs neither a model nor the selected CUDA environment.
                capture_folder=out/'capture_only';capture_folder.mkdir()
                app.model.set(str(out/'missing.pt'));app.environment.set('missing-python.exe')
                capture_process=type('Process',(),{'stdout':[], 'wait':lambda self:0,'poll':lambda self:0})()
                with patch.object(app,'_out',return_value=capture_folder),patch.object(app_module.subprocess,'Popen',return_value=capture_process) as launch,patch.object(app_module.threading,'Thread') as reader:
                    reader.return_value.start.side_effect=lambda:reader.call_args.kwargs['target']()
                    app._start_capture()
                    command=launch.call_args.args[0]
                    assert command[0]==sys.executable and '--capture-only' in command
                    assert '--model' not in command and '--device' not in command and '--bridge-service' not in command
                app.capture_active=False;app.capture_process=None
                # Remove the simulated capture's completion before the next independent run.
                import queue
                while True:
                    try:app.messages.get_nowait()
                    except queue.Empty:break
                # A capture owner is independent of AI and of bridge verification.
                fake=type('Process',(),{'poll':lambda self:None})()
                app.capture_active=True;app.capture_out=out;app.capture_process=fake
                capture_owner=fake
                (out/'capture_status.json').write_text(json.dumps(dict(state='capturing',timestamp=time.time(),frames=1,
                    capture_fps=60,reported_capture=dict(width=1920,height=1080,fps=60))))
                app.model.set(str(model));app.environment.set(sys.executable);app.bridge_enabled.set(True)
                with patch.object(app,'_prepare_bridge') as prepare,patch.object(app,'_save'),patch.object(app,'_out',return_value=out),patch.object(app,'_start') as start:
                    app._live()
                    prepare.assert_not_called()
                    assert '--capture-session' in start.call_args.args[0]
                    assert '--bridge-service' in start.call_args.args[0]
                    assert '--live-direct' not in start.call_args.args[0]
                app.model.set(str(model));app.environment.set(sys.executable)
                app.seconds.set('0');app.compute.set('0');app.bridge_enabled.set(True)
                with patch.object(app,'_save'),patch.object(app,'_out',return_value=out),patch.object(app,'_start') as start:
                    app._live(bridge_ready=True)
                    command=start.call_args.args[0]
                    assert command[0]==sys.executable
                    assert command[command.index('--seconds')+1]=='0.0'
                    assert command[command.index('--device')+1]=='0'
                    assert command[command.index('--imgsz')+1]=='1280'
                    app.imgsz.set('640');app._live(bridge_ready=True)
                    trial=start.call_args.args[0]
                    assert trial[trial.index('--imgsz')+1]=='640'
                    assert trial[trial.index('--model')+1]==str(model)
                    app.imgsz.set('1280')
                    assert '--capture-session' in command and '--bridge-service' in command
                    assert '--green-settings' in command
                    assert json.loads((out/'green_settings.json').read_text())['armed'] is False
                    app.green_vars['delay_ms'].set('35')
                    app._apply_green()
                    assert json.loads((out/'green_settings.json').read_text())['delay_ms']==35
                    assert json.loads((out/'app_profile.json').read_text())['controller']=='Xbox / GameSir'
                import numpy as np
                from live_link import snapshot
                snapshot(out,dict(timestamp=time.time(),fps=59,inference_ms=10,status='detected',bridge=None),
                         cv2,np.zeros((100,200,3),dtype=np.uint8))
                app._preview_tick()
                assert app.capture_active
                assert 'AI 59.0 FPS' in app.live_metrics.get()
                fake=type('Process',(),{'poll':lambda self:None})()
                app.process=fake
                app._stop()
                assert (out/'stop.request').is_file()
                app.process=None
                assert app.capture_active and app.capture_process is capture_owner
                from capture_pipe import RawMailbox
                with_raw=RawMailbox(out/'live_bgr.bin',write=True)
                try:
                    with_raw.publish(np.zeros((100,200,3),dtype=np.uint8),cv2,17,time.time())
                    app._display_tick()
                    assert app.raw_preview_sequence==1
                    assert 'Direct card BGR' in app.display_metrics.get()
                finally:
                    app._close_raw_preview();with_raw.close()
                app.render_mode.set('Resolution')
                with patch.object(app,'_save'):app._render_preset()
                assert app.display_fps.get()=='60'
                app.render_mode.set('Performance')
                with patch.object(app,'_save'):app._render_preset()
                assert app.display_fps.get()=='120'
                app.capture_type.set('Media Foundation / YUY2')
                assert app._capture_mode()==('msmf','YUY2')
                app._show_slots({1:'NBA2K CV Meter Bridge'},1)
                assert app.slot_table.item('1','values')[0]=='NBA2K CV Meter Bridge'
                assert app.slot_table.item('1','values')[1]=='Active — heartbeat verified'
                app._show_slots(None,None)
                assert app.slot_table.item('1','values')[1]=='Not verified'
                app.bridge_enabled.set(True);app.capture_launch_requested=False
                result=dict(ready=True,active_slot=4)
                with patch.object(app,'_live') as launch:
                    app.messages.put(('bridge_prepared',(True,result,None,{4:'NBA2K CV Meter Bridge'})))
                    app._poll()
                    launch.assert_not_called()
                    app.capture_launch_requested=True
                    app.messages.put(('bridge_prepared',(True,result,None,{4:'NBA2K CV Meter Bridge'})))
                    app._poll()
                    launch.assert_not_called()
                app.capture_active=False;app.capture_process=None


        finally:
            app.destroy()

    print(f'Windows app startup passed: Python {sys.version.split()[0]}, '
          f'OpenCV {cv2.__version__}, PyTorch {torch.__version__}, '
          f'Ultralytics {ultralytics.__version__}; CUDA available: {torch.cuda.is_available()}')


def run_bounded(command,timeout=120):
    """Supervise outside Tk: a blocked native call cannot defeat this deadline."""
    process=subprocess.Popen(command)
    try:
        return process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        print(f'Windows GUI smoke exceeded {timeout}s; terminating worker.',file=sys.stderr,flush=True)
        return 124
    finally:
        if process.poll() is None:
            if sys.platform=='win32':
                try:
                    subprocess.run(['taskkill','/PID',str(process.pid),'/T','/F'],
                                   timeout=10,check=False)
                except (OSError,subprocess.TimeoutExpired):
                    pass
            if process.poll() is None:process.kill()
            process.wait(timeout=10)


if __name__ == '__main__':
    if '--worker' in sys.argv:
        faulthandler.enable()
        faulthandler.dump_traceback_later(90)
        try:main()
        finally:faulthandler.cancel_dump_traceback_later()
    else:
        raise SystemExit(run_bounded([sys.executable,'-u',str(Path(__file__).resolve()),'--worker']))

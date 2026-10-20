"""Windows-only application startup check without a capture card or trained weight."""
import sys
import tempfile
import time
import json
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
    from app import Desktop

    with patch.object(Desktop, '_check_online'):
        app = Desktop()
        try:
            app.update_idletasks()
            app.update()
            names = [app.tabs.tab(i, 'text') for i in range(app.tabs.index('end'))]
            required = {'Models', 'Live Detection', 'Titan Two', 'Results', 'Updates', 'Improve Model'}
            if not required.issubset(names):
                raise AssertionError(f'Missing app tabs: {required - set(names)}')
            # Startup notification is nonmodal, persistent in the banner, and shown once per version.
            release={'version':2026102005}
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
                app.model.set(str(model));app.environment.set(sys.executable)
                app.seconds.set('0');app.compute.set('0');app.bridge_enabled.set(True)
                with patch.object(app,'_save'),patch.object(app,'_out',return_value=out),patch.object(app,'_start') as start:
                    app._live()
                    command=start.call_args.args[0]
                    assert command[0]==sys.executable
                    assert command[command.index('--seconds')+1]=='0.0'
                    assert command[command.index('--device')+1]=='0'
                    assert command[command.index('--imgsz')+1]=='1280'
                    app.imgsz.set('640');app._live()
                    trial=start.call_args.args[0]
                    assert trial[trial.index('--imgsz')+1]=='640'
                    assert trial[trial.index('--model')+1]==str(model)
                    app.imgsz.set('1280')
                    assert '--ui-preview' in command and '--bridge-port' in command
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
                assert app.preview_image is not None
                assert '59.0 processed FPS' in app.live_metrics.get()
                fake=type('Process',(),{'poll':lambda self:None})()
                app.process=fake
                app._stop()
                assert (out/'stop.request').is_file()
                app.process=None
        finally:
            app.destroy()

    print(f'Windows app startup passed: Python {sys.version.split()[0]}, '
          f'OpenCV {cv2.__version__}, PyTorch {torch.__version__}, '
          f'Ultralytics {ultralytics.__version__}; CUDA available: {torch.cuda.is_available()}')


if __name__ == '__main__':
    main()

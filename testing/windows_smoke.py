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
            required = {'Live Detection', 'Titan Two', 'Results', 'Updates', 'Improve Model'}
            if not required.issubset(names):
                raise AssertionError(f'Missing app tabs: {required - set(names)}')
            # Check selected runtime, untimed launch and safe Stop without hardware.
            with tempfile.TemporaryDirectory() as folder:
                out=Path(folder);model=out/'best.pt';model.write_bytes(b'test-only')
                app.model.set(str(model));app.environment.set(sys.executable)
                app.seconds.set('0');app.compute.set('0');app.bridge_enabled.set(True)
                with patch.object(app,'_save'),patch.object(app,'_out',return_value=out),patch.object(app,'_start') as start:
                    app._live()
                    command=start.call_args.args[0]
                    assert command[0]==sys.executable
                    assert command[command.index('--seconds')+1]=='0.0'
                    assert command[command.index('--device')+1]=='0'
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

"""Windows-only application startup check without a capture card or trained weight."""
import sys
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
            required = {'Live Detection', 'Results', 'Updates', 'Improve Model'}
            if not required.issubset(names):
                raise AssertionError(f'Missing app tabs: {required - set(names)}')
        finally:
            app.destroy()

    print(f'Windows app startup passed: Python {sys.version.split()[0]}, '
          f'OpenCV {cv2.__version__}, PyTorch {torch.__version__}, '
          f'Ultralytics {ultralytics.__version__}; CUDA available: {torch.cuda.is_available()}')


if __name__ == '__main__':
    main()

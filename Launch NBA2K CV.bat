@echo off
setlocal
cd /d "%~dp0"
where py >nul 2>&1
if errorlevel 1 (
  echo Python launcher required. Install Python 3.11 x64 from python.org, then rerun.
  goto setup_failed
)
py -3.11 -c "import struct; assert struct.calcsize('P') == 8" >nul 2>&1
if errorlevel 1 (
  echo Installing dedicated Python 3.11 x64 support...
  where winget >nul 2>&1
  if errorlevel 1 goto python_missing
  winget install --id Python.Python.3.11 --exact --architecture x64 --scope user --accept-package-agreements --accept-source-agreements
  if errorlevel 1 goto python_missing
  py -3.11 -c "import struct; assert struct.calcsize('P') == 8" >nul 2>&1
  if errorlevel 1 goto python_missing
)
if not exist ".venv311\Scripts\python.exe" (
  echo Creating the app's dedicated .venv311 environment...
  py -3.11 -m venv .venv311
  if errorlevel 1 goto setup_failed
)
".venv311\Scripts\python.exe" -c "import sys,struct; assert sys.version_info[:2] == (3,11) and struct.calcsize('P') == 8" >nul 2>&1
if errorlevel 1 (
  echo The existing .venv311 is not Python 3.11 x64. It was left intact; move it aside and rerun.
  goto setup_failed
)
nvidia-smi >nul 2>&1
if not errorlevel 1 (
  ".venv311\Scripts\python.exe" -c "import torch; assert torch.cuda.is_available(); torch.empty(1,device='cuda')" >nul 2>&1
  if errorlevel 1 (
    echo Installing CUDA 12.8 PyTorch for the NVIDIA detector...
    ".venv311\Scripts\python.exe" -m pip install "torch==2.11.0+cu128" "torchvision==0.26.0+cu128" --index-url https://download.pytorch.org/whl/cu128
    if errorlevel 1 goto setup_failed
  )
  ".venv311\Scripts\python.exe" -c "import torch; assert torch.cuda.is_available(), 'CUDA unavailable: check NVIDIA driver'; torch.empty(1,device='cuda'); print('CUDA:',torch.cuda.get_device_name(0))"
  if errorlevel 1 goto setup_failed
)
".venv311\Scripts\python.exe" -c "import cv2, torch, ultralytics, PIL, win32api, pywintypes, pythoncom, pywinauto, psutil" >nul 2>&1
if errorlevel 1 (
  echo Installing OpenCV, PyWin32, UI and detection dependencies into .venv311...
  ".venv311\Scripts\python.exe" -m pip install -r requirements.txt
  if errorlevel 1 goto setup_failed
)
".venv311\Scripts\python.exe" -c "import cv2, torch, ultralytics, PIL, win32api, pywintypes, pythoncom, pywinauto, psutil; print('OpenCV and PyWin32 ready')"
if errorlevel 1 goto setup_failed
start "" ".venv311\Scripts\pythonw.exe" "app.py"
exit /b 0
:python_missing
echo Python 3.11 x64 is required. Install it from https://www.python.org/downloads/release/python-3119/
:setup_failed
echo Setup did not complete. Existing models and other environments were not removed.
pause
exit /b 1

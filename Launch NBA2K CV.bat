@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  where py >nul 2>&1
  if errorlevel 1 (
    echo Python is required for this launcher. Install Python for Windows, then run it again.
    pause
    exit /b 1
  )
  echo Creating the app's Python environment...
  py -3 -m venv .venv
  if errorlevel 1 goto setup_failed
)
".venv\Scripts\python.exe" -c "import cv2, torch, ultralytics" >nul 2>&1
if errorlevel 1 (
  echo Installing detection dependencies. This may take several minutes...
  ".venv\Scripts\python.exe" -m pip install -r requirements.txt
  if errorlevel 1 goto setup_failed
)
start "" ".venv\Scripts\pythonw.exe" "app.py"
exit /b 0
:setup_failed
echo Setup failed. Check the message above, then rerun this launcher.
pause
exit /b 1

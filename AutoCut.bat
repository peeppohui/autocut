@echo off
rem AutoCut launcher (ASCII only - cmd.exe cannot read UTF-8 bat files)
cd /d "%~dp0"
where python >nul 2>nul
if errorlevel 1 (
  echo [AutoCut] python not found in PATH.
  echo Install Python from python.org and tick "Add python to PATH".
  pause
  exit /b 1
)
python plugin\src\ui\app.py
if errorlevel 1 (
  echo.
  echo [AutoCut] app closed with error. See messages above.
  pause
)

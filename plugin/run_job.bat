@echo off
rem AutoCut launcher for the UXP panel (ASCII only - cmd.exe cannot read UTF-8)
rem The panel writes autocut_job.json next to this file, then opens this script.
cd /d "%~dp0"
where python >nul 2>nul
if errorlevel 1 (
  echo [AutoCut] python not found in PATH.
  echo Install Python from python.org and tick "Add python to PATH".
  pause
  exit /b 1
)
python src\ui\run_job.py --job "%~dp0autocut_job.json"
if errorlevel 1 (
  echo.
  echo [AutoCut] job failed. See autocut_result.json next to this script.
) else (
  echo.
  echo [AutoCut] job finished. Return to Premiere to see the report.
)
timeout /t 3 >nul
exit /b 0
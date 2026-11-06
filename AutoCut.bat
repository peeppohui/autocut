@echo off
rem AutoCut engine launcher (ASCII only - cmd.exe cannot read UTF-8 bat files)
rem The UI is the Premiere CEP panel. This starts only the local engine.
cd /d "%~dp0"

set "ROOT=%~dp0"
set "PY=%ROOT%.tools\python311\python.exe"
if not exist "%PY%" set "PY=python"

for /d %%D in ("%ROOT%.tools\ffmpeg\*") do (
  if exist "%%D\bin\ffmpeg.exe" set "PATH=%%D\bin;%PATH%"
)

set "PYTHONIOENCODING=utf-8"
set "PYTHONUTF8=1"

cd /d "%ROOT%plugin"
"%PY%" run_mcp.py --bridge-only
if errorlevel 1 (
  echo.
  echo [AutoCut] engine stopped with error. See messages above.
  pause
)

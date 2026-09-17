@echo off
setlocal
cd /d "%~dp0"
where python >nul 2>&1
if errorlevel 1 (
  echo Python non trovato.
  pause
  exit /b 1
)
python -m pip install --disable-pip-version-check playwright
python conad_capodrise_local.py --bootstrap
pause

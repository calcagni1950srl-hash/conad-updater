@echo off
setlocal
cd /d "%~dp0"
python -m pip install --disable-pip-version-check playwright
python conad_capodrise_local.py --smoke --headful
if errorlevel 1 (
  echo.
  echo TEST CONAD CAPODRISE FALLITO. Leggi il messaggio sopra.
  pause
  exit /b 1
)
echo.
echo TEST CONAD CAPODRISE OK.
pause

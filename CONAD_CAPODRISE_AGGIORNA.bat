@echo off
setlocal
cd /d "%~dp0"
python -m pip install --disable-pip-version-check playwright
python conad_capodrise_local.py
if errorlevel 1 (
  echo.
  echo AGGIORNAMENTO CONAD CAPODRISE FALLITO. Il database precedente non viene sostituito con dati non validati.
  pause
  exit /b 1
)
echo.
echo AGGIORNAMENTO CONAD CAPODRISE COMPLETATO.
pause

@echo off
REM Abre o painel DPN DEDICADO (sem bot, sem outras abas).
cd /d "%~dp0.."
"%~dp0..\..\venv\Scripts\python.exe" "%~dp0panel_dpn.py"
if errorlevel 1 (
  echo.
  echo Falhou ao iniciar. Verifique se as dependencias estao instaladas:
  echo   python -m pip install -r requirements.txt
  pause
)

@echo off
REM Executa o DPN Control a partir do Python instalado.
cd /d "%~dp0"
python -m dpn
if errorlevel 1 (
  echo.
  echo Falhou ao iniciar. Verifique se as dependencias estao instaladas:
  echo   python -m pip install -r requirements.txt
  pause
)

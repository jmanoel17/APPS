@echo off
echo Iniciando interface SLN V2...
start "" "%~dp0venv\Scripts\python.exe" "%~dp0SLN V2\interface_sln_v2.py"

echo Iniciando IA...
set "SLN_IA_MODEL_PATH=%~dp0runs\detect\train3\weights\best.pt"
start "" py "%~dp0SLN V2\aiMain_sln_v2.py"

echo Ambos foram iniciados.
pause

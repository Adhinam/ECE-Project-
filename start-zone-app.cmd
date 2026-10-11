@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo The project Python environment is missing. See zone-editor\README.md.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -m zone_app.server
pause

@echo off
chcp 65001 >nul
cd /d "%~dp0\.."
python -X utf8 -c "from app.sidecar.drpys import ensure_started; import sys; sys.exit(0 if ensure_started(25) else 1)"
if errorlevel 1 (
  echo drpyS failed to start. Check sidecar\logs\drpys.err.log
  exit /b 1
)
echo drpyS is ready on http://127.0.0.1:5757

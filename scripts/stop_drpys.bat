@echo off
chcp 65001 >nul
cd /d "%~dp0\.."
for /f "tokens=5" %%p in ('netstat -ano ^| findstr ":5757" ^| findstr "LISTENING"') do (
  taskkill /f /pid %%p >nul 2>&1
)
echo drpyS stopped.

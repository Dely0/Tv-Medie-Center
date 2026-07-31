@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo Stopping all...
taskkill /f /im python.exe >nul 2>&1
taskkill /f /im msedge.exe >nul 2>&1

echo Starting server...
start "" /B python -X utf8 main.py > data\server.log 2>&1

:wait
timeout /t 2 /nobreak >nul
powershell -Command "try{($wc=New-Object Net.WebClient).DownloadString('http://localhost:8080/')|Out-Null;exit 0}catch{exit 1}" >nul 2>&1
if errorlevel 1 goto wait

echo Server ready!
start msedge.exe --start-fullscreen --new-window http://localhost:8080

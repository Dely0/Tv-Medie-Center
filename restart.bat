@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo Stopping old processes...
taskkill /f /im python.exe >nul 2>&1
taskkill /f /im mpv.exe >nul 2>&1

echo Cleaning python cache...
if exist app\__pycache__ rmdir /s /q app\__pycache__ >nul 2>&1
if exist app\maccms_source\__pycache__ rmdir /s /q app\maccms_source\__pycache__ >nul 2>&1

echo Starting server...
start "" /B python -X utf8 main.py > data\server.log 2>&1

echo Waiting for server...
:wait
timeout /t 2 /nobreak >nul
powershell -Command "try{($wc=New-Object Net.WebClient).DownloadString('http://localhost:8080/')|Out-Null;exit 0}catch{exit 1}" >nul 2>&1
if errorlevel 1 goto wait

echo Server ready!
start msedge.exe --start-fullscreen --new-window http://localhost:8080

:: 延迟后发送 F11 确保全屏
timeout /t 2 /nobreak >nul
powershell -NoProfile -Command "$w=New-Object -ComObject WScript.Shell;for($i=0;$i-lt10;$i++){$r=$w.AppActivate('TV Media Center');if($r){$w.SendKeys('{F11}');break};Start-Sleep -Milliseconds 500}"

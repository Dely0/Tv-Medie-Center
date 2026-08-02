@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo Stopping all processes...
taskkill /f /im msedge.exe >nul 2>&1
rem 只结束主服务（main.py），保留 drpyS 及其 Python 守护进程
powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match 'main\.py' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }" >nul 2>&1

echo Clearing Python bytecode cache...
if exist app\__pycache__ rmdir /s /q app\__pycache__ >nul 2>&1

echo Clearing Edge --app mode cache...
if exist "%LOCALAPPDATA%\Microsoft\Edge\User Data\Default\Cache" (
    del /f /s /q "%LOCALAPPDATA%\Microsoft\Edge\User Data\Default\Cache\*" >nul 2>&1
    echo  Edge cache cleared
) else (
    echo  Edge cache directory not found, trying alternate location...
)
if exist "%LOCALAPPDATA%\Microsoft\Edge\User Data\Default\Code Cache" (
    del /f /s /q "%LOCALAPPDATA%\Microsoft\Edge\User Data\Default\Code Cache\*" >nul 2>&1
)
if exist "%LOCALAPPDATA%\Microsoft\Edge\User Data\Default\Service Worker\CacheStorage" (
    rmdir /s /q "%LOCALAPPDATA%\Microsoft\Edge\User Data\Default\Service Worker\CacheStorage" >nul 2>&1
)

echo Clearing Edge --app isolated cache (AppSpecific)...
for /d %%i in ("%LOCALAPPDATA%\Microsoft\Edge\User Data\Default\AppSpecific\*") do (
    rmdir /s /q "%%i" >nul 2>&1
)

echo.
echo Starting server...
start "" /B python -X utf8 main.py > data\server.log 2>&1

echo Waiting for server...
:wait
timeout /t 2 /nobreak >nul
powershell -Command "try{($wc=New-Object Net.WebClient).DownloadString('http://localhost:8080/')|Out-Null;exit 0}catch{exit 1}" >nul 2>&1
if errorlevel 1 goto wait

echo Server ready!
start msedge.exe --app=http://localhost:8080
echo.
echo Done! Cache cleared and new session started.

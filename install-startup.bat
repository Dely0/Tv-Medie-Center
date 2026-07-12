@echo off
chcp 65001 >nul
cd /d "%~dp0"

for /f "tokens=*" %%i in ('where python 2^>nul') do set PY=%%i
if not defined PY (
    echo Python not found, please install Python first
    pause
    exit /b 1
)

set SCRIPT="%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\TV-Media-Center.bat"

(
echo @echo off
echo chcp 65001 ^>nul
echo cd /d "%~dp0"
echo start "" /B "%PY%" -X utf8 main.py ^> data\server.log 2^>^&1
echo :wait
echo timeout /t 2 /nobreak ^>nul
echo powershell -Command "try{^($wc=New-Object Net.WebClient^).DownloadString('http://localhost:8080/'^)^|Out-Null;exit 0^}catch{exit 1^}" ^>nul 2^>^&1
echo if errorlevel 1 goto wait
echo start msedge.exe --start-fullscreen --new-window http://localhost:8080
) > %SCRIPT%

echo Startup shortcut created at %SCRIPT%
pause

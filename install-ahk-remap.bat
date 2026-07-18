@echo off
cd /d "%~dp0"
echo TV Media Center - Remote Key Mapping
echo =====================================
echo.
echo Step 1: Download AutoHotkey...
powershell -Command "[Net.ServicePointManager]::SecurityProtocol='tls12';$c=New-Object Net.WebClient;$c.DownloadFile('https://github.com/AutoHotkey/AutoHotkey/releases/download/v2.0.19/AutoHotkey_2.0.19.zip','%TEMP%\ahk.zip')"
if not exist "%TEMP%\ahk.zip" (
    echo Download failed. Please install AutoHotkey manually:
    echo   https://www.autohotkey.com/
    pause
    exit /b
)
echo Done.
echo.
echo Step 2: Extracting...
if not exist "data\ahk" mkdir data\ahk
powershell -Command "Expand-Archive '%TEMP%\ahk.zip' -DestinationPath 'data\ahk' -Force"
copy /Y remap-remote.ahk data\ahk\remap-remote.ahk >nul
echo Done.
echo.
echo Step 3: Running...
start "" /MIN data\ahk\AutoHotkey64.exe data\ahk\remap-remote.ahk
echo.
echo Remote back key is now mapped to ESC.
echo You can close this window.
echo.
pause

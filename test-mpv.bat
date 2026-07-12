@echo off
cd /d "%~dp0"
echo TV Media Center - MPV 播放测试
echo =============================
echo.
echo 测试 1: mpv 版本
"%cd%\data\mpv.exe" --version | findstr mpv
echo.
echo 测试 2: 播放测试视频流（能看到画面说明 mpv 正常）
echo 窗口开启后按 q 键退出
echo.
"%cd%\data\mpv.exe" --no-config --fullscreen --ontop --keep-open=yes --cache=yes "https://test-streams.mux.dev/x36xhzz/x36xhzz.m3u8"
echo.
pause

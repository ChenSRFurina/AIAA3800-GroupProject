@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

echo.
echo ====================================================
echo   VPet 一键启动（四个后端 + 桌宠前端）
echo ====================================================
echo.
echo   将打开多个窗口:
echo     [1] Speaking   F5-TTS     :8765
echo     [2] Gaze       视线跟随   :8766
echo     [3] FaceDetect 人脸检测   :8000  (默认本地推理)
echo         + 自动打开浏览器测试页推流
echo     [4] Audio      语音助手   :8010
echo     [5] VPet       桌宠前端
echo.
echo   环境约定:
echo     Speaking / Gaze  -^> conda F5TTS
echo     FaceDetect       -^> conda FACE
echo     Audio            -^> audio\backend\.venv  (先运行 audio\setup.bat)
echo.
echo   可选参数（传给 start-all.ps1）:
echo     start-all.bat -Remote
echo     start-all.bat -Remote -FaceRemoteUrl http://192.168.1.10:8000
echo     start-all.bat -GazeMock
echo     start-all.bat -SkipAudio -Device cpu
echo     start-all.bat -NoFrontend
echo     start-all.bat -NoFaceBrowser
echo     start-all.bat -Release
echo.
echo   停止全部: 双击 stop-all.bat
echo.

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start-all.ps1" %*
set ERR=%ERRORLEVEL%
if %ERR% neq 0 (
    echo.
    echo [错误] 启动失败，退出码 %ERR%
    pause
    exit /b %ERR%
)

echo.
echo 启动请求已发出。本窗口可关；后端窗口请保留。
timeout /t 3 >nul
exit /b 0

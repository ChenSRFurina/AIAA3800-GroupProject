@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0backend"

echo.
echo ====================================================
echo   FaceDetect backend :8000  [conda FACE]
echo ====================================================
echo   HF mirror: https://hf-mirror.com
echo   Test page: http://127.0.0.1:8000/test-frontend/
echo.

set "HF_ENDPOINT=https://hf-mirror.com"

where conda >nul 2>&1
if %errorlevel% equ 0 (
    call conda activate FACE 2>nul
)

python server.py
if %errorlevel% neq 0 (
    echo.
    echo [ERROR] start failed. Activate conda FACE and install deps first.
    echo   install: ..\setup.bat
    pause
)
exit /b %errorlevel%

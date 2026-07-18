@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0backend"

echo.
echo ====================================================
echo   FaceDetect relay :8000 -^> remote server
echo ====================================================
echo   Set FACE_REMOTE_URL in VPet\.env, e.g.:
echo     FACE_REMOTE_URL=http://192.168.1.10:8000
echo   Test page: http://127.0.0.1:8000/test-frontend/
echo.

if not defined FACE_REMOTE_URL (
    if exist "%~dp0..\.env" (
        for /f "usebackq tokens=1,* delims==" %%A in (`findstr /b /i "FACE_REMOTE_URL=" "%~dp0..\.env"`) do (
            set "FACE_REMOTE_URL=%%B"
        )
    )
)

if not defined FACE_REMOTE_URL (
    echo [WARN] FACE_REMOTE_URL not set. Relay will start but health will fail.
)

where conda >nul 2>&1
if %errorlevel% equ 0 (
    call conda activate FACE 2>nul
)

python relay.py
if %errorlevel% neq 0 (
    echo.
    echo [ERROR] relay start failed. Run ..\setup.bat in conda FACE first.
    pause
)
exit /b %errorlevel%

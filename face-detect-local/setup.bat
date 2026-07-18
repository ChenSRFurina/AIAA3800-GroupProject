@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

echo.
echo ====================================================
echo   FaceDetect-local env setup [conda FACE]
echo ====================================================
echo.

where conda >nul 2>&1
if %errorlevel% neq 0 (
    echo [错误] 未找到 conda，请先安装 Miniconda/Anaconda
    pause
    exit /b 1
)

call conda activate FACE 2>nul
if %errorlevel% neq 0 (
    echo [1/3] create conda env FACE python=3.13 ...
    call conda create -n FACE python=3.13 -y
    if %errorlevel% neq 0 exit /b 1
    call conda activate FACE
)

echo [2/3] install ffmpeg from conda-forge ...
call conda install -y ffmpeg -c conda-forge
if %errorlevel% neq 0 (
    echo [WARN] ffmpeg install failed; JPEG infer may still work
)

echo [3/3] 安装 Python 依赖 ...
pip install -r "%~dp0requirements.txt"
if %errorlevel% neq 0 (
    echo [错误] pip install 失败
    pause
    exit /b 1
)

echo.
echo [完成] 环境 FACE 已就绪。
echo   模型: 将 RetinaFace 放到 model\model.safetensors
echo   启动: run_backend.bat
echo   测试: http://127.0.0.1:8000/test-frontend/
echo.
pause

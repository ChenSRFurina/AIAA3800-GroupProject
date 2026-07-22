@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
set "TORCH_INDEX=https://download.pytorch.org/whl/cu130"
if not "%VPET_TORCH_INDEX%"=="" set "TORCH_INDEX=%VPET_TORCH_INDEX%"

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

conda deactivate

call conda activate FACE 2>nul
if %errorlevel% neq 0 (
    echo [1/3] create conda env FACE python=3.12 ...
    call conda create -n FACE python=3.12 -y
    if %errorlevel% neq 0 exit /b 1
    call conda activate FACE
)

echo [2/3] install ffmpeg
call conda install -y ffmpeg
if %errorlevel% neq 0 (
    echo [WARN] ffmpeg install failed; JPEG infer may still work
)

echo [3/4] 安装 PyTorch CUDA 版本 ...
pip install --upgrade torch torchvision --index-url %TORCH_INDEX%
if %errorlevel% neq 0 (
    echo [错误] PyTorch CUDA 安装失败（index: %TORCH_INDEX%）
    pause
    exit /b 1
)

echo [4/4] 安装 Python 依赖 ...
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

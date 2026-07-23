@echo off
setlocal EnableExtensions EnableDelayedExpansion

cd /d "%~dp0"

set "ENV_NAME=F5TTS"
set "PYTHON_VERSION=3.11"
set "TORCH_INDEX=%VPET_TORCH_INDEX%"
if "%TORCH_INDEX%"=="" set "TORCH_INDEX=https://download.pytorch.org/whl/cu130"

set "SKIP_MODEL=0"
set "QWEN_SIZE=1.7b"

for %%A in (%*) do (
    if /I "%%~A"=="--skip-model" set "SKIP_MODEL=1"
    if /I "%%~A"=="--qwen-1.7b" set "QWEN_SIZE=1.7b"
)

if /I "%QWEN_SIZE%"=="1.7b" (
    set "QWEN_MODEL=Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign"
    set "QWEN_MODEL_DIR=Local_model\model\Qwen3-TTS-12Hz-1.7B-VoiceDesign"
)

echo ==================================================
echo VPet-Speaking Setup
echo - Conda env   : %ENV_NAME%
echo - Torch index : %TORCH_INDEX%
echo - Qwen size   : %QWEN_SIZE%
echo - Qwen model  : %QWEN_MODEL%
echo ==================================================

where conda >nul 2>&1
if %errorlevel% neq 0 (
    echo [错误] 未找到 conda，请先安装 Miniconda/Anaconda 并确保 conda 在 PATH 中。
    pause
    exit /b 1
)

echo.
echo [1/8] 检查 F5-TTS 子模块 ...
if not exist "Local_model\F5-TTS\src" (
    echo [错误] 缺少 Local_model\F5-TTS\src
    echo 请先执行: git submodule update --init --recursive VPet-Speaking/Local_model/F5-TTS
    pause
    exit /b 1
)
echo   子模块存在 ✓

echo.
echo [2/8] 准备 conda 环境 %ENV_NAME% ...
set "ENV_EXISTS=0"
for /f "tokens=1" %%i in ('conda env list ^| findstr /R /I "^%ENV_NAME% "') do (
    set "ENV_EXISTS=1"
)
if "!ENV_EXISTS!"=="0" (
    echo   创建环境: %ENV_NAME% (python=%PYTHON_VERSION%)
    call conda create -y -n %ENV_NAME% python=%PYTHON_VERSION%
    if !errorlevel! neq 0 (
        echo [错误] conda create 失败
        pause
        exit /b 1
    )
) else (
    echo   环境已存在，复用 ✓
)

echo.
echo [3/8] 安装 ffmpeg ...
call conda install -y -n %ENV_NAME% ffmpeg
if %errorlevel% neq 0 (
    echo [警告] ffmpeg 安装失败，可稍后手动安装。
)

echo.
echo [4/8] 升级 pip 基础工具 ...
call conda run --live-stream -n %ENV_NAME% python -m pip install --upgrade pip setuptools wheel
if %errorlevel% neq 0 (
    echo [错误] pip/setuptools/wheel 升级失败
    pause
    exit /b 1
)

echo.
echo [5/8] 安装 CUDA PyTorch ...
call conda run --live-stream -n %ENV_NAME% python -m pip install --upgrade torch torchaudio --index-url %TORCH_INDEX%
if %errorlevel% neq 0 (
    echo [错误] PyTorch CUDA 安装失败（index=%TORCH_INDEX%）
    pause
    exit /b 1
)

echo.
echo [6/8] 安装本地 F5-TTS (editable) ...
call conda run --live-stream -n %ENV_NAME% python -m pip install -e "Local_model\F5-TTS"
if %errorlevel% neq 0 (
    echo [错误] F5-TTS 安装失败
    pause
    exit /b 1
)

echo.
echo [7/8] 安装 Qwen3-TTS Python 包（qwen-tts）...
call conda run --live-stream -n %ENV_NAME% python -m pip install --upgrade qwen-tts --extra-index-url %TORCH_INDEX%
if %errorlevel% neq 0 (
    echo [警告] 标准安装 qwen-tts 失败，尝试兼容安装（跳过 sox 构建）...
    call conda run --live-stream -n %ENV_NAME% python -m pip install --upgrade --no-deps qwen-tts
    if !errorlevel! neq 0 (
        echo [错误] qwen-tts 安装失败
        pause
        exit /b 1
    )
    call conda run --live-stream -n %ENV_NAME% python -m pip install --upgrade transformers==4.57.3 accelerate==1.12.0 gradio librosa soundfile
    if !errorlevel! neq 0 (
        echo [错误] qwen-tts 依赖补装失败
        pause
        exit /b 1
    )
)

echo.
echo [8/8] 可选预下载 Qwen3-TTS 模型（当前=%QWEN_SIZE%，默认 1.7B）...
if "%SKIP_MODEL%"=="1" (
    echo   已跳过模型下载（运行时会自动下载）
) else (
    if not exist "%QWEN_MODEL_DIR%" mkdir "%QWEN_MODEL_DIR%"
    call conda run --live-stream -n %ENV_NAME% python -c "from huggingface_hub import snapshot_download; snapshot_download(repo_id='%QWEN_MODEL%', local_dir=r'%QWEN_MODEL_DIR%', local_dir_use_symlinks=False)"
    if %errorlevel% neq 0 (
        echo [警告] 模型预下载失败，首次运行 qwentts 时将在线下载。
    ) else (
        echo   模型已下载到 %QWEN_MODEL_DIR% ✓
    )
)

echo.
echo [验证] Python 依赖检查 ...
call conda run --live-stream -n %ENV_NAME% python -c "import torch, qwen_tts; print('torch=', torch.__version__, 'cuda=', torch.cuda.is_available()); print('qwen_tts=', qwen_tts.__file__)"
if %errorlevel% neq 0 (
    echo [错误] 安装后验证失败
    pause
    exit /b 1
)

echo.
echo ==================================================
echo 安装完成 ✓
echo 启动 qwentts:
echo   conda run --live-stream -n %ENV_NAME% python Local_model\Fast_generating\start_server.py --tts_backend qwentts --device cuda --qwen_model %QWEN_MODEL_DIR%
echo
echo 启动 F5:
echo   conda run --live-stream -n %ENV_NAME% python Local_model\Fast_generating\start_server.py --tts_backend f5 --device cuda
echo ==================================================
echo.
pause

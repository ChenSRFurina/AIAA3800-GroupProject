@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion

echo.
echo ==================================================
echo Agentic Desktop Pet - Audio setup (conda + pip)
echo ==================================================
echo.

cd /d "%~dp0"
set "TORCH_INDEX=https://download.pytorch.org/whl/cu130"
if not "%VPET_TORCH_INDEX%"=="" set "TORCH_INDEX=%VPET_TORCH_INDEX%"

:: 1. Check Python
echo [1/6] 检查 Python ...
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo   [错误] 未找到 Python，请先安装 Python 3.11+
    echo   下载: https://www.python.org/downloads/
    pause
    exit /b 1
)
for /f "tokens=2" %%v in ('python --version 2^>^&1') do echo   Python %%v ✓
where python >nul 2>&1
if %errorlevel% equ 0 (
    for /f "delims=" %%p in ('where python') do (
        echo   python path: %%p
        goto :after_where_python
    )
)
:after_where_python
if defined CONDA_DEFAULT_ENV (
    echo   conda env: %CONDA_DEFAULT_ENV%
) else (
    echo   [提示] 当前未检测到 conda activate，建议先激活目标环境再运行本脚本。
)

:: 2. Check pip
echo [2/6] 检查 pip ...
python -m pip --version >nul 2>&1
if %errorlevel% neq 0 (
    echo   [错误] 当前 Python 缺少 pip，请先修复环境后重试。
    pause
    exit /b 1
)
for /f "delims=" %%v in ('python -m pip --version') do echo   %%v

:: 3. Install Python dependencies
echo [3/6] 安装 Python 依赖 (可能需要几分钟) ...
cd backend
python -m pip install --upgrade pip
echo   安装 PyTorch (Windows CUDA wheel index: %TORCH_INDEX%) ...
python -m pip install --upgrade torch --index-url %TORCH_INDEX%
if %errorlevel% neq 0 (
    echo   [错误] PyTorch 安装失败，请检查 CUDA wheel 源后重试。
    cd ..
    pause
    exit /b 1
)
python -c "import pathlib, subprocess, sys, tomllib; p=pathlib.Path('pyproject.toml'); data=tomllib.loads(p.read_text(encoding='utf-8')); deps=data.get('project', {}).get('dependencies', []); print('  dependencies =', len(deps)); subprocess.check_call([sys.executable, '-m', 'pip', 'install', *deps])"
if %errorlevel% neq 0 (
    echo   [错误] 依赖安装失败，请检查网络或镜像配置后重试。
    cd ..
    pause
    exit /b 1
)
echo   依赖安装完成 ✓
cd ..

:: 3.5 Repair broken click installation (seen on some Windows pip states)
echo [3.5/6] 检查 click 包完整性 ...
cd backend
python -c "import click,sys; sys.exit(0 if hasattr(click, 'command') else 1)" >nul 2>&1
if %errorlevel% neq 0 (
    echo   [修复] 检测到 click 包异常，强制重装 click==8.4.2 ...
    python -m pip install --upgrade --force-reinstall click==8.4.2
    if %errorlevel% neq 0 (
        echo   [错误] click 修复失败，请重试 setup.bat
        cd ..
        pause
        exit /b 1
    )
)

python -c "import h11,sys; sys.exit(0 if hasattr(h11, 'Request') else 1)" >nul 2>&1
if %errorlevel% neq 0 (
    echo   [修复] 检测到 h11/http 栈异常，重装 h11/httpcore/httpx/huggingface_hub ...
    python -m pip install --upgrade --force-reinstall h11==0.16.0 httpcore==1.0.9 httpx==0.28.1 huggingface_hub==1.24.0
    if %errorlevel% neq 0 (
        echo   [错误] h11/http 栈修复失败，请重试 setup.bat
        cd ..
        pause
        exit /b 1
    )
)

python -c "from transformers import pipeline; import PIL; import sys; sys.exit(0)" >nul 2>&1
if %errorlevel% neq 0 (
    echo   [修复] 检测到 transformers/PIL 导入异常，重装 pillow + transformers + accelerate ...
    python -m pip install --upgrade --force-reinstall pillow "transformers<5" accelerate
    if %errorlevel% neq 0 (
        echo   [错误] transformers/PIL 修复失败，请重试 setup.bat
        cd ..
        pause
        exit /b 1
    )
)
cd ..

:: 4. Download whisper model
echo [4/6] 下载 Whisper 语音模型 ...
cd backend
python -c "from audio import WhisperSTT, VoiceConfig; import os; cfg = VoiceConfig(); cfg.model_cache_dir = os.path.join('..', 'models'); cfg.whisper_backend = os.getenv('VPET_WHISPER_BACKEND', 'faster'); stt = WhisperSTT(cfg); stt._load(); print('Model ready')" 2>&1
if %errorlevel% neq 0 (
    echo   [警告] Whisper 模型下载失败
    echo   语音功能将不可用，文字聊天不受影响
)
cd ..

:: 5. Configure API key file
echo [5/6] 配置 DeepSeek API Key ...
cd backend
if not exist .env (
    copy .env.example .env >nul 2>&1
    echo   已创建 .env 文件，请编辑 backend\.env 填入你的 API Key
    echo   获取 Key: https://platform.deepseek.com/api_keys
) else (
    echo   .env 文件已存在 ✓
)
cd ..

:: 6. Check Godot
echo [6/6] 检查 Godot 引擎 ...
set "GODOT_FOUND=0"
for %%p in (
    "C:\Program Files\Godot\Godot_v4*.exe"
    "C:\Godot\Godot_v4*.exe"
    "%LOCALAPPDATA%\Godot\Godot_v4*.exe"
    "%USERPROFILE%\Godot\Godot_v4*.exe"
) do (
    if exist %%p (
        echo   Godot: %%p ✓
        set "GODOT_FOUND=1"
    )
)
if "!GODOT_FOUND!"=="0" (
    echo   [提示] 未找到 Godot 引擎，请安装 Godot 4.x
    echo   下载: https://godotengine.org/download/
)

echo.
echo ==================================================
echo 安装完成
echo 启动方式:
echo   1. 双击 run_backend.bat 启动后端
echo   2. 用 Godot 打开 godot\ 文件夹运行 F5
echo ==================================================
echo.
pause

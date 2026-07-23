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

:: 4. Preload STT backend/model
echo [4/6] 预热 STT 后端模型 ...
cd backend
python -c "import os; from audio import VoiceConfig; from whisper_stt_factory import create_whisper_stt; cfg=VoiceConfig(); cfg.model_cache_dir=os.path.join('..','models'); cfg.whisper_backend=os.getenv('VPET_WHISPER_BACKEND', cfg.whisper_backend); stt=create_whisper_stt(cfg); stt.preload(); print(f'STT ready: backend={cfg.whisper_backend}')" 2>&1
if %errorlevel% neq 0 (
    echo   [警告] STT 预热失败（可稍后运行时自动加载/回退）
)
cd ..

:: 5. Configure API key file
echo [5/6] 配置 DeepSeek API Key ...
if not exist ..\.env (
    copy ..\.env.example ..\.env >nul 2>&1
    echo   已创建根目录 .env 文件，请编辑 ..\.env 填入你的 API Key
    echo   获取 Key: https://platform.deepseek.com/api_keys
) else (
    echo   根目录 .env 文件已存在 ✓
)

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
echo   2. 默认 STT 后端为 qwen3；可用 VPET_WHISPER_BACKEND=torch^|faster 覆盖
echo   3. Qwen3-ASR 模型可用 VPET_QWEN3_ASR_MODEL 覆盖
echo   4. 用 Godot 打开 godot\ 文件夹运行 F5
echo ==================================================
echo.
pause

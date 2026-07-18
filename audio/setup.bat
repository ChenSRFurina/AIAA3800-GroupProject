@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion

echo.
echo ╔══════════════════════════════════════════════════╗
echo ║   Agentic Desktop Pet — 一键环境安装脚本         ║
echo ╚══════════════════════════════════════════════════╝
echo.

cd /d "%~dp0"

:: ── 1. 检查 Python ──────────────────────────────────────────
echo [1/6] 检查 Python ...
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo   [错误] 未找到 Python，请先安装 Python 3.13+
    echo   下载: https://www.python.org/downloads/
    pause
    exit /b 1
)
for /f "tokens=2" %%v in ('python --version 2^>^&1') do echo   Python %%v ✓

:: ── 2. 检查/安装 uv ────────────────────────────────────────
echo [2/6] 检查 uv 包管理器 ...
uv --version >nul 2>&1
if %errorlevel% neq 0 (
    echo   正在安装 uv ...
    powershell -ExecutionPolicy Bypass -Command "irm https://astral.sh/uv/install.ps1 | iex"
    set "PATH=%USERPROFILE%\.cargo\bin;%PATH%"
)
for /f "tokens=2" %%v in ('uv --version 2^>^&1') do echo   uv %%v ✓

:: ── 3. 安装 Python 依赖 ────────────────────────────────────
echo [3/6] 安装 Python 依赖 (可能需要几分钟) ...
cd backend
uv sync
if %errorlevel% neq 0 (
    echo   [警告] 部分依赖安装失败，语音功能可能不可用
)
echo   依赖安装完成 ✓
cd ..

:: ── 4. 下载 Whisper 模型 ───────────────────────────────────
echo [4/6] 下载 Whisper 语音模型 (~140MB) ...
cd backend
.\.venv\Scripts\python.exe -c "from audio import WhisperSTT, VoiceConfig; import os; cfg = VoiceConfig(); cfg.model_cache_dir = os.path.join('..', 'models'); stt = WhisperSTT(cfg); stt._load(); print('Model ready')" 2>&1
if %errorlevel% neq 0 (
    echo   [警告] Whisper 模型下载失败
    echo   语音功能将不可用，文字聊天不受影响
)
cd ..

:: ── 5. 配置 API Key ───────────────────────────────────────
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

:: ── 6. 检查 Godot ──────────────────────────────────────────
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
echo ╔══════════════════════════════════════════════════╗
echo ║   安装完成！                                      ║
echo ╠══════════════════════════════════════════════════╣
echo ║  启动方式:                                        ║
echo ║   1. 双击 run_backend.bat  启动后端              ║
echo ║   2. 用 Godot 打开 godot\ 文件夹运行 F5         ║
echo ╚══════════════════════════════════════════════════╝
echo.
pause

@echo off
chcp 65001 >nul
cd /d "%~dp0\backend"

echo.
echo ╔══════════════════════════════════════════════════╗
echo ║   Agentic Desktop Pet — 后端服务                 ║
echo ╚══════════════════════════════════════════════════╝
echo.
echo   文字聊天: http://127.0.0.1:8010/chat
echo   语音消息: http://127.0.0.1:8010/voice/messages
echo   按 Ctrl+C 停止
echo.

uv run main.py

pause

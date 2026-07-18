import asyncio
import json
from collections import deque
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse
from fastapi.middleware.cors import CORSMiddleware

from pathlib import Path
import os

from dotenv import load_dotenv

# 统一从 VPet/.env 读密钥（backend -> audio -> VPet）
_VPET_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(_VPET_ROOT / ".env", override=True)

from learn_agent.agent.agent import Agent
from learn_agent.llm import DeepSeek
from learn_agent.memory import Memory
from learn_agent.tool.weather_tool import WeatherTool
from learn_agent.tool.file_tool import FileTool
from learn_agent.tool.todo_tool import TodoTool
import uvicorn

# 语音模块 (可选)
try:
    from audio import VoiceAssistant, VoiceConfig, print_status
    _VOICE_AVAILABLE = True
except ImportError:
    _VOICE_AVAILABLE = False

# 全局 Agent 实例
agent_instance: Agent | None = None

# 语音助手实例
voice_assistant: "VoiceAssistant | None" = None

# 语音消息队列 (供 Godot 轮询)
voice_messages: deque[dict] = deque(maxlen=100)


def _on_voice_response(text: str) -> None:
    """语音助手回复回调 — 将消息放入队列供 Godot 轮询。"""
    voice_messages.append({
        "type": "assistant",
        "content": text,
        "source": "voice",
    })


def _on_voice_transcript(text: str) -> None:
    """语音转写回调 — 用户说的话。"""
    voice_messages.append({
        "type": "user_message",
        "content": text,
        "source": "voice",
    })


@asynccontextmanager
async def lifespan(app: FastAPI):
    global agent_instance, voice_assistant

    api_key = (os.getenv("DEEPSEEK_API_KEY") or "").strip().strip("'\"")
    if not api_key:
        raise RuntimeError(
            "缺少 DEEPSEEK_API_KEY。\n"
            f"请编辑 {_VPET_ROOT / '.env'} ，填写:\n"
            "  DEEPSEEK_API_KEY=sk-xxxxxxxx\n"
            "获取: https://platform.deepseek.com/api_keys"
        )

    agent_instance = Agent(
        session_id="web-session",
        name="web-assistant",
        system_prompt=(
            "你是用户的桌面宠物助手，说话简洁友好。"
            "你有文件操作能力：可以读文件、写文件、编辑文件、执行简单命令。"
            "所有文件操作在 work_dir 目录内进行。"
            "当用户要求创建/读取/修改文件时，直接调用工具执行，不要说做不到。"
        ),
        llm=DeepSeek(api_key=api_key, model="deepseek-chat"),
        tools=[
            WeatherTool(),
            FileTool(work_dir=Path.cwd() / "work_dir"),
            TodoTool(),
        ],
        memory=Memory(),
    )

    # 启动语音助手 (可选)
    if _VOICE_AVAILABLE:
        print("\n" + "=" * 50)
        print_status()
        print("=" * 50 + "\n")

        cfg = VoiceConfig()
        # 使用 Agent 而非本地 Qwen (无需下载 GGUF 模型)
        voice_assistant = VoiceAssistant(
            config=cfg,
            on_response=_on_voice_response,
            on_transcript=_on_voice_transcript,
            use_agent=True,  # 使用 DeepSeek Agent 生成回复
        )
        voice_assistant.agent = agent_instance
        voice_assistant.start()
        print("[Main] 语音助手已启动 (Agent 模式)")
    else:
        print("[Main] 语音模块未加载，跳过语音功能")

    print("Starting Agentic-Desktop-Pet")
    yield

    # 清理
    if voice_assistant:
        voice_assistant.stop()
    print("Stopping Agentic-Desktop-Pet")


app = FastAPI(
    title="Agentic-Desktop-Pet",
    description="Agentic-Desktop-Pet",
    version="0.1.0",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"],  # 暴露所有响应头
)

@app.get("/health")
async def health_check():
    """健康检查"""
    return {"status": "Online"}


def format_sse_event(data: dict) -> str:
    """将数据格式化为 SSE 格式"""
    try:
        print("SSE Event:", data)
    except UnicodeEncodeError:
        # Windows GBK console can't print emoji — silently skip
        pass
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


async def stream_generator(user_input: str) -> AsyncGenerator[str, None]:
    """流式生成器"""
    if agent_instance is None:
        yield format_sse_event({"type": "error", "message": "Agent 未初始化"})
        return
    try:
        for event in agent_instance.run_stream(user_input):
            yield format_sse_event(event)
    except Exception as exc:
        yield format_sse_event({"type": "error", "message": str(exc)})


@app.post("/chat")
async def chat(request: Request):
    """
    流式对话接口

    请求体:
    {
        "message": "用户输入"
    }

    响应: SSE 流
    """
    body = await request.json()
    user_input = body.get("message", "")

    if not user_input:
        headers = {"Cache-Control": "no-cache", "Connection": "keep-alive"}
        return StreamingResponse(
            iter([format_sse_event({"type": "error", "message": "消息不能为空"})]),
            media_type="text/event-stream",
            headers=headers,
        )

    headers = {"Cache-Control": "no-cache", "Connection": "keep-alive"}
    return StreamingResponse(
        stream_generator(user_input),
        media_type="text/event-stream",
        headers=headers,
    )


@app.post("/chat/reply")
async def chat_reply(request: Request):
    """
    同步对话接口（供 VPet-Speaking 等客户端取完整回复再做 TTS）。

    请求体: {"message": "用户输入"}
    响应: {"ok": true, "reply": "...", "message": "..."}
    """
    body = await request.json()
    user_input = (body.get("message") or "").strip()
    if not user_input:
        return {"ok": False, "error": "消息不能为空", "reply": ""}

    if agent_instance is None:
        return {"ok": False, "error": "Agent 未初始化", "reply": ""}

    try:
        # agent.run 为同步阻塞调用，放到线程池避免卡住事件循环
        reply = await asyncio.to_thread(agent_instance.run, user_input)
        reply_text = (reply or "").strip()
        return {
            "ok": True,
            "message": user_input,
            "reply": reply_text,
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc), "reply": ""}


# ── 语音相关接口 ────────────────────────────────────────────────────────

@app.get("/voice/status")
async def voice_status():
    """获取语音助手状态。"""
    if not _VOICE_AVAILABLE or voice_assistant is None:
        return {
            "available": False,
            "voice_mode": False,
            "running": False,
        }
    return {
        "available": True,
        "voice_mode": voice_assistant.voice_mode,
        "running": voice_assistant._running,
    }


@app.get("/voice/messages")
async def voice_get_messages():
    """
    轮询获取语音消息。
    Godot 定期调用此接口，获取通过语音产生的新消息。
    """
    msgs = list(voice_messages)
    voice_messages.clear()
    return {"messages": msgs}


@app.post("/voice/toggle")
async def voice_toggle(request: Request):
    """
    手动切换语音模式 (也可以通过语音指令切换)。
    请求体: {"enable": true/false}
    """
    if not _VOICE_AVAILABLE or voice_assistant is None:
        return {"ok": False, "error": "语音模块未加载"}

    body = await request.json()
    enable = body.get("enable", None)
    if enable is None:
        return {"ok": False, "error": "缺少 enable 字段"}

    voice_assistant.voice_mode = bool(enable)
    return {"ok": True, "voice_mode": voice_assistant.voice_mode}


if __name__ == "__main__":
    # 默认 8010，避免与 face-detect-local:8000 冲突；可用 AUDIO_PORT 覆盖
    port = int(os.getenv("AUDIO_PORT", "8010"))
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=port,
        log_level="info"
    )

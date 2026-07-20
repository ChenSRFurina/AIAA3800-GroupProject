import asyncio
import json
import random
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
from learn_agent.character_setting import (
    DEFAULT_MEMORY_DIR,
    LayeredMemoryManager,
    PersonaAwareResponder,
    PersonaConfig,
)
from learn_agent.tool.weather_tool import WeatherTool
from learn_agent.tool.file_tool import FileTool
from learn_agent.tool.todo_tool import TodoTool
from care_prompts import (
    FALLBACK_LINES,
    SCENE_USER_PROMPTS,
    build_care_messages,
    sanitize_care_reply,
)
import uvicorn

# 语音模块 (可选)
try:
    from audio import VoiceAssistant, VoiceConfig, print_status
    _VOICE_AVAILABLE = True
except ImportError:
    _VOICE_AVAILABLE = False

# 全局 Agent 实例
agent_instance: Agent | None = None

# 长期记忆（持久化到 audio/backend/memory）
memory_manager: LayeredMemoryManager | None = None
persona_responder = PersonaAwareResponder()
persona_config = PersonaConfig()
DEFAULT_MEMORY_USER = "default"

# 语音助手实例
voice_assistant: "VoiceAssistant | None" = None

# 语音消息队列 (供 Godot 轮询)
voice_messages: deque[dict] = deque(maxlen=100)


# 文件工具能力（必须始终内嵌在 system prompt 中，勿被人格段覆盖）
FILE_TOOLS_PROMPT = (
    "你有文件操作能力：可以读文件、写文件、编辑文件、执行简单命令。"
    "所有文件操作在 work_dir 目录内进行。"
    "当用户要求创建/读取/修改文件时，直接调用工具执行，不要说做不到。"
)


def _base_system_prompt() -> str:
    return (
        "你是用户的桌面宠物助手，说话简洁友好。\n"
        f"{FILE_TOOLS_PROMPT}"
    )


def _refresh_system_prompt(user_text: str = "") -> None:
    """把人设、文件工具能力、长期记忆合并进 Agent 的 system 消息。"""
    if agent_instance is None or memory_manager is None:
        return

    memory_text = memory_manager.format_for_prompt(
        DEFAULT_MEMORY_USER, topic=user_text, limit=5
    )
    full_prompt = persona_responder.build_system_prompt(
        persona_config,
        memory_text=memory_text,
        tools_prompt=FILE_TOOLS_PROMPT,
    )

    agent_instance.system_prompt = full_prompt
    ctx = agent_instance.memory.get_context()
    if ctx and ctx[0].get("role") == "system":
        ctx[0]["content"] = full_prompt
    else:
        agent_instance.memory.messages.insert(
            0, {"role": "system", "content": full_prompt}
        )


def _after_user_turn(user_text: str) -> None:
    """普通对话回合：只刷新 system prompt，不落盘记忆。"""
    if not (user_text or "").strip():
        return
    _refresh_system_prompt(user_text)


def _on_voice_response(text: str) -> None:
    """语音助手回复回调 — 将消息放入队列供 Godot 轮询。"""
    voice_messages.append({
        "type": "assistant",
        "content": text,
        "source": "voice",
    })


def _on_voice_transcript(text: str) -> None:
    """语音转写回调 — 仅此处写入长期记忆（用户原话）。"""
    cleaned = (text or "").strip()
    voice_messages.append({
        "type": "user_message",
        "content": cleaned,
        "source": "voice",
    })

    if memory_manager is None or not cleaned:
        return
    entry = memory_manager.remember_voice_utterance(
        DEFAULT_MEMORY_USER,
        cleaned,
        source_conversation_id="voice-asr",
    )
    if entry:
        print(
            f"[Memory] 语音用户话已记录: [{entry.category}/{entry.importance.name}] "
            f"{entry.content}"
        )
        _refresh_system_prompt(cleaned)


@asynccontextmanager
async def lifespan(app: FastAPI):
    global agent_instance, voice_assistant, memory_manager

    api_key = (os.getenv("DEEPSEEK_API_KEY") or "").strip().strip("'\"")
    if not api_key:
        raise RuntimeError(
            "缺少 DEEPSEEK_API_KEY。\n"
            f"请编辑 {_VPET_ROOT / '.env'} ，填写:\n"
            "  DEEPSEEK_API_KEY=sk-xxxxxxxx\n"
            "获取: https://platform.deepseek.com/api_keys"
        )

    # 固定写入 VPet/audio/backend/memory/{user_id}.json（绝对路径）
    memory_dir = DEFAULT_MEMORY_DIR
    memory_dir.mkdir(parents=True, exist_ok=True)
    memory_manager = LayeredMemoryManager(storage_dir=memory_dir)
    print(f"[Memory] 长期记忆目录: {memory_dir}")
    print(f"[Memory] 默认文件: {memory_dir / (DEFAULT_MEMORY_USER + '.json')}")

    agent_instance = Agent(
        session_id="web-session",
        name="web-assistant",
        system_prompt=_base_system_prompt(),
        llm=DeepSeek(api_key=api_key, model="deepseek-chat"),
        tools=[
            WeatherTool(),
            FileTool(work_dir=Path.cwd() / "work_dir"),
            TodoTool(),
        ],
        memory=Memory(),
    )
    _refresh_system_prompt()

    # 包装 run / run_stream：只注入已有记忆到 prompt；落盘仅语音转写回调
    _orig_run = agent_instance.run
    _orig_stream = agent_instance.run_stream

    def _run_with_memory(user_text: str) -> str:
        _after_user_turn(user_text)
        return _orig_run(user_text)

    def _stream_with_memory(user_text: str):
        _after_user_turn(user_text)
        yield from _orig_stream(user_text)

    agent_instance.run = _run_with_memory  # type: ignore[method-assign]
    agent_instance.run_stream = _stream_with_memory  # type: ignore[method-assign]

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


@app.post("/chat/care")
async def chat_care(request: Request):
    """
    桌宠情绪陪伴（供 VPet-FaceDetect）：
    不走工具 Agent、不写长期记忆；可注入已有语音记忆上下文。

    请求体: {"scene":"happy|sad|surprise|fear|disgust|anger|fatigue", "hint":"..."}
    响应: {"ok": true, "scene": "...", "reply": "..."}
    """
    body = await request.json()
    scene = (body.get("scene") or "").strip().lower()
    hint = (body.get("hint") or "").strip()

    if scene not in SCENE_USER_PROMPTS:
        return {
            "ok": False,
            "error": f"scene 需为 {sorted(SCENE_USER_PROMPTS)} 之一",
            "reply": "",
            "scene": scene,
        }

    def _fallback() -> str:
        lines = FALLBACK_LINES.get(scene) or ["我在这儿陪着你。"]
        return random.choice(lines)

    memory_text = ""
    if memory_manager is not None:
        try:
            memory_text = memory_manager.format_for_prompt(
                DEFAULT_MEMORY_USER,
                topic=hint or scene,
                limit=5,
            )
        except Exception as mem_exc:
            print(f"[Memory] care context skip: {mem_exc}")

    if agent_instance is None or agent_instance.llm is None:
        return {
            "ok": True,
            "scene": scene,
            "reply": _fallback(),
            "fallback": True,
            "error": "Agent 未初始化，已用本地台词",
            "memory_used": bool(memory_text and memory_text != "暂无历史信息"),
        }

    try:
        messages = build_care_messages(
            scene,
            hint=hint,
            memory_text=memory_text,
        )

        def _call() -> str:
            llm = agent_instance.llm
            old_max = getattr(llm, "max_tokens", None)
            old_temp = getattr(llm, "temperature", None)
            try:
                llm.max_tokens = 64
                llm.temperature = 0.6
                msg = llm.chat(messages=messages, tools=None)
            finally:
                llm.max_tokens = old_max
                llm.temperature = old_temp
            return getattr(msg, "content", None) or ""

        raw = await asyncio.to_thread(_call)
        reply = sanitize_care_reply(raw)
        mem_used = bool(memory_text and memory_text != "暂无历史信息")
        if not reply:
            reply = _fallback()
            return {
                "ok": True,
                "scene": scene,
                "reply": reply,
                "fallback": True,
                "raw_rejected": True,
                "memory_used": mem_used,
            }
        return {
            "ok": True,
            "scene": scene,
            "reply": reply,
            "fallback": False,
            "memory_used": mem_used,
        }
    except Exception as exc:
        return {
            "ok": True,
            "scene": scene,
            "reply": _fallback(),
            "fallback": True,
            "error": str(exc),
        }


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

"""
语音控制模块 — Agentic Desktop Pet

用户可通过说"开启语音功能"/"关闭语音功能"来控制桌宠是否对语音内容进行回复。

流程:  麦克风录音 → WAV 音频 → Whisper 离线语音转文本 → Qwen 量化模型 → 文字回复
"""

import os
import time
import atexit
import threading
from collections import deque
from pathlib import Path
from typing import Callable, Optional

import numpy as np
from transcript_utils import collapse_repetitive_transcript
from wav_utils import merge_wav_chunks, numpy_to_wav
from whisper_stt_base import BaseWhisperSTT
from whisper_stt_factory import create_whisper_stt
from whisper_stt_faster import faster_whisper_available
from whisper_stt_torch import transformers_whisper_available

# ── 可选依赖 (运行时按需加载) ──────────────────────────────────────────
_SOUNDDEVICE_AVAILABLE = True
try:
    import sounddevice as sd
except ImportError:
    _SOUNDDEVICE_AVAILABLE = False
    sd = None

_FASTER_WHISPER_AVAILABLE = faster_whisper_available()
_TRANSFORMERS_AVAILABLE = transformers_whisper_available()

from learn_agent.llm import GenerationCancelledError

_LLAMA_CPP_AVAILABLE = True
try:
    from llama_cpp import Llama
except ImportError:
    _LLAMA_CPP_AVAILABLE = False
    Llama = None


# ╔══════════════════════════════════════════════════════════════════════════════╗
# ║                            ◆  配  置  ◆                                      ║
# ╚══════════════════════════════════════════════════════════════════════════════╝

class VoiceConfig:
    """语音功能全局配置。"""

    # ── 音频 ──
    sample_rate: int = 16000          # 采样率 (Hz)
    channels: int = 1                 # 单声道
    chunk_duration: float = 0.3       # 每个音频块时长 (秒)
    silence_threshold: float = 0.015  # 静音 RMS 阈值 (0~1)
    silence_sec: float = 0.6          # 连续静音多少秒后停止录音
    max_record_sec: float = 30.0      # 单次录音最长时长
    reply_settle_sec: float = 0.3    # 停顿后短暂等待，随后立即开始转写/回复

    # ── Whisper ──
    whisper_model: str = "small"       # tiny / base / small / medium / large
    whisper_backend: str = "torch"   # faster | torch | auto
    whisper_device: str = "auto"      # auto | cpu | cuda
    whisper_compute: str = "auto"     # auto | int8 (CPU) | float16 (GPU)
    whisper_allowed_languages: str = "zh,en"  # 仅允许识别这些语种（逗号分隔）
    whisper_language_mode: str = "whitelist"  # whitelist | force
    whisper_force_language: str = ""           # force 模式下使用，留空则按 allowed 第一项
    whisper_preferred_language: str = "zh"    # 白名单模式下默认更偏向中文
    whisper_use_initial_prompt: bool = True     # faster-whisper 使用 initial_prompt（比 prompt_ids 更稳）
    whisper_use_prompt_ids: bool = False        # 不使用 prompt_ids，统一使用 initial_prompt
    whisper_initial_prompt: str = "请准确转写语音内容，内容可能为中文、英文或中英混杂。"
    whisper_hf_mirror: str = "https://hf-mirror.com"  # torch Whisper 下载镜像

    # ── Qwen GGUF ──
    qwen_model_path: str = ""          # Qwen GGUF 文件路径 (留空则使用 Agent)
    qwen_n_ctx: int = 2048
    qwen_max_tokens: int = 256
    qwen_temperature: float = 0.7

    # ── 其它 ──
    model_cache_dir: str = ""  # 默认在 _resolve_model_dir() 中自动设置


# ╔══════════════════════════════════════════════════════════════════════════════╗
# ║                          ◆  录 音 器  ◆                                      ║
# ╚══════════════════════════════════════════════════════════════════════════════╝

class AudioRecorder:
    """麦克风录音，静音自动停止，返回 WAV 字节。"""

    def __init__(self, config: VoiceConfig):
        self.cfg = config
        if not _SOUNDDEVICE_AVAILABLE:
            raise ImportError("请安装 sounddevice: pip install sounddevice")
        # 列出可用设备供调试
        self._list_devices()

    @staticmethod
    def _list_devices() -> None:
        try:
            devices = sd.query_devices()
            print("[AudioRecorder] 可用音频设备:")
            for i, d in enumerate(devices):
                in_flag = "← 输入" if d["max_input_channels"] > 0 else ""
                print(f"  [{i}] {d['name']}  {in_flag}")
            default_in = sd.default.device[0]
            if default_in is not None:
                print(f"  默认输入设备: [{default_in}] {devices[default_in]['name']}")
        except Exception:
            pass

    def record(self) -> Optional[bytes]:
        """录制一段语音，静音自动停止，返回 WAV 字节。没有有效语音则返回 None。"""
        if not _SOUNDDEVICE_AVAILABLE:
            print("[AudioRecorder] sounddevice 未安装")
            return None

        frames: list = []
        silent_chunks = 0
        max_silent = int(self.cfg.silence_sec / self.cfg.chunk_duration)
        max_chunks = int(self.cfg.max_record_sec / self.cfg.chunk_duration)
        block = int(self.cfg.sample_rate * self.cfg.chunk_duration)

        # 预录音缓冲：开始说话前可能有短暂静音
        pre_buffer: list = []
        pre_buffer_max = 5

        def callback(indata, _frames, _time_info, status):
            if status:
                print(f"[AudioRecorder] 警告: {status}")
            frames.append(indata.copy())

        started_speaking = False
        try:
            with sd.InputStream(
                samplerate=self.cfg.sample_rate,
                channels=self.cfg.channels,
                callback=callback,
                blocksize=block,
                dtype="float32",
            ):
                while len(frames) < max_chunks:
                    time.sleep(self.cfg.chunk_duration)

                    if not frames:
                        continue

                    rms = float(np.sqrt(np.mean(frames[-1] ** 2)))

                    if rms < self.cfg.silence_threshold:
                        if started_speaking:
                            silent_chunks += 1
                        else:
                            # 还没开始说话，只保留预缓冲
                            pre_buffer.append(frames[-1])
                            if len(pre_buffer) > pre_buffer_max:
                                pre_buffer.pop(0)
                    else:
                        if not started_speaking:
                            started_speaking = True
                        silent_chunks = 0

                    if started_speaking and silent_chunks >= max_silent:
                        break  # 静音足够久，停止录音

        except sd.PortAudioError as e:
            print(f"[AudioRecorder] 设备错误: {e}")
            return None
        except Exception as e:
            print(f"[AudioRecorder] 录音异常: {e}")
            return None

        # 丢弃末尾的静音帧
        if silent_chunks > 0:
            frames = frames[:-silent_chunks]

        if not started_speaking or len(frames) < 3:
            return None

        audio = np.concatenate(frames, axis=0)
        return numpy_to_wav(audio, self.cfg.sample_rate, self.cfg.channels)

    def try_record_followup(self, max_record_sec: float | None = None) -> Optional[bytes]:
        """在回复前短暂补听：若用户继续说，则并入同一轮输入。"""
        original_max = self.cfg.max_record_sec
        try:
            if max_record_sec is not None:
                self.cfg.max_record_sec = max_record_sec
            return self.record()
        finally:
            self.cfg.max_record_sec = original_max


# ╔══════════════════════════════════════════════════════════════════════════════╗
# ║                  ◆  量 化 大 模 型  (Qwen GGUF)  ◆                           ║
# ╚══════════════════════════════════════════════════════════════════════════════╝

class QwenLLM:
    """本地 Qwen 量化模型推理 — 基于 llama.cpp (GGUF)。"""

    SYSTEM_PROMPT = (
        "你是用户的桌面宠物助手，一个可爱、贴心的小伙伴。"
        "请用简洁、口语化、友好的中文回复，控制在2~4句话以内。"
        "适当使用语气词和表情符号让对话更生动。"
    )

    def __init__(self, config: VoiceConfig):
        self.cfg = config
        self._model = None

    def _load(self) -> None:
        if self._model is not None:
            return
        if not _LLAMA_CPP_AVAILABLE:
            raise ImportError("请安装 llama-cpp-python: pip install llama-cpp-python")

        path = self.cfg.qwen_model_path
        if not path:
            raise FileNotFoundError(
                "未设置 Qwen GGUF 模型路径。请下载模型并设置 VoiceConfig.qwen_model_path\n"
                "推荐: https://huggingface.co/Qwen/Qwen2.5-1.5B-Instruct-GGUF\n"
                "下载 qwen2.5-1.5b-instruct-q4_k_m.gguf"
            )
        if not Path(path).exists():
            raise FileNotFoundError(f"Qwen GGUF 模型不存在: {path}")

        print(f"[QwenLLM] 加载模型 {Path(path).name} …")
        self._model = Llama(
            model_path=path,
            n_ctx=self.cfg.qwen_n_ctx,
            n_threads=os.cpu_count() or 4,
            verbose=False,
        )
        print("[QwenLLM] 模型就绪 ✓")

    def generate(self, prompt: str) -> str:
        """用 Qwen 模型生成回复文字。"""
        self._load()
        response = self._model.create_chat_completion(
            messages=[
                {"role": "system", "content": self.SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            max_tokens=self.cfg.qwen_max_tokens,
            temperature=self.cfg.qwen_temperature,
        )
        return response["choices"][0]["message"]["content"].strip()


# ╔══════════════════════════════════════════════════════════════════════════════╗
# ║                    ◆  语 音 助 手 总 控  ◆                                   ║
# ╚══════════════════════════════════════════════════════════════════════════════╝

class VoiceAssistant:
    """
    语音助手总控。

    用法::

        assistant = VoiceAssistant(on_response=my_callback)
        assistant.start()   # 后台开始监听
        ...
        assistant.stop()    # 停止监听

    - 说 "开启语音功能" → voice_mode = True  , 之后每次说话都会触发 LLM 回复
    - 说 "关闭语音功能" → voice_mode = False , 停止回复 (仍在监听开关指令)
    """

    WAKE_ON  = "开启语音功能"
    WAKE_OFF = "关闭语音功能"

    def __init__(
        self,
        config: Optional[VoiceConfig] = None,
        on_response: Optional[Callable[[str], None]] = None,
        on_transcript: Optional[Callable[[str], None]] = None,
        use_agent: bool = False,
    ):
        self.cfg = config or VoiceConfig()
        self._on_response = on_response     # LLM 回复回调
        self._on_transcript = on_transcript # 转写文本回调 (调试用)
        self._use_agent = use_agent         # True=使用外部 Agent, False=使用本地 Qwen

        # 状态
        self.voice_mode: bool = True    # 默认开启语音回复
        self._running: bool = False
        self._thread: Optional[threading.Thread] = None
        self._reply_thread: Optional[threading.Thread] = None
        self._pending_audio: deque[bytes] = deque()
        self._pending_deadline: float = 0.0
        self._pending_version: int = 0
        self._pending_lock = threading.Lock()
        self._reply_wakeup = threading.Event()
        self._active_stt_cancel_event: threading.Event | None = None
        self._active_cancel_event: threading.Event | None = None

        # 组件 (延迟加载)
        self.recorder: Optional[AudioRecorder] = None
        self.stt: Optional[BaseWhisperSTT] = None
        self.llm: Optional[QwenLLM] = None
        self.agent = None  # 外部 Agent 引用 (当 use_agent=True 时设置)

        atexit.register(self.stop)

    # ── 生命周期 ────────────────────────────────────────────────────────

    def start(self) -> None:
        """启动后台监听线程。"""
        if self._running:
            return

        # 初始化组件
        self.recorder = AudioRecorder(self.cfg)
        self.stt = create_whisper_stt(self.cfg)
        if not self._use_agent:
            self.llm = QwenLLM(self.cfg)

        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True, name="voice-thread")
        self._reply_thread = threading.Thread(target=self._reply_loop, daemon=True, name="voice-reply-thread")
        self._thread.start()
        self._reply_thread.start()
        print("[VoiceAssistant] Started - listening in background...")
        print(f"  Say '{self.WAKE_ON}' to enable voice replies")
        print(f"  Say '{self.WAKE_OFF}' to disable voice replies")

    def stop(self) -> None:
        """停止后台监听线程。"""
        self._running = False
        self._reply_wakeup.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3.0)
        if self._reply_thread and self._reply_thread.is_alive():
            self._reply_thread.join(timeout=3.0)
        print("[VoiceAssistant] Stopped")

    # ── 主循环 ──────────────────────────────────────────────────────────

    def _loop(self) -> None:
        while self._running:
            try:
                wav = self.recorder.record()
                if wav is None:
                    continue
                self._queue_pending_audio(wav)

            except Exception as exc:
                print(f"[VoiceAssistant] Error: {exc}")
                time.sleep(0.5)

    def _reply_loop(self) -> None:
        while self._running:
            payload = self._take_ready_payload()
            if payload is None:
                self._reply_wakeup.wait(timeout=0.2)
                self._reply_wakeup.clear()
                continue

            version, wav = payload
            if not wav:
                continue

            stt_cancel_event = threading.Event()
            with self._pending_lock:
                self._active_stt_cancel_event = stt_cancel_event

            text = self.stt.transcribe(wav, cancel_event=stt_cancel_event)

            with self._pending_lock:
                if self._active_stt_cancel_event is stt_cancel_event:
                    self._active_stt_cancel_event = None

            if stt_cancel_event.is_set():
                if self._requeue_interrupted_audio(wav, version):
                    print("[VoiceAssistant] Cancel transcription and merge interrupted audio with supplemental speech")
                else:
                    print("[VoiceAssistant] Drop cancelled transcription")
                continue

            with self._pending_lock:
                if version != self._pending_version:
                    self._requeue_interrupted_audio(wav, version)
                    print("[VoiceAssistant] Drop stale transcription due to newer speech")
                    continue

            if not text:
                continue

            text = collapse_repetitive_transcript(text)
            if not text:
                continue

            print(f"[VoiceAssistant] Heard: '{text}'")

            if self.WAKE_OFF in text:
                self.voice_mode = False
                self._clear_pending_inputs()
                print("[VoiceAssistant] Voice mode -> OFF")
                self._emit("语音功能已关闭")
                continue

            if self.WAKE_ON in text:
                self.voice_mode = True
                self._clear_pending_inputs()
                print("[VoiceAssistant] Voice mode -> ON")
                self._emit("语音功能已开启！我在听~")
                continue

            if self._on_transcript:
                try:
                    self._on_transcript(text)
                except Exception:
                    pass

            if not self.voice_mode:
                continue

            cancel_event = threading.Event()
            with self._pending_lock:
                self._active_cancel_event = cancel_event

            reply, reply_cancelled = self._generate_reply(text, cancel_event=cancel_event)

            with self._pending_lock:
                if self._active_cancel_event is cancel_event:
                    self._active_cancel_event = None

            if reply_cancelled:
                if self._requeue_interrupted_audio(wav, version):
                    print("[VoiceAssistant] Agent reply cancelled, merge current+supplemental audio and retry")
                continue

            if not reply:
                continue

            with self._pending_lock:
                if version != self._pending_version:
                    print("[VoiceAssistant] Drop stale reply due to newer speech")
                    continue

            print(f"[VoiceAssistant] Reply: '{reply}'")
            self._emit(reply)

    def _queue_pending_audio(self, wav: bytes) -> None:
        if not wav:
            return
        with self._pending_lock:
            if self._active_stt_cancel_event is not None:
                self._active_stt_cancel_event.set()
            if self._active_cancel_event is not None:
                self._active_cancel_event.set()
            self._pending_audio.append(wav)
            self._pending_version += 1
            self._pending_deadline = time.time() + max(0.1, self.cfg.reply_settle_sec)
        self._reply_wakeup.set()

    def _clear_pending_inputs(self) -> None:
        with self._pending_lock:
            if self._active_stt_cancel_event is not None:
                self._active_stt_cancel_event.set()
            if self._active_cancel_event is not None:
                self._active_cancel_event.set()
            self._pending_audio.clear()
            self._pending_deadline = 0.0
            self._pending_version += 1
        self._reply_wakeup.set()

    def _requeue_interrupted_audio(self, wav: bytes, base_version: int) -> bool:
        """仅在检测到新语音到来时，将当前回合音频回插到队列头并累计重试。"""
        with self._pending_lock:
            # 只有存在更新版本（新语音）时才累计，避免无意义重复。
            if base_version == self._pending_version or not self._pending_audio:
                return False

            self._pending_audio.appendleft(wav)
            if self._pending_deadline <= 0:
                self._pending_deadline = time.time() + max(0.1, self.cfg.reply_settle_sec)

        self._reply_wakeup.set()
        return True

    def _take_ready_payload(self) -> tuple[int, bytes] | None:
        with self._pending_lock:
            if not self._pending_audio:
                return None

            now = time.time()
            if now < self._pending_deadline:
                return None

            pending = list(self._pending_audio)
            version = self._pending_version
            self._pending_audio.clear()
            self._pending_deadline = 0.0
        merged_wav = merge_wav_chunks(
            pending,
            self.cfg.sample_rate,
            self.cfg.channels,
        )
        return version, merged_wav

    # ── 内部方法 ────────────────────────────────────────────────────────

    def _generate_reply(self, text: str, cancel_event: threading.Event | None = None) -> tuple[str, bool]:
        """调用 LLM (本地 Qwen 或外部 Agent) 生成回复。"""
        if self._use_agent and self.agent:
            try:
                return self.agent.run(text, cancel_event=cancel_event), False
            except GenerationCancelledError:
                print("[VoiceAssistant] Agent reply cancelled")
                return "", True
            except Exception as e:
                return f"[Agent 错误] {e}", False

        if self.llm:
            return self.llm.generate(text), False

        return "语音助手未配置语言模型", False

    def _emit(self, msg: str) -> None:
        """触发回复回调 (安全调用，忽略回调中的异常)。"""
        if self._on_response:
            try:
                self._on_response(msg)
            except Exception:
                pass


# ╔══════════════════════════════════════════════════════════════════════════════╗
# ║                        ◆  便 捷 工 厂 函 数  ◆                               ║
# ╚══════════════════════════════════════════════════════════════════════════════╝

def create_assistant(
    qwen_model_path: str = "",
    whisper_model: str = "base",
    on_response: Optional[Callable[[str], None]] = None,
    on_transcript: Optional[Callable[[str], None]] = None,
    use_agent: bool = False,
) -> VoiceAssistant:
    """
    快速创建 VoiceAssistant。

    Args:
        qwen_model_path: Qwen GGUF 模型路径 (use_agent=False 时必需)
            推荐: Qwen2.5-1.5B-Instruct-GGUF (q4_k_m 量化版)
            下载: https://huggingface.co/Qwen/Qwen2.5-1.5B-Instruct-GGUF
        whisper_model: Whisper 模型大小 (tiny/base/small/medium/large)
        on_response: LLM 回复回调 fn(text: str)
        on_transcript: 语音转写回调 fn(text: str)
        use_agent: True = 使用外部 Agent | False = 使用本地 Qwen

    Returns:
        VoiceAssistant 实例
    """
    cfg = VoiceConfig()
    cfg.whisper_model = whisper_model
    if qwen_model_path:
        cfg.qwen_model_path = qwen_model_path

    return VoiceAssistant(
        config=cfg,
        on_response=on_response,
        on_transcript=on_transcript,
        use_agent=use_agent,
    )


# ╔══════════════════════════════════════════════════════════════════════════════╗
# ║                        ◆  依 赖 检 查  ◆                                    ║
# ╚══════════════════════════════════════════════════════════════════════════════╝

def check_dependencies() -> dict:
    """检查语音功能所需的依赖是否就绪。返回各组件状态。"""
    status = {
        "sounddevice": _SOUNDDEVICE_AVAILABLE,
        "faster_whisper": _FASTER_WHISPER_AVAILABLE,
        "transformers": _TRANSFORMERS_AVAILABLE,
        "llama_cpp": _LLAMA_CPP_AVAILABLE,
        "numpy": True,  # fastapi 已依赖
    }

    # 额外检查麦克风
    mic_ok = False
    if _SOUNDDEVICE_AVAILABLE:
        try:
            devices = sd.query_devices()
            mic_ok = any(d["max_input_channels"] > 0 for d in devices)
        except Exception:
            pass
    status["microphone"] = mic_ok

    return status


def print_status() -> None:
    """打印依赖状态表。"""
    s = check_dependencies()
    print("\n+---------------------------------------+")
    print("|      Voice Feature - Dependencies     |")
    print("+---------------------------------------+")
    for name, ok in s.items():
        icon = "[OK]" if ok else "[MISSING]"
        label = {
            "sounddevice": "Microphone (sounddevice)",
            "faster_whisper": "Whisper STT (faster-whisper)",
            "transformers": "Whisper STT (torch/transformers)",
            "llama_cpp": "Qwen Local LLM (llama-cpp)",
            "numpy": "NumPy audio processing",
            "microphone": "Microphone device",
        }.get(name, name)
        print(f"|  {icon}  {label:<28} |")
    print("+---------------------------------------+\n")


# ╔══════════════════════════════════════════════════════════════════════════════╗
# ║                      ◆  直 接 运 行 测 试  ◆                                 ║
# ╚══════════════════════════════════════════════════════════════════════════════╝

if __name__ == "__main__":
    print("=" * 50)
    print("  Agentic Desktop Pet - Voice Module Test")
    print("=" * 50)

    print_status()

    # 简单回调：打印回复
    def on_reply(text: str) -> None:
        print(f"\n  >>> [Pet]: {text}\n")

    def on_heard(text: str) -> None:
        print(f"  >>> [Heard]: {text}")

    assistant = create_assistant(
        on_response=on_reply,
        on_transcript=on_heard,
        use_agent=False,
    )

    try:
        assistant.start()
        print("\n  按 Ctrl+C 退出 …\n")
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n  正在退出 …")
    finally:
        assistant.stop()

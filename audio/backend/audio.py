"""
语音控制模块 — Agentic Desktop Pet

用户可通过说"开启语音功能"/"关闭语音功能"来控制桌宠是否对语音内容进行回复。

流程:  麦克风录音 → WAV 音频 → Whisper 离线语音转文本 → Qwen 量化模型 → 文字回复
"""

import io
import os
import sys
import wave
import time
import json
import atexit
import threading
import tempfile
from pathlib import Path
from typing import Callable, Optional

import numpy as np

# ── 可选依赖 (运行时按需加载) ──────────────────────────────────────────
_SOUNDDEVICE_AVAILABLE = True
try:
    import sounddevice as sd
except ImportError:
    _SOUNDDEVICE_AVAILABLE = False
    sd = None

_FASTER_WHISPER_AVAILABLE = True
try:
    from faster_whisper import WhisperModel
except ImportError:
    _FASTER_WHISPER_AVAILABLE = False
    WhisperModel = None

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
    silence_sec: float = 1.5          # 连续静音多少秒后停止录音
    max_record_sec: float = 30.0      # 单次录音最长时长

    # ── Whisper ──
    whisper_model: str = "base"       # tiny / base / small / medium / large
    whisper_device: str = "cpu"       # cpu | cuda
    whisper_compute: str = "int8"     # int8 (CPU) | float16 (GPU) | auto

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

        audio = np.concatenate(pre_buffer + frames, axis=0)
        return _numpy_to_wav(audio, self.cfg.sample_rate, self.cfg.channels)


def _numpy_to_wav(audio: np.ndarray, sr: int, channels: int) -> bytes:
    """将 float32 numpy 数组转为 16-bit PCM WAV 字节。"""
    buf = io.BytesIO()
    audio_i16 = np.clip(audio * 32767, -32768, 32767).astype(np.int16)
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(audio_i16.tobytes())
    buf.seek(0)
    return buf.read()


# ╔══════════════════════════════════════════════════════════════════════════════╗
# ║                    ◆  语 音 转 文 字  (Whisper)  ◆                           ║
# ╚══════════════════════════════════════════════════════════════════════════════╝

def _get_models_dir() -> str:
    """获取模型存储目录 (项目内的 models/ 文件夹，可随项目复制)。"""
    # 优先使用项目内的 models 目录
    audio_file = Path(__file__).resolve()
    project_models = audio_file.parent.parent / "models"  # backend/../models
    project_models.mkdir(parents=True, exist_ok=True)
    return str(project_models)


class WhisperSTT:
    """本地离线语音转文字 — 基于 faster-whisper (CTranslate2)。"""

    # faster-whisper base 模型需要的文件
    _MODEL_FILES = [
        "config.json",
        "model.bin",
        "tokenizer.json",
        "vocabulary.txt",
    ]
    _MODEL_REPO = "Systran/faster-whisper-base"
    _MIRROR_BASE = "https://hf-mirror.com"

    def __init__(self, config: VoiceConfig):
        self.cfg = config
        self._model = None

    def _download_model(self) -> str:
        """从 HF 镜像直接下载模型文件 (绕过 huggingface_hub 的 Xet 协议)。"""
        import httpx

        cache_dir = self.cfg.model_cache_dir or _get_models_dir()
        model_dir = os.path.join(cache_dir, "faster-whisper-base")
        os.makedirs(model_dir, exist_ok=True)

        # 检查是否已下载完成
        all_exist = all(
            os.path.exists(os.path.join(model_dir, f)) for f in self._MODEL_FILES
        )
        if all_exist:
            print(f"[WhisperSTT] 模型已存在: {model_dir}")
            return model_dir

        print(f"[WhisperSTT] 下载模型文件到 {model_dir} …")

        for filename in self._MODEL_FILES:
            url = f"{self._MIRROR_BASE}/{self._MODEL_REPO}/resolve/main/{filename}"
            dest = os.path.join(model_dir, filename)

            if os.path.exists(dest):
                size = os.path.getsize(dest)
                if filename == "model.bin" and size > 100_000_000:
                    print(f"  [跳过] {filename} ({size / 1024 / 1024:.0f} MB)")
                    continue
                elif filename != "model.bin":
                    print(f"  [跳过] {filename}")
                    continue

            print(f"  [下载] {filename} …")
            try:
                with httpx.stream("GET", url, follow_redirects=True, timeout=600) as resp:
                    resp.raise_for_status()
                    total = int(resp.headers.get("content-length", 0))
                    downloaded = 0
                    with open(dest, "wb") as f:
                        for chunk in resp.iter_bytes(chunk_size=8192):
                            f.write(chunk)
                            downloaded += len(chunk)
                            if total > 0 and downloaded % (total // 20 + 1) < 8192:
                                pct = downloaded * 100 // total
                                print(f"    {pct}% ({downloaded / 1024 / 1024:.0f}/{total / 1024 / 1024:.0f} MB)", end="\r")
                    print(f"    100% ({downloaded / 1024 / 1024:.0f} MB) ✓")
            except Exception as e:
                print(f"  [失败] {filename}: {e}")
                raise

        print(f"[WhisperSTT] 模型下载完成: {model_dir}")
        return model_dir

    def _load(self) -> None:
        if self._model is not None:
            return
        if not _FASTER_WHISPER_AVAILABLE:
            raise ImportError("请安装 faster-whisper: pip install faster-whisper")

        device = self.cfg.whisper_device
        compute = self.cfg.whisper_compute

        # 先尝试直接下载模型文件
        model_path = self._download_model()

        print(f"[WhisperSTT] 加载模型 '{self.cfg.whisper_model}' ({device}/{compute}) …")
        self._model = WhisperModel(
            model_path,  # 使用本地路径而非模型名
            device=device,
            compute_type=compute,
        )
        print("[WhisperSTT] 模型就绪 ✓")

    def transcribe(self, wav_bytes: bytes) -> str:
        """将 WAV 字节转写为中文文本。"""
        self._load()

        # faster-whisper 需要文件路径
        tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        try:
            tmp.write(wav_bytes)
            tmp.close()
            segments, _info = self._model.transcribe(
                tmp.name,
                language="zh",
                vad_filter=True,
                beam_size=5,
            )
            text = " ".join(seg.text.strip() for seg in segments)
            return text
        finally:
            try:
                os.unlink(tmp.name)
            except OSError:
                pass


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

        # 组件 (延迟加载)
        self.recorder: Optional[AudioRecorder] = None
        self.stt: Optional[WhisperSTT] = None
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
        self.stt = WhisperSTT(self.cfg)
        if not self._use_agent:
            self.llm = QwenLLM(self.cfg)

        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True, name="voice-thread")
        self._thread.start()
        print("[VoiceAssistant] Started - listening in background...")
        print(f"  Say '{self.WAKE_ON}' to enable voice replies")
        print(f"  Say '{self.WAKE_OFF}' to disable voice replies")

    def stop(self) -> None:
        """停止后台监听线程。"""
        self._running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3.0)
        print("[VoiceAssistant] Stopped")

    # ── 主循环 ──────────────────────────────────────────────────────────

    def _loop(self) -> None:
        while self._running:
            try:
                wav = self.recorder.record()
                if wav is None:
                    continue

                text = self.stt.transcribe(wav)
                if not text:
                    continue

                print(f"[VoiceAssistant] Heard: '{text}'")

                if self._on_transcript:
                    self._on_transcript(text)

                # ── 检测开关指令 ──
                if self.WAKE_OFF in text:
                    self.voice_mode = False
                    print("[VoiceAssistant] Voice mode -> OFF")
                    self._emit("语音功能已关闭")
                    continue

                if self.WAKE_ON in text:
                    self.voice_mode = True
                    print("[VoiceAssistant] Voice mode -> ON")
                    self._emit("语音功能已开启！我在听~")
                    continue

                # ── 语音模式下处理用户语音 ──
                if self.voice_mode:
                    reply = self._generate_reply(text)
                    if reply:
                        print(f"[VoiceAssistant] Reply: '{reply}'")
                        self._emit(reply)

            except Exception as exc:
                print(f"[VoiceAssistant] Error: {exc}")
                time.sleep(0.5)

    # ── 内部方法 ────────────────────────────────────────────────────────

    def _generate_reply(self, text: str) -> str:
        """调用 LLM (本地 Qwen 或外部 Agent) 生成回复。"""
        if self._use_agent and self.agent:
            try:
                return self.agent.run(text)
            except Exception as e:
                return f"[Agent 错误] {e}"

        if self.llm:
            return self.llm.generate(text)

        return "语音助手未配置语言模型"

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

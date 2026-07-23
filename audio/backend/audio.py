"""
语音控制模块 — Agentic Desktop Pet

用户可通过说"开启语音功能"/"关闭语音功能"来控制桌宠是否对语音内容进行回复。

流程:  麦克风录音 → WAV 音频 → Whisper 离线语音转文本 → Qwen 量化模型 → 文字回复
"""

import os
import time
import atexit
import threading
import queue
from collections import deque
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
    silence_sec: float = 0.9          # 连续静音多少秒后停止录音
    max_record_sec: float = 30.0      # 单次录音最长时长
    reply_settle_sec: float = 0.3    # 停顿后短暂等待，随后立即开始转写/回复
    input_latency_mode: str = "low"  # low | high，high 更抗 overflow
    input_blocksize: int = 0  # 0 = 让 PortAudio 选择最稳 blocksize
    overflow_log_cooldown_sec: float = 2.0
    stt_task_timeout_sec: float = 45.0  # 单次转写子任务最长等待
    llm_task_timeout_sec: float = 90.0  # 单次 LLM 子任务最长等待

    # ── Whisper ──
    whisper_model: str ="medium"       # tiny / base / small / medium / large
    whisper_backend: str = "qwen3"   # faster | torch | qwen3 | auto
    whisper_device: str = "auto"      # auto | cpu | cuda
    whisper_compute: str = "auto"     # auto | int8 (CPU) | float16 (GPU)
    qwen3_asr_model: str = "Qwen/Qwen3-ASR-0.6B"  # Qwen3-ASR 模型 ID
    qwen3_forced_aligner: str = "Qwen/Qwen3-ForcedAligner-0.6B"
    qwen3_max_inference_batch_size: int = 32
    qwen3_max_new_tokens: int = 256
    whisper_allowed_languages: str = "zh,en"  # 仅允许识别这些语种（逗号分隔）
    whisper_language_mode: str = "whitelist"  # whitelist | force
    whisper_force_language: str = ""           # force 模式下使用，留空则按 allowed 第一项
    whisper_preferred_language: str = "zh"    # 白名单模式下默认更偏向中文
    whisper_hf_mirror: str = "https://hf-mirror.com"  # torch Whisper 下载镜像

    # ── 其它 ──
    model_cache_dir: str = ""  # 默认在 _resolve_model_dir() 中自动设置


# ╔══════════════════════════════════════════════════════════════════════════════╗
# ║                          ◆  录 音 器  ◆                                      ║
# ╚══════════════════════════════════════════════════════════════════════════════╝

class AudioRecorder:
    """麦克风录音，静音自动停止，返回 WAV 字节。"""

    def __init__(self, config: VoiceConfig):
        self.cfg = config
        self._last_overflow_log_ts = 0.0
        if not _SOUNDDEVICE_AVAILABLE:
            raise ImportError("请安装 sounddevice: pip install sounddevice")

    def record(self, on_speech_start: Optional[Callable[[], None]] = None) -> Optional[bytes]:
        """录制一段语音，静音自动停止，返回 WAV 字节。没有有效语音则返回 None。"""
        if not _SOUNDDEVICE_AVAILABLE:
            print("[AudioRecorder] sounddevice 未安装")
            return None

        frames: list = []
        silent_chunks = 0
        overflowed = False
        max_silent = int(self.cfg.silence_sec / self.cfg.chunk_duration)
        max_chunks = int(self.cfg.max_record_sec / self.cfg.chunk_duration)
        block = int(self.cfg.sample_rate * self.cfg.chunk_duration)
        stream_blocksize = self.cfg.input_blocksize if self.cfg.input_blocksize >= 0 else block

        # 预录音缓冲：开始说话前可能有短暂静音
        pre_buffer: list = []
        pre_buffer_max = 5

        def callback(indata, _frames, _time_info, status):
            nonlocal overflowed
            if status:
                is_overflow = bool(getattr(status, "input_overflow", False))
                if is_overflow:
                    overflowed = True
                now_ts = time.time()
                if (not is_overflow) or (now_ts - self._last_overflow_log_ts >= self.cfg.overflow_log_cooldown_sec):
                    if is_overflow:
                        self._last_overflow_log_ts = now_ts
                    print(f"[AudioRecorder] 警告: {status}")
            frames.append(indata.copy())

        started_speaking = False
        try:
            with sd.InputStream(
                samplerate=self.cfg.sample_rate,
                channels=self.cfg.channels,
                callback=callback,
                blocksize=stream_blocksize,
                dtype="float32",
                latency=self.cfg.input_latency_mode,
            ):
                while len(frames) < max_chunks:
                    time.sleep(self.cfg.chunk_duration)

                    if overflowed:
                        # 当前缓冲已丢样，直接放弃本轮，下一轮重建流再录。
                        return None

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
                            if on_speech_start:
                                try:
                                    on_speech_start()
                                except Exception:
                                    pass
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
        on_speech_start: Optional[Callable[[], None]] = None,
        use_agent: bool = False,
    ):
        self.cfg = config or VoiceConfig()
        self._on_response = on_response     # LLM 回复回调
        self._on_transcript = on_transcript # 转写文本回调 (调试用)
        self._on_speech_start = on_speech_start  # 开始说话瞬时回调
        self._use_agent = use_agent         # True=使用外部 Agent, False=使用本地 Qwen

        # 状态
        self.voice_mode: bool = True    # 默认开启语音回复
        self._running: bool = False
        self._thread: Optional[threading.Thread] = None
        self._reply_thread: Optional[threading.Thread] = None
        self._pending_audio: deque[bytes] = deque()
        self._pending_deadline: float = 0.0
        self._pending_version: int = 0
        self._awaiting_supplemental_audio: bool = False
        self._pending_lock = threading.Lock()
        self._reply_wakeup = threading.Event()
        self._active_stt_cancel_event: threading.Event | None = None
        self._active_cancel_event: threading.Event | None = None

        # 组件 (延迟加载)
        self.recorder: Optional[AudioRecorder] = None
        self.stt: Optional[BaseWhisperSTT] = None
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
        self.stt.preload()

        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True, name="voice-thread")
        self._reply_thread = threading.Thread(target=self._reply_loop, daemon=True, name="voice-reply-thread")
        self._thread.start()
        self._reply_thread.start()

    def stop(self) -> None:
        """停止后台监听线程。"""
        self._running = False
        self._reply_wakeup.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3.0)
        if self._reply_thread and self._reply_thread.is_alive():
            self._reply_thread.join(timeout=3.0)

    # ── 主循环 ──────────────────────────────────────────────────────────

    def _loop(self) -> None:
        while self._running:
            try:
                wav = self.recorder.record(on_speech_start=self._notify_speech_start)
                if wav is None:
                    continue
                self._queue_pending_audio(wav)

            except Exception as exc:
                print(f"[VoiceAssistant] Error: {exc}")
                time.sleep(0.5)

    def _notify_speech_start(self) -> None:
        self._interrupt_for_speech_start()
        if self._on_speech_start:
            try:
                self._on_speech_start()
            except Exception:
                pass

    def _interrupt_for_speech_start(self) -> None:
        """用户一开口就立刻打断当前 STT/LLM，避免等整句录完才取消。"""
        with self._pending_lock:
            if self._active_stt_cancel_event is not None:
                self._active_stt_cancel_event.set()
            if self._active_cancel_event is not None:
                self._active_cancel_event.set()
            self._pending_version += 1
            self._awaiting_supplemental_audio = True
        self._reply_wakeup.set()

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

            try:
                text, stt_abandoned = self._run_subtask(
                    lambda: self.stt.transcribe(wav, cancel_event=stt_cancel_event),
                    cancel_event=stt_cancel_event,
                    timeout_sec=self.cfg.stt_task_timeout_sec,
                    stage="stt",
                )
            except Exception as exc:
                print(f"[VoiceAssistant] STT failed: {exc}")
                if self._handle_stt_failure():
                    self._requeue_interrupted_audio(wav, version)
                time.sleep(0.05)
                continue

            with self._pending_lock:
                if self._active_stt_cancel_event is stt_cancel_event:
                    self._active_stt_cancel_event = None

            if stt_cancel_event.is_set() or stt_abandoned:
                if self._requeue_interrupted_audio(wav, version):
                    print("[VoiceAssistant] Cancel transcription and merge interrupted audio with supplemental speech")
                else:
                    print("[VoiceAssistant] Drop cancelled transcription")
                continue

            with self._pending_lock:
                stale_transcript = version != self._pending_version
            if stale_transcript:
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
                except Exception as e:
                    print(f"[VoiceAssistant] Exception: {e}")
                    pass

            if not self.voice_mode:
                continue

            cancel_event = threading.Event()
            with self._pending_lock:
                self._active_cancel_event = cancel_event

            try:
                reply_result, llm_abandoned = self._run_subtask(
                    lambda: self._generate_reply(text, cancel_event=cancel_event),
                    cancel_event=cancel_event,
                    timeout_sec=self.cfg.llm_task_timeout_sec,
                    stage="llm",
                )
            except Exception as exc:
                print(f"[VoiceAssistant] LLM failed: {exc}")
                continue
            if llm_abandoned:
                reply, reply_cancelled = "", True
            else:
                reply, reply_cancelled = reply_result

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
                stale_reply = version != self._pending_version
            if stale_reply:
                self._requeue_interrupted_audio(wav, version)
                print("[VoiceAssistant] Drop stale reply and merge interrupted audio with newer speech")
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
            self._awaiting_supplemental_audio = False
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
            self._awaiting_supplemental_audio = False
        self._reply_wakeup.set()

    def _requeue_interrupted_audio(self, wav: bytes, base_version: int) -> bool:
        """仅在检测到新语音到来时，将当前回合音频回插到队列头并累计重试。"""
        with self._pending_lock:
            has_newer_version = base_version != self._pending_version
            has_supplemental_audio = bool(self._pending_audio)
            if not has_newer_version:
                return False

            # speech_start 已触发但补充音频还没入队时，也先保留当前 wav，避免吞字。
            if not has_supplemental_audio and not self._awaiting_supplemental_audio:
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

    def _run_subtask(
        self,
        fn: Callable[[], object],
        *,
        cancel_event: threading.Event | None,
        timeout_sec: float,
        stage: str,
    ) -> tuple[object, bool]:
        """Run a stage in a daemon subtask so we can abandon waits on cancel/timeout."""
        result_q: queue.Queue[tuple[bool, object]] = queue.Queue(maxsize=1)
        done = threading.Event()

        def _worker() -> None:
            try:
                result_q.put((True, fn()))
            except Exception as exc:
                result_q.put((False, exc))
            finally:
                done.set()

        worker = threading.Thread(target=_worker, daemon=True, name=f"voice-{stage}-task")
        worker.start()

        started = time.time()
        while self._running:
            if done.wait(timeout=0.05):
                break
            if cancel_event is not None and cancel_event.is_set():
                return "", True
            if timeout_sec > 0 and (time.time() - started) >= timeout_sec:
                print(f"[VoiceAssistant] {stage} timeout -> abandon current subtask")
                return "", True

        if not done.is_set():
            return "", True

        ok, payload = result_q.get()
        if ok:
            return payload, False
        raise payload

    def _handle_stt_failure(self) -> bool:
        """Qwen3-ASR 失败时自动回退，避免 reply 线程崩溃。"""
        current = (getattr(self.cfg, "whisper_backend", "") or "").strip().lower()
        if current != "qwen3":
            return False

        for backend in ("torch", "faster"):
            try:
                self.cfg.whisper_backend = backend
                next_stt = create_whisper_stt(self.cfg)
                next_stt.preload()
                self.stt = next_stt
                print(f"[VoiceAssistant] STT fallback -> {backend}")
                return True
            except Exception as exc:
                print(f"[VoiceAssistant] STT fallback {backend} failed: {exc}")

        self.cfg.whisper_backend = current
        return False

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
    whisper_model: str = "base",
    on_response: Optional[Callable[[str], None]] = None,
    on_transcript: Optional[Callable[[str], None]] = None,
    on_speech_start: Optional[Callable[[], None]] = None,
    use_agent: bool = False,
) -> VoiceAssistant:
    """
    快速创建 VoiceAssistant。

    Args:
        whisper_model: Whisper 模型大小 (tiny/base/small/medium/large)
        on_response: LLM 回复回调 fn(text: str)
        on_transcript: 语音转写回调 fn(text: str)
        on_speech_start: 用户开始说话回调 fn()
        use_agent: True = 使用外部 Agent

    Returns:
        VoiceAssistant 实例
    """
    cfg = VoiceConfig()
    cfg.whisper_model = whisper_model

    return VoiceAssistant(
        config=cfg,
        on_response=on_response,
        on_transcript=on_transcript,
        on_speech_start=on_speech_start,
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

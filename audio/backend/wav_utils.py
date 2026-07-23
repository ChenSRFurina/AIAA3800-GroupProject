import io
import wave

import numpy as np


def numpy_to_wav(audio: np.ndarray, sample_rate: int, channels: int) -> bytes:
    """将 float32 numpy 数组转为 16-bit PCM WAV 字节。"""
    buf = io.BytesIO()
    audio_i16 = np.clip(audio * 32767, -32768, 32767).astype(np.int16)
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(audio_i16.tobytes())
    buf.seek(0)
    return buf.read()


def wav_to_numpy(wav_bytes: bytes) -> tuple[np.ndarray, int, int]:
    with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
        channels = wf.getnchannels()
        sample_rate = wf.getframerate()
        sample_width = wf.getsampwidth()
        frames = wf.readframes(wf.getnframes())

    if sample_width != 2:
        raise ValueError(f"Unsupported WAV sample width: {sample_width}")

    audio_i16 = np.frombuffer(frames, dtype=np.int16)
    if channels > 1:
        audio_i16 = audio_i16.reshape(-1, channels)
    audio = audio_i16.astype(np.float32) / 32767.0
    return audio, sample_rate, channels


def merge_wav_chunks(chunks: list[bytes], fallback_sr: int, fallback_channels: int) -> bytes:
    arrays: list[np.ndarray] = []
    sample_rate = fallback_sr
    channels = fallback_channels
    for chunk in chunks:
        audio, sr, ch = wav_to_numpy(chunk)
        sample_rate = sr
        channels = ch
        arrays.append(audio)

    if not arrays:
        return b""

    merged = np.concatenate(arrays, axis=0)
    return numpy_to_wav(merged, sample_rate, channels)

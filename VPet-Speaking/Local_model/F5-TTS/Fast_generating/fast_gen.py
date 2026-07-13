"""
F5-TTS 快速合成客户端：连接已加载模型的 start_server.py，GPU 端合成。

窗口 2（服务端保持运行）:
    python fast_gen.py "你好，我是太乙真人"
    python fast_gen.py -t "短句测速" -o out.wav --nfe_step 8
    python fast_gen.py --interactive
"""

from __future__ import annotations

import argparse
import json
import socket
import struct
import sys
import time
import wave
from pathlib import Path

import numpy as np


HERE = Path(__file__).resolve().parent
DEFAULT_OUT_DIR = HERE / "outputs"


def recv_exact(sock: socket.socket, n: int) -> bytes:
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("服务端断开连接")
        buf.extend(chunk)
    return bytes(buf)


def recv_json(sock: socket.socket) -> dict:
    (length,) = struct.unpack("<I", recv_exact(sock, 4))
    payload = recv_exact(sock, length)
    return json.loads(payload.decode("utf-8"))


def recv_bytes(sock: socket.socket) -> bytes:
    (length,) = struct.unpack("<I", recv_exact(sock, 4))
    return recv_exact(sock, length)


def send_json(sock: socket.socket, obj: dict) -> None:
    raw = json.dumps(obj, ensure_ascii=False).encode("utf-8")
    sock.sendall(struct.pack("<I", len(raw)) + raw)


def connect(host: str, port: int, timeout: float = 10.0) -> socket.socket:
    sock = socket.create_connection((host, port), timeout=timeout)
    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    return sock


def ping(host: str, port: int) -> dict:
    with connect(host, port) as sock:
        send_json(sock, {"cmd": "ping"})
        return recv_json(sock)


def generate(
    text: str,
    host: str = "127.0.0.1",
    port: int = 8765,
    nfe_step: int | None = None,
    seed: int | None = None,
    speed: float | None = None,
    out_path: Path | None = None,
    play: bool = False,
) -> dict:
    req: dict = {"cmd": "gen", "text": text}
    if nfe_step is not None:
        req["nfe_step"] = int(nfe_step)
    if seed is not None:
        req["seed"] = int(seed)
    if speed is not None:
        req["speed"] = float(speed)

    t_client0 = time.perf_counter()
    with connect(host, port) as sock:
        send_json(sock, req)
        meta = recv_json(sock)
        if not meta.get("ok"):
            raise RuntimeError(meta.get("error") or "服务端返回失败")
        pcm = recv_bytes(sock)
    client_ms = (time.perf_counter() - t_client0) * 1000.0

    wave_f32 = np.frombuffer(pcm, dtype=np.float32)
    sr = int(meta["sr"])

    if out_path is None:
        DEFAULT_OUT_DIR.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d_%H%M%S")
        out_path = DEFAULT_OUT_DIR / f"gen_{stamp}.wav"
    else:
        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)

    save_wav(out_path, wave_f32, sr)

    if play:
        try_play(wave_f32, sr)

    result = {
        **meta,
        "client_ms": round(client_ms, 2),
        "out_path": str(out_path),
        "duration_s": round(len(wave_f32) / sr, 3),
    }
    return result


def save_wav(path: Path, audio: np.ndarray, sr: int) -> None:
    audio = np.clip(audio, -1.0, 1.0)
    pcm16 = (audio * 32767.0).astype(np.int16)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(pcm16.tobytes())


def try_play(audio: np.ndarray, sr: int) -> None:
    try:
        import sounddevice as sd

        sd.play(audio, sr)
        sd.wait()
    except Exception as e:
        print(f"[warn] 播放失败（可忽略）: {e}")


def print_result(result: dict) -> None:
    infer_ms = result.get("infer_ms")
    client_ms = result.get("client_ms")
    ok_flag = ""
    if isinstance(infer_ms, (int, float)):
        ok_flag = "  OK (<0.3s)" if infer_ms <= 300 else "  SLOW (>0.3s)"
    print("--------")
    print(f"text      : {result.get('text')}")
    print(f"infer_ms  : {infer_ms}{ok_flag}")
    print(f"client_ms : {client_ms}")
    print(f"nfe_step  : {result.get('nfe_step')}")
    print(f"duration  : {result.get('duration_s')} s")
    print(f"saved     : {result.get('out_path')}")
    print("--------")


def interactive_loop(args) -> None:
    print("交互模式。输入文本回车合成；空行 / quit 退出。")
    while True:
        try:
            text = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not text or text.lower() in {"q", "quit", "exit"}:
            break
        try:
            result = generate(
                text=text,
                host=args.host,
                port=args.port,
                nfe_step=args.nfe_step,
                seed=args.seed,
                speed=args.speed,
                out_path=Path(args.output) if args.output else None,
                play=args.play,
            )
            print_result(result)
        except Exception as e:
            print(f"[error] {e}")


def parse_args():
    p = argparse.ArgumentParser(description="F5-TTS Fast Generating Client")
    p.add_argument("text", nargs="?", default="", help="要合成的文本")
    p.add_argument("-t", "--text-arg", dest="text_opt", default="", help="同位置参数 text")
    p.add_argument("-o", "--output", default="", help="输出 wav 路径")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--nfe_step", type=int, default=None, help="ODE 步数，越小越快（默认用服务端配置，建议 4~16）")
    p.add_argument("--seed", type=int, default=None)
    p.add_argument("--speed", type=float, default=None)
    p.add_argument("--play", action="store_true", help="合成后播放（需 sounddevice）")
    p.add_argument("--interactive", "-i", action="store_true", help="交互输入多句")
    p.add_argument("--ping", action="store_true", help="仅探测服务是否就绪")
    p.add_argument("--bench", type=int, default=0, help="连续测速 N 次（不含首轮预热）")
    return p.parse_args()


def main():
    args = parse_args()
    text = (args.text_opt or args.text or "").strip()

    try:
        info = ping(args.host, args.port)
        print(
            f"服务就绪 | device={info.get('device')} | "
            f"default_nfe={info.get('default_nfe_step')} | "
            f"ref={info.get('ref_text')}"
        )
    except OSError as e:
        print(
            f"[error] 无法连接 {args.host}:{args.port}\n"
            f"请先在另一个窗口运行: python start_server.py\n详情: {e}"
        )
        sys.exit(1)

    if args.ping:
        print(json.dumps(info, ensure_ascii=False, indent=2))
        return

    if args.interactive:
        interactive_loop(args)
        return

    if args.bench > 0:
        if not text:
            text = "你好"
        print(f"预热 1 次...")
        generate(text, host=args.host, port=args.port, nfe_step=args.nfe_step, seed=args.seed, speed=args.speed)
        times = []
        for i in range(args.bench):
            r = generate(
                text,
                host=args.host,
                port=args.port,
                nfe_step=args.nfe_step,
                seed=args.seed,
                speed=args.speed,
                out_path=Path(args.output) if args.output else None,
            )
            times.append(float(r["infer_ms"]))
            print(f"[{i + 1}/{args.bench}] infer_ms={r['infer_ms']}")
        arr = np.asarray(times, dtype=np.float64)
        print(
            f"bench avg={arr.mean():.1f} ms  p50={np.percentile(arr, 50):.1f} ms  "
            f"min={arr.min():.1f} ms  max={arr.max():.1f} ms"
        )
        return

    if not text:
        print("请提供文本，例如:\n  python fast_gen.py \"你好啊\"\n  python fast_gen.py -i")
        sys.exit(2)

    result = generate(
        text=text,
        host=args.host,
        port=args.port,
        nfe_step=args.nfe_step,
        seed=args.seed,
        speed=args.speed,
        out_path=Path(args.output) if args.output else None,
        play=args.play,
    )
    print_result(result)
    if isinstance(result.get("infer_ms"), (int, float)) and result["infer_ms"] > 300:
        print(
            "提示: 若要逼近 0.3s，可尝试:\n"
            "  1) 服务端/客户端降低 --nfe_step 到 4 或 8\n"
            "  2) 使用更短文本\n"
            "  3) 确认 CUDA GPU 可用且驱动正常"
        )


if __name__ == "__main__":
    main()

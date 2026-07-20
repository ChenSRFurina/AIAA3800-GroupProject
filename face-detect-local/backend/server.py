import asyncio
import importlib
import os
import threading
import time
from contextlib import suppress
from dataclasses import dataclass
from typing import Any

# 国内直连 huggingface.co 易被重置；未设置时默认走镜像
# 若需官方源：set HF_ENDPOINT=https://huggingface.co
if not os.getenv("HF_ENDPOINT"):
    os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles

import local_models

from infer import infer_emotions_from_jpeg_bytes

FatigueMonitor = importlib.import_module("fatigue").FatigueMonitor

MAX_INFER_WINDOW_SIZE = int(os.getenv("MAX_INFER_WINDOW_SIZE", "8"))
HOST = os.getenv("INFER_SERVER_HOST", "0.0.0.0")
PORT = int(os.getenv("INFER_SERVER_PORT", "8000"))
# 默认从 Gaze 共享摄像头帧，避免浏览器再开一次摄像头（与 Gaze 冲突）
FACE_USE_GAZE_CAMERA = os.getenv("FACE_USE_GAZE_CAMERA", "1").strip().lower() not in (
    "0",
    "false",
    "no",
    "off",
)
FACE_GAZE_JPEG_URL = os.getenv(
    "FACE_GAZE_JPEG_URL",
    "http://127.0.0.1:8766/camera/jpeg",
)
FACE_GAZE_PULL_FPS = float(os.getenv("FACE_GAZE_PULL_FPS", "4"))
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEST_FRONTEND_DIR = os.path.join(PROJECT_ROOT, "test-frontend")

app = FastAPI()
app.mount(
    "/test-frontend",
    StaticFiles(directory=TEST_FRONTEND_DIR, html=True),
    name="test-frontend",
)

# 最近一次推理摘要，供 VPet-FaceDetect 轮询（测试页 WS 推流时也会更新）
_latest_lock = threading.Lock()
_latest_summary: dict[str, Any] = {
    "valid": False,
    "timestamp": 0.0,
}


def _publish_latest(payload: dict[str, Any]) -> None:
    faces = payload.get("faces") or []
    fatigue = payload.get("fatigue") or {}
    top_emotion = ""
    top_probability = 0.0
    if faces:
        primary = max(faces, key=lambda f: float(f.get("face_score") or 0.0))
        top_emotion = str(primary.get("top_emotion") or "")
        probs = primary.get("probabilities") or {}
        if isinstance(probs, dict):
            if top_emotion and probs.get(top_emotion) is not None:
                top_probability = float(probs[top_emotion])
            else:
                nums = [float(v) for v in probs.values() if v is not None]
                top_probability = max(nums) if nums else 0.0
        elif isinstance(probs, list) and probs:
            top_probability = float(max(probs))

    summary = {
        "valid": True,
        "timestamp": time.time(),
        "frame_id": payload.get("frame_id"),
        "top_emotion": top_emotion or str(fatigue.get("dominant_emotion") or ""),
        "top_probability": round(top_probability, 4),
        "dominant_emotion": str(fatigue.get("dominant_emotion") or top_emotion or ""),
        "fatigue_score": float(fatigue.get("fatigue_score") or 0.0),
        "fatigue_level": str(fatigue.get("fatigue_level") or "low"),
        "faces_count": len(faces),
        "blink_rate_per_min": float(fatigue.get("blink_rate_per_min") or 0.0),
        "yawn_rate_per_min": float(fatigue.get("yawn_rate_per_min") or 0.0),
    }
    with _latest_lock:
        _latest_summary.clear()
        _latest_summary.update(summary)


@dataclass
class FrameJob:
    frame_id: int
    jpeg_bytes: bytes
    queued_at: float


async def inference_worker(inference_queue, outbound_queue, fatigue_monitor):
    while True:
        job = await inference_queue.get()
        started_at = time.time()

        try:
            inference_result = await asyncio.to_thread(
                infer_emotions_from_jpeg_bytes,
                job.jpeg_bytes,
            )
            fatigue_result = fatigue_monitor.update(inference_result, now=time.time())
            payload = {
                "type": "inference_result",
                "frame_id": job.frame_id,
                "queued_at": job.queued_at,
                "started_at": started_at,
                "completed_at": time.time(),
                "queue_size": inference_queue.qsize(),
                "fatigue": fatigue_result,
                **inference_result,
            }
            _publish_latest(payload)
            await outbound_queue.put(payload)
        except Exception as exc:
            await outbound_queue.put(
                {
                    "type": "inference_error",
                    "frame_id": job.frame_id,
                    "message": str(exc),
                }
            )
        finally:
            inference_queue.task_done()


async def sender_loop(websocket, outbound_queue):
    while True:
        payload = await outbound_queue.get()
        try:
            await websocket.send_json(payload)
        finally:
            outbound_queue.task_done()


async def receiver_loop(websocket, inference_queue, outbound_queue):
    next_frame_id = 0

    while True:
        message = await websocket.receive()
        if message["type"] == "websocket.disconnect":
            raise WebSocketDisconnect

        jpeg_bytes = message.get("bytes")
        if not jpeg_bytes:
            await outbound_queue.put(
                {
                    "type": "protocol_error",
                    "message": "Only binary websocket frames containing JPEG bytes are supported.",
                }
            )
            continue

        dropped_frame_ids = []
        while inference_queue.full():
            dropped_job = inference_queue.get_nowait()
            inference_queue.task_done()
            dropped_frame_ids.append(dropped_job.frame_id)

        frame_id = next_frame_id
        next_frame_id += 1
        inference_queue.put_nowait(
            FrameJob(
                frame_id=frame_id,
                jpeg_bytes=jpeg_bytes,
                queued_at=time.time(),
            )
        )

        if dropped_frame_ids:
            await outbound_queue.put(
                {
                    "type": "dropped_frames",
                    "dropped_frame_ids": dropped_frame_ids,
                    "reason": "inference_window_full",
                    "max_infer_window_size": MAX_INFER_WINDOW_SIZE,
                }
            )


def _run_one_inference(
    jpeg_bytes: bytes,
    frame_id: int,
    fatigue_monitor: Any,
) -> dict[str, Any]:
    inference_result = infer_emotions_from_jpeg_bytes(jpeg_bytes)
    fatigue_result = fatigue_monitor.update(inference_result, now=time.time())
    payload = {
        "type": "inference_result",
        "frame_id": frame_id,
        "queued_at": time.time(),
        "started_at": time.time(),
        "completed_at": time.time(),
        "queue_size": 0,
        "source": "gaze_shared",
        "fatigue": fatigue_result,
        **inference_result,
    }
    _publish_latest(payload)
    return payload


def gaze_frame_pull_loop() -> None:
    """从 Gaze /camera/jpeg 拉帧并推理，更新 /latest（可不占用浏览器摄像头）。"""
    import urllib.error
    import urllib.request

    fatigue_monitor = FatigueMonitor(window_sec=60, fps=max(1, int(FACE_GAZE_PULL_FPS)))
    frame_id = 0
    interval = 1.0 / max(0.5, FACE_GAZE_PULL_FPS)
    print(
        f"[FaceDetect] Gaze camera share ON → {FACE_GAZE_JPEG_URL} "
        f"@ ~{FACE_GAZE_PULL_FPS:.1f} fps"
    )

    while True:
        t0 = time.time()
        try:
            req = urllib.request.Request(
                FACE_GAZE_JPEG_URL,
                headers={"User-Agent": "face-detect-gaze-pull/1.0"},
            )
            with urllib.request.urlopen(req, timeout=2.5) as resp:
                jpeg_bytes = resp.read()
            if jpeg_bytes and jpeg_bytes[:2] == b"\xff\xd8":
                _run_one_inference(jpeg_bytes, frame_id, fatigue_monitor)
                frame_id += 1
            else:
                time.sleep(0.5)
        except urllib.error.HTTPError as exc:
            if exc.code == 503:
                time.sleep(0.8)
            else:
                print(f"[FaceDetect] Gaze pull HTTP {exc.code}: {exc.reason}")
                time.sleep(1.5)
        except Exception as exc:
            # Gaze 未启动时安静重试
            msg = str(exc)
            if "10061" in msg or "refused" in msg.lower() or "timed out" in msg.lower():
                time.sleep(1.5)
            else:
                print(f"[FaceDetect] Gaze pull error: {exc}")
                time.sleep(1.5)

        elapsed = time.time() - t0
        time.sleep(max(0.05, interval - elapsed))


@app.get("/health")
async def health_check():
    with _latest_lock:
        latest_valid = bool(_latest_summary.get("valid"))
        latest_ts = float(_latest_summary.get("timestamp") or 0.0)
    return {
        "status": "ok",
        "mode": "local",
        "endpoint": f"http://127.0.0.1:{PORT}",
        "ws_infer": f"ws://127.0.0.1:{PORT}/ws/infer",
        "use_gaze_camera": FACE_USE_GAZE_CAMERA,
        "gaze_jpeg_url": FACE_GAZE_JPEG_URL,
        "max_infer_window_size": MAX_INFER_WINDOW_SIZE,
        "local_models": local_models.list_local_models(),
        "latest_valid": latest_valid,
        "latest_age_sec": round(max(0.0, time.time() - latest_ts), 2) if latest_ts else None,
        "note": "默认从 Gaze 共享摄像头；浏览器可不抢摄像头。VPet 轮询 /latest。",
    }


@app.get("/latest")
async def latest_inference():
    """VPet-FaceDetect 轮询：最近一次情绪/疲劳摘要。"""
    with _latest_lock:
        return dict(_latest_summary)


@app.websocket("/ws/infer")
async def infer_websocket(websocket: WebSocket):
    await websocket.accept()

    inference_queue = asyncio.Queue(maxsize=MAX_INFER_WINDOW_SIZE)
    outbound_queue = asyncio.Queue()
    fatigue_monitor = FatigueMonitor(window_sec=60, fps=30)

    worker_task = asyncio.create_task(inference_worker(inference_queue, outbound_queue, fatigue_monitor))
    sender_task = asyncio.create_task(sender_loop(websocket, outbound_queue))
    receiver_task = asyncio.create_task(receiver_loop(websocket, inference_queue, outbound_queue))

    done, pending = await asyncio.wait(
        {worker_task, sender_task, receiver_task},
        return_when=asyncio.FIRST_EXCEPTION,
    )

    for task in pending:
        task.cancel()

    for task in done:
        with suppress(WebSocketDisconnect, asyncio.CancelledError):
            task.result()

    await asyncio.gather(*pending, return_exceptions=True)


def main():
    if FACE_USE_GAZE_CAMERA:
        threading.Thread(
            target=gaze_frame_pull_loop,
            name="gaze-frame-pull",
            daemon=True,
        ).start()
    else:
        print(
            "[FaceDetect] FACE_USE_GAZE_CAMERA=0 → 仅浏览器 WS 推流 "
            "(与 Gaze 同时开会抢摄像头)"
        )

    uvicorn.run(app, host=HOST, port=PORT)


if __name__ == "__main__":
    main()
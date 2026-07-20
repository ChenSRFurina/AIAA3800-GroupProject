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

from infer import infer_emotions_from_jpeg_bytes

FatigueMonitor = importlib.import_module("fatigue").FatigueMonitor

MAX_INFER_WINDOW_SIZE = int(os.getenv("MAX_INFER_WINDOW_SIZE", "8"))
HOST = os.getenv("INFER_SERVER_HOST", "0.0.0.0")
PORT = int(os.getenv("INFER_SERVER_PORT", "8000"))
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEST_FRONTEND_DIR = os.path.join(PROJECT_ROOT, "test-frontend")

app = FastAPI()
app.mount(
    "/test-frontend",
    StaticFiles(directory=TEST_FRONTEND_DIR, html=True),
    name="test-frontend",
)

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


@app.get("/health")
async def health_check():
    return {
        "status": "ok",
        "max_infer_window_size": MAX_INFER_WINDOW_SIZE,
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
    uvicorn.run(app, host=HOST, port=PORT)


if __name__ == "__main__":
    main()
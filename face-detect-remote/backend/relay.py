"""Local relay: listen on :8000 and forward to a remote face-detect server.

Use when the GPU inference server runs elsewhere (face-detect-remote/server.py)
and this machine only needs VPet / test-frontend to talk to 127.0.0.1:8000.
"""
from __future__ import annotations

import asyncio
import os
from contextlib import suppress
from urllib.parse import urlparse

import httpx
import uvicorn
import websockets
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

HOST = os.getenv("INFER_SERVER_HOST", "127.0.0.1")
PORT = int(os.getenv("INFER_SERVER_PORT", "8000"))
REMOTE_BASE = os.getenv("FACE_REMOTE_URL", "").strip().rstrip("/")
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEST_FRONTEND_DIR = os.path.join(PROJECT_ROOT, "test-frontend")

app = FastAPI()
app.mount(
    "/test-frontend",
    StaticFiles(directory=TEST_FRONTEND_DIR, html=True),
    name="test-frontend",
)


def _require_remote_base() -> str:
    if not REMOTE_BASE:
        raise RuntimeError(
            "FACE_REMOTE_URL is not set. Example: http://192.168.1.10:8000"
        )
    return REMOTE_BASE


def _remote_http_url(path: str) -> str:
    return f"{_require_remote_base()}{path}"


def _remote_ws_url(path: str) -> str:
    parsed = urlparse(_require_remote_base())
    scheme = "wss" if parsed.scheme == "https" else "ws"
    host = parsed.netloc or parsed.path
    return f"{scheme}://{host}{path}"


@app.get("/health")
async def health_check():
    if not REMOTE_BASE:
        return JSONResponse(
            status_code=503,
            content={
                "status": "error",
                "mode": "relay",
                "message": "FACE_REMOTE_URL is not set",
            },
        )

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(_remote_http_url("/health"))
            remote_payload = response.json()
    except Exception as exc:
        return JSONResponse(
            status_code=503,
            content={
                "status": "error",
                "mode": "relay",
                "remote_url": REMOTE_BASE,
                "message": f"remote health check failed: {exc}",
            },
        )

    return {
        "status": "ok",
        "mode": "relay",
        "remote_url": REMOTE_BASE,
        "remote_health": remote_payload,
    }


@app.get("/latest")
async def latest_inference():
    """转发远端最近一次情绪/疲劳摘要，供 VPet-FaceDetect 轮询。"""
    if not REMOTE_BASE:
        return JSONResponse(
            status_code=503,
            content={"valid": False, "message": "FACE_REMOTE_URL is not set"},
        )

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(_remote_http_url("/latest"))
            return response.json()
    except Exception as exc:
        return JSONResponse(
            status_code=503,
            content={"valid": False, "message": f"remote /latest failed: {exc}"},
        )


async def _forward_client_to_remote(client_ws: WebSocket, remote_ws):
    while True:
        message = await client_ws.receive()
        if message["type"] == "websocket.disconnect":
            raise WebSocketDisconnect

        payload = message.get("bytes")
        if payload is not None:
            await remote_ws.send(payload)
            continue

        text = message.get("text")
        if text is not None:
            await remote_ws.send(text)


async def _forward_remote_to_client(client_ws: WebSocket, remote_ws):
    async for message in remote_ws:
        if isinstance(message, bytes):
            await client_ws.send_bytes(message)
        else:
            await client_ws.send_text(message)


@app.websocket("/ws/infer")
async def infer_relay(websocket: WebSocket):
    await websocket.accept()

    if not REMOTE_BASE:
        await websocket.send_json(
            {
                "type": "inference_error",
                "frame_id": -1,
                "message": "FACE_REMOTE_URL is not set on relay",
            }
        )
        await websocket.close()
        return

    remote_uri = _remote_ws_url("/ws/infer")
    try:
        async with websockets.connect(remote_uri, max_size=None) as remote_ws:
            client_task = asyncio.create_task(
                _forward_client_to_remote(websocket, remote_ws)
            )
            remote_task = asyncio.create_task(
                _forward_remote_to_client(websocket, remote_ws)
            )

            done, pending = await asyncio.wait(
                {client_task, remote_task},
                return_when=asyncio.FIRST_EXCEPTION,
            )

            for task in pending:
                task.cancel()

            for task in done:
                with suppress(WebSocketDisconnect, asyncio.CancelledError):
                    task.result()

            await asyncio.gather(*pending, return_exceptions=True)
    except Exception as exc:
        with suppress(RuntimeError):
            await websocket.send_json(
                {
                    "type": "inference_error",
                    "frame_id": -1,
                    "message": f"relay failed to connect remote: {exc}",
                }
            )


def main():
    if not REMOTE_BASE:
        print("[WARN] FACE_REMOTE_URL is empty; /health will report error until set.")
    else:
        print(f"[relay] remote target: {REMOTE_BASE}")
    print(f"[relay] listening on http://{HOST}:{PORT}")
    uvicorn.run(app, host=HOST, port=PORT)


if __name__ == "__main__":
    main()

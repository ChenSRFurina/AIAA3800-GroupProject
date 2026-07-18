# Face Detect Remote (GPU Server + Local Relay)

Two deployment roles:

| Role | Script | Where to run |
|------|--------|--------------|
| **Inference server** | `run_backend.bat` → `backend/server.py` | GPU machine with py-feat models |
| **Local relay** | `run_relay.bat` → `backend/relay.py` | Client PC (forwards to remote server) |

VPet and the test page always talk to `http://127.0.0.1:8000`. In remote mode the relay listens locally and forwards `/health` + `/ws/infer` to the GPU server.

## 1. GPU server (inference)

From project root (`face-detect-remote`):

```powershell
conda activate FACE
.\setup.bat
.\run_backend.bat
```

Default listen: `0.0.0.0:8000` (reachable from LAN).

Models download from Hugging Face (mirror via `HF_ENDPOINT=https://hf-mirror.com`).

Health: `GET http://<server-ip>:8000/health`  
WebSocket: `ws://<server-ip>:8000/ws/infer`

## 2. Client relay (no local inference)

On the machine running VPet:

1. Set in `VPet/.env`:

```env
FACE_REMOTE_URL=http://192.168.1.10:8000
```

2. Start relay only:

```powershell
cd face-detect-remote
.\run_relay.bat
```

Or one-click with all backends:

```powershell
cd VPet
.\start-all.bat -Remote
```

Relay health: `GET http://127.0.0.1:8000/health` → includes `mode: relay` and remote status.

## 3. Project structure

- `backend/server.py`: full py-feat inference (GPU server)
- `backend/relay.py`: local HTTP/WS proxy to `FACE_REMOTE_URL`
- `backend/infer.py`: JPEG decode + `Detectorv2` (server only)
- `test-frontend/index.html`: browser test page (served by server or relay)

## 4. Environment variables

| Variable | Used by | Default |
|----------|---------|---------|
| `FACE_REMOTE_URL` | relay | _(required for relay)_ |
| `INFER_SERVER_HOST` | server / relay | server `0.0.0.0`, relay `127.0.0.1` |
| `INFER_SERVER_PORT` | server / relay | `8000` |
| `HF_ENDPOINT` | server | `https://hf-mirror.com` if unset |
| `FEAT_DEVICE` | server | `cuda` (falls back to CPU) |

See the original WebSocket API sections below for message formats (`inference_result`, `fatigue`, etc.).

---

## WebSocket API (unchanged)

- URL: `ws://<host>:8000/ws/infer`
- Client → server: binary JPEG frames
- Server → client: JSON with `type` field

(Full payload examples are identical to the local backend; relay is transparent.)

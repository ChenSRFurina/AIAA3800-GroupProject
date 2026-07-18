# Face Detect Backend (WebSocket)

This project provides a FastAPI backend that accepts JPEG frame bytes over WebSocket, runs `py-feat` inference, and returns:

- Emotion probabilities per face
- Face rectangle (for frontend overlay)
- Derived fatigue metrics (blink/yawn/windowed score)

## 1. Project Structure

- `backend/server.py`: FastAPI app, WebSocket endpoint, queue/worker pipeline
- `backend/infer.py`: JPEG decode + `Detectorv2` inference + signal extraction
- `backend/fatigue.py`: Temporal fatigue monitor (blink/yawn/fatigue score)
- `test-frontend/index.html`: Browser test page (camera + WebSocket)
- `setup.sh`: Dependency installation helper

## 2. Start Backend (Conda env `aiaa3800`)

From project root (`face-detect`):

```bash
conda create -n aiaa3800 python=3.13
sh ./setup.sh
```

Default host/port:

- Host: `0.0.0.0`
- Port: `8000`

Optional environment variables:

- `INFER_SERVER_HOST` (default `0.0.0.0`)
- `INFER_SERVER_PORT` (default `8000`)
- `MAX_INFER_WINDOW_SIZE` (default `8`)
- `FEAT_DEVICE` (default `cuda`, falls back to CPU if CUDA unavailable)

Health check:

- `GET /health`

Test frontend (served by backend):

- `http://<server-host>:8000/test-frontend/`

## 3. WebSocket Endpoint

- URL: `ws://<server-host>:8000/ws/infer`
- Method: WebSocket

### Client -> Server (required)

Send **binary WebSocket frames** where each frame is the raw JPEG bytes of one image.

- Supported: binary frame with JPEG bytes
- Not supported: text JSON as frame payload for inference

If the frame is not binary JPEG bytes, server responds with `protocol_error`.

## 4. Server -> Client Message Types

The backend sends JSON messages with a `type` field.

### 4.1 `inference_result`

Main result for one processed frame.

```json
{
  "type": "inference_result",
  "frame_id": 12,
  "queued_at": 1750000000.123,
  "started_at": 1750000000.200,
  "completed_at": 1750000000.320,
  "queue_size": 3,
  "emotion_labels": ["Neutral", "Happy", "Sad", "Surprise", "Fear", "Disgust", "Anger"],
  "faces": [
    {
      "face_index": 0,
      "face_score": 0.98,
      "face_rect": {
        "x": 120.5,
        "y": 80.0,
        "width": 160.0,
        "height": 200.0
      },
      "probabilities": {
        "Neutral": 0.10,
        "Happy": 0.75,
        "Sad": 0.03,
        "Surprise": 0.02,
        "Fear": 0.03,
        "Disgust": 0.03,
        "Anger": 0.04
      },
      "top_emotion": "Happy",
      "signals": {
        "eye_signal": 0.62,
        "mouth_signal": 0.28,
        "arousal": 0.11,
        "valence": 0.33,
        "gaze_x": -0.02,
        "gaze_y": 0.04,
        "head_pose": {
          "pitch": 1.2,
          "yaw": -3.5,
          "roll": 0.7,
          "gaze_pitch": 0.04,
          "gaze_yaw": -0.02,
          "gaze_angle": 0.05
        },
        "signal_sources": {
          "eye_signal": "blendshape+geometry",
          "mouth_signal": "blendshape"
        },
        "geometry_signals": {
          "eye_closure": 0.58,
          "mouth_open": 0.19
        }
      }
    }
  ],
  "fatigue": {
    "window_sec": 60,
    "sample_count": 142,
    "primary_face_index": 0,
    "primary_face_score": 0.98,
    "dominant_emotion": "Happy",
    "blink_event": false,
    "yawn_event": false,
    "blink_rate_per_min": 9.0,
    "yawn_rate_per_min": 0.5,
    "eye_closed_ratio": 0.12,
    "fatigue_score": 0.36,
    "fatigue_level": "low",
    "signals": {
      "eye_signal": 0.62,
      "mouth_signal": 0.28,
      "arousal": 0.11,
      "valence": 0.33,
      "head_pose": {
        "pitch": 1.2,
        "yaw": -3.5,
        "roll": 0.7,
        "gaze_pitch": 0.04,
        "gaze_yaw": -0.02,
        "gaze_angle": 0.05
      },
      "gaze_x": -0.02,
      "gaze_y": 0.04,
      "signal_sources": {
        "eye_signal": "blendshape+geometry",
        "mouth_signal": "blendshape"
      }
    }
  }
}
```

Notes:

- `faces` may be empty if no face is detected in the frame.
- `face_rect` fields may be `null` when not available.
- Fatigue metrics are computed from a temporal window inside the current WebSocket connection.

### 4.2 `dropped_frames`

Sent when pending queue reaches `MAX_INFER_WINDOW_SIZE`; oldest queued frames are dropped.

```json
{
  "type": "dropped_frames",
  "dropped_frame_ids": [7, 8],
  "reason": "inference_window_full",
  "max_infer_window_size": 8
}
```

### 4.3 `protocol_error`

Sent when input frame format is invalid (for example text frame instead of binary JPEG bytes).

```json
{
  "type": "protocol_error",
  "message": "Only binary websocket frames containing JPEG bytes are supported."
}
```

### 4.4 `inference_error`

Sent when inference fails for a frame.

```json
{
  "type": "inference_error",
  "frame_id": 12,
  "message": "<error details>"
}
```

## 5. Interaction Summary

1. Connect to `ws://<host>:8000/ws/infer`
2. Continuously send JPEG bytes as binary WebSocket frames
3. Receive JSON messages and branch by `type`
4. Use `faces[*].face_rect` for drawing boxes
5. Use `faces[*].probabilities` for emotion charts
6. Use `fatigue` object for blink/yawn/fatigue UI

## 6. Minimal Frontend Integration Tips

- Keep sending at a stable rate (for example 10-15 FPS) to balance latency and load.
- If you receive `dropped_frames`, reduce capture FPS or JPEG resolution.
- Use same-origin by default if frontend is served from `/test-frontend/`.

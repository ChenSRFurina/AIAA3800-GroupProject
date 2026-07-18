import math
import time
from fastapi import FastAPI
import uvicorn

app = FastAPI(title="VPet Gaze Mock Server")


@app.get("/")
def root() -> dict[str, str]:
    return {"message": "Open /gaze to view mock gaze data."}


@app.get("/gaze")
def gaze() -> dict[str, float | bool]:
    now = time.time()
    screen_x = 0.5 + 0.36 * math.sin(now * 0.65)
    screen_y = 0.68 + 0.10 * math.sin(now * 0.37)
    return {
        "valid": True,
        "gaze_x": (screen_x - 0.5) * 2.0,
        "gaze_y": (screen_y - 0.5) * 2.0,
        "screen_x": screen_x,
        "screen_y": screen_y,
        "timestamp": now,
    }


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8766)

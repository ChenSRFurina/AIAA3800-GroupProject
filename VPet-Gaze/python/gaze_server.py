from __future__ import annotations

import threading
import time
from dataclasses import dataclass

import cv2
import mediapipe as mp
import numpy as np
import uvicorn
from fastapi import FastAPI


# ============================================================
# FastAPI
# ============================================================

app = FastAPI(title="VPet Gaze Server")


# ============================================================
# MediaPipe Face Mesh 关键点
# ============================================================

# 左眼虹膜：468 为中心点，469~472 为轮廓点
LEFT_IRIS = (468, 469, 470, 471, 472)

# 右眼虹膜：473 为中心点，474~477 为轮廓点
RIGHT_IRIS = (473, 474, 475, 476, 477)

# 左右眼角
LEFT_CORNERS = (33, 133)
RIGHT_CORNERS = (362, 263)

# 多个上下眼睑关键点，比只用一个点更稳定
LEFT_UPPER_LIDS = (159, 160, 158)
LEFT_LOWER_LIDS = (145, 144, 153)

RIGHT_UPPER_LIDS = (386, 385, 387)
RIGHT_LOWER_LIDS = (374, 380, 373)


# ============================================================
# 参数配置
# ============================================================

CAMERA_INDEX = 0

# 启动后中立视线校准时间
CALIBRATION_SECONDS = 2.0

# 水平方向的经验范围
# 当前水平识别效果正常，因此继续沿用原有范围
HORIZONTAL_MIN = 0.34
HORIZONTAL_MAX = 0.66

# 纵向灵敏度
# 纵向放大倍数
VERTICAL_GAIN = 18.0

# 水平与纵向平滑参数
# 数值越大，响应越快；数值越小，画面越稳定
SMOOTHING_X = 0.25
SMOOTHING_Y = 0.55

# 视线死区，防止正视时轻微抖动
HORIZONTAL_DEADZONE = 0.025
VERTICAL_DEADZONE = 0.035

# 屏幕位置允许范围
SCREEN_X_MIN = 0.03
SCREEN_X_MAX = 0.97

SCREEN_Y_MIN = 0.08
SCREEN_Y_MAX = 0.92

# 纵向映射幅度
# 0.5 表示 gaze_y=-1~1 会映射到屏幕中央上下约 50%
VERTICAL_SCREEN_SCALE = 0.5

# 眼睛过度闭合时不更新纵向视线
MIN_NORMALIZED_EYE_OPENNESS = 0.010


# ============================================================
# 共享状态
# ============================================================

@dataclass
class SharedState:
    valid: bool = False
    calibrating: bool = True

    gaze_x: float = 0.0
    gaze_y: float = 0.0

    screen_x: float = 0.5
    screen_y: float = 0.5

    raw_x: float = 0.5
    raw_y: float = 0.5

    timestamp: float = 0.0


state = SharedState()
state_lock = threading.Lock()


# 重新校准标志
recalibration_requested = threading.Event()


# ============================================================
# 通用函数
# ============================================================

def clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))


def apply_deadzone(value: float, threshold: float) -> float:
    """
    去除中心附近的小幅抖动，同时保持输出连续。
    """
    if abs(value) <= threshold:
        return 0.0

    sign = 1.0 if value > 0 else -1.0
    normalized = (abs(value) - threshold) / (1.0 - threshold)

    return sign * clamp(normalized, 0.0, 1.0)


def point(
    landmarks,
    index: int,
    width: int,
    height: int,
) -> np.ndarray:
    landmark = landmarks[index]

    return np.array(
        (
            landmark.x * width,
            landmark.y * height,
        ),
        dtype=np.float32,
    )


def center(
    landmarks,
    indices: tuple[int, ...],
    width: int,
    height: int,
) -> np.ndarray:
    points = [
        point(landmarks, index, width, height)
        for index in indices
    ]

    return np.mean(points, axis=0)


def projected_ratio(
    target: np.ndarray,
    start: np.ndarray,
    end: np.ndarray,
) -> float:
    """
    计算 target 在 start -> end 方向上的投影比例。
    """
    axis = end - start
    denominator = float(np.dot(axis, axis)) + 1e-6

    ratio = float(
        np.dot(target - start, axis) / denominator
    )

    return ratio


# ============================================================
# 眼球比例计算
# ============================================================

def horizontal_eye_ratio(
    iris: np.ndarray,
    first_corner: np.ndarray,
    second_corner: np.ndarray,
) -> float:
    ratio = projected_ratio(
        iris,
        first_corner,
        second_corner,
    )

    return clamp(ratio, 0.0, 1.0)


def vertical_eye_ratio(
    iris: np.ndarray,
    upper_lid: np.ndarray,
    lower_lid: np.ndarray,
) -> float:
    """
    计算虹膜在上眼睑与下眼睑之间的相对位置。

    0：靠近上眼睑
    1：靠近下眼睑
    """
    eye_vector = lower_lid - upper_lid
    denominator = float(np.dot(eye_vector, eye_vector)) + 1e-6

    ratio = float(
        np.dot(iris - upper_lid, eye_vector) / denominator
    )

    return clamp(ratio, -0.5, 1.5)


def normalized_eye_openness(
    upper_lid: np.ndarray,
    lower_lid: np.ndarray,
    first_corner: np.ndarray,
    second_corner: np.ndarray,
) -> float:
    """
    使用眼睛高度 / 眼睛宽度估计眼睛张开程度。
    可用于过滤眨眼或闭眼时的不可靠视线数据。
    """
    eye_height = float(
        np.linalg.norm(lower_lid - upper_lid)
    )

    eye_width = float(
        np.linalg.norm(second_corner - first_corner)
    ) + 1e-6

    return eye_height / eye_width


def calculate_eye_ratios(
    landmarks,
    width: int,
    height: int,
) -> tuple[float, float, float]:
    """
    返回：
        horizontal_ratio
        vertical_ratio
        eye_openness
    """

    # --------------------------------------------------------
    # 虹膜中心
    # --------------------------------------------------------

    left_iris = center(
        landmarks,
        LEFT_IRIS,
        width,
        height,
    )

    right_iris = center(
        landmarks,
        RIGHT_IRIS,
        width,
        height,
    )

    # --------------------------------------------------------
    # 眼角
    # --------------------------------------------------------

    left_corner_1 = point(
        landmarks,
        LEFT_CORNERS[0],
        width,
        height,
    )

    left_corner_2 = point(
        landmarks,
        LEFT_CORNERS[1],
        width,
        height,
    )

    right_corner_1 = point(
        landmarks,
        RIGHT_CORNERS[0],
        width,
        height,
    )

    right_corner_2 = point(
        landmarks,
        RIGHT_CORNERS[1],
        width,
        height,
    )

    # --------------------------------------------------------
    # 上下眼睑中心
    # --------------------------------------------------------

    left_upper = center(
        landmarks,
        LEFT_UPPER_LIDS,
        width,
        height,
    )

    left_lower = center(
        landmarks,
        LEFT_LOWER_LIDS,
        width,
        height,
    )

    right_upper = center(
        landmarks,
        RIGHT_UPPER_LIDS,
        width,
        height,
    )

    right_lower = center(
        landmarks,
        RIGHT_LOWER_LIDS,
        width,
        height,
    )

    # --------------------------------------------------------
    # 水平比例
    # --------------------------------------------------------

    left_horizontal = horizontal_eye_ratio(
        left_iris,
        left_corner_1,
        left_corner_2,
    )

    right_horizontal = horizontal_eye_ratio(
        right_iris,
        right_corner_1,
        right_corner_2,
    )

    horizontal_ratio = (
        left_horizontal + right_horizontal
    ) / 2.0

    # --------------------------------------------------------
    # 纵向比例
    # --------------------------------------------------------

    left_vertical = vertical_eye_ratio(
        left_iris,
        left_upper,
        left_lower,
    )

    right_vertical = vertical_eye_ratio(
        right_iris,
        right_upper,
        right_lower,
    )

    vertical_ratio = (
        left_vertical + right_vertical
    ) / 2.0

    # --------------------------------------------------------
    # 睁眼程度
    # --------------------------------------------------------

    left_openness = normalized_eye_openness(
        left_upper,
        left_lower,
        left_corner_1,
        left_corner_2,
    )

    right_openness = normalized_eye_openness(
        right_upper,
        right_lower,
        right_corner_1,
        right_corner_2,
    )

    eye_openness = (
        left_openness + right_openness
    ) / 2.0

    return (
        horizontal_ratio,
        vertical_ratio,
        eye_openness,
    )


# ============================================================
# 状态更新
# ============================================================

def set_invalid_state(calibrating: bool = False) -> None:
    with state_lock:
        state.valid = False
        state.calibrating = calibrating
        state.timestamp = time.time()


def update_shared_state(
    raw_x: float,
    raw_y: float,
    gaze_x: float,
    gaze_y: float,
    screen_x: float,
    screen_y: float,
) -> None:
    global state

    with state_lock:
        state = SharedState(
            valid=True,
            calibrating=False,
            gaze_x=gaze_x,
            gaze_y=gaze_y,
            screen_x=screen_x,
            screen_y=screen_y,
            raw_x=raw_x,
            raw_y=raw_y,
            timestamp=time.time(),
        )


# ============================================================
# 摄像头线程
# ============================================================

def camera_loop() -> None:
    camera = cv2.VideoCapture(
        CAMERA_INDEX,
        cv2.CAP_DSHOW,
    )

    camera.set(
        cv2.CAP_PROP_FRAME_WIDTH,
        640,
    )

    camera.set(
        cv2.CAP_PROP_FRAME_HEIGHT,
        480,
    )

    if not camera.isOpened():
        print(
            f"[VPet-Gaze] Cannot open camera {CAMERA_INDEX}."
        )

        set_invalid_state()
        return

    face_mesh_module = mp.solutions.face_mesh

    smooth_x = 0.5
    smooth_y = 0.5

    vertical_baseline: float | None = None
    vertical_samples: list[float] = []

    calibration_start_time = time.time()

    def begin_calibration() -> None:
        nonlocal vertical_baseline
        nonlocal vertical_samples
        nonlocal calibration_start_time
        nonlocal smooth_y

        vertical_baseline = None
        vertical_samples = []
        calibration_start_time = time.time()
        smooth_x = 0.5
        smooth_y = 0.5

        print(
            "[VPet-Gaze] Vertical calibration started. "
            "Please look naturally at the center of the screen."
        )

    begin_calibration()

    with face_mesh_module.FaceMesh(
        static_image_mode=False,
        max_num_faces=1,
        refine_landmarks=True,
        min_detection_confidence=0.55,
        min_tracking_confidence=0.55,
    ) as face_mesh:

        while True:
            if recalibration_requested.is_set():
                recalibration_requested.clear()
                begin_calibration()

            ok, frame = camera.read()

            if not ok:
                set_invalid_state()
                time.sleep(0.05)
                continue

            # 镜像画面，使预览方向符合用户直觉
            frame = cv2.flip(frame, 1)

            height, width = frame.shape[:2]

            rgb = cv2.cvtColor(
                frame,
                cv2.COLOR_BGR2RGB,
            )

            result = face_mesh.process(rgb)

            if not result.multi_face_landmarks:
                set_invalid_state(
                    calibrating=vertical_baseline is None
                )

                cv2.putText(
                    frame,
                    "No face detected",
                    (18, 32),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.72,
                    (0, 0, 255),
                    2,
                )

                cv2.imshow(
                    "VPet Gaze Tracking - Q: quit, C: recalibrate",
                    frame,
                )

                key = cv2.waitKey(1) & 0xFF

                if key in (ord("q"), 27):
                    break

                if key == ord("c"):
                    begin_calibration()

                continue

            landmarks = (
                result.multi_face_landmarks[0].landmark
            )

            raw_x, raw_y, eye_openness = (
                calculate_eye_ratios(
                    landmarks,
                    width,
                    height,
                )
            )

            # ------------------------------------------------
            # 纵向校准
            # ------------------------------------------------

            if vertical_baseline is None:
                elapsed = (
                    time.time() - calibration_start_time
                )

                # 只在眼睛正常睁开时收集校准数据
                if (
                    eye_openness
                    > MIN_NORMALIZED_EYE_OPENNESS
                ):
                    vertical_samples.append(raw_y)

                set_invalid_state(calibrating=True)

                remaining = max(
                    0.0,
                    CALIBRATION_SECONDS - elapsed,
                )

                cv2.putText(
                    frame,
                    "Look at screen center",
                    (18, 32),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.72,
                    (0, 255, 255),
                    2,
                )

                cv2.putText(
                    frame,
                    f"Calibrating: {remaining:.1f}s",
                    (18, 64),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.65,
                    (0, 255, 255),
                    2,
                )

                if elapsed >= CALIBRATION_SECONDS:
                    if vertical_samples:
                        vertical_baseline = float(
                            np.median(vertical_samples)
                        )
                    else:
                        vertical_baseline = 0.5

                    print(
                        "[VPet-Gaze] Calibration complete. "
                        f"Vertical baseline={vertical_baseline:.4f}"
                    )

                cv2.imshow(
                    "VPet Gaze Tracking - Q: quit, C: recalibrate",
                    frame,
                )

                key = cv2.waitKey(1) & 0xFF

                if key in (ord("q"), 27):
                    break

                if key == ord("c"):
                    begin_calibration()

                continue

            # ------------------------------------------------
            # 眨眼或闭眼时，不更新视线
            # ------------------------------------------------

            if (
                eye_openness
                <= MIN_NORMALIZED_EYE_OPENNESS
            ):
                set_invalid_state()

                cv2.putText(
                    frame,
                    "Blink detected",
                    (18, 32),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.72,
                    (0, 165, 255),
                    2,
                )

                cv2.imshow(
                    "VPet Gaze Tracking - Q: quit, C: recalibrate",
                    frame,
                )

                key = cv2.waitKey(1) & 0xFF

                if key in (ord("q"), 27):
                    break

                if key == ord("c"):
                    begin_calibration()

                continue

            # ------------------------------------------------
            # 水平方向
            # ------------------------------------------------

            screen_x_raw = (
                raw_x - HORIZONTAL_MIN
            ) / (
                HORIZONTAL_MAX - HORIZONTAL_MIN
            )

            screen_x_raw = clamp(
                screen_x_raw,
                SCREEN_X_MIN,
                SCREEN_X_MAX,
            )

            gaze_x_raw = (
                screen_x_raw - 0.5
            ) * 2.0

            gaze_x_raw = apply_deadzone(
                gaze_x_raw,
                HORIZONTAL_DEADZONE,
            )

            screen_x_target = clamp(
                0.5 + gaze_x_raw * 0.47,
                SCREEN_X_MIN,
                SCREEN_X_MAX,
            )

            # ------------------------------------------------
            # 纵向方向
            # ------------------------------------------------

            vertical_delta = raw_y - vertical_baseline

            # tanh 非线性放大：
            # 小幅眼球变化也能产生明显输出，同时避免直接硬截断造成跳变
            gaze_y_raw = float(
                np.tanh(vertical_delta * VERTICAL_GAIN)
            )

            gaze_y_raw = apply_deadzone(
                gaze_y_raw,
                0.015,
            )

            screen_y_target = clamp(
                0.5 + gaze_y_raw * 0.5,
                0.0,
                1.0,
            )

            # 如果上下方向反了，可以改成：
            #
            # screen_y_target = clamp(
            #     0.5 - gaze_y_raw * 0.5,
            #     0.0,
            #     1.0,
            # )

            # ------------------------------------------------
            # 平滑滤波
            # ------------------------------------------------

            smooth_x += (
                SMOOTHING_X
                * (screen_x_target - smooth_x)
            )

            smooth_y += (
                SMOOTHING_Y
                * (screen_y_target - smooth_y)
            )

            smooth_x = clamp(
                smooth_x,
                SCREEN_X_MIN,
                SCREEN_X_MAX,
            )

            smooth_y = clamp(
                smooth_y,
                SCREEN_Y_MIN,
                SCREEN_Y_MAX,
            )

            final_gaze_x = (
                smooth_x - 0.5
            ) * 2.0

            final_gaze_y = (
                smooth_y - 0.5
            ) * 2.0

            update_shared_state(
                raw_x=raw_x,
                raw_y=raw_y,
                gaze_x=final_gaze_x,
                gaze_y=final_gaze_y,
                screen_x=smooth_x,
                screen_y=smooth_y,
            )

            # ------------------------------------------------
            # 预览信息
            # ------------------------------------------------

            cv2.putText(
                frame,
                (
                    f"screen=({smooth_x:.2f}, "
                    f"{smooth_y:.2f})"
                ),
                (18, 32),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.70,
                (255, 255, 255),
                2,
            )

            cv2.putText(
                frame,
                (
                    f"raw_y={raw_y:.4f} base={vertical_baseline:.4f} "
                    f"delta={raw_y - vertical_baseline:+.4f}"
                ),
                (18, 62),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.58,
                (255, 255, 255),
                2,
            )

            cv2.putText(
                frame,
                (
                    f"gaze_y={final_gaze_y:.2f} "
                    f"eye={eye_openness:.3f}"
                ),
                (18, 90),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.58,
                (255, 255, 255),
                2,
            )

            marker_x = int(
                clamp(smooth_x, 0.0, 1.0) * (width - 1)
            )

            marker_y = int(
                clamp(smooth_y, 0.0, 1.0) * (height - 1)
            )

            cv2.circle(
                frame,
                (marker_x, marker_y),
                12,
                (0, 255, 0),
                2,
            )

            cv2.imshow(
                "VPet Gaze Tracking - Q: quit, C: recalibrate",
                frame,
            )

            key = cv2.waitKey(1) & 0xFF

            if key in (ord("q"), 27):
                break

            if key == ord("c"):
                begin_calibration()

    camera.release()
    cv2.destroyAllWindows()


# ============================================================
# API
# ============================================================

@app.get("/")
def root() -> dict[str, str]:
    return {
        "message": (
            "VPet gaze service is running. "
            "Open /gaze for tracking data."
        )
    }


@app.get("/gaze")
def get_gaze() -> dict[str, float | bool]:
    with state_lock:
        return {
            "valid": state.valid,
            "calibrating": state.calibrating,
            "gaze_x": round(state.gaze_x, 5),
            "gaze_y": round(state.gaze_y, 5),
            "screen_x": round(state.screen_x, 5),
            "screen_y": round(state.screen_y, 5),
            "raw_x": round(state.raw_x, 5),
            "raw_y": round(state.raw_y, 5),
            "timestamp": state.timestamp,
        }


@app.post("/recalibrate")
def recalibrate() -> dict[str, str]:
    recalibration_requested.set()

    return {
        "message": "Vertical gaze recalibration requested."
    }


# ============================================================
# 主程序
# ============================================================

if __name__ == "__main__":
    worker = threading.Thread(
        target=camera_loop,
        daemon=True,
    )

    worker.start()

    uvicorn.run(
        app,
        host="127.0.0.1",
        port=8766,  # 避免与 VPet-Speaking F5-TTS TCP:8765 冲突
        log_level="info",
    )
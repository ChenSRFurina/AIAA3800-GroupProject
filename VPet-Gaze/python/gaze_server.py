from __future__ import annotations

import threading
import time
from dataclasses import dataclass

import cv2
import mediapipe as mp
import numpy as np
import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response

from gaze_model import (
    KalmanGazeFilter,
    RidgeGazeMapper,
    build_nine_point_targets,
    extract_gaze_features,
    fit_ridge_mapper,
    reset_head_pose_smoother,
)


# ============================================================
# FastAPI
# ============================================================

app = FastAPI(title="VPet Gaze Server")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


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

# 九点校准（3×3）：中心 + 四边中点 + 四角
CALIBRATION_MARGIN = 0.06
CALIBRATION_SETTLE_SECONDS = 0.45
CALIBRATION_CAPTURE_SECONDS = 1.05
CALIBRATION_MIN_SAMPLES = 8

# 映射后屏幕空间死区（相对 [-1,1] 注视轴；过大→中心吸附并来回弹）
SCREEN_DEADZONE = 0.02

# 输出端不再混几何点（几何只作 Ridge 特征），避免与校准符号打架来回晃
GEO_BLEND = 0.0

# 屏幕位置允许范围
SCREEN_X_MIN = 0.0
SCREEN_X_MAX = 1.0
SCREEN_Y_MIN = 0.0
SCREEN_Y_MAX = 1.0

# 眼睛过度闭合时不更新视线（提高以避开半闭眼噪声）
MIN_NORMALIZED_EYE_OPENNESS = 0.018

# 全屏预览：按真实屏幕坐标画注视点，便于核对准不准
PREVIEW_WINDOW_NAME = "VPet Gaze Preview"
PREVIEW_START_FULLSCREEN = True

# I-DT 调试（与 C# GazeConfig 对齐，改这里即可）
FIXATION_DURATION_SECONDS = 3.0
IDT_DISPERSION_THRESHOLD = 0.08
IDT_MIN_SAMPLE_COUNT = 8


@dataclass(frozen=True)
class CalibrationTarget:
    name: str
    screen_x: float
    screen_y: float


def build_calibration_targets(
    margin: float = CALIBRATION_MARGIN,
) -> list[CalibrationTarget]:
    return [
        CalibrationTarget(name, sx, sy)
        for name, sx, sy in build_nine_point_targets(margin)
    ]


CALIBRATION_TARGETS = build_calibration_targets()


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

    # I-DT 调试：是否已触发注视判定
    fixation: bool = False
    fixation_duration: float = 0.0
    fixation_x: float = 0.5
    fixation_y: float = 0.5

    timestamp: float = 0.0


state = SharedState()
state_lock = threading.Lock()

# 最新摄像头 JPEG（供 FaceDetect / 浏览器共享，避免再开一次摄像头）
_latest_jpeg_lock = threading.Lock()
_latest_jpeg: bytes | None = None
_latest_jpeg_ts: float = 0.0
_latest_jpeg_size: tuple[int, int] = (0, 0)


def publish_camera_jpeg(frame_bgr: np.ndarray) -> None:
    """把当前帧编码为 JPEG，供 /camera/jpeg 读取。"""
    global _latest_jpeg, _latest_jpeg_ts, _latest_jpeg_size

    ok, buf = cv2.imencode(
        ".jpg",
        frame_bgr,
        [int(cv2.IMWRITE_JPEG_QUALITY), 75],
    )
    if not ok:
        return

    h, w = frame_bgr.shape[:2]
    with _latest_jpeg_lock:
        _latest_jpeg = buf.tobytes()
        _latest_jpeg_ts = time.time()
        _latest_jpeg_size = (int(w), int(h))


# 重新校准标志
recalibration_requested = threading.Event()


# ============================================================
# I-DT（服务端调试用，与 C# 插件逻辑一致）
# ============================================================

class IdtFixationDetector:
    """Identification by Dispersion Threshold（流式）。"""

    def __init__(self) -> None:
        self._window: list[tuple[float, float, float]] = []
        self._triggered = False

    def reset(self) -> None:
        self._window.clear()
        self._triggered = False

    @staticmethod
    def _dispersion(
        points: list[tuple[float, float, float]],
    ) -> float:
        if not points:
            return 0.0
        xs = [p[1] for p in points]
        ys = [p[2] for p in points]
        return max(max(xs) - min(xs), max(ys) - min(ys))

    def update(
        self,
        screen_x: float,
        screen_y: float,
        now: float,
    ) -> tuple[bool, float, float, float]:
        """
        返回: (是否已达注视阈值, 持续秒数, 质心x, 质心y)
        """
        self._window.append((now, screen_x, screen_y))

        while (
            len(self._window) > 1
            and self._dispersion(self._window) > IDT_DISPERSION_THRESHOLD
        ):
            self._window.pop(0)
            self._triggered = False

        while (
            len(self._window) > 1
            and now - self._window[0][0] > FIXATION_DURATION_SECONDS * 3.0
        ):
            self._window.pop(0)

        if not self._window:
            return False, 0.0, 0.5, 0.5

        duration = now - self._window[0][0]
        cx = sum(p[1] for p in self._window) / len(self._window)
        cy = sum(p[2] for p in self._window) / len(self._window)

        ok = (
            len(self._window) >= IDT_MIN_SAMPLE_COUNT
            and duration >= FIXATION_DURATION_SECONDS
            and self._dispersion(self._window) <= IDT_DISPERSION_THRESHOLD
        )

        if ok and not self._triggered:
            self._triggered = True
            print(
                "[VPet-Gaze][I-DT] FIXATION triggered "
                f"dur={duration:.2f}s center=({cx:.3f}, {cy:.3f}) "
                f"(threshold={FIXATION_DURATION_SECONDS}s)"
            )

        if not ok:
            self._triggered = False

        return ok, duration, cx, cy


# ============================================================
# 通用函数
# ============================================================

def clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))


def get_primary_screen_size() -> tuple[int, int]:
    """读取主显示器分辨率，用于全屏预览画布。"""
    try:
        import ctypes

        user32 = ctypes.windll.user32
        width = int(user32.GetSystemMetrics(0))
        height = int(user32.GetSystemMetrics(1))
        if width > 0 and height > 0:
            return width, height
    except Exception:
        pass

    return 1920, 1080


def set_preview_fullscreen(enabled: bool) -> None:
    prop = (
        cv2.WINDOW_FULLSCREEN
        if enabled
        else cv2.WINDOW_NORMAL
    )
    cv2.setWindowProperty(
        PREVIEW_WINDOW_NAME,
        cv2.WND_PROP_FULLSCREEN,
        prop,
    )


def build_screen_preview(
    screen_w: int,
    screen_h: int,
    camera_frame: np.ndarray | None,
    *,
    screen_x: float | None,
    screen_y: float | None,
    lines: list[tuple[str, tuple[int, int, int]]],
    calibrating: bool = False,
    calib_target: CalibrationTarget | None = None,
    calib_done_names: set[str] | None = None,
    marker_fixating: bool = False,
) -> np.ndarray:
    """
    在真实屏幕坐标系上绘制注视点。
    screen_x/screen_y 为 0~1，(0,0)=左上，(1,1)=右下。
    """
    canvas = np.full((screen_h, screen_w, 3), 28, dtype=np.uint8)
    cx = screen_w // 2
    cy = screen_h // 2
    done_names = calib_done_names or set()

    if calibrating:
        for target in CALIBRATION_TARGETS:
            tx = int(clamp(target.screen_x, 0.0, 1.0) * (screen_w - 1))
            ty = int(clamp(target.screen_y, 0.0, 1.0) * (screen_h - 1))
            if calib_target is not None and target.name == calib_target.name:
                color = (0, 255, 255)
                radius = 34
                thickness = 3
            elif target.name in done_names:
                color = (80, 180, 80)
                radius = 14
                thickness = 2
            else:
                color = (70, 70, 70)
                radius = 12
                thickness = 1
            cv2.circle(canvas, (tx, ty), radius, color, thickness)
            cv2.circle(canvas, (tx, ty), 4, color, -1)

        if calib_target is not None:
            tx = int(
                clamp(calib_target.screen_x, 0.0, 1.0) * (screen_w - 1)
            )
            ty = int(
                clamp(calib_target.screen_y, 0.0, 1.0) * (screen_h - 1)
            )
            cv2.line(canvas, (tx - 90, ty), (tx + 90, ty), (0, 200, 200), 1)
            cv2.line(canvas, (tx, ty - 90), (tx, ty + 90), (0, 200, 200), 1)
    else:
        cv2.drawMarker(
            canvas,
            (cx, cy),
            (55, 55, 55),
            markerType=cv2.MARKER_CROSS,
            markerSize=48,
            thickness=1,
        )

    if (
        not calibrating
        and screen_x is not None
        and screen_y is not None
    ):
        mx = int(clamp(screen_x, 0.0, 1.0) * (screen_w - 1))
        my = int(clamp(screen_y, 0.0, 1.0) * (screen_h - 1))
        # I-DT 触发注视后变红，否则绿色
        marker = (0, 0, 255) if marker_fixating else (0, 255, 0)
        cross = (0, 0, 180) if marker_fixating else (0, 170, 0)
        radius = 32 if marker_fixating else 26
        cv2.line(canvas, (mx - 48, my), (mx + 48, my), cross, 1)
        cv2.line(canvas, (mx, my - 48), (mx, my + 48), cross, 1)
        cv2.circle(canvas, (mx, my), radius, marker, 3)
        cv2.circle(canvas, (mx, my), 6, marker, -1)
        label = (
            f"FIXATION ({screen_x:.2f}, {screen_y:.2f})"
            if marker_fixating
            else f"({screen_x:.2f}, {screen_y:.2f})"
        )
        cv2.putText(
            canvas,
            label,
            (mx + 34, my - 18),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.7,
            marker,
            2,
            cv2.LINE_AA,
        )

    # 校准四角时隐藏摄像头小窗，避免挡住目标点
    if (
        not calibrating
        and camera_frame is not None
        and camera_frame.size > 0
    ):
        inset_w = max(260, screen_w // 5)
        inset_h = int(
            inset_w
            * camera_frame.shape[0]
            / max(camera_frame.shape[1], 1)
        )
        inset = cv2.resize(camera_frame, (inset_w, inset_h))
        x0 = screen_w - inset_w - 28
        y0 = screen_h - inset_h - 56
        canvas[y0 : y0 + inset_h, x0 : x0 + inset_w] = inset
        cv2.rectangle(
            canvas,
            (x0 - 1, y0 - 1),
            (x0 + inset_w, y0 + inset_h),
            (90, 90, 90),
            1,
        )
        cv2.putText(
            canvas,
            "camera",
            (x0, y0 - 8),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.55,
            (140, 140, 140),
            1,
            cv2.LINE_AA,
        )

    text_y = 48
    for text, color in lines:
        cv2.putText(
            canvas,
            text,
            (36, text_y),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.9,
            color,
            2,
            cv2.LINE_AA,
        )
        text_y += 40

    cv2.putText(
        canvas,
        "F: fullscreen   Esc: window   C: recalibrate   Q: quit",
        (36, screen_h - 28),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.72,
        (150, 150, 150),
        1,
        cv2.LINE_AA,
    )

    return canvas


def apply_deadzone(value: float, threshold: float) -> float:
    """
    去除中心附近的小幅抖动，同时保持输出连续。
    """
    if abs(value) <= threshold:
        return 0.0

    sign = 1.0 if value > 0 else -1.0
    normalized = (abs(value) - threshold) / (1.0 - threshold)

    return sign * clamp(normalized, 0.0, 1.0)


def apply_screen_deadzone(
    screen_x: float,
    screen_y: float,
    threshold: float = SCREEN_DEADZONE,
) -> tuple[float, float]:
    """屏幕中心附近的小死区，不削弱边缘行程。"""
    gx = apply_deadzone((screen_x - 0.5) * 2.0, threshold)
    gy = apply_deadzone((screen_y - 0.5) * 2.0, threshold)
    return 0.5 + gx * 0.5, 0.5 + gy * 0.5


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
    eye_height = float(np.linalg.norm(eye_vector))

    # 眼缝过窄时投影比例数值极不稳定
    if eye_height < 2.0:
        return 0.5

    denominator = float(np.dot(eye_vector, eye_vector)) + 1e-6

    ratio = float(
        np.dot(iris - upper_lid, eye_vector) / denominator
    )

    # 收紧钳位，避免眨眼/landmark 抖动把比值打到极端
    return clamp(ratio, 0.05, 0.95)


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

    # --------------------------------------------------------
    # 睁眼程度（先算，纵向按睁眼程度加权）
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

    # 左右差异过大视为噪声帧，交给上层用上一帧
    if abs(left_vertical - right_vertical) > 0.12:
        vertical_ratio = float("nan")
    else:
        left_w = max(left_openness, 1e-3)
        right_w = max(right_openness, 1e-3)
        vertical_ratio = (
            left_vertical * left_w + right_vertical * right_w
        ) / (left_w + right_w)

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
    *,
    fixation: bool = False,
    fixation_duration: float = 0.0,
    fixation_x: float = 0.5,
    fixation_y: float = 0.5,
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
            fixation=fixation,
            fixation_duration=fixation_duration,
            fixation_x=fixation_x,
            fixation_y=fixation_y,
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
    last_raw_x = 0.5
    last_raw_y = 0.5
    last_features: np.ndarray | None = None

    gaze_mapper: RidgeGazeMapper | None = None
    # 跟手优先，仍限速避免来回甩
    kalman = KalmanGazeFilter(
        process_var=4.0e-3,
        measure_var=1.2e-3,
        jump_threshold=0.12,
        jump_blend=0.40,
        max_velocity=2.8,
    )
    calib_index = 0
    calib_phase_start = time.time()
    calib_point_samples = 0
    calib_results: list[tuple[np.ndarray, float, float]] = []
    calib_done_names: set[str] = set()
    last_frame_time = time.time()
    idt = IdtFixationDetector()

    def begin_calibration() -> None:
        nonlocal gaze_mapper
        nonlocal calib_index
        nonlocal calib_phase_start
        nonlocal calib_point_samples
        nonlocal calib_results
        nonlocal calib_done_names
        nonlocal smooth_x
        nonlocal smooth_y
        nonlocal last_raw_x
        nonlocal last_raw_y
        nonlocal last_features

        gaze_mapper = None
        kalman.reset(0.5, 0.5)
        reset_head_pose_smoother()
        idt.reset()
        calib_index = 0
        calib_phase_start = time.time()
        calib_point_samples = 0
        calib_results = []
        calib_done_names = set()
        smooth_x = 0.5
        smooth_y = 0.5
        last_raw_x = 0.5
        last_raw_y = 0.5
        last_features = None

        print(
            "[VPet-Gaze] 9-point Ridge calibration started. "
            "Look at each yellow target until it turns green."
        )

    screen_w, screen_h = get_primary_screen_size()
    fullscreen = PREVIEW_START_FULLSCREEN

    cv2.namedWindow(PREVIEW_WINDOW_NAME, cv2.WINDOW_NORMAL)
    cv2.resizeWindow(PREVIEW_WINDOW_NAME, screen_w, screen_h)
    set_preview_fullscreen(fullscreen)

    print(
        f"[VPet-Gaze] Preview {screen_w}x{screen_h}. "
        f"I-DT debug {FIXATION_DURATION_SECONDS:.0f}s → red marker. "
        "F=fullscreen, Esc=window, C=recalibrate, Q=quit"
    )

    def show_preview(
        camera_frame: np.ndarray | None,
        *,
        screen_x: float | None,
        screen_y: float | None,
        lines: list[tuple[str, tuple[int, int, int]]],
        calibrating: bool = False,
        calib_target: CalibrationTarget | None = None,
        marker_fixating: bool = False,
    ) -> str:
        """
        显示全屏注视预览。
        返回: 'quit' | 'continue'
        """
        nonlocal fullscreen

        canvas = build_screen_preview(
            screen_w,
            screen_h,
            camera_frame,
            screen_x=screen_x,
            screen_y=screen_y,
            lines=lines,
            calibrating=calibrating,
            calib_target=calib_target,
            calib_done_names=calib_done_names,
            marker_fixating=marker_fixating,
        )
        cv2.imshow(PREVIEW_WINDOW_NAME, canvas)
        key = cv2.waitKey(1) & 0xFF

        if key == ord("q"):
            return "quit"

        if key == 27:
            if fullscreen:
                fullscreen = False
                set_preview_fullscreen(False)
                return "continue"
            return "quit"

        if key == ord("f"):
            fullscreen = not fullscreen
            set_preview_fullscreen(fullscreen)
            return "continue"

        if key == ord("c"):
            begin_calibration()

        return "continue"

    def hold_last_gaze(message: str, color: tuple[int, int, int]) -> bool:
        """
        眨眼/噪声帧时保持上一帧输出，避免 valid 断流造成桌宠跳变。
        返回 True 表示应退出主循环。
        """
        if gaze_mapper is not None:
            fixating, fix_dur, fix_cx, fix_cy = idt.update(
                smooth_x,
                smooth_y,
                time.time(),
            )
            update_shared_state(
                raw_x=last_raw_x,
                raw_y=last_raw_y,
                gaze_x=(smooth_x - 0.5) * 2.0,
                gaze_y=(smooth_y - 0.5) * 2.0,
                screen_x=smooth_x,
                screen_y=smooth_y,
                fixation=fixating,
                fixation_duration=fix_dur,
                fixation_x=fix_cx,
                fixation_y=fix_cy,
            )
            marker_x: float | None = smooth_x
            marker_y: float | None = smooth_y
            idt_hint = (
                f"I-DT FIXATION {fix_dur:.1f}s"
                if fixating
                else f"I-DT {fix_dur:.1f}s / {FIXATION_DURATION_SECONDS:.0f}s"
            )
            lines = [
                (message, color),
                (idt_hint, (0, 0, 255) if fixating else (180, 180, 180)),
            ]
            marker_fixating = fixating
        else:
            set_invalid_state(calibrating=True)
            marker_x = None
            marker_y = None
            lines = [(message, color)]
            marker_fixating = False

        return show_preview(
            frame,
            screen_x=marker_x,
            screen_y=marker_y,
            lines=lines,
            calibrating=gaze_mapper is None,
            marker_fixating=marker_fixating,
        ) == "quit"

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
            publish_camera_jpeg(frame)

            height, width = frame.shape[:2]

            rgb = cv2.cvtColor(
                frame,
                cv2.COLOR_BGR2RGB,
            )

            result = face_mesh.process(rgb)

            if not result.multi_face_landmarks:
                set_invalid_state(
                    calibrating=gaze_mapper is None
                )

                active_target = (
                    CALIBRATION_TARGETS[calib_index]
                    if gaze_mapper is None
                    and calib_index < len(CALIBRATION_TARGETS)
                    else None
                )

                if show_preview(
                    frame,
                    screen_x=None,
                    screen_y=None,
                    lines=[("No face detected", (0, 0, 255))],
                    calibrating=gaze_mapper is None,
                    calib_target=active_target,
                ) == "quit":
                    break

                continue

            landmarks = (
                result.multi_face_landmarks[0].landmark
            )

            extracted = extract_gaze_features(
                landmarks,
                width,
                height,
            )

            if extracted is None:
                if hold_last_gaze("Feature invalid", (0, 165, 255)):
                    break
                continue

            features, raw_x, raw_y, eye_openness = extracted

            # ------------------------------------------------
            # 九点校准 + 全帧特征采样 → Ridge 映射
            # ------------------------------------------------

            if gaze_mapper is None:
                set_invalid_state(calibrating=True)

                if calib_index >= len(CALIBRATION_TARGETS):
                    if show_preview(
                        frame,
                        screen_x=None,
                        screen_y=None,
                        lines=[
                            ("Calibration failed. Press C to retry.", (0, 0, 255)),
                        ],
                        calibrating=True,
                    ) == "quit":
                        break
                    continue

                target = CALIBRATION_TARGETS[calib_index]
                elapsed = time.time() - calib_phase_start
                sample_ok = eye_openness > MIN_NORMALIZED_EYE_OPENNESS

                if elapsed < CALIBRATION_SETTLE_SECONDS:
                    phase_text = "Get ready..."
                    remaining = CALIBRATION_SETTLE_SECONDS - elapsed
                elif (
                    elapsed
                    < CALIBRATION_SETTLE_SECONDS
                    + CALIBRATION_CAPTURE_SECONDS
                ):
                    phase_text = "Hold gaze"
                    remaining = (
                        CALIBRATION_SETTLE_SECONDS
                        + CALIBRATION_CAPTURE_SECONDS
                        - elapsed
                    )
                    if sample_ok:
                        calib_results.append(
                            (
                                features.copy(),
                                target.screen_x,
                                target.screen_y,
                            )
                        )
                        calib_point_samples += 1
                else:
                    if calib_point_samples >= CALIBRATION_MIN_SAMPLES:
                        calib_done_names.add(target.name)
                        print(
                            f"[VPet-Gaze] Calibrated {target.name}: "
                            f"n={calib_point_samples} -> "
                            f"screen=({target.screen_x:.2f}, {target.screen_y:.2f})"
                        )
                    else:
                        print(
                            f"[VPet-Gaze] Not enough samples for "
                            f"{target.name}, retrying this point..."
                        )
                        # 丢掉本点刚采的样本（注意 list[:-0] 会变成空列表）
                        if calib_point_samples > 0:
                            calib_results = calib_results[:-calib_point_samples]
                        calib_phase_start = time.time()
                        calib_point_samples = 0
                        continue

                    calib_index += 1
                    calib_phase_start = time.time()
                    calib_point_samples = 0

                    if calib_index >= len(CALIBRATION_TARGETS):
                        gaze_mapper = fit_ridge_mapper(calib_results)
                        if gaze_mapper is None:
                            print(
                                "[VPet-Gaze] Failed to fit Ridge mapper. "
                                "Press C to recalibrate."
                            )
                            calib_index = len(CALIBRATION_TARGETS)
                        else:
                            kalman.reset(0.5, 0.5)
                            smooth_x = 0.5
                            smooth_y = 0.5
                            print(
                                "[VPet-Gaze] 9-point calibration complete "
                                "(3D head+eye fusion + Ridge)."
                            )

                    continue

                step = calib_index + 1
                total = len(CALIBRATION_TARGETS)
                if show_preview(
                    frame,
                    screen_x=None,
                    screen_y=None,
                    lines=[
                        (
                            f"Look at yellow target: {target.name} "
                            f"({step}/{total})",
                            (0, 255, 255),
                        ),
                        (
                            f"{phase_text}  {remaining:.1f}s  "
                            f"samples={calib_point_samples}",
                            (0, 255, 255),
                        ),
                    ],
                    calibrating=True,
                    calib_target=target,
                ) == "quit":
                    break

                continue

            # ------------------------------------------------
            # 眨眼或闭眼：保持上一帧，不断流
            # ------------------------------------------------

            if eye_openness <= MIN_NORMALIZED_EYE_OPENNESS:
                if hold_last_gaze("Blink hold", (0, 165, 255)):
                    break
                continue

            last_raw_x = float(raw_x)
            last_raw_y = float(raw_y)
            last_features = features

            # ------------------------------------------------
            # 3D 几何 + Ridge → 屏幕坐标 → Kalman 平滑
            # features: ... fused_yaw, fused_pitch, geo_sx, geo_sy
            # ------------------------------------------------

            ridge_x, ridge_y = gaze_mapper.map(features)
            geo_sx = float(features[17])
            geo_sy = float(features[18])
            mapped_x = (1.0 - GEO_BLEND) * ridge_x + GEO_BLEND * geo_sx
            mapped_y = (1.0 - GEO_BLEND) * ridge_y + GEO_BLEND * geo_sy
            screen_x_target, screen_y_target = apply_screen_deadzone(
                mapped_x,
                mapped_y,
            )

            now = time.time()
            dt = now - last_frame_time
            last_frame_time = now
            smooth_x, smooth_y = kalman.update(
                screen_x_target,
                screen_y_target,
                dt=dt,
            )

            final_gaze_x = (smooth_x - 0.5) * 2.0
            final_gaze_y = (smooth_y - 0.5) * 2.0

            # I-DT 调试：触发注视后预览点变红 + 控制台提示
            fixating, fix_dur, fix_cx, fix_cy = idt.update(
                smooth_x,
                smooth_y,
                now,
            )

            update_shared_state(
                raw_x=last_raw_x,
                raw_y=last_raw_y,
                gaze_x=final_gaze_x,
                gaze_y=final_gaze_y,
                screen_x=smooth_x,
                screen_y=smooth_y,
                fixation=fixating,
                fixation_duration=fix_dur,
                fixation_x=fix_cx,
                fixation_y=fix_cy,
            )

            head_yaw = float(features[10])
            head_pitch = float(features[11])
            fused_yaw = float(features[15])
            fused_pitch = float(features[16])

            # ------------------------------------------------
            # 全屏预览：绿点=跟踪中；红点=I-DT 已触发注视
            # ------------------------------------------------

            idt_color = (0, 0, 255) if fixating else (180, 180, 180)
            idt_line = (
                f"I-DT FIXATION  {fix_dur:.1f}s / {FIXATION_DURATION_SECONDS:.0f}s  "
                f"center=({fix_cx:.2f},{fix_cy:.2f})"
                if fixating
                else (
                    f"I-DT accumulating  {fix_dur:.1f}s / "
                    f"{FIXATION_DURATION_SECONDS:.0f}s  "
                    f"(dispersion<{IDT_DISPERSION_THRESHOLD})"
                )
            )

            if show_preview(
                frame,
                screen_x=smooth_x,
                screen_y=smooth_y,
                marker_fixating=fixating,
                lines=[
                    (
                        f"gaze screen=({smooth_x:.2f}, {smooth_y:.2f})",
                        (255, 255, 255),
                    ),
                    (idt_line, idt_color),
                    (
                        f"3D head=({head_yaw:+.2f},{head_pitch:+.2f})  "
                        f"fused=({fused_yaw:+.2f},{fused_pitch:+.2f})  "
                        f"C=recalibrate",
                        (200, 200, 200),
                    ),
                ],
            ) == "quit":
                break

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
            "Open /gaze for tracking data; "
            "/camera/jpeg shares the live frame with FaceDetect."
        )
    }


@app.get("/camera/jpeg")
def get_camera_jpeg():
    """共享当前摄像头帧（JPEG），供 FaceDetect 浏览器/后端复用。"""
    with _latest_jpeg_lock:
        data = _latest_jpeg
        ts = _latest_jpeg_ts
        size = _latest_jpeg_size

    if not data:
        return Response(
            content=b"no camera frame yet",
            status_code=503,
            media_type="text/plain",
        )

    age = max(0.0, time.time() - ts)
    return Response(
        content=data,
        media_type="image/jpeg",
        headers={
            "Cache-Control": "no-store",
            "X-Frame-Age": f"{age:.3f}",
            "X-Frame-Width": str(size[0]),
            "X-Frame-Height": str(size[1]),
        },
    )


@app.get("/camera/status")
def get_camera_status() -> dict[str, float | bool | int]:
    with _latest_jpeg_lock:
        ts = _latest_jpeg_ts
        size = _latest_jpeg_size
        has = _latest_jpeg is not None
    age = (time.time() - ts) if has and ts > 0 else -1.0
    return {
        "ok": has and 0 <= age < 2.0,
        "has_frame": has,
        "age_sec": round(age, 3),
        "width": size[0],
        "height": size[1],
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
            "fixation": state.fixation,
            "fixation_duration": round(state.fixation_duration, 3),
            "fixation_x": round(state.fixation_x, 5),
            "fixation_y": round(state.fixation_y, 5),
            "timestamp": state.timestamp,
        }


@app.post("/recalibrate")
def recalibrate() -> dict[str, str]:
    recalibration_requested.set()

    return {
        "message": "9-point Ridge gaze recalibration requested."
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
"""
VPet-Gaze 升级核心：3D 头姿 + 眼球硬融合 + Ridge + Kalman。

管线：
1. MediaPipe landmarks → solvePnP 得到 3D 头部位姿 R
2. 虹膜比例 → 眼球相对转角 (eye_yaw / eye_pitch)
3. 硬融合：gaze_cam = R_head · R_eye · forward
4. 投影到虚拟屏幕得到几何注视点，并与眼/头特征一起做 Ridge 校准
5. Kalman 平滑输出
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np


# MediaPipe Face Mesh 索引
LEFT_IRIS = (468, 469, 470, 471, 472)
RIGHT_IRIS = (473, 474, 475, 476, 477)
LEFT_CORNERS = (33, 133)
RIGHT_CORNERS = (362, 263)
LEFT_UPPER_LIDS = (159, 160, 158)
LEFT_LOWER_LIDS = (145, 144, 153)
RIGHT_UPPER_LIDS = (386, 385, 387)
RIGHT_LOWER_LIDS = (374, 380, 373)

NOSE_TIP = 1
CHIN = 152
FOREHEAD = 10
LEFT_EYE_OUTER = 33
RIGHT_EYE_OUTER = 263
LEFT_MOUTH = 61
RIGHT_MOUTH = 291

# solvePnP 用的通用 3D 人脸模型点（毫米量级）
# 顺序：鼻尖、下巴、左眼外角、右眼外角、左嘴角、右嘴角
HEAD_POSE_LANDMARKS = (
    NOSE_TIP,
    CHIN,
    LEFT_EYE_OUTER,
    RIGHT_EYE_OUTER,
    LEFT_MOUTH,
    RIGHT_MOUTH,
)
HEAD_MODEL_POINTS = np.array(
    [
        (0.0, 0.0, 0.0),
        (0.0, -63.6, -12.5),
        (-43.3, 32.7, -26.0),
        (43.3, 32.7, -26.0),
        (-28.9, -28.9, -24.1),
        (28.9, -28.9, -24.1),
    ],
    dtype=np.float64,
)

# 眼球相对转角量程（弧度）：虹膜从中心扫到边缘时的近似贡献
EYE_YAW_SCALE = 0.55
EYE_PITCH_SCALE = 0.48

# 虚拟屏幕：用融合角映射到 [0,1] 的视野半角（弧度）
SCREEN_YAW_FOV = 0.55
SCREEN_PITCH_FOV = 0.42

# 镜像画面下 solvePnP 的 yaw 常与虹膜左右相反，取反后与屏幕左右一致
INVERT_HEAD_YAW = True
# 加性融合里眼球水平是否再取反（一般 False；若盯右仍偏左再改 True）
INVERT_EYE_YAW = False

FEATURE_NAMES = (
    "left_h",
    "right_h",
    "mean_h",
    "left_v",
    "right_v",
    "mean_v",
    "left_open",
    "right_open",
    "iris_dx",
    "iris_dy",
    "head_yaw",
    "head_pitch",
    "head_roll",
    "eye_yaw",
    "eye_pitch",
    "fused_yaw",
    "fused_pitch",
    "geo_sx",
    "geo_sy",
)

FEATURE_DIM = len(FEATURE_NAMES)
RIDGE_ALPHA = 1.0
POLY_DEGREE = 2


def _clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))


def _point(landmarks, index: int, width: int, height: int) -> np.ndarray:
    landmark = landmarks[index]
    return np.array(
        (landmark.x * width, landmark.y * height),
        dtype=np.float64,
    )


def _center(
    landmarks,
    indices: tuple[int, ...],
    width: int,
    height: int,
) -> np.ndarray:
    return np.mean(
        [_point(landmarks, index, width, height) for index in indices],
        axis=0,
    )


def _projected_ratio(
    target: np.ndarray,
    start: np.ndarray,
    end: np.ndarray,
) -> float:
    axis = end - start
    denominator = float(np.dot(axis, axis)) + 1e-6
    return float(np.dot(target - start, axis) / denominator)


def _eye_openness(
    upper: np.ndarray,
    lower: np.ndarray,
    corner_a: np.ndarray,
    corner_b: np.ndarray,
) -> float:
    height = float(np.linalg.norm(lower - upper))
    width = float(np.linalg.norm(corner_b - corner_a)) + 1e-6
    return height / width


def _rotation_matrix_from_euler(
    yaw: float,
    pitch: float,
    roll: float = 0.0,
) -> np.ndarray:
    """ZYX：先 roll，再 pitch，再 yaw（弧度）。"""
    cy, sy = np.cos(yaw), np.sin(yaw)
    cp, sp = np.cos(pitch), np.sin(pitch)
    cr, sr = np.cos(roll), np.sin(roll)

    return np.array(
        [
            [cy * cp, cy * sp * sr - sy * cr, cy * sp * cr + sy * sr],
            [sy * cp, sy * sp * sr + cy * cr, sy * sp * cr - cy * sr],
            [-sp, cp * sr, cp * cr],
        ],
        dtype=np.float64,
    )


def _rotation_to_ypr(rotation: np.ndarray) -> tuple[float, float, float]:
    """从旋转矩阵提取 yaw / pitch / roll（弧度）。"""
    sy = float(np.sqrt(rotation[0, 0] ** 2 + rotation[1, 0] ** 2))
    singular = sy < 1e-6

    if not singular:
        yaw = float(np.arctan2(rotation[1, 0], rotation[0, 0]))
        pitch = float(np.arctan2(-rotation[2, 0], sy))
        roll = float(np.arctan2(rotation[2, 1], rotation[2, 2]))
    else:
        yaw = float(np.arctan2(-rotation[0, 1], rotation[1, 1]))
        pitch = float(np.arctan2(-rotation[2, 0], sy))
        roll = 0.0

    return yaw, pitch, roll


def estimate_head_pose_2d_fallback(landmarks) -> tuple[float, float, float]:
    """solvePnP 失败时的 2D 粗估。"""
    nose = landmarks[NOSE_TIP]
    chin = landmarks[CHIN]
    forehead = landmarks[FOREHEAD]
    left = landmarks[LEFT_EYE_OUTER]
    right = landmarks[RIGHT_EYE_OUTER]

    eye_mid_x = 0.5 * (left.x + right.x)
    eye_mid_y = 0.5 * (left.y + right.y)
    eye_width = abs(right.x - left.x) + 1e-6
    face_height = abs(chin.y - forehead.y) + 1e-6

    yaw = float((nose.x - eye_mid_x) / eye_width) * 0.8
    pitch = float((nose.y - eye_mid_y) / face_height) * 0.8
    roll = float(np.arctan2(right.y - left.y, right.x - left.x))
    return yaw, pitch, roll


class HeadPoseSmoother:
    """对头姿欧拉角做 EMA，抑制 solvePnP 帧间翻转/抖动。"""

    def __init__(self, alpha: float = 0.42) -> None:
        self.alpha = alpha
        self.yaw: float | None = None
        self.pitch: float | None = None
        self.roll: float | None = None
        self.rvec: np.ndarray | None = None
        self.tvec: np.ndarray | None = None

    def reset(self) -> None:
        self.yaw = None
        self.pitch = None
        self.roll = None
        self.rvec = None
        self.tvec = None

    def update(
        self,
        yaw: float,
        pitch: float,
        roll: float,
        rvec: np.ndarray | None = None,
        tvec: np.ndarray | None = None,
    ) -> tuple[float, float, float, np.ndarray]:
        if self.yaw is None:
            self.yaw, self.pitch, self.roll = yaw, pitch, roll
        else:
            a = self.alpha
            self.yaw = (1.0 - a) * self.yaw + a * yaw
            self.pitch = (1.0 - a) * self.pitch + a * pitch
            self.roll = (1.0 - a) * self.roll + a * roll

        if rvec is not None:
            self.rvec = rvec.copy()
        if tvec is not None:
            self.tvec = tvec.copy()

        rotation = _rotation_matrix_from_euler(
            float(self.yaw),
            float(self.pitch),
            float(self.roll),
        )
        return float(self.yaw), float(self.pitch), float(self.roll), rotation


_head_pose_smoother = HeadPoseSmoother()


def reset_head_pose_smoother() -> None:
    _head_pose_smoother.reset()


def estimate_head_pose_3d(
    landmarks,
    width: int,
    height: int,
) -> tuple[float, float, float, np.ndarray]:
    """
    MediaPipe + solvePnP 估计 3D 头部位姿。

    返回:
        head_yaw, head_pitch, head_roll (弧度)
        rotation_matrix: 3x3，把头坐标系向量变到相机坐标系
    """
    image_points = np.array(
        [_point(landmarks, idx, width, height) for idx in HEAD_POSE_LANDMARKS],
        dtype=np.float64,
    )

    focal = float(width)
    camera_matrix = np.array(
        [
            [focal, 0.0, width * 0.5],
            [0.0, focal, height * 0.5],
            [0.0, 0.0, 1.0],
        ],
        dtype=np.float64,
    )
    dist_coeffs = np.zeros((4, 1), dtype=np.float64)

    use_guess = (
        _head_pose_smoother.rvec is not None
        and _head_pose_smoother.tvec is not None
    )
    if use_guess:
        ok, rvec, tvec = cv2.solvePnP(
            HEAD_MODEL_POINTS,
            image_points,
            camera_matrix,
            dist_coeffs,
            _head_pose_smoother.rvec.copy(),
            _head_pose_smoother.tvec.copy(),
            useExtrinsicGuess=True,
            flags=cv2.SOLVEPNP_ITERATIVE,
        )
    else:
        ok, rvec, tvec = cv2.solvePnP(
            HEAD_MODEL_POINTS,
            image_points,
            camera_matrix,
            dist_coeffs,
            flags=cv2.SOLVEPNP_ITERATIVE,
        )

    if not ok:
        yaw, pitch, roll = estimate_head_pose_2d_fallback(landmarks)
        if INVERT_HEAD_YAW:
            yaw = -float(yaw)
        return _head_pose_smoother.update(yaw, pitch, roll)

    try:
        rvec, tvec = cv2.solvePnPRefineVVS(
            HEAD_MODEL_POINTS,
            image_points,
            camera_matrix,
            dist_coeffs,
            rvec,
            tvec,
        )
    except Exception:
        pass

    rotation, _ = cv2.Rodrigues(rvec)
    yaw, pitch, roll = _rotation_to_ypr(rotation)

    if INVERT_HEAD_YAW:
        yaw = -float(yaw)

    return _head_pose_smoother.update(
        float(yaw),
        float(pitch),
        float(roll),
        rvec=rvec,
        tvec=tvec,
    )


def eye_ratios_to_angles(
    mean_h: float,
    mean_v: float,
) -> tuple[float, float]:
    """
    虹膜比例 → 眼球相对头部的转角（弧度）。

    mean_h: 0 左 / 1 右（镜像画面下与屏幕左右一致）
    mean_v: 眼睑坐标系中虹膜位置；pitch 用 (0.5 - mean_v)，
            使向下看 → 正 pitch → 屏幕下方。
    """
    eye_yaw = (mean_h - 0.5) * 2.0 * EYE_YAW_SCALE
    if INVERT_EYE_YAW:
        eye_yaw = -eye_yaw
    eye_pitch = (0.5 - mean_v) * 2.0 * EYE_PITCH_SCALE
    return float(eye_yaw), float(eye_pitch)


def fuse_head_and_eye(
    head_yaw: float,
    head_pitch: float,
    eye_yaw: float,
    eye_pitch: float,
) -> tuple[float, float, float, float]:
    """
    加性融合（比旋转矩阵连乘更稳，避免轴向约定错误导致左右反了）：

        gaze ≈ head + eye

    返回:
        fused_yaw, fused_pitch, geo_sx, geo_sy
    """
    fused_yaw = float(head_yaw + eye_yaw)
    fused_pitch = float(head_pitch + eye_pitch)

    geo_sx = _clamp(0.5 + fused_yaw / SCREEN_YAW_FOV, 0.0, 1.0)
    geo_sy = _clamp(0.5 + fused_pitch / SCREEN_PITCH_FOV, 0.0, 1.0)

    return fused_yaw, fused_pitch, geo_sx, geo_sy


def extract_gaze_features(
    landmarks,
    width: int,
    height: int,
) -> tuple[np.ndarray, float, float, float] | None:
    """
    提取视线特征（含 3D 头姿硬融合）。

    返回:
        features, mean_h, mean_v, eye_openness
    """
    left_iris = _center(landmarks, LEFT_IRIS, width, height)
    right_iris = _center(landmarks, RIGHT_IRIS, width, height)

    left_c1 = _point(landmarks, LEFT_CORNERS[0], width, height)
    left_c2 = _point(landmarks, LEFT_CORNERS[1], width, height)
    right_c1 = _point(landmarks, RIGHT_CORNERS[0], width, height)
    right_c2 = _point(landmarks, RIGHT_CORNERS[1], width, height)

    left_upper = _center(landmarks, LEFT_UPPER_LIDS, width, height)
    left_lower = _center(landmarks, LEFT_LOWER_LIDS, width, height)
    right_upper = _center(landmarks, RIGHT_UPPER_LIDS, width, height)
    right_lower = _center(landmarks, RIGHT_LOWER_LIDS, width, height)

    left_h = _clamp(_projected_ratio(left_iris, left_c1, left_c2), 0.0, 1.0)
    right_h = _clamp(_projected_ratio(right_iris, right_c1, right_c2), 0.0, 1.0)

    def vertical_ratio(iris, upper, lower) -> float:
        eye_vec = lower - upper
        if float(np.linalg.norm(eye_vec)) < 2.0:
            return float("nan")
        ratio = _projected_ratio(iris, upper, lower)
        return _clamp(ratio, 0.05, 0.95)

    left_v = vertical_ratio(left_iris, left_upper, left_lower)
    right_v = vertical_ratio(right_iris, right_upper, right_lower)

    left_open = _eye_openness(left_upper, left_lower, left_c1, left_c2)
    right_open = _eye_openness(right_upper, right_lower, right_c1, right_c2)
    eye_openness = 0.5 * (left_open + right_open)

    if np.isnan(left_v) and np.isnan(right_v):
        return None

    if np.isnan(left_v):
        left_v = right_v
    if np.isnan(right_v):
        right_v = left_v

    if abs(left_v - right_v) > 0.18:
        mean_v = 0.5 * (left_v + right_v)
        left_v = mean_v
        right_v = mean_v

    mean_h = 0.5 * (left_h + right_h)
    mean_v = 0.5 * (left_v + right_v)

    left_outer = landmarks[LEFT_EYE_OUTER]
    right_outer = landmarks[RIGHT_EYE_OUTER]
    chin = landmarks[CHIN]
    forehead = landmarks[FOREHEAD]
    face_cx = 0.5 * (left_outer.x + right_outer.x) * width
    face_cy = 0.5 * (forehead.y + chin.y) * height
    face_w = abs(right_outer.x - left_outer.x) * width + 1e-6
    face_h = abs(chin.y - forehead.y) * height + 1e-6

    iris_mid = 0.5 * (left_iris + right_iris)
    iris_dx = float((iris_mid[0] - face_cx) / face_w)
    iris_dy = float((iris_mid[1] - face_cy) / face_h)

    head_yaw, head_pitch, head_roll, _head_rotation = estimate_head_pose_3d(
        landmarks,
        width,
        height,
    )
    eye_yaw, eye_pitch = eye_ratios_to_angles(mean_h, mean_v)
    fused_yaw, fused_pitch, geo_sx, geo_sy = fuse_head_and_eye(
        head_yaw,
        head_pitch,
        eye_yaw,
        eye_pitch,
    )

    features = np.array(
        [
            left_h,
            right_h,
            mean_h,
            left_v,
            right_v,
            mean_v,
            left_open,
            right_open,
            iris_dx,
            iris_dy,
            head_yaw,
            head_pitch,
            head_roll,
            eye_yaw,
            eye_pitch,
            fused_yaw,
            fused_pitch,
            geo_sx,
            geo_sy,
        ],
        dtype=np.float64,
    )

    return features, mean_h, mean_v, eye_openness


def expand_polynomial(
    features: np.ndarray,
    degree: int = POLY_DEGREE,
) -> np.ndarray:
    """二次多项式特征展开（含交叉项），首项为偏置 1。"""
    x = np.asarray(features, dtype=np.float64).ravel()
    terms = [1.0]
    terms.extend(float(v) for v in x)

    if degree >= 2:
        for i in range(len(x)):
            for j in range(i, len(x)):
                terms.append(float(x[i] * x[j]))

    return np.asarray(terms, dtype=np.float64)


@dataclass
class RidgeGazeMapper:
    """
    screen_x/y = w · φ(features)
    φ 为二次多项式展开；w 由 Ridge 最小二乘求解。
    """

    weights_x: np.ndarray
    weights_y: np.ndarray
    feature_mean: np.ndarray
    feature_std: np.ndarray
    mean_error: float

    def _normalize(self, features: np.ndarray) -> np.ndarray:
        return (features - self.feature_mean) / self.feature_std

    def map(self, features: np.ndarray) -> tuple[float, float]:
        phi = expand_polynomial(self._normalize(features))
        sx = float(np.dot(self.weights_x, phi))
        sy = float(np.dot(self.weights_y, phi))
        return _clamp(sx, 0.0, 1.0), _clamp(sy, 0.0, 1.0)


def fit_ridge_mapper(
    samples: list[tuple[np.ndarray, float, float]],
    alpha: float = RIDGE_ALPHA,
) -> RidgeGazeMapper | None:
    """
    samples: (features, screen_x, screen_y)
    """
    if len(samples) < FEATURE_DIM + 2:
        print(
            f"[VPet-Gaze] Not enough calibration samples: {len(samples)}"
        )
        return None

    feature_matrix = np.stack(
        [np.asarray(feat, dtype=np.float64) for feat, _, _ in samples],
        axis=0,
    )
    target_x = np.array([sx for _, sx, _ in samples], dtype=np.float64)
    target_y = np.array([sy for _, _, sy in samples], dtype=np.float64)

    feature_mean = feature_matrix.mean(axis=0)
    feature_std = feature_matrix.std(axis=0)
    feature_std = np.where(feature_std < 1e-6, 1.0, feature_std)
    normalized = (feature_matrix - feature_mean) / feature_std

    design = np.stack(
        [expand_polynomial(row) for row in normalized],
        axis=0,
    )

    def _solve(y: np.ndarray) -> np.ndarray | None:
        xtx = design.T @ design
        reg = alpha * np.eye(xtx.shape[0], dtype=np.float64)
        reg[0, 0] = 0.0
        try:
            return np.linalg.solve(xtx + reg, design.T @ y)
        except np.linalg.LinAlgError:
            try:
                return np.linalg.lstsq(xtx + reg, design.T @ y, rcond=None)[0]
            except np.linalg.LinAlgError:
                return None

    weights_x = _solve(target_x)
    weights_y = _solve(target_y)
    if weights_x is None or weights_y is None:
        return None

    mapper = RidgeGazeMapper(
        weights_x=weights_x,
        weights_y=weights_y,
        feature_mean=feature_mean,
        feature_std=feature_std,
        mean_error=0.0,
    )

    errors = []
    for feat, sx, sy in samples:
        px, py = mapper.map(feat)
        errors.append(float(np.hypot(px - sx, py - sy)))

    mapper.mean_error = float(np.mean(errors))
    print(
        "[VPet-Gaze] 3D-fusion Ridge mapper fitted. "
        f"samples={len(samples)} mean_err={mapper.mean_error:.3f}"
    )

    if mapper.mean_error > 0.18:
        print(
            "[VPet-Gaze] Calibration quality looks weak; "
            "press C and stare steadily at each yellow target."
        )

    return mapper


class KalmanGazeFilter:
    """
    二维常速度模型 Kalman：状态 [x, y, vx, vy]。

    process_var 越大 / measure_var 越小 → 越跟手（也会略更抖）。
    """

    def __init__(
        self,
        process_var: float = 1.5e-3,
        measure_var: float = 2.5e-3,
        jump_threshold: float = 0.22,
        jump_blend: float = 0.25,
        max_velocity: float = 1.8,
    ) -> None:
        self.process_var = process_var
        self.measure_var = measure_var
        self.jump_threshold = jump_threshold
        self.jump_blend = jump_blend
        self.max_velocity = max_velocity
        self.x = np.array([0.5, 0.5, 0.0, 0.0], dtype=np.float64)
        self.P = np.eye(4, dtype=np.float64) * 0.05
        self.initialized = False

    def reset(self, screen_x: float = 0.5, screen_y: float = 0.5) -> None:
        self.x[:] = (screen_x, screen_y, 0.0, 0.0)
        self.P = np.eye(4, dtype=np.float64) * 0.05
        self.initialized = True

    def update(
        self,
        measurement_x: float,
        measurement_y: float,
        dt: float = 1.0 / 30.0,
    ) -> tuple[float, float]:
        if not self.initialized:
            self.reset(measurement_x, measurement_y)
            return measurement_x, measurement_y

        dt = float(_clamp(dt, 1e-3, 0.05))
        f = np.array(
            [
                [1.0, 0.0, dt, 0.0],
                [0.0, 1.0, 0.0, dt],
                [0.0, 0.0, 1.0, 0.0],
                [0.0, 0.0, 0.0, 1.0],
            ],
            dtype=np.float64,
        )

        q = self.process_var * np.array(
            [
                [dt**4 / 4, 0.0, dt**3 / 2, 0.0],
                [0.0, dt**4 / 4, 0.0, dt**3 / 2],
                [dt**3 / 2, 0.0, dt**2, 0.0],
                [0.0, dt**3 / 2, 0.0, dt**2],
            ],
            dtype=np.float64,
        )

        self.x = f @ self.x
        self.P = f @ self.P @ f.T + q

        h = np.array(
            [[1.0, 0.0, 0.0, 0.0], [0.0, 1.0, 0.0, 0.0]],
            dtype=np.float64,
        )
        r = np.eye(2, dtype=np.float64) * self.measure_var
        z = np.array([measurement_x, measurement_y], dtype=np.float64)
        innov = z - h @ self.x
        s = h @ self.P @ h.T + r
        k = self.P @ h.T @ np.linalg.inv(s)
        self.x = self.x + k @ innov
        self.P = (np.eye(4) - k @ h) @ self.P

        jump = float(np.hypot(innov[0], innov[1]))
        if jump > self.jump_threshold:
            b = self.jump_blend
            self.x[0] = (1.0 - b) * self.x[0] + b * measurement_x
            self.x[1] = (1.0 - b) * self.x[1] + b * measurement_y
            # 大跨度后清零速度，避免惯性来回甩
            self.x[2] = 0.0
            self.x[3] = 0.0

        self.x[2] = _clamp(float(self.x[2]), -self.max_velocity, self.max_velocity)
        self.x[3] = _clamp(float(self.x[3]), -self.max_velocity, self.max_velocity)

        sx = _clamp(float(self.x[0]), 0.0, 1.0)
        sy = _clamp(float(self.x[1]), 0.0, 1.0)
        self.x[0], self.x[1] = sx, sy
        return sx, sy


def build_nine_point_targets(
    margin: float = 0.06,
) -> list[tuple[str, float, float]]:
    """经典 3×3 九点。"""
    m = max(0.02, min(0.2, margin))
    c = 0.5
    left, right = m, 1.0 - m
    top, bottom = m, 1.0 - m

    return [
        ("center", c, c),
        ("top-left", left, top),
        ("top", c, top),
        ("top-right", right, top),
        ("left", left, c),
        ("right", right, c),
        ("bottom-left", left, bottom),
        ("bottom", c, bottom),
        ("bottom-right", right, bottom),
    ]

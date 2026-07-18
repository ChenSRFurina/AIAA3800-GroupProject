import io
import os
import threading

import feat_bootstrap  # noqa: F401  — stub torchcodec before py-feat import
import local_models

local_models.apply_local_model_overrides()

import numpy as np
from feat import Detectorv2
from PIL import Image
import torch

DEFAULT_DEVICE = os.getenv("FEAT_DEVICE", "cuda")

_detector = None
_detector_lock = threading.Lock()
_inference_lock = threading.Lock()

LEFT_EYE_MESH = [33, 160, 158, 133, 153, 144]
RIGHT_EYE_MESH = [362, 385, 387, 263, 373, 380]
MOUTH_MESH = [61, 291, 13, 14, 78, 308]


def _resolve_device(device=None):
	if device is not None:
		return device
	if DEFAULT_DEVICE == "cuda" and not torch.cuda.is_available():
		return "cpu"
	return DEFAULT_DEVICE


def get_detector(device=None):
	global _detector

	if _detector is None:
		with _detector_lock:
			if _detector is None:
				# identity 分支非情绪推理必需，跳过可减少额外 HF 下载
				_detector = Detectorv2(
					device=_resolve_device(device),
					identity_model=None,
				)

	return _detector


def decode_jpeg_bytes(jpeg_bytes):
	with Image.open(io.BytesIO(jpeg_bytes)) as image:
		rgb_image = image.convert("RGB")
		frame = np.array(rgb_image, dtype=np.uint8)

	return torch.from_numpy(frame).permute(2, 0, 1).contiguous()


def _to_optional_float(value):
	if value is None:
		return None

	try:
		numeric_value = float(value)
	except (TypeError, ValueError):
		return None

	if np.isnan(numeric_value):
		return None

	return numeric_value


def _collect_signal(row, patterns, reducer="mean"):
	matches = []
	for column_name in row.index:
		column_text = str(column_name).lower()
		if not any(pattern in column_text for pattern in patterns):
			continue

		numeric_value = _to_optional_float(row.get(column_name))
		if numeric_value is not None:
			matches.append(numeric_value)

	if not matches:
		return None

	if reducer == "max":
		return float(np.max(matches))
	if reducer == "min":
		return float(np.min(matches))
	return float(np.mean(matches))


def _mesh_point(row, index):
	x_value = _to_optional_float(row.get(f"mesh_x_{index}"))
	y_value = _to_optional_float(row.get(f"mesh_y_{index}"))

	if x_value is None or y_value is None:
		return None

	return np.array([x_value, y_value], dtype=float)


def _distance(point_a, point_b):
	if point_a is None or point_b is None:
		return None

	return float(np.linalg.norm(point_a - point_b))


def _eye_aspect_ratio(row, indices):
	points = [_mesh_point(row, index) for index in indices]
	if any(point is None for point in points):
		return None

	horizontal = _distance(points[0], points[3])
	if horizontal is None or horizontal <= 0:
		return None

	vertical_one = _distance(points[1], points[5])
	vertical_two = _distance(points[2], points[4])
	if vertical_one is None or vertical_two is None:
		return None

	return float((vertical_one + vertical_two) / (2.0 * horizontal))


def _mouth_aspect_ratio(row):
	points = [_mesh_point(row, index) for index in MOUTH_MESH]
	if any(point is None for point in points):
		return None

	horizontal = _distance(points[0], points[1])
	if horizontal is None or horizontal <= 0:
		return None

	vertical = _distance(points[2], points[3])
	if vertical is None:
		return None

	return float(vertical / horizontal)


def _geometry_eye_closure(row):
	ear_left = _eye_aspect_ratio(row, LEFT_EYE_MESH)
	ear_right = _eye_aspect_ratio(row, RIGHT_EYE_MESH)
	ear_values = [value for value in [ear_left, ear_right] if value is not None]
	if not ear_values:
		return None

	ear = float(np.mean(ear_values))
	# Typical open-eye EAR is roughly 0.20-0.30; closed-eye EAR is much smaller.
	return float(np.clip((0.28 - ear) / 0.18, 0.0, 1.0))


def _geometry_mouth_open(row):
	mar = _mouth_aspect_ratio(row)
	if mar is None:
		return None

	# Typical closed-mouth MAR is low; a wide-open mouth during yawning is larger.
	return float(np.clip((mar - 0.18) / 0.32, 0.0, 1.0))


def _extract_pose(row):
	return {
		"pitch": _to_optional_float(row.get("Pitch")),
		"yaw": _to_optional_float(row.get("Yaw")),
		"roll": _to_optional_float(row.get("Roll")),
		"gaze_pitch": _to_optional_float(row.get("gaze_pitch")),
		"gaze_yaw": _to_optional_float(row.get("gaze_yaw")),
		"gaze_angle": _to_optional_float(row.get("gaze_angle")),
	}


def _extract_signals(row):
	eye_signal = _collect_signal(
		row,
		["eyeblink", "eyesquint", "eyeclose", "eye_close", "blink", "squint"],
		reducer="mean",
	)
	eye_signal_source = "blendshape" if eye_signal is not None else None

	geometry_eye_signal = _geometry_eye_closure(row)
	if eye_signal is None:
		eye_signal = geometry_eye_signal
		eye_signal_source = "geometry" if eye_signal is not None else None
	elif geometry_eye_signal is not None:
		eye_signal = max(eye_signal, geometry_eye_signal)
		eye_signal_source = "blendshape+geometry"

	mouth_signal = _collect_signal(
		row,
		["mouthopen", "mouthstretch", "mouthfunnel", "jawopen", "mouthpucker", "mouthshrug", "mouthclose"],
		reducer="mean",
	)
	mouth_signal_source = "blendshape" if mouth_signal is not None else None

	geometry_mouth_signal = _geometry_mouth_open(row)
	if mouth_signal is None:
		mouth_signal = geometry_mouth_signal
		mouth_signal_source = "geometry" if mouth_signal is not None else None
	elif geometry_mouth_signal is not None:
		mouth_signal = max(mouth_signal, geometry_mouth_signal)
		mouth_signal_source = "blendshape+geometry"

	return {
		"eye_signal": eye_signal,
		"mouth_signal": mouth_signal,
		"arousal": _to_optional_float(row.get("arousal")),
		"valence": _to_optional_float(row.get("valence")),
		"gaze_x": _to_optional_float(row.get("gaze_yaw")),
		"gaze_y": _to_optional_float(row.get("gaze_pitch")),
		"head_pose": _extract_pose(row),
		"signal_sources": {
			"eye_signal": eye_signal_source,
			"mouth_signal": mouth_signal_source,
		},
		"geometry_signals": {
			"eye_closure": geometry_eye_signal,
			"mouth_open": geometry_mouth_signal,
		},
	}


def infer_emotions_from_tensor(frame_tensor):
	detector = get_detector()
	batched_tensor = frame_tensor.unsqueeze(0)

	with _inference_lock:
		fex = detector.detect(
			batched_tensor,
			data_type="tensor",
			batch_size=1,
			progress_bar=False,
		)

	emotion_columns = list(getattr(fex, "emotion_columns", []))
	face_rows = []

	for row_index, (_, row) in enumerate(fex.iterrows()):
		face_score = row.get("FaceScore")
		if face_score is None or np.isnan(face_score) or face_score <= 0:
			continue

		probabilities = {}
		for emotion_name in emotion_columns:
			value = row.get(emotion_name)
			probabilities[emotion_name] = None if value is None or np.isnan(value) else float(value)

		top_emotion = None
		if probabilities:
			top_emotion = max(
				probabilities.items(),
				key=lambda item: float("-inf") if item[1] is None else item[1],
			)[0]

		face_rows.append(
			{
				"face_index": row_index,
				"face_score": float(face_score),
				"face_rect": {
					"x": None if row.get("FaceRectX") is None or np.isnan(row.get("FaceRectX")) else float(row.get("FaceRectX")),
					"y": None if row.get("FaceRectY") is None or np.isnan(row.get("FaceRectY")) else float(row.get("FaceRectY")),
					"width": None if row.get("FaceRectWidth") is None or np.isnan(row.get("FaceRectWidth")) else float(row.get("FaceRectWidth")),
					"height": None if row.get("FaceRectHeight") is None or np.isnan(row.get("FaceRectHeight")) else float(row.get("FaceRectHeight")),
				},
				"probabilities": probabilities,
				"top_emotion": top_emotion,
				"signals": _extract_signals(row),
			}
		)

	return {
		"emotion_labels": emotion_columns,
		"faces": face_rows,
	}


def infer_emotions_from_jpeg_bytes(jpeg_bytes):
	return infer_emotions_from_tensor(decode_jpeg_bytes(jpeg_bytes))


def infer_frame_features_from_jpeg_bytes(jpeg_bytes):
	return infer_emotions_from_jpeg_bytes(jpeg_bytes)
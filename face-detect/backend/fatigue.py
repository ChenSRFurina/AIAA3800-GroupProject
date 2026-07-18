import time
from collections import deque
from dataclasses import dataclass

import numpy as np


@dataclass
class Thresholds:
	eye_close_th: float = 0.65
	eye_open_th: float = 0.45
	mouth_open_th: float = 0.60
	blink_min_sec: float = 0.04
	blink_max_sec: float = 0.45
	yawn_min_sec: float = 0.80


def _is_missing(value):
	if value is None:
		return True

	try:
		return bool(np.isnan(value))
	except (TypeError, ValueError):
		return False


def _clamp(value, lower=0.0, upper=1.0):
	return float(np.clip(value, lower, upper))


def _select_primary_face(faces):
	if not faces:
		return None

	def face_score(face):
		value = face.get("face_score")
		return -1.0 if _is_missing(value) else float(value)

	return max(faces, key=face_score)


class FatigueMonitor:
	def __init__(self, fps=30, window_sec=60, thresholds=None):
		self.fps = fps
		self.window_sec = window_sec
		self.thresholds = thresholds or Thresholds()

		self.samples = deque(maxlen=max(1, int(fps * window_sec)))
		self.blink_times = deque()
		self.yawn_times = deque()

		self.eye_closed = False
		self.eye_close_start = None
		self.mouth_open = False
		self.mouth_open_start = None

	def _cleanup_old(self, now):
		cutoff = now - self.window_sec
		while self.blink_times and self.blink_times[0] < cutoff:
			self.blink_times.popleft()
		while self.yawn_times and self.yawn_times[0] < cutoff:
			self.yawn_times.popleft()

	def update(self, inference_result, now=None):
		now = time.time() if now is None else now
		self._cleanup_old(now)

		faces = inference_result.get("faces", [])
		primary_face = _select_primary_face(faces)
		if primary_face is None:
			return {
				"window_sec": self.window_sec,
				"sample_count": len(self.samples),
				"primary_face_index": None,
				"primary_face_score": None,
				"dominant_emotion": None,
				"blink_event": False,
				"yawn_event": False,
				"blink_rate_per_min": 0.0,
				"yawn_rate_per_min": 0.0,
				"eye_closed_ratio": 0.0,
				"fatigue_score": 0.0,
				"fatigue_level": "low",
				"signals": {},
			}

		signals = primary_face.get("signals", {}) or {}
		eye_signal = signals.get("eye_signal")
		mouth_signal = signals.get("mouth_signal")
		arousal_signal = signals.get("arousal")
		valence_signal = signals.get("valence")

		self.samples.append(
			{
				"ts": now,
				"eye_signal": eye_signal,
				"mouth_signal": mouth_signal,
				"arousal": arousal_signal,
				"valence": valence_signal,
			}
		)

		blink_event = False
		if not _is_missing(eye_signal):
			if not self.eye_closed and eye_signal >= self.thresholds.eye_close_th:
				self.eye_closed = True
				self.eye_close_start = now
			elif self.eye_closed and eye_signal <= self.thresholds.eye_open_th:
				closed_duration = now - self.eye_close_start if self.eye_close_start else 0.0
				self.eye_closed = False
				self.eye_close_start = None
				if self.thresholds.blink_min_sec <= closed_duration <= self.thresholds.blink_max_sec:
					self.blink_times.append(now)
					blink_event = True

		yawn_event = False
		if not _is_missing(mouth_signal):
			if not self.mouth_open and mouth_signal >= self.thresholds.mouth_open_th:
				self.mouth_open = True
				self.mouth_open_start = now
			elif self.mouth_open and mouth_signal < self.thresholds.mouth_open_th * 0.8:
				open_duration = now - self.mouth_open_start if self.mouth_open_start else 0.0
				self.mouth_open = False
				self.mouth_open_start = None
				if open_duration >= self.thresholds.yawn_min_sec:
					self.yawn_times.append(now)
					yawn_event = True

		blink_rate_per_min = len(self.blink_times) * 60.0 / self.window_sec
		yawn_rate_per_min = len(self.yawn_times) * 60.0 / self.window_sec

		eye_values = [sample["eye_signal"] for sample in self.samples if not _is_missing(sample["eye_signal"])]
		eye_closed_ratio = 0.0
		if eye_values:
			eye_closed_ratio = float(np.mean(np.array(eye_values) >= self.thresholds.eye_close_th))

		arousal_values = [sample["arousal"] for sample in self.samples if not _is_missing(sample["arousal"])]
		arousal_term = 0.5
		if arousal_values:
			arousal_term = _clamp((float(np.mean(arousal_values)) + 1.0) / 2.0)

		fatigue_score = (
			0.35 * _clamp(blink_rate_per_min / 25.0)
			+ 0.35 * _clamp(eye_closed_ratio / 0.20)
			+ 0.20 * _clamp(yawn_rate_per_min / 5.0)
			+ 0.10 * (1.0 - arousal_term)
		)
		fatigue_score = _clamp(fatigue_score)

		if fatigue_score >= 0.75:
			fatigue_level = "high"
		elif fatigue_score >= 0.45:
			fatigue_level = "medium"
		else:
			fatigue_level = "low"

		return {
			"window_sec": self.window_sec,
			"sample_count": len(self.samples),
			"primary_face_index": primary_face.get("face_index"),
			"primary_face_score": primary_face.get("face_score"),
			"dominant_emotion": primary_face.get("top_emotion"),
			"blink_event": blink_event,
			"yawn_event": yawn_event,
			"blink_rate_per_min": blink_rate_per_min,
			"yawn_rate_per_min": yawn_rate_per_min,
			"eye_closed_ratio": eye_closed_ratio,
			"fatigue_score": fatigue_score,
			"fatigue_level": fatigue_level,
			"signals": {
				"eye_signal": eye_signal,
				"mouth_signal": mouth_signal,
				"arousal": arousal_signal,
				"valence": valence_signal,
				"head_pose": signals.get("head_pose"),
				"gaze_x": signals.get("gaze_x"),
				"gaze_y": signals.get("gaze_y"),
				"signal_sources": signals.get("signal_sources"),
			},
		}
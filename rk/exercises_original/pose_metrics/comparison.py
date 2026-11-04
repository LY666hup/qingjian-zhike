"""Read-only 2D/world-3D joint angle comparisons."""

import math
import os
from dataclasses import dataclass

import numpy as np

from .geometry import (
    angle_3points_nd,
    chain_confidence,
    get_xy,
    get_xyz,
)
from .types import MetricComparison, PoseObservation


_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})


def pose_metric_comparison_enabled(environ=None) -> bool:
    environ = os.environ if environ is None else environ
    return environ.get("FITNESS_POSE_METRIC_ENABLED", "0").strip().lower() in _TRUE_VALUES


def pose_metric_debug_enabled(environ=None) -> bool:
    environ = os.environ if environ is None else environ
    return environ.get("FITNESS_POSE_DEBUG_METRICS", "0").strip().lower() in _TRUE_VALUES


def create_metric_comparison_estimator(config, environ=None):
    factory = config.get("metric_comparison_factory")
    if factory is None or not pose_metric_comparison_enabled(environ):
        return None
    environ = os.environ if environ is None else environ
    default_alpha = config.get("landmark_smooth_alpha", 0.68)
    alpha = _environment_float(
        environ, "FITNESS_POSE_3D_SMOOTH_ALPHA", default_alpha, 0.0, 1.0
    )
    min_quality = _environment_float(
        environ, "FITNESS_POSE_3D_MIN_Q", 0.25, 0.0, 1.0
    )
    max_speed = _environment_float(
        environ, "FITNESS_POSE_3D_MAX_SPEED_DPS", 360.0, 0.0, None
    )
    hard_jump = _environment_float(
        environ, "FITNESS_POSE_3D_HARD_JUMP_DEG", 90.0, 0.0, 180.0
    )
    reset_gap_ms = _environment_int(
        environ, "FITNESS_POSE_3D_RESET_GAP_MS", 1000, 1, None
    )
    return factory(
        smooth_alpha=alpha,
        min_quality=min_quality,
        max_speed_dps=max_speed,
        hard_jump_deg=hard_jump,
        reset_gap_ms=reset_gap_ms,
    )


def append_comparison_text(aux_text: str, comparison: MetricComparison) -> str:
    if not comparison.aux_text:
        return aux_text
    if not aux_text:
        return comparison.aux_text
    return f"{aux_text} | {comparison.aux_text}"


def first_world_landmarks(result):
    groups = getattr(result, "pose_world_landmarks", None)
    if not groups:
        return None
    return groups[0]


@dataclass
class _SideFilter:
    points: object = None
    raw_angle: float = float("nan")
    timestamp_ms: int = -1

    def reset(self):
        self.points = None
        self.raw_angle = float("nan")
        self.timestamp_ms = -1


class JointAngleComparisonEstimator:
    def __init__(
        self,
        left_indices,
        right_indices,
        *,
        smooth_alpha=0.68,
        min_quality=0.25,
        max_speed_dps=360.0,
        hard_jump_deg=90.0,
        reset_gap_ms=1000,
    ):
        self.left_indices = tuple(left_indices)
        self.right_indices = tuple(right_indices)
        self.smooth_alpha = smooth_alpha
        self.min_quality = min_quality
        self.max_speed_dps = max_speed_dps
        self.hard_jump_deg = hard_jump_deg
        self.reset_gap_ms = reset_gap_ms
        self._left_filter = _SideFilter()
        self._right_filter = _SideFilter()

    def reset(self):
        self._left_filter.reset()
        self._right_filter.reset()

    def update(self, observation: PoseObservation) -> MetricComparison:
        image = observation.image_landmarks
        world = observation.world_landmarks

        left_2d = self._angle(image, self.left_indices, dimensions=2)
        right_2d = self._angle(image, self.right_indices, dimensions=2)
        left_confidence = chain_confidence(image, self.left_indices)
        right_confidence = chain_confidence(image, self.right_indices)

        left_3d, left_reason = self._update_side(
            world,
            self.left_indices,
            left_confidence,
            observation.timestamp_ms,
            self._left_filter,
        )
        right_3d, right_reason = self._update_side(
            world,
            self.right_indices,
            right_confidence,
            observation.timestamp_ms,
            self._right_filter,
        )
        world_available = math.isfinite(left_3d) or math.isfinite(right_3d)
        legacy_value = _finite_min(left_2d, right_2d)

        aux_text = (
            f"2D L:{_format_angle(left_2d)} R:{_format_angle(right_2d)} "
            f"3DF L:{_format_angle(left_3d, left_reason)} "
            f"R:{_format_angle(right_3d, right_reason)} "
            f"Q L:{left_confidence:.2f} R:{right_confidence:.2f}"
        )
        return MetricComparison(
            left_angle_2d=left_2d,
            right_angle_2d=right_2d,
            left_angle_3d=left_3d,
            right_angle_3d=right_3d,
            left_chain_confidence=left_confidence,
            right_chain_confidence=right_confidence,
            legacy_value=legacy_value,
            world_available=world_available,
            aux_text=aux_text,
        )

    def _update_side(self, landmarks, indices, quality, timestamp_ms, state):
        if quality < self.min_quality:
            return float("nan"), "Q"
        if landmarks is None:
            state.reset()
            return float("nan"), "WORLD"

        points = np.asarray([get_xyz(landmarks, index) for index in indices])
        raw_angle = angle_3points_nd(*points)
        if not np.all(np.isfinite(points)) or not math.isfinite(raw_angle):
            state.reset()
            return float("nan"), "GEOM"

        if state.timestamp_ms >= 0:
            elapsed_ms = timestamp_ms - state.timestamp_ms
            if elapsed_ms <= 0:
                state.reset()
                return float("nan"), "TIME"
            if elapsed_ms > self.reset_gap_ms:
                state.reset()
            else:
                jump = abs(raw_angle - state.raw_angle)
                allowed_jump = min(
                    self.hard_jump_deg,
                    max(10.0, self.max_speed_dps * elapsed_ms / 1000.0),
                )
                if jump > allowed_jump:
                    return float("nan"), "JUMP"

        if state.points is None:
            filtered_points = points.copy()
        else:
            filtered_points = (
                self.smooth_alpha * state.points
                + (1.0 - self.smooth_alpha) * points
            )
        filtered_angle = angle_3points_nd(*filtered_points)
        if not math.isfinite(filtered_angle):
            state.reset()
            return float("nan"), "GEOM"

        state.points = filtered_points
        state.raw_angle = raw_angle
        state.timestamp_ms = timestamp_ms
        return filtered_angle, ""

    @staticmethod
    def _angle(landmarks, indices, dimensions):
        if landmarks is None:
            return float("nan")
        getter = get_xy if dimensions == 2 else get_xyz
        points = [getter(landmarks, index) for index in indices]
        return angle_3points_nd(*points)


def _finite_min(*values):
    finite_values = [value for value in values if math.isfinite(value)]
    return min(finite_values) if finite_values else float("nan")


def _format_angle(value, reason=""):
    if math.isfinite(value):
        return f"{value:.1f}"
    return f"N/A({reason})" if reason else "N/A"


def _environment_float(environ, name, default, minimum, maximum):
    raw = environ.get(name, "").strip()
    if not raw:
        return float(default)
    try:
        value = float(raw)
    except ValueError:
        return float(default)
    if not math.isfinite(value) or value < minimum:
        return float(default)
    if maximum is not None and value > maximum:
        return float(default)
    return value


def _environment_int(environ, name, default, minimum, maximum):
    raw = environ.get(name, "").strip()
    if not raw:
        return int(default)
    try:
        value = int(raw)
    except ValueError:
        return int(default)
    if value < minimum:
        return int(default)
    if maximum is not None and value > maximum:
        return int(default)
    return value

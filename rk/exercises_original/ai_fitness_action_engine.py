from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
import logging
from types import MappingProxyType
from typing import Mapping, Optional

from ai_fitness_imu_validation import (
    IMU_STATE,
    IMU_VALIDATION_CONFIG,
    validate_imu_window,
)

from pose_metrics.comparison import (
    append_comparison_text,
    create_metric_comparison_estimator,
    pose_metric_debug_enabled,
)
from pose_metrics.shadow_counter import Shadow3DRepCounter
from pose_metrics.types import PoseObservation


LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class ActionSnapshot:
    mode: str
    count: int
    score: int
    tip: str
    phase: str
    metric_name: str
    metric_value: float = 0.0
    aux_text: str = ""
    person_text: str = "No person"
    stats: Mapping[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class ActionUpdate:
    snapshot: ActionSnapshot
    landmarks: Optional[list] = None
    completed: bool = False
    publish_feedback: bool = True


class SmoothedLandmark:
    def __init__(self, landmark):
        self.x = landmark.x
        self.y = landmark.y
        self.z = getattr(landmark, "z", 0.0)
        self.visibility = getattr(landmark, "visibility", 0.0)
        self.presence = getattr(landmark, "presence", 0.0)


class LandmarkSmoother:
    def __init__(self, alpha: float):
        self.alpha = alpha
        self._cache = None

    def reset(self):
        self._cache = None

    def update(self, landmarks):
        if landmarks is None:
            self.reset()
            return None

        if self._cache is None or len(self._cache) != len(landmarks):
            self._cache = [SmoothedLandmark(landmark) for landmark in landmarks]
            return self._cache

        alpha = self.alpha
        for old, landmark in zip(self._cache, landmarks):
            old.x = alpha * old.x + (1 - alpha) * landmark.x
            old.y = alpha * old.y + (1 - alpha) * landmark.y
            old.z = alpha * old.z + (1 - alpha) * getattr(landmark, "z", 0.0)
            old.visibility = getattr(landmark, "visibility", 0.0)
            old.presence = getattr(landmark, "presence", 0.0)
        return self._cache


class RepetitionActionEngine:
    """State-only repetition recognizer built from an existing action config."""

    RESULT_TIPS = ("GOOD", "TOO_FAST", "RANGE_SMALL", "SWING")

    def __init__(self, config):
        self.config = config
        self.mode = config["mode"]
        self.metric_name = config.get("metric_name", "Value")
        self.compute_metric = config["compute_metric"]
        self.metric_comparison = create_metric_comparison_estimator(config)
        self.debug_metric_comparison = pose_metric_debug_enabled()
        self._last_debug_metric_time = 0.0
        self._shadow_counter: Optional[Shadow3DRepCounter] = None
        if self.metric_comparison is not None:
            shadow_cfg = config.get("shadow_3d")
            if shadow_cfg:
                self._shadow_counter = Shadow3DRepCounter(
                    target_threshold_3d=shadow_cfg["target_threshold_3d"],
                    finish_threshold_3d=shadow_cfg["finish_threshold_3d"],
                    target_hold_frames=shadow_cfg.get("target_hold_frames", 3),
                    finish_hold_frames=shadow_cfg.get("finish_hold_frames", 4),
                    min_count_interval=shadow_cfg.get("min_count_interval", 1.5),
                    label=shadow_cfg.get("label", self.mode),
                    debug=shadow_cfg.get("debug", False),
                )
        self.metric_history = deque(maxlen=config.get("metric_history_len", 7))
        self.smoother = LandmarkSmoother(
            config.get("landmark_smooth_alpha", 0.68)
        )
        self.reset()

    def reset(self):
        self.count = 0
        self.score = 100
        self.stats = {
            "GOOD": 0,
            "TOO_FAST": 0,
            "RANGE_SMALL": 0,
            "SWING": 0,
        }
        self.last_sent_count = -1
        self.last_sent_tip = ""
        self.reset_transient()
        if self._shadow_counter is not None:
            self._shadow_counter.reset()
        return self.snapshot()

    def reset_transient(self):
        self.tip = "READY"
        self.phase = "WAIT_TARGET"
        self.last_count_time = 0.0
        self.target_start_time = 0.0
        self.candidate_start = None
        self.best_metric = None
        self.target_frame_count = 0
        self.finish_frame_count = 0
        self.tip_hold_until = 0.0
        self.metric_value = 0.0
        self.aux_text = ""
        self.person_text = "No person"
        self.metric_history.clear()
        self.smoother.reset()
        self._last_debug_metric_time = 0.0
        if self.metric_comparison is not None:
            self.metric_comparison.reset()
        if self._shadow_counter is not None:
            self._shadow_counter.reset_transient()
        return self.snapshot()

    def activate(self):
        return self.reset_transient()

    def deactivate(self):
        return self.reset_transient()

    def recover_after_camera_reconnect(self):
        return self.reset_transient()

    def process_fresh(
        self,
        landmarks,
        imu_data,
        now: float,
        *,
        world_landmarks=None,
        timestamp_ms=None,
    ):
        smoothed = self.smoother.update(landmarks)
        metric_value, aux_text = self.compute_metric(smoothed)
        if self.metric_comparison is not None:
            comparison = self.metric_comparison.update(
                PoseObservation(
                    image_landmarks=smoothed,
                    world_landmarks=world_landmarks,
                    timestamp_ms=int(timestamp_ms if timestamp_ms is not None else now * 1000),
                )
            )
            aux_text = append_comparison_text(aux_text, comparison)
            if self.debug_metric_comparison and now - self._last_debug_metric_time >= 1.0:
                print(f"[{self.mode}] {comparison.aux_text}")
                self._last_debug_metric_time = now
            if self._shadow_counter is not None:
                shadow_event = self._shadow_counter.update(comparison, now)
                if shadow_event:
                    print(f"[{self.mode}] {shadow_event} (3D shadow count={self._shadow_counter.count})")
        update = self.process_metric(metric_value, imu_data, now, aux_text=aux_text)
        return ActionUpdate(
            update.snapshot,
            smoothed,
            update.completed,
            update.publish_feedback,
        )

    def process_metric(
        self,
        metric_value: float,
        imu_data,
        now: float,
        aux_text: str = "",
    ):
        self.person_text = "Person detected"
        self.aux_text = aux_text
        if self.tip == "NO_PERSON" and self.phase == "WAIT_TARGET":
            self.tip = "READY"

        self.metric_history.append(metric_value)
        self.metric_value = sum(self.metric_history) / len(self.metric_history)

        is_target = _condition(
            self.metric_value,
            self.config["target_op"],
            self.config["target_threshold"],
        )
        is_finish = _condition(
            self.metric_value,
            self.config["finish_op"],
            self.config["finish_threshold"],
        )

        self.target_frame_count = self.target_frame_count + 1 if is_target else 0
        self.finish_frame_count = self.finish_frame_count + 1 if is_finish else 0
        completed = False
        publish_feedback = True

        if self.phase == "WAIT_TARGET":
            enough_interval = (
                now - self.last_count_time
                > self.config.get("min_count_interval", 1.5)
            )
            enough_target_frames = (
                self.target_frame_count
                >= self.config.get("target_hold_frames", 3)
            )
            if enough_interval and enough_target_frames:
                self.phase = "WAIT_FINISH"
                self.target_start_time = now
                self.candidate_start = now
                self.best_metric = self.metric_value
                self.tip = "UP"

        elif self.phase == "WAIT_FINISH":
            if self.config.get("range_type", "max") == "max":
                self.best_metric = max(self.best_metric, self.metric_value)
            else:
                self.best_metric = min(self.best_metric, self.metric_value)

            enough_interval = (
                now - self.last_count_time
                > self.config.get("min_count_interval", 1.5)
            )
            enough_finish_frames = (
                self.finish_frame_count
                >= self.config.get("finish_hold_frames", 4)
            )
            if enough_interval and enough_finish_frames:
                validation = self._validate_candidate(now)
                self._log_imu_validation(validation, now)
                if validation.accepted:
                    self._complete_repetition(
                        imu_data if not validation.bypassed else {},
                        now,
                    )
                    completed = True
                else:
                    self._reject_candidate()
                    publish_feedback = False

        if (
            self.phase == "WAIT_TARGET"
            and self.tip in self.RESULT_TIPS
            and now > self.tip_hold_until
        ):
            self.tip = "READY"

        return ActionUpdate(
            self.snapshot(),
            completed=completed,
            publish_feedback=publish_feedback,
        )

    def no_person_fresh(self):
        self.tip = "NO_PERSON"
        self.phase = "WAIT_TARGET"
        self.target_start_time = 0.0
        self.candidate_start = None
        self.best_metric = None
        self.target_frame_count = 0
        self.finish_frame_count = 0
        self.metric_value = 0.0
        self.aux_text = ""
        self.person_text = "No person"
        self.metric_history.clear()
        self.smoother.reset()
        if self.metric_comparison is not None:
            self.metric_comparison.reset()
        if self._shadow_counter is not None:
            self._shadow_counter.reset_transient()
        return ActionUpdate(self.snapshot())

    def snapshot(self):
        return ActionSnapshot(
            mode=self.mode,
            count=self.count,
            score=self.score,
            tip=self.tip,
            phase=self.phase,
            metric_name=self.metric_name,
            metric_value=self.metric_value,
            aux_text=self.aux_text,
            person_text=self.person_text,
            stats=MappingProxyType(dict(self.stats)),
        )

    def _validate_candidate(self, now):
        config = IMU_VALIDATION_CONFIG
        return validate_imu_window(
            imu_snapshot=IMU_STATE.snapshot(),
            now=now,
            window_start=self.candidate_start - config.window_preroll_sec,
            window_end=now,
            min_accel_g=self.config.get("imu_min_accel_g"),
            max_accel_g=self.config.get("imu_max_accel_g"),
            use_min_threshold=config.use_min_threshold,
            stale_sec=config.stale_sec,
            min_window_samples=config.min_window_samples,
            config_valid=config.is_valid,
        )

    def _log_imu_validation(self, validation, now):
        motion_peak = (
            "NA" if validation.motion_peak_g is None
            else f"{validation.motion_peak_g:.3f}"
        )
        gyro_peak = (
            "NA" if validation.gyro_peak_dps is None
            else f"{validation.gyro_peak_dps:.3f}"
        )
        LOGGER.info(
            "IMU_VALIDATE action=%s result=%s samples=%d duration_sec=%.3f "
            "preroll_sec=%.3f motion_peak_g=%s min_g=%s max_g=%s "
            "min_enabled=%d gyro_peak_dps=%s",
            self.mode,
            validation.reason,
            validation.sample_count,
            now - self.target_start_time,
            IMU_VALIDATION_CONFIG.window_preroll_sec,
            motion_peak,
            self.config.get("imu_min_accel_g"),
            self.config.get("imu_max_accel_g"),
            int(bool(IMU_VALIDATION_CONFIG.use_min_threshold)),
            gyro_peak,
        )

    def _reject_candidate(self):
        self.tip = "READY"
        self.phase = "WAIT_TARGET"
        self.candidate_start = None
        self.target_start_time = 0.0
        self.best_metric = None
        self.target_frame_count = 0
        self.finish_frame_count = 0

    def _complete_repetition(self, imu_data, now):
        action_time = now - self.target_start_time
        self.count += 1
        self.last_count_time = now
        self.phase = "WAIT_TARGET"
        self.candidate_start = None

        range_type = self.config.get("range_type", "max")
        range_threshold = self.config.get(
            "range_threshold", self.config["target_threshold"]
        )
        if action_time < self.config.get("min_action_time", 0.6):
            self.tip = "TOO_FAST"
        elif range_type == "max" and self.best_metric < range_threshold:
            self.tip = "RANGE_SMALL"
        elif range_type == "min" and self.best_metric > range_threshold:
            self.tip = "RANGE_SMALL"
        elif imu_data.get("gyro_mag", 0.0) > self.config.get(
            "swing_gyro_threshold", 7.0
        ):
            self.tip = "SWING"
        else:
            self.tip = "GOOD"

        if self.tip != "GOOD":
            self.score = max(0, self.score - 10)
        self.stats[self.tip] += 1
        self.tip_hold_until = now + self.config.get("tip_hold_time", 1.5)
        self.target_frame_count = 0
        self.finish_frame_count = 0


def _condition(value, operator, threshold):
    if operator == ">":
        return value > threshold
    if operator == "<":
        return value < threshold
    return False

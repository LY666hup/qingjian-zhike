"""3D world-landmark shadow repetition counter.

Runs in parallel with the 2D state machine. Counts candidate repetitions
from the filtered 3D angle but does NOT affect the formal count, score,
CMD protocol, or device feedback.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional

from .types import MetricComparison


@dataclass
class ShadowRepState:
    """Snapshot of the shadow state machine for logging."""

    phase: str = "WAIT_TARGET"
    count: int = 0
    metric_3d: float = float("nan")


class Shadow3DRepCounter:
    """Independent 3D repetition counter that shadows the 2D state machine.

    This is a diagnostic tool. It MUST NOT alter the formal count, score,
    CMD protocol output, or device feedback.
    """

    def __init__(
        self,
        *,
        target_threshold_3d: float,
        finish_threshold_3d: float,
        target_hold_frames: int = 3,
        finish_hold_frames: int = 4,
        min_count_interval: float = 1.5,
        label: str = "3DS",
        debug: bool = False,
    ):
        self.target_threshold = target_threshold_3d
        self.finish_threshold = finish_threshold_3d
        self.target_hold_frames = target_hold_frames
        self.finish_hold_frames = finish_hold_frames
        self.min_count_interval = min_count_interval
        self.label = label
        self.debug = debug

        self._phase = "WAIT_TARGET"
        self._count = 0
        self._target_frame_count = 0
        self._finish_frame_count = 0
        self._last_count_time = 0.0
        self._metric_3d = float("nan")
        self._last_event: str = ""

    # -- public read-only helpers ------------------------------------------------

    @property
    def phase(self) -> str:
        return self._phase

    @property
    def count(self) -> int:
        return self._count

    @property
    def metric_3d(self) -> float:
        return self._metric_3d

    def reset(self):
        self._phase = "WAIT_TARGET"
        self._count = 0
        self._target_frame_count = 0
        self._finish_frame_count = 0
        self._last_count_time = 0.0
        self._metric_3d = float("nan")
        self._last_event = ""

    def reset_transient(self):
        """Clear transient phase state without resetting the count."""
        self._phase = "WAIT_TARGET"
        self._target_frame_count = 0
        self._finish_frame_count = 0
        self._last_count_time = 0.0
        self._metric_3d = float("nan")
        self._last_event = ""

    def snapshot(self) -> ShadowRepState:
        return ShadowRepState(
            phase=self._phase,
            count=self._count,
            metric_3d=self._metric_3d,
        )

    # -- main update -------------------------------------------------------------

    def update(
        self,
        comparison: MetricComparison,
        now: float,
    ) -> Optional[str]:
        """Feed one fresh-frame 3D angle pair.

        Returns a short event string when a rep is completed, or None.
        """
        angle = self._extract_metric(comparison)
        self._metric_3d = angle

        if not math.isfinite(angle):
            self._target_frame_count = 0
            self._finish_frame_count = 0
            self._last_event = ""
            return None

        # Check for squat-type action (target < threshold) vs pushup-type.
        # Squat: angle smaller = squatting down, target_op "<", finish_op ">"
        # Pushup: angle smaller = lowering, target_op "<", finish_op ">"
        # Both use the same "<" target pattern (angle decreases when entering
        # the movement), so we can use a single direction assumption.
        is_target = angle < self.target_threshold
        is_finish = angle > self.finish_threshold

        self._target_frame_count = (
            self._target_frame_count + 1 if is_target else 0
        )
        self._finish_frame_count = (
            self._finish_frame_count + 1 if is_finish else 0
        )

        event: Optional[str] = None

        if self._phase == "WAIT_TARGET":
            enough_interval = now - self._last_count_time > self.min_count_interval
            enough_frames = self._target_frame_count >= self.target_hold_frames
            if enough_interval and enough_frames:
                self._phase = "WAIT_FINISH"
                self._last_event = f"{self.label}_TARGET"

        elif self._phase == "WAIT_FINISH":
            enough_interval = now - self._last_count_time > self.min_count_interval
            enough_frames = self._finish_frame_count >= self.finish_hold_frames
            if enough_interval and enough_frames:
                self._count += 1
                self._last_count_time = now
                self._phase = "WAIT_TARGET"
                self._target_frame_count = 0
                self._finish_frame_count = 0
                event = f"{self.label}_REP#{self._count}"
                self._last_event = event

        return event

    # -- internal ----------------------------------------------------------------

    @staticmethod
    def _extract_metric(comparison: MetricComparison) -> float:
        """min(left, right) of the filtered 3D angles."""
        left = comparison.left_angle_3d
        right = comparison.right_angle_3d
        finite = [v for v in (left, right) if math.isfinite(v)]
        return min(finite) if finite else float("nan")

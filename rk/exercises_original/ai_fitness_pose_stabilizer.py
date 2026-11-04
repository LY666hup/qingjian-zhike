from __future__ import annotations

"""Time-aware pose landmark smoothing for MediaPipe output.

The stabilizer is intentionally placed before both action metrics and drawing so
there is only one pose representation in the system: the pose the user sees is
also the pose used for counting and correction.

It uses a One Euro style adaptive low-pass filter:
- when a landmark is nearly stationary, smoothing is stronger to suppress jitter;
- when it moves quickly, the cutoff rises so the filter follows the movement with
  less lag;
- after a long input gap (camera reconnect/stall), the filter re-locks directly to
  the new pose instead of dragging an old skeleton toward the new one.

No confidence/presence thresholding is done here.  Those MediaPipe settings are
left unchanged on purpose.
"""

from dataclasses import dataclass
import math
from typing import Optional, Sequence


DEFAULT_REFERENCE_FPS = 15.0
DEFAULT_BETA = 1.6
DEFAULT_D_CUTOFF = 1.0
DEFAULT_RESET_GAP_SEC = 0.35
_MIN_CUTOFF_HZ = 0.10
_MAX_CUTOFF_HZ = 20.0


def _clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))


def _alpha_for_cutoff(cutoff_hz: float, dt: float) -> float:
    """Return the *new-sample* weight for a first-order low-pass filter."""
    cutoff_hz = max(_MIN_CUTOFF_HZ, float(cutoff_hz))
    dt = max(1e-6, float(dt))
    tau = 1.0 / (2.0 * math.pi * cutoff_hz)
    return 1.0 / (1.0 + tau / dt)


def legacy_alpha_to_cutoff(alpha: float, reference_fps: float = DEFAULT_REFERENCE_FPS) -> float:
    """Map the project's legacy EMA coefficient to an equivalent static cutoff.

    Legacy smoothing was::

        filtered = alpha * previous + (1 - alpha) * current

    The mapping preserves approximately the same stationary smoothing at the
    reference FPS, while the adaptive beta term lets fast motion follow more
    closely than the old fixed EMA.
    """
    alpha = _clamp(float(alpha), 0.0, 0.995)
    reference_fps = max(1.0, float(reference_fps))
    dt = 1.0 / reference_fps
    new_weight = max(1e-4, 1.0 - alpha)

    if alpha <= 1e-6:
        return _MAX_CUTOFF_HZ

    tau = dt * alpha / new_weight
    cutoff = 1.0 / (2.0 * math.pi * tau)
    return _clamp(cutoff, _MIN_CUTOFF_HZ, _MAX_CUTOFF_HZ)


@dataclass
class StableLandmark:
    x: float
    y: float
    z: float = 0.0
    visibility: float = 0.0
    presence: float = 0.0


@dataclass
class _AxisState:
    raw: float
    filtered: float
    derivative: float = 0.0


@dataclass
class _LandmarkState:
    x: _AxisState
    y: _AxisState
    z: _AxisState


class PoseLandmarkStabilizer:
    """Adaptive, frame-rate-independent smoothing for pose landmarks."""

    def __init__(
        self,
        *,
        legacy_alpha: float = 0.68,
        reference_fps: float = DEFAULT_REFERENCE_FPS,
        beta: float = DEFAULT_BETA,
        derivative_cutoff_hz: float = DEFAULT_D_CUTOFF,
        reset_gap_sec: float = DEFAULT_RESET_GAP_SEC,
    ):
        self.legacy_alpha = float(legacy_alpha)
        self.reference_fps = max(1.0, float(reference_fps))
        self.min_cutoff_hz = legacy_alpha_to_cutoff(
            self.legacy_alpha,
            self.reference_fps,
        )
        self.beta = max(0.0, float(beta))
        self.derivative_cutoff_hz = max(
            _MIN_CUTOFF_HZ,
            float(derivative_cutoff_hz),
        )
        self.reset_gap_sec = max(0.05, float(reset_gap_sec))

        self._states: Optional[list[_LandmarkState]] = None
        self._output: Optional[list[StableLandmark]] = None
        self._last_time: Optional[float] = None

    def reset(self) -> None:
        self._states = None
        self._output = None
        self._last_time = None

    def _initialize(self, landmarks: Sequence, now: float) -> list[StableLandmark]:
        states: list[_LandmarkState] = []
        output: list[StableLandmark] = []

        for landmark in landmarks:
            x = float(landmark.x)
            y = float(landmark.y)
            z = float(getattr(landmark, "z", 0.0))
            states.append(
                _LandmarkState(
                    x=_AxisState(x, x),
                    y=_AxisState(y, y),
                    z=_AxisState(z, z),
                )
            )
            output.append(
                StableLandmark(
                    x=x,
                    y=y,
                    z=z,
                    visibility=float(getattr(landmark, "visibility", 0.0)),
                    presence=float(getattr(landmark, "presence", 0.0)),
                )
            )

        self._states = states
        self._output = output
        self._last_time = float(now)
        return output

    def _filter_axis(self, state: _AxisState, value: float, dt: float) -> float:
        raw_derivative = (value - state.raw) / dt
        derivative_alpha = _alpha_for_cutoff(self.derivative_cutoff_hz, dt)
        derivative = (
            derivative_alpha * raw_derivative
            + (1.0 - derivative_alpha) * state.derivative
        )

        adaptive_cutoff = self.min_cutoff_hz + self.beta * abs(derivative)
        value_alpha = _alpha_for_cutoff(adaptive_cutoff, dt)
        filtered = value_alpha * value + (1.0 - value_alpha) * state.filtered

        state.raw = value
        state.filtered = filtered
        state.derivative = derivative
        return filtered

    def update(self, landmarks: Optional[Sequence], now: float) -> Optional[list[StableLandmark]]:
        if landmarks is None:
            self.reset()
            return None

        now = float(now)
        if (
            self._states is None
            or self._output is None
            or len(self._states) != len(landmarks)
            or self._last_time is None
        ):
            return self._initialize(landmarks, now)

        dt = now - self._last_time
        if dt <= 1e-6:
            dt = 1.0 / self.reference_fps

        # A long stall/reconnect means the previous pose is no longer a useful
        # temporal prior.  Snap to the newly observed pose rather than slowly
        # dragging the old skeleton across the image.
        if dt >= self.reset_gap_sec:
            return self._initialize(landmarks, now)

        # Bound pathological timing values while still keeping the filter
        # time-aware under normal LatestFrame operation.
        dt = _clamp(dt, 1.0 / 120.0, 0.25)

        for state, stable, landmark in zip(self._states, self._output, landmarks):
            stable.x = self._filter_axis(state.x, float(landmark.x), dt)
            stable.y = self._filter_axis(state.y, float(landmark.y), dt)
            stable.z = self._filter_axis(
                state.z,
                float(getattr(landmark, "z", 0.0)),
                dt,
            )
            # Preserve MediaPipe metadata exactly; no confidence gating is
            # introduced in this version.
            stable.visibility = float(getattr(landmark, "visibility", 0.0))
            stable.presence = float(getattr(landmark, "presence", 0.0))

        self._last_time = now
        return self._output

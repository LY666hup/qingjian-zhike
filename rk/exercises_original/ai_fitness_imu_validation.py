"""Pure IMU parsing, configuration, and repetition-window validation."""

from __future__ import annotations

from ai_fitness_env import load_project_env

load_project_env()

import math
import os
import threading
from collections import deque
from dataclasses import dataclass
from typing import Callable, Mapping


IMU_ACCEPT = "IMU_ACCEPT"
IMU_REJECT_LOW_MOTION = "IMU_REJECT_LOW_MOTION"
IMU_REJECT_HIGH_MOTION = "IMU_REJECT_HIGH_MOTION"
IMU_BYPASS_DISCONNECTED = "IMU_BYPASS_DISCONNECTED"
IMU_BYPASS_STALE = "IMU_BYPASS_STALE"
IMU_BYPASS_INSUFFICIENT_SAMPLES = "IMU_BYPASS_INSUFFICIENT_SAMPLES"
IMU_BYPASS_INVALID_CONFIG = "IMU_BYPASS_INVALID_CONFIG"

DEFAULT_IMU_ACCEL_THRESHOLDS = {
    "CURL": (0.04, 2.50),
    "LATERAL_RAISE": (0.04, 3.00),
    "FRONT_RAISE": (0.04, 3.00),
    "OVERHEAD_PRESS": (0.04, 3.00),
    "BENT_OVER_ROW": (0.04, 3.50),
    "OVERHEAD_TRICEPS": (0.03, 2.50),
    "PUSHUP": (0.00, 2.00),
    "SQUAT": (0.00, 2.50),
}

_DEFAULT_ACCEL_INPUT_MODE = "legacy"
_DEFAULT_USE_MIN_THRESHOLD = True
_DEFAULT_WINDOW_PREROLL_SEC = 0.20
_DEFAULT_MIN_WINDOW_SAMPLES = 3
_DEFAULT_STALE_SEC = 1.0
_DEFAULT_BUFFER_DURATION_SEC = 5.0


@dataclass(frozen=True)
class ImuSample:
    timestamp: float
    ax: float
    ay: float
    az: float
    gx: float
    gy: float
    gz: float
    pitch: float

    def to_legacy_dict(self) -> dict[str, float]:
        """Return the deployed mutable dictionary shape with L1 gyro magnitude."""
        return {
            "ax": self.ax,
            "ay": self.ay,
            "az": self.az,
            "gx": self.gx,
            "gy": self.gy,
            "gz": self.gz,
            "pitch": self.pitch,
            "gyro_mag": abs(self.gx) + abs(self.gy) + abs(self.gz),
        }


@dataclass(frozen=True)
class ImuBufferSnapshot:
    samples: tuple[ImuSample, ...]
    connected: bool
    last_received_timestamp: float | None


@dataclass(frozen=True)
class ImuValidationResult:
    accepted: bool
    bypassed: bool
    reason: str
    sample_count: int
    motion_peak_g: float | None
    gyro_peak_dps: float | None


@dataclass(frozen=True)
class ImuValidationConfig:
    accel_input_mode: str
    use_min_threshold: bool | None
    window_preroll_sec: float | None
    min_window_samples: int | None
    stale_sec: float | None
    buffer_duration_sec: float | None
    is_valid: bool


class ImuStateOwner:
    """Own all mutable IMU samples, connection state, and reader lifecycle."""

    def __init__(self, buffer_duration_sec: float):
        duration = float(buffer_duration_sec)
        if not math.isfinite(duration) or duration <= 0.0:
            raise ValueError("buffer_duration_sec must be finite and positive")

        self._lock = threading.Lock()
        self._buffer_duration_sec = duration
        self._samples: deque[ImuSample] = deque()
        self._latest: ImuSample | None = None
        self._connection_state = "DISCONNECTED"
        self._last_received_timestamp: float | None = None
        self._line_count = 0
        self._running = False
        self._reader_thread = None

    @property
    def running(self) -> bool:
        with self._lock:
            return self._running

    def snapshot(self) -> ImuBufferSnapshot:
        with self._lock:
            samples = tuple(self._samples)
            connected = self._connection_state == "CONNECTED"
            last_received = self._last_received_timestamp
        return ImuBufferSnapshot(samples, connected, last_received)

    def _prune_locked(self, now: float) -> None:
        cutoff = now - self._buffer_duration_sec
        while self._samples and self._samples[0].timestamp < cutoff:
            self._samples.popleft()

    def record_sample(self, sample: ImuSample) -> None:
        if not isinstance(sample, ImuSample):
            raise TypeError("sample must be an ImuSample")
        if not all(math.isfinite(value) for value in sample.__dict__.values()):
            raise ValueError("sample values must be finite")

        with self._lock:
            self._samples.append(sample)
            self._latest = sample
            self._last_received_timestamp = sample.timestamp
            self._line_count += 1
            self._prune_locked(sample.timestamp)

    def prune(self, now: float) -> None:
        checked_now = float(now)
        if not math.isfinite(checked_now):
            raise ValueError("now must be finite")
        with self._lock:
            self._prune_locked(checked_now)

    def latest_legacy(self, fallback: dict) -> dict:
        with self._lock:
            latest = self._latest
        return fallback if latest is None else latest.to_legacy_dict()

    def mark_connecting(self) -> None:
        with self._lock:
            self._connection_state = "CONNECTING"

    def mark_connected(self) -> None:
        with self._lock:
            self._connection_state = "CONNECTED"

    def mark_disconnected(self) -> None:
        with self._lock:
            self._connection_state = "DISCONNECTED"

    def status_text(self, now: float, old_after_sec: float) -> str:
        with self._lock:
            state = self._connection_state
            last_received = self._last_received_timestamp
            line_count = self._line_count

        if state == "CONNECTING":
            return "CONNECTING"
        if state == "DISCONNECTED":
            return "DISCONNECTED"
        if line_count == 0:
            return "NO DATA"
        if last_received is None or now - last_received > old_after_sec:
            return "OLD"
        return "OK"

    def start_reader(self, target: Callable[[], None], thread_factory=threading.Thread) -> bool:
        with self._lock:
            thread = self._reader_thread
            if thread is not None and thread.is_alive():
                self._running = True
                return True

            self._running = True
            self._connection_state = "CONNECTING"
            thread = thread_factory(target=target, daemon=True)
            self._reader_thread = thread
            try:
                thread.start()
            except Exception:
                self._reader_thread = None
                self._running = False
                self._connection_state = "DISCONNECTED"
                raise
        return True

    def stop_reader(self, join_timeout: float = 3.0) -> None:
        with self._lock:
            self._running = False
            self._connection_state = "DISCONNECTED"
            thread = self._reader_thread

        if thread is not None and thread is not threading.current_thread():
            thread.join(join_timeout)

        with self._lock:
            if self._reader_thread is thread and (
                thread is None or not thread.is_alive()
            ):
                self._reader_thread = None


def parse_imu_sample(
    line: str,
    timestamp: float,
    *,
    input_mode: str,
) -> ImuSample | None:
    """Parse one IMU packet and normalize acceleration to canonical g units."""
    if input_mode not in ("legacy", "g"):
        return None

    try:
        parts = line.strip().split(",")
        if len(parts) != 8 or parts[0] != "IMU":
            return None

        parsed_timestamp = float(timestamp)
        values = tuple(float(value) for value in parts[1:])
    except (AttributeError, TypeError, ValueError):
        return None

    if not math.isfinite(parsed_timestamp) or not all(map(math.isfinite, values)):
        return None

    ax, ay, az, gx, gy, gz, pitch = values
    if input_mode == "legacy":
        ax /= 4.9
        ay /= 4.9
        az /= 4.9

    return ImuSample(parsed_timestamp, ax, ay, az, gx, gy, gz, pitch)


def _load_input_mode(
    environ: Mapping[str, str], key: str
) -> tuple[str, bool]:
    if key not in environ:
        return _DEFAULT_ACCEL_INPUT_MODE, True
    raw = environ.get(key)
    if raw in ("legacy", "g"):
        return raw, True
    return _DEFAULT_ACCEL_INPUT_MODE, False


def _load_binary(
    environ: Mapping[str, str], key: str, default: bool
) -> tuple[bool, bool]:
    raw = environ.get(key)
    if raw is None:
        return default, True
    if raw == "0":
        return False, True
    if raw == "1":
        return True, True
    return default, False


def _load_float(
    environ: Mapping[str, str], key: str, default: float
) -> tuple[float, bool]:
    raw = environ.get(key)
    if raw is None:
        return default, True
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return default, False
    if not math.isfinite(value):
        return default, False
    return value, True


def _load_int(
    environ: Mapping[str, str], key: str, default: int
) -> tuple[int, bool]:
    raw = environ.get(key)
    if raw is None:
        return default, True
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default, False
    return value, True


def load_imu_validation_config(
    environ: Mapping[str, str] | None = None,
) -> ImuValidationConfig:
    """Load global IMU validation settings without raising on bad input."""
    source = os.environ if environ is None else environ

    accel_input_mode, input_mode_valid = _load_input_mode(
        source,
        "FITNESS_IMU_ACCEL_INPUT_MODE",
    )
    use_min_threshold, binary_valid = _load_binary(
        source,
        "FITNESS_IMU_USE_MIN_THRESHOLD",
        _DEFAULT_USE_MIN_THRESHOLD,
    )
    window_preroll_sec, preroll_parsed = _load_float(
        source,
        "FITNESS_IMU_WINDOW_PREROLL_SEC",
        _DEFAULT_WINDOW_PREROLL_SEC,
    )
    min_window_samples, samples_parsed = _load_int(
        source,
        "FITNESS_IMU_MIN_WINDOW_SAMPLES",
        _DEFAULT_MIN_WINDOW_SAMPLES,
    )
    stale_sec, stale_parsed = _load_float(
        source,
        "FITNESS_IMU_STALE_SEC",
        _DEFAULT_STALE_SEC,
    )
    buffer_duration_sec, duration_parsed = _load_float(
        source,
        "FITNESS_IMU_BUFFER_DURATION_SEC",
        _DEFAULT_BUFFER_DURATION_SEC,
    )

    is_valid = all(
        (
            input_mode_valid,
            binary_valid,
            preroll_parsed,
            samples_parsed,
            stale_parsed,
            duration_parsed,
            window_preroll_sec >= 0.0,
            min_window_samples >= 1,
            stale_sec > 0.0,
            buffer_duration_sec > window_preroll_sec,
        )
    )

    if window_preroll_sec < 0.0:
        window_preroll_sec = _DEFAULT_WINDOW_PREROLL_SEC
    if min_window_samples < 1:
        min_window_samples = _DEFAULT_MIN_WINDOW_SAMPLES
    if stale_sec <= 0.0:
        stale_sec = _DEFAULT_STALE_SEC
    if not duration_parsed or buffer_duration_sec <= window_preroll_sec:
        buffer_duration_sec = _DEFAULT_BUFFER_DURATION_SEC
        if buffer_duration_sec <= window_preroll_sec:
            window_preroll_sec = _DEFAULT_WINDOW_PREROLL_SEC

    return ImuValidationConfig(
        accel_input_mode=accel_input_mode,
        use_min_threshold=use_min_threshold,
        window_preroll_sec=window_preroll_sec,
        min_window_samples=min_window_samples,
        stale_sec=stale_sec,
        buffer_duration_sec=buffer_duration_sec,
        is_valid=is_valid,
    )


IMU_VALIDATION_CONFIG = load_imu_validation_config()
IMU_STATE = ImuStateOwner(
    buffer_duration_sec=(
        IMU_VALIDATION_CONFIG.buffer_duration_sec
        if IMU_VALIDATION_CONFIG.buffer_duration_sec
        and IMU_VALIDATION_CONFIG.buffer_duration_sec > 0.0
        else _DEFAULT_BUFFER_DURATION_SEC
    )
)


def _finite_threshold(raw: object) -> float | None:
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def load_action_imu_thresholds(
    action: str, environ: Mapping[str, str] | None = None
) -> dict[str, float | None]:
    """Load exactly the lower and upper acceleration thresholds for an action."""
    default_minimum, default_maximum = DEFAULT_IMU_ACCEL_THRESHOLDS[action]
    source = os.environ if environ is None else environ
    prefix = f"FITNESS_IMU_{action}"

    minimum = _finite_threshold(
        source.get(f"{prefix}_MIN_ACCEL_G", default_minimum)
    )
    maximum = _finite_threshold(
        source.get(f"{prefix}_MAX_ACCEL_G", default_maximum)
    )
    return {
        "imu_min_accel_g": minimum,
        "imu_max_accel_g": maximum,
    }


def _validator_config_is_valid(
    *,
    config_valid: bool,
    min_accel_g: object,
    max_accel_g: object,
    use_min_threshold: object,
    stale_sec: object,
    min_window_samples: object,
) -> bool:
    if config_valid is not True or type(use_min_threshold) is not bool:
        return False
    if type(min_window_samples) is not int or min_window_samples < 1:
        return False

    numeric_values = (max_accel_g, stale_sec)
    if use_min_threshold:
        numeric_values += (min_accel_g,)
    if any(type(value) not in (int, float) for value in numeric_values):
        return False
    if not all(math.isfinite(value) for value in numeric_values):
        return False
    return (
        max_accel_g > 0.0
        and stale_sec > 0.0
        and (
            not use_min_threshold
            or (min_accel_g >= 0.0 and min_accel_g <= max_accel_g)
        )
    )


def _result(
    reason: str,
    sample_count: int,
    *,
    motion_peak_g: float | None = None,
    gyro_peak_dps: float | None = None,
) -> ImuValidationResult:
    bypassed = reason.startswith("IMU_BYPASS_")
    return ImuValidationResult(
        accepted=reason == IMU_ACCEPT or bypassed,
        bypassed=bypassed,
        reason=reason,
        sample_count=sample_count,
        motion_peak_g=motion_peak_g,
        gyro_peak_dps=gyro_peak_dps,
    )


def validate_imu_window(
    *,
    imu_snapshot: ImuBufferSnapshot,
    now: float,
    window_start: float,
    window_end: float,
    min_accel_g: float | None,
    max_accel_g: float | None,
    use_min_threshold: bool,
    stale_sec: float,
    min_window_samples: int,
    config_valid: bool = True,
) -> ImuValidationResult:
    """Validate an immutable snapshot over an inclusive candidate time window."""
    window_samples = tuple(
        sample
        for sample in imu_snapshot.samples
        if window_start <= sample.timestamp <= window_end
    )
    sample_count = len(window_samples)

    if not _validator_config_is_valid(
        config_valid=config_valid,
        min_accel_g=min_accel_g,
        max_accel_g=max_accel_g,
        use_min_threshold=use_min_threshold,
        stale_sec=stale_sec,
        min_window_samples=min_window_samples,
    ):
        return _result(IMU_BYPASS_INVALID_CONFIG, sample_count)
    if not imu_snapshot.connected:
        return _result(IMU_BYPASS_DISCONNECTED, sample_count)
    if (
        imu_snapshot.last_received_timestamp is None
        or now - imu_snapshot.last_received_timestamp > stale_sec
    ):
        return _result(IMU_BYPASS_STALE, sample_count)
    if sample_count < min_window_samples:
        return _result(IMU_BYPASS_INSUFFICIENT_SAMPLES, sample_count)

    motion_peak_g = max(
        abs(math.sqrt(sample.ax**2 + sample.ay**2 + sample.az**2) - 1.0)
        for sample in window_samples
    )
    gyro_peak_dps = max(
        math.sqrt(sample.gx**2 + sample.gy**2 + sample.gz**2)
        for sample in window_samples
    )

    if motion_peak_g > max_accel_g:
        reason = IMU_REJECT_HIGH_MOTION
    elif use_min_threshold and motion_peak_g < min_accel_g:
        reason = IMU_REJECT_LOW_MOTION
    else:
        reason = IMU_ACCEPT

    return _result(
        reason,
        sample_count,
        motion_peak_g=motion_peak_g,
        gyro_peak_dps=gyro_peak_dps,
    )

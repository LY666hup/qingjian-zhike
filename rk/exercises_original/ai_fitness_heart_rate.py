"""Pure heart-rate protocol parsing, state, and forwarding helpers."""

from __future__ import annotations

import math
import numbers
import re
import threading
from dataclasses import dataclass


_ASCII_DIGITS = re.compile(r"[0-9]+", re.ASCII)


@dataclass(frozen=True)
class HeartRateSample:
    timestamp: float
    bpm: int
    valid: bool


@dataclass(frozen=True)
class HeartRateSnapshot:
    latest: HeartRateSample | None
    last_received: float | None


class HeartRateState:
    """Thread-safe storage for the latest heart-rate sample."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._latest: HeartRateSample | None = None
        self._last_received: float | None = None

    def record_sample(self, sample: HeartRateSample) -> None:
        if not isinstance(sample, HeartRateSample):
            raise TypeError("sample must be a HeartRateSample")
        with self._lock:
            self._latest = sample
            self._last_received = sample.timestamp

    def snapshot(self) -> HeartRateSnapshot:
        with self._lock:
            return HeartRateSnapshot(
                latest=self._latest,
                last_received=self._last_received,
            )


HEART_RATE_STATE = HeartRateState()


def parse_heart_rate_sample(
    line: bytes | str,
    timestamp: float,
) -> HeartRateSample | None:
    """Parse one strict ``HR,bpm,valid`` record, or return ``None``."""
    if (
        isinstance(timestamp, bool)
        or not isinstance(timestamp, numbers.Real)
        or not math.isfinite(timestamp)
    ):
        return None

    if isinstance(line, bytes):
        try:
            text = line.decode("ascii")
        except UnicodeDecodeError:
            return None
    elif isinstance(line, str):
        try:
            line.encode("ascii")
        except UnicodeEncodeError:
            return None
        text = line
    else:
        return None

    if text.endswith("\n"):
        text = text[:-1]
        if text.endswith("\r"):
            text = text[:-1]

    fields = text.split(",")
    if len(fields) != 3 or fields[0] != "HR":
        return None

    bpm_text, valid_text = fields[1], fields[2]
    if (
        _ASCII_DIGITS.fullmatch(bpm_text) is None
        or len(bpm_text) > 3
        or valid_text not in {"0", "1"}
    ):
        return None

    bpm = int(bpm_text)
    valid = valid_text == "1"
    if (valid and not 1 <= bpm <= 300) or (not valid and bpm != 0):
        return None

    return HeartRateSample(timestamp=float(timestamp), bpm=bpm, valid=valid)


def format_heart_rate_command(bpm: int, valid: bool) -> bytes:
    """Format one canonical heart-rate command after strict validation."""
    if isinstance(bpm, bool) or not isinstance(bpm, int):
        raise TypeError("bpm must be an int")
    if not isinstance(valid, bool):
        raise TypeError("valid must be a bool")
    if valid:
        if not 1 <= bpm <= 300:
            raise ValueError("valid bpm must be between 1 and 300")
    elif bpm != 0:
        raise ValueError("invalid samples must use bpm 0")

    return f"HR,{bpm},{int(valid)}\n".encode("ascii")


def pending_heart_rate_sample(
    snapshot: HeartRateSnapshot,
    last_forwarded_timestamp: float | None,
) -> HeartRateSample | None:
    """Return the latest sample only when its timestamp is newer."""
    sample = snapshot.latest
    if sample is None:
        return None
    if last_forwarded_timestamp is not None and sample.timestamp <= last_forwarded_timestamp:
        return None
    return sample

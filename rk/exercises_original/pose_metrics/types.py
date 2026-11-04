"""Data types shared by pose metric diagnostics."""

from dataclasses import dataclass
from typing import Optional, Sequence


@dataclass(frozen=True)
class PoseObservation:
    image_landmarks: Sequence
    world_landmarks: Optional[Sequence]
    timestamp_ms: int


@dataclass(frozen=True)
class MetricComparison:
    left_angle_2d: float
    right_angle_2d: float
    left_angle_3d: float
    right_angle_3d: float
    left_chain_confidence: float
    right_chain_confidence: float
    legacy_value: float
    world_available: bool
    aux_text: str

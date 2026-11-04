"""Safe geometry helpers for image and world pose landmarks."""

import math

import numpy as np


def _coordinates(landmarks, index, dimensions):
    try:
        landmark = landmarks[index]
        values = [landmark.x, landmark.y]
        if dimensions == 3:
            values.append(landmark.z)
        point = np.asarray(values, dtype=float)
    except (AttributeError, IndexError, TypeError, ValueError):
        return np.full(dimensions, np.nan, dtype=float)
    if not np.all(np.isfinite(point)):
        return np.full(dimensions, np.nan, dtype=float)
    return point


def get_xy(landmarks, index) -> np.ndarray:
    return _coordinates(landmarks, index, 2)


def get_xyz(landmarks, index) -> np.ndarray:
    return _coordinates(landmarks, index, 3)


def angle_3points_nd(a, b, c) -> float:
    try:
        a = np.asarray(a, dtype=float)
        b = np.asarray(b, dtype=float)
        c = np.asarray(c, dtype=float)
    except (TypeError, ValueError):
        return float("nan")

    if a.shape != b.shape or b.shape != c.shape or a.ndim != 1:
        return float("nan")
    if a.size not in {2, 3} or not np.all(np.isfinite([a, b, c])):
        return float("nan")

    first = a - b
    second = c - b
    denominator = float(np.linalg.norm(first) * np.linalg.norm(second))
    if not math.isfinite(denominator) or denominator <= 1e-12:
        return float("nan")

    cosine = float(np.dot(first, second) / denominator)
    if not math.isfinite(cosine):
        return float("nan")
    return float(np.degrees(np.arccos(np.clip(cosine, -1.0, 1.0))))


def landmark_confidence(landmark) -> float:
    try:
        visibility = float(landmark.visibility)
        presence = float(landmark.presence)
    except (AttributeError, TypeError, ValueError):
        return 0.0
    if not math.isfinite(visibility) or not math.isfinite(presence):
        return 0.0
    return max(0.0, min(1.0, visibility, presence))


def chain_confidence(landmarks, indices) -> float:
    if landmarks is None:
        return 0.0
    try:
        selected = [landmarks[index] for index in indices]
    except (IndexError, TypeError):
        return 0.0
    for landmark in selected:
        try:
            coordinates = np.asarray(
                [landmark.x, landmark.y, getattr(landmark, "z", 0.0)],
                dtype=float,
            )
        except (AttributeError, TypeError, ValueError):
            return 0.0
        if not np.all(np.isfinite(coordinates)):
            return 0.0
    return min((landmark_confidence(landmark) for landmark in selected), default=0.0)


def midpoint(a, b) -> np.ndarray:
    try:
        a = np.asarray(a, dtype=float)
        b = np.asarray(b, dtype=float)
    except (TypeError, ValueError):
        return np.asarray([float("nan")], dtype=float)
    if a.shape != b.shape or not np.all(np.isfinite([a, b])):
        return np.full(a.shape if a.ndim else (1,), np.nan, dtype=float)
    return (a + b) / 2.0


def safe_distance(a, b) -> float:
    try:
        a = np.asarray(a, dtype=float)
        b = np.asarray(b, dtype=float)
    except (TypeError, ValueError):
        return float("nan")
    if a.shape != b.shape or not np.all(np.isfinite([a, b])):
        return float("nan")
    distance = float(np.linalg.norm(a - b))
    return distance if math.isfinite(distance) else float("nan")

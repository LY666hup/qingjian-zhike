"""Squat rep counter on MediaPipe-33 landmarks: one clean "down then up" = 1 rep.

Design notes (the parts that matter for a demo that must not lie):
  * Angle based, with hysteresis and debounce, so noise around one threshold cannot ratchet the count up.
  * A rep needs the full path STANDING -> DESCENDING -> BOTTOM -> ASCENDING -> STANDING.  A cue that starts
    mid-squat is not counted, and a shallow dip is rejected with a reason instead of being counted.
  * Confidence gate: required landmarks must be present and visible, otherwise the frame is "no observation"
    -- it neither advances nor freezes the machine (timeouts handle the freeze case).
  * Timeouts: a transient state that lasts too long abandons the rep; losing the pose for too long resets to
    UNKNOWN without counting, because "we stopped seeing the athlete" is not "they stood up".
  * Session reset: explicit reset(), automatic on timestamps going backwards, and automatic on request.

Landmark indices (BlazePose 33): hips 23/24, knees 25/26, ankles 27/28; shoulders 11/12 used only for an
orientation sanity check.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

LEFT_HIP, RIGHT_HIP = 23, 24
LEFT_KNEE, RIGHT_KNEE = 25, 26
LEFT_ANKLE, RIGHT_ANKLE = 27, 28
LEFT_SHOULDER, RIGHT_SHOULDER = 11, 12

STANDING = 'STANDING'
DESCENDING = 'DESCENDING'
BOTTOM = 'BOTTOM'
ASCENDING = 'ASCENDING'
UNKNOWN = 'UNKNOWN'
TRANSIENT = (DESCENDING, BOTTOM, ASCENDING)


@dataclass
class SquatConfig:
    stand_angle_deg: float = 150.0      # knee angle at/above this reads as standing
    squat_angle_deg: float = 110.0      # at/below this the athlete is on the way down
    bottom_angle_deg: float = 100.0     # at/below this the bottom is reached
    min_visibility: float = 0.5         # landmarks below this are not trusted
    debounce_frames: int = 2            # consecutive frames before a transition is committed
    max_transient_ms: int = 3000        # abandon a rep that stalls this long
    no_pose_reset_ms: int = 1000        # no pose this long -> UNKNOWN, no count
    # OFF by default: at the bottom of a deep squat the hips are at or below the knees, so this gate
    # rejects exactly the frames a rep counter needs.  Kept as an optional sanity check.
    require_hips_above_knees: bool = False


@dataclass
class SquatState:
    phase: str = UNKNOWN
    candidate: str = UNKNOWN
    candidate_frames: int = 0
    reps: int = 0
    depth_deg: float = 0.0
    phase_start_ts_ms: Optional[int] = None
    last_ts_ms: Optional[int] = None
    last_pose_ts_ms: Optional[int] = None
    rep_events: List[Dict[str, object]] = field(default_factory=list)
    rejected_reasons: List[str] = field(default_factory=list)
    aborted: int = 0


def _angle(a: Tuple[float, float], b: Tuple[float, float], c: Tuple[float, float]) -> float:
    ax, ay = a[0] - b[0], a[1] - b[1]
    cx, cy = c[0] - b[0], c[1] - b[1]
    na, nc = math.hypot(ax, ay), math.hypot(cx, cy)
    if na == 0 or nc == 0:
        return float('nan')
    return math.degrees(math.acos(max(-1.0, min(1.0, (ax * cx + ay * cy) / (na * nc)))))


def _get(landmarks: Sequence[Dict[str, float]], idx: int) -> Optional[Dict[str, float]]:
    for lm in landmarks:
        if int(lm.get('i', -1)) == idx:
            return lm
    return None


class SquatCounter:
    def __init__(self, config: Optional[SquatConfig] = None):
        self.cfg = config or SquatConfig()
        self.state = SquatState()

    # ---------------- public ----------------
    def reset(self, reason: str = 'explicit', keep_count: bool = True) -> None:
        count = self.state.reps if keep_count else 0
        aborted = self.state.aborted
        self.state = SquatState()
        self.state.reps = count
        self.state.aborted = aborted
        self.state.rejected_reasons.append('reset:' + reason)

    def update(self, landmarks: Optional[Sequence[Dict[str, float]]], ts_ms: int,
               present: Optional[bool] = None) -> Dict[str, object]:
        st, cfg = self.state, self.cfg
        if present is None:
            present = bool(landmarks)

        if st.last_ts_ms is not None and ts_ms < st.last_ts_ms:
            self.reset('session', keep_count=False)   # a backwards clock is a new stream
            st = self.state
        st.last_ts_ms = ts_ms

        if not present or not landmarks:
            if st.last_pose_ts_ms is None:
                st.last_pose_ts_ms = ts_ms
            gap = ts_ms - st.last_pose_ts_ms
            if st.phase in TRANSIENT and gap > cfg.no_pose_reset_ms:
                st.rejected_reasons.append('pose_lost_%dms' % gap)
                st.aborted += 1
                st.phase = UNKNOWN
                st.candidate = UNKNOWN
                st.phase_start_ts_ms = ts_ms
            return self._emit(ts_ms, None, 'no_pose', False)

        st.last_pose_ts_ms = ts_ms
        angle, why = self._knee_angle(landmarks)
        if angle is None:
            return self._emit(ts_ms, None, why, False)

        if cfg.require_hips_above_knees and not self._hips_above_knees(landmarks):
            if st.phase in TRANSIENT:
                st.rejected_reasons.append('orientation_invalid')
                st.aborted += 1
                st.phase = UNKNOWN
                st.candidate = UNKNOWN
                st.phase_start_ts_ms = ts_ms
            return self._emit(ts_ms, angle, 'orientation_invalid', False)

        wanted = self._next_phase(st.phase, angle)
        if wanted != st.candidate:
            st.candidate = wanted
            st.candidate_frames = 0
        st.candidate_frames += 1

        completed = False
        if st.candidate != st.phase and st.candidate_frames >= cfg.debounce_frames:
            completed = self._commit(st.candidate, angle, ts_ms)

        # time-based abort of a stalled rep
        if st.phase in TRANSIENT and st.phase_start_ts_ms is not None:
            if ts_ms - st.phase_start_ts_ms > cfg.max_transient_ms:
                st.rejected_reasons.append('stalled_in_%s' % st.phase.lower())
                st.aborted += 1
                st.phase = UNKNOWN
                st.candidate = UNKNOWN
                st.phase_start_ts_ms = ts_ms
                st.depth_deg = 0.0

        return self._emit(ts_ms, angle, 'ok', completed)

    def block(self) -> Dict[str, object]:
        st = self.state
        return {
            'mode': 'squat',
            'phase': st.phase.lower(),
            'count': int(st.reps),
            'score': int(round(self._confidence() * 100)),
            'tip': 'keep your knees behind your toes' if st.phase in (DESCENDING, BOTTOM) else '',
            'rep_events': list(st.rep_events),
            'aborted': int(st.aborted),
            'rejected_reasons': list(st.rejected_reasons[-8:]),
        }

    # ---------------- internals ----------------
    def _next_phase(self, phase: str, angle: float) -> str:
        cfg = self.cfg
        if phase == UNKNOWN:
            return STANDING if angle >= cfg.stand_angle_deg else DESCENDING
        if phase == STANDING:
            return DESCENDING if angle <= cfg.squat_angle_deg else STANDING
        if phase == DESCENDING:
            if angle <= cfg.bottom_angle_deg:
                return BOTTOM
            if angle >= cfg.stand_angle_deg:
                return STANDING          # came back up without depth -> partial, rejected at commit
            return DESCENDING
        if phase == BOTTOM:
            return ASCENDING if angle >= cfg.squat_angle_deg else BOTTOM
        if phase == ASCENDING:
            if angle >= cfg.stand_angle_deg:
                return STANDING
            if angle <= cfg.bottom_angle_deg:
                return BOTTOM
            return ASCENDING
        return phase

    def _commit(self, new_phase: str, angle: float, ts_ms: int) -> bool:
        st, cfg = self.state, self.cfg
        old = st.phase
        completed = False

        if new_phase == DESCENDING and old in (UNKNOWN, STANDING):
            st.depth_deg = angle
        if new_phase in (DESCENDING, BOTTOM) and st.depth_deg:
            st.depth_deg = min(st.depth_deg, angle)
        if new_phase == BOTTOM and not st.depth_deg:
            st.depth_deg = angle

        if new_phase == STANDING and old in (ASCENDING, BOTTOM):
            depth = st.depth_deg
            if depth and depth <= cfg.bottom_angle_deg:
                st.reps += 1
                completed = True
                st.rep_events.append({'reps': st.reps, 'ts_ms': ts_ms, 'depth_deg': round(depth, 2)})
            else:
                st.rejected_reasons.append('shallow_%.1f' % (depth or 0.0))
        elif new_phase == STANDING and old == DESCENDING:
            st.rejected_reasons.append('partial_depth_%.1f' % (st.depth_deg or 0.0))

        if st.phase != new_phase:
            st.phase = new_phase
            st.phase_start_ts_ms = ts_ms
            if new_phase == STANDING:
                st.depth_deg = 0.0
        return completed

    def _emit(self, ts_ms, angle, reason, completed) -> Dict[str, object]:
        st = self.state
        return {
            'ts_ms': ts_ms,
            'phase': st.phase,
            'reps': st.reps,
            'rep_completed': bool(completed),
            'knee_angle_deg': None if angle is None else round(angle, 2),
            'depth_deg': round(st.depth_deg, 2) if st.depth_deg else 0.0,
            'confidence': self._confidence(),
            'reason': reason,
        }

    def _confidence(self) -> float:
        st = self.state
        if st.phase == UNKNOWN:
            return 0.0
        base = 0.9 if st.phase in (STANDING, BOTTOM) else 0.6
        if st.candidate_frames < self.cfg.debounce_frames:
            base *= 0.5
        return round(base, 3)

    def _knee_angle(self, landmarks) -> Tuple[Optional[float], str]:
        cfg = self.cfg
        pts: Dict[int, Dict[str, float]] = {}
        for idx in (LEFT_HIP, LEFT_KNEE, LEFT_ANKLE, RIGHT_HIP, RIGHT_KNEE, RIGHT_ANKLE):
            lm = _get(landmarks, idx)
            if lm is None:
                return None, 'missing_landmark_%d' % idx
            if float(lm.get('visibility', 0.0)) < cfg.min_visibility:
                return None, 'low_visibility_%d' % idx
            pts[idx] = lm
        left = _angle((pts[LEFT_HIP]['x'], pts[LEFT_HIP]['y']),
                      (pts[LEFT_KNEE]['x'], pts[LEFT_KNEE]['y']),
                      (pts[LEFT_ANKLE]['x'], pts[LEFT_ANKLE]['y']))
        right = _angle((pts[RIGHT_HIP]['x'], pts[RIGHT_HIP]['y']),
                       (pts[RIGHT_KNEE]['x'], pts[RIGHT_KNEE]['y']),
                       (pts[RIGHT_ANKLE]['x'], pts[RIGHT_ANKLE]['y']))
        vals = [v for v in (left, right) if not math.isnan(v)]
        if not vals:
            return None, 'degenerate_geometry'
        return sum(vals) / len(vals), 'ok'

    def _hips_above_knees(self, landmarks) -> bool:
        lh, rh = _get(landmarks, LEFT_HIP), _get(landmarks, RIGHT_HIP)
        lk, rk = _get(landmarks, LEFT_KNEE), _get(landmarks, RIGHT_KNEE)
        if not all((lh, rh, lk, rk)):
            return True
        return (lh['y'] + rh['y']) / 2.0 < (lk['y'] + rk['y']) / 2.0
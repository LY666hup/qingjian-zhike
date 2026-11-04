"""Unit tests for the 3D shadow rep counter."""

import math
import unittest
from types import SimpleNamespace

from pose_metrics.shadow_counter import Shadow3DRepCounter
from pose_metrics.types import MetricComparison


def _comp(left_3d, right_3d):
    """Minimal MetricComparison for shadow counter testing."""
    return MetricComparison(
        left_angle_2d=0.0,
        right_angle_2d=0.0,
        left_angle_3d=left_3d,
        right_angle_3d=right_3d,
        left_chain_confidence=1.0,
        right_chain_confidence=1.0,
        legacy_value=0.0,
        world_available=True,
        aux_text="",
    )


class ShadowCounterTests(unittest.TestCase):
    def setUp(self):
        self.counter = Shadow3DRepCounter(
            target_threshold_3d=105,
            finish_threshold_3d=135,
            target_hold_frames=3,
            finish_hold_frames=4,
            min_count_interval=1.5,
            label="3DS_TEST",
        )

    def _feed(self, angle, now, count=1):
        """Feed the same angle `count` times at monotonically increasing times."""
        for i in range(count):
            self.counter.update(_comp(angle, angle + 1.0), now + i * 0.05)

    def test_initial_state(self):
        self.assertEqual(self.counter.phase, "WAIT_TARGET")
        self.assertEqual(self.counter.count, 0)

    def test_full_rep_cycle(self):
        now = 100.0
        # Standing: 3D angle ~160° (above finish threshold, not target)
        self._feed(160.0, now, count=5)
        self.assertEqual(self.counter.phase, "WAIT_TARGET")
        self.assertEqual(self.counter.count, 0)

        # Squat down: 3D angle drops below 105°
        now += 2.0  # past min_count_interval
        event = self.counter.update(_comp(80.0, 82.0), now)
        self.assertIsNone(event)
        now += 0.1
        self.counter.update(_comp(75.0, 78.0), now)
        now += 0.1
        event = self.counter.update(_comp(72.0, 74.0), now)
        # After 3 target frames, should transition to WAIT_FINISH
        self.assertEqual(self.counter.phase, "WAIT_FINISH")
        self.assertEqual(self.counter.count, 0)

        # Stand up: 3D angle rises above 135°
        now += 2.0  # past min_count_interval
        self.counter.update(_comp(140.0, 142.0), now)
        now += 0.1
        self.counter.update(_comp(150.0, 152.0), now)
        now += 0.1
        self.counter.update(_comp(155.0, 157.0), now)
        now += 0.1
        event = self.counter.update(_comp(158.0, 160.0), now)
        # After 4 finish frames, should complete a rep
        self.assertEqual(self.counter.phase, "WAIT_TARGET")
        self.assertEqual(self.counter.count, 1)
        self.assertEqual(event, "3DS_TEST_REP#1")

    def test_nan_skips_without_losing_progress(self):
        now = 100.0
        # Enter target phase
        now += 2.0
        self._feed(80.0, now, count=3)
        self.assertEqual(self.counter.phase, "WAIT_FINISH")

        # Feed NaN — phase stays, counters reset
        self.counter.update(_comp(float("nan"), float("nan")), now + 0.1)
        self.assertEqual(self.counter.phase, "WAIT_FINISH")
        self.assertTrue(math.isnan(self.counter.metric_3d))

        # Continue with valid data
        now += 2.0
        self._feed(155.0, now, count=4)
        self.assertEqual(self.counter.phase, "WAIT_TARGET")
        self.assertEqual(self.counter.count, 1)

    def test_one_side_nan_uses_finite_side(self):
        now = 100.0
        counter = Shadow3DRepCounter(
            target_threshold_3d=105,
            finish_threshold_3d=135,
            target_hold_frames=1,
            finish_hold_frames=1,
            min_count_interval=0.1,
            label="3DS",
        )
        # Left NaN, right valid
        now += 1.0
        counter.update(_comp(float("nan"), 80.0), now)
        self.assertEqual(counter.phase, "WAIT_FINISH")

        now += 1.0
        counter.update(_comp(float("nan"), 150.0), now)
        self.assertEqual(counter.count, 1)

    def test_min_count_interval_blocks_early_rep(self):
        now = 100.0
        # Complete one rep
        self._feed(80.0, now + 2.0, count=3)
        now += 2.5
        self._feed(155.0, now, count=4)
        self.assertEqual(self.counter.count, 1)

        # Try to start another rep immediately
        event = self.counter.update(_comp(75.0, 77.0), now + 0.1)
        self.assertIsNone(event)
        # Should stay in WAIT_TARGET because min_count_interval not elapsed
        self.assertEqual(self.counter.phase, "WAIT_TARGET")

    def test_reset_clears_count(self):
        now = 100.0
        self._feed(80.0, now + 2.0, count=3)
        now += 3.0
        self._feed(155.0, now, count=4)
        self.assertEqual(self.counter.count, 1)

        self.counter.reset()
        self.assertEqual(self.counter.count, 0)
        self.assertEqual(self.counter.phase, "WAIT_TARGET")

    def test_reset_transient_keeps_count(self):
        now = 100.0
        self._feed(80.0, now + 2.0, count=3)
        now += 3.0
        self._feed(155.0, now, count=4)
        self.assertEqual(self.counter.count, 1)
        self.assertEqual(self.counter.phase, "WAIT_TARGET")

        # Feed to enter WAIT_FINISH again
        now += 3.0
        self._feed(75.0, now, count=3)
        self.assertEqual(self.counter.phase, "WAIT_FINISH")

        # Transient reset: clears phase but preserves count
        self.counter.reset_transient()
        self.assertEqual(self.counter.count, 1)
        self.assertEqual(self.counter.phase, "WAIT_TARGET")

    def test_snapshot_reflects_current_state(self):
        now = 100.0
        self.counter.update(_comp(160.0, 162.0), now)
        snap = self.counter.snapshot()
        self.assertEqual(snap.phase, "WAIT_TARGET")
        self.assertEqual(snap.count, 0)
        self.assertTrue(math.isfinite(snap.metric_3d))


if __name__ == "__main__":
    unittest.main()

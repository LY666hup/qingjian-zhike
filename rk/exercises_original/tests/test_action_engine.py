import unittest
from types import SimpleNamespace
from unittest.mock import patch

from ai_fitness_action_engine import RepetitionActionEngine
from pose_metrics.types import MetricComparison


def make_config(**overrides):
    config = {
        "mode": "TEST",
        "metric_name": "Angle",
        "compute_metric": lambda landmarks: (landmarks, "metric"),
        "target_op": "<",
        "target_threshold": 50,
        "finish_op": ">",
        "finish_threshold": 80,
        "range_type": "min",
        "range_threshold": 45,
        "min_action_time": 0.5,
        "min_count_interval": 0.1,
        "target_hold_frames": 2,
        "finish_hold_frames": 2,
        "metric_history_len": 1,
        "tip_hold_time": 1.5,
        "swing_gyro_threshold": 100,
    }
    config.update(overrides)
    return config


class RepetitionActionEngineTests(unittest.TestCase):
    def test_counts_one_good_repetition_from_fresh_metrics(self):
        engine = RepetitionActionEngine(make_config())
        imu = {"gyro_mag": 0}

        engine.process_metric(40, imu, 1.0)
        engine.process_metric(40, imu, 1.1)
        self.assertEqual(engine.snapshot().phase, "WAIT_FINISH")

        engine.process_metric(90, imu, 2.0)
        update = engine.process_metric(90, imu, 2.1)
        self.assertTrue(update.completed)
        self.assertEqual(update.snapshot.count, 1)
        self.assertEqual(update.snapshot.score, 100)
        self.assertEqual(update.snapshot.tip, "GOOD")

    def test_display_only_snapshot_does_not_advance_hold_frames(self):
        engine = RepetitionActionEngine(make_config())
        engine.process_metric(40, {"gyro_mag": 0}, 1.0)

        for _ in range(10):
            engine.snapshot()

        self.assertEqual(engine.target_frame_count, 1)
        self.assertEqual(engine.snapshot().phase, "WAIT_TARGET")

    def test_switch_preserves_completed_result_but_discards_half_rep(self):
        engine = RepetitionActionEngine(make_config(target_hold_frames=1))
        engine.count = 3
        engine.score = 90
        engine.process_metric(40, {"gyro_mag": 0}, 1.0)
        self.assertEqual(engine.snapshot().phase, "WAIT_FINISH")

        engine.deactivate()
        engine.activate()

        self.assertEqual(engine.snapshot().count, 3)
        self.assertEqual(engine.snapshot().score, 90)
        self.assertEqual(engine.snapshot().phase, "WAIT_TARGET")
        self.assertEqual(engine.snapshot().tip, "READY")

    def test_monotonic_action_duration_classifies_too_fast(self):
        engine = RepetitionActionEngine(
            make_config(target_hold_frames=1, finish_hold_frames=1)
        )
        imu = {"gyro_mag": 0}
        engine.process_metric(40, imu, 10.0)
        update = engine.process_metric(90, imu, 10.2)

        self.assertEqual(update.snapshot.tip, "TOO_FAST")
        self.assertEqual(update.snapshot.score, 90)

    def test_comparison_does_not_change_legacy_metric_or_state(self):
        landmarks = [SimpleNamespace(x=40, y=0, z=0, visibility=1, presence=1)]

        class FakeComparison:
            def __init__(self):
                self.reset_count = 0

            def update(self, observation):
                return MetricComparison(
                    10, 20, 30, 40, 1, 1, 10, True, "3D diagnostic"
                )

            def reset(self):
                self.reset_count += 1

        comparison = FakeComparison()
        config = make_config(
            compute_metric=lambda points: (points[0].x, "legacy"),
            metric_comparison_factory=lambda **options: comparison,
            target_hold_frames=1,
        )
        with patch.dict("os.environ", {"FITNESS_POSE_METRIC_ENABLED": "1"}):
            engine = RepetitionActionEngine(config)

        update = engine.process_fresh(
            landmarks,
            {"gyro_mag": 0},
            1.0,
            world_landmarks=landmarks,
            timestamp_ms=1000,
        )

        self.assertEqual(update.snapshot.metric_value, 40)
        self.assertEqual(update.snapshot.phase, "WAIT_FINISH")
        self.assertIn("legacy | 3D diagnostic", update.snapshot.aux_text)
        resets_after_init = comparison.reset_count
        engine.recover_after_camera_reconnect()
        self.assertEqual(comparison.reset_count, resets_after_init + 1)

    def test_default_path_does_not_create_comparison(self):
        calls = []
        config = make_config(metric_comparison_factory=lambda: calls.append(True))
        with patch.dict("os.environ", {}, clear=True):
            engine = RepetitionActionEngine(config)
        self.assertIsNone(engine.metric_comparison)
        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()

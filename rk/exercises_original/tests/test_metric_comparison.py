import math
import unittest
from types import SimpleNamespace

from pose_metrics.comparison import (
    JointAngleComparisonEstimator,
    append_comparison_text,
    create_metric_comparison_estimator,
    first_world_landmarks,
)
from pose_metrics.types import PoseObservation


def landmark(x=0.0, y=0.0, z=0.0, confidence=1.0):
    return SimpleNamespace(
        x=x,
        y=y,
        z=z,
        visibility=confidence,
        presence=confidence,
    )


def make_landmarks():
    return [landmark() for _ in range(33)]


class MetricComparisonTests(unittest.TestCase):
    def test_compares_bilateral_2d_and_3d_angles(self):
        image = make_landmarks()
        world = make_landmarks()
        image[23], image[25], image[27] = (
            landmark(1, 0, confidence=0.9),
            landmark(0, 0, confidence=0.8),
            landmark(0, 1, confidence=0.7),
        )
        image[24], image[26], image[28] = (
            landmark(1, 0, confidence=0.95),
            landmark(0, 0, confidence=0.85),
            landmark(-1, 0, confidence=0.75),
        )
        world[23], world[25], world[27] = (
            landmark(1, 0, 0),
            landmark(0, 0, 0),
            landmark(0, 0, 1),
        )
        world[24], world[26], world[28] = (
            landmark(1, 0, 0),
            landmark(0, 0, 0),
            landmark(-1, 0, 0),
        )
        estimator = JointAngleComparisonEstimator(
            (23, 25, 27), (24, 26, 28)
        )

        result = estimator.update(PoseObservation(image, world, 123))

        self.assertAlmostEqual(result.left_angle_2d, 90.0)
        self.assertAlmostEqual(result.right_angle_2d, 180.0)
        self.assertAlmostEqual(result.left_angle_3d, 90.0)
        self.assertAlmostEqual(result.right_angle_3d, 180.0)
        self.assertEqual(result.left_chain_confidence, 0.7)
        self.assertEqual(result.right_chain_confidence, 0.75)
        self.assertEqual(result.legacy_value, 90.0)
        self.assertTrue(result.world_available)
        self.assertIn("3DF L:90.0 R:180.0", result.aux_text)

    def test_missing_world_landmarks_stays_diagnostic_only(self):
        image = make_landmarks()
        image[11], image[13], image[15] = (
            landmark(1, 0),
            landmark(0, 0),
            landmark(0, 1),
        )
        image[12], image[14], image[16] = (
            landmark(1, 0),
            landmark(0, 0),
            landmark(0, 1),
        )
        result = JointAngleComparisonEstimator(
            (11, 13, 15), (12, 14, 16)
        ).update(PoseObservation(image, None, 1))

        self.assertFalse(result.world_available)
        self.assertTrue(math.isnan(result.left_angle_3d))
        self.assertIn("3DF L:N/A(WORLD) R:N/A(WORLD)", result.aux_text)

    def test_factory_is_disabled_by_default(self):
        calls = []

        def factory(**options):
            calls.append(options)

        config = {"metric_comparison_factory": factory}
        self.assertIsNone(create_metric_comparison_estimator(config, {}))
        self.assertEqual(calls, [])

        estimator = create_metric_comparison_estimator(
            config, {"FITNESS_POSE_METRIC_ENABLED": "1"}
        )
        self.assertIsNone(estimator)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["smooth_alpha"], 0.68)
        self.assertEqual(calls[0]["min_quality"], 0.25)

    def test_filters_each_world_side_independently(self):
        image = make_landmarks()
        world = make_landmarks()
        for index in (23, 25, 27, 24, 26, 28):
            image[index] = landmark(confidence=1.0)
        world[23], world[25], world[27] = (
            landmark(1, 0, 0), landmark(0, 0, 0), landmark(0, 1, 0)
        )
        world[24], world[26], world[28] = (
            landmark(1, 0, 0), landmark(0, 0, 0), landmark(0, 1, 0)
        )
        estimator = JointAngleComparisonEstimator(
            (23, 25, 27),
            (24, 26, 28),
            smooth_alpha=0.5,
            min_quality=0.0,
            hard_jump_deg=180,
            max_speed_dps=10000,
        )
        first = estimator.update(PoseObservation(image, world, 100))
        self.assertAlmostEqual(first.left_angle_3d, 90.0)

        world[27] = landmark(-1, 0, 0)
        second = estimator.update(PoseObservation(image, world, 200))
        self.assertAlmostEqual(second.left_angle_3d, 135.0)
        self.assertAlmostEqual(second.right_angle_3d, 90.0)

    def test_low_quality_side_is_na_without_hiding_other_side(self):
        image = make_landmarks()
        world = make_landmarks()
        for index in (23, 25, 27):
            image[index] = landmark(confidence=0.2)
        for index in (24, 26, 28):
            image[index] = landmark(confidence=0.9)
        world[24], world[26], world[28] = (
            landmark(1, 0, 0), landmark(0, 0, 0), landmark(0, 1, 0)
        )
        result = JointAngleComparisonEstimator(
            (23, 25, 27), (24, 26, 28), min_quality=0.5
        ).update(PoseObservation(image, world, 100))
        self.assertTrue(math.isnan(result.left_angle_3d))
        self.assertAlmostEqual(result.right_angle_3d, 90.0)
        self.assertIn("L:N/A(Q)", result.aux_text)

    def test_jump_is_rejected_without_polluting_next_sample(self):
        image = make_landmarks()
        world = make_landmarks()
        for index in (23, 25, 27, 24, 26, 28):
            image[index] = landmark(confidence=1.0)
        for indices in ((23, 25, 27), (24, 26, 28)):
            world[indices[0]], world[indices[1]], world[indices[2]] = (
                landmark(1, 0, 0), landmark(0, 0, 0), landmark(0, 1, 0)
            )
        estimator = JointAngleComparisonEstimator(
            (23, 25, 27),
            (24, 26, 28),
            smooth_alpha=0.5,
            min_quality=0.0,
            max_speed_dps=100,
            hard_jump_deg=20,
        )
        estimator.update(PoseObservation(image, world, 100))
        world[27] = landmark(-1, 0, 0)
        rejected = estimator.update(PoseObservation(image, world, 200))
        self.assertTrue(math.isnan(rejected.left_angle_3d))
        self.assertIn("L:N/A(JUMP)", rejected.aux_text)

        world[27] = landmark(0.1, 1, 0)
        recovered = estimator.update(PoseObservation(image, world, 300))
        self.assertTrue(math.isfinite(recovered.left_angle_3d))
        self.assertLess(recovered.left_angle_3d, 100.0)

    def test_invalid_time_and_reset_do_not_reuse_old_state(self):
        image = make_landmarks()
        world = make_landmarks()
        for index in (23, 25, 27, 24, 26, 28):
            image[index] = landmark(confidence=1.0)
        for indices in ((23, 25, 27), (24, 26, 28)):
            world[indices[0]], world[indices[1]], world[indices[2]] = (
                landmark(1, 0, 0), landmark(0, 0, 0), landmark(0, 1, 0)
            )
        estimator = JointAngleComparisonEstimator(
            (23, 25, 27), (24, 26, 28), min_quality=0.0
        )
        estimator.update(PoseObservation(image, world, 100))
        duplicate = estimator.update(PoseObservation(image, world, 100))
        self.assertTrue(math.isnan(duplicate.left_angle_3d))
        self.assertIn("L:N/A(TIME)", duplicate.aux_text)

        estimator.reset()
        world[27] = landmark(-1, 0, 0)
        after_reset = estimator.update(PoseObservation(image, world, 200))
        self.assertAlmostEqual(after_reset.left_angle_3d, 180.0)

    def test_aux_text_and_world_result_helpers(self):
        self.assertEqual(append_comparison_text("L:90", SimpleNamespace(aux_text="3D:91")), "L:90 | 3D:91")
        self.assertIsNone(first_world_landmarks(SimpleNamespace()))
        self.assertIsNone(first_world_landmarks(SimpleNamespace(pose_world_landmarks=[])))
        points = [landmark()]
        self.assertIs(
            first_world_landmarks(SimpleNamespace(pose_world_landmarks=[points])),
            points,
        )


if __name__ == "__main__":
    unittest.main()

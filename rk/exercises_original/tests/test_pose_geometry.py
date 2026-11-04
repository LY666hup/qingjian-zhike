import math
import unittest
from types import SimpleNamespace

import numpy as np

from pose_metrics.geometry import (
    angle_3points_nd,
    chain_confidence,
    get_xy,
    get_xyz,
    landmark_confidence,
    midpoint,
    safe_distance,
)


class PoseGeometryTests(unittest.TestCase):
    def test_computes_2d_and_3d_angles(self):
        self.assertAlmostEqual(
            angle_3points_nd([1, 0], [0, 0], [0, 1]), 90.0
        )
        self.assertAlmostEqual(
            angle_3points_nd([1, 0, 0], [0, 0, 0], [-1, 0, 0]),
            180.0,
        )

    def test_angle_is_translation_and_scale_invariant(self):
        baseline = angle_3points_nd([1, 0], [0, 0], [0, 1])
        transformed = angle_3points_nd([12, 10], [10, 10], [10, 12])
        self.assertAlmostEqual(baseline, transformed)

    def test_rejects_non_finite_and_zero_length_vectors(self):
        for args in [
            ([0, 0], [0, 0], [1, 0]),
            ([math.nan, 0], [0, 0], [1, 0]),
            ([math.inf, 0, 0], [0, 0, 0], [1, 0, 0]),
            ([0, 0], [0, 0, 0], [1, 0]),
        ]:
            with self.subTest(args=args):
                self.assertTrue(math.isnan(angle_3points_nd(*args)))

    def test_getters_fail_safely(self):
        landmark = SimpleNamespace(x=1, y=2, z=3)
        np.testing.assert_allclose(get_xy([landmark], 0), [1, 2])
        np.testing.assert_allclose(get_xyz([landmark], 0), [1, 2, 3])
        self.assertTrue(np.all(np.isnan(get_xyz([], 0))))

    def test_confidence_uses_visibility_and_presence(self):
        landmark = SimpleNamespace(visibility=0.8, presence=0.6)
        self.assertEqual(landmark_confidence(landmark), 0.6)
        self.assertEqual(landmark_confidence(SimpleNamespace()), 0.0)
        self.assertEqual(
            chain_confidence(
                [
                    SimpleNamespace(
                        x=0,
                        y=0,
                        z=0,
                        visibility=0.9,
                        presence=0.8,
                    ),
                    SimpleNamespace(
                        x=1,
                        y=1,
                        z=0,
                        visibility=0.5,
                        presence=0.7,
                    ),
                ],
                (0, 1),
            ),
            0.5,
        )
        self.assertEqual(
            chain_confidence(
                [
                    SimpleNamespace(
                        x=math.nan,
                        y=0,
                        z=0,
                        visibility=1,
                        presence=1,
                    )
                ],
                (0,),
            ),
            0.0,
        )

    def test_midpoint_and_distance(self):
        np.testing.assert_allclose(midpoint([0, 0], [2, 4]), [1, 2])
        self.assertEqual(safe_distance([0, 0], [3, 4]), 5.0)
        self.assertTrue(math.isnan(safe_distance([math.nan, 0], [0, 0])))


if __name__ == "__main__":
    unittest.main()

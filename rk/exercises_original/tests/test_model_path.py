import tempfile
import unittest
from pathlib import Path

from ai_fitness_common import resolve_model_path


class ModelPathTests(unittest.TestCase):
    def test_relative_override_resolves_from_source_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory)
            expected = source / "models" / "pose.task"
            self.assertEqual(
                resolve_model_path(
                    {"FITNESS_MODEL_PATH": "models/pose.task"}, source
                ),
                expected,
            )

    def test_default_prefers_full_from_source_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory)
            (source / "pose_landmarker_full.task").touch()
            self.assertEqual(
                resolve_model_path({}, source),
                source / "pose_landmarker_full.task",
            )

    def test_default_falls_back_to_lite_from_source_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory)
            self.assertEqual(
                resolve_model_path({}, source),
                source / "pose_landmarker_lite.task",
            )


if __name__ == "__main__":
    unittest.main()

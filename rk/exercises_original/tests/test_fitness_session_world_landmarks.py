import unittest
from types import SimpleNamespace

import ai_fitness_session as session_module
from ai_fitness_session import FitnessSession, SessionResources


class FakeCv2:
    COLOR_BGR2RGB = 1

    @staticmethod
    def cvtColor(frame, conversion):
        return frame


class FakeEngine:
    def __init__(self):
        self.calls = []
        self.metric_comparison = object()

    def process_fresh(self, landmarks, imu, now, **kwargs):
        self.calls.append((landmarks, kwargs))
        return SimpleNamespace(
            landmarks=landmarks,
            completed=False,
            snapshot=SimpleNamespace(),
        )

    def snapshot(self):
        return SimpleNamespace(count=0, tip="READY")

    def no_person_fresh(self):
        return SimpleNamespace(snapshot=SimpleNamespace())


class FakeLandmarker:
    def __init__(self, result):
        self.result = result
        self.calls = 0

    def detect_for_video(self, image, timestamp_ms):
        self.calls += 1
        return self.result


class FitnessSessionWorldLandmarksTests(unittest.TestCase):
    def make_session(self):
        clock_values = iter([1.0, 1.1, 1.2, 1.3])
        resources = SessionResources(
            clock=lambda: next(clock_values),
            cv2_module=FakeCv2,
        )
        runtime = FitnessSession(resources=resources)
        runtime.active_engine = FakeEngine()
        runtime._publish_feedback = lambda force=False: None
        runtime._last_imu = {"gyro_mag": 0}
        return runtime

    def test_passes_world_landmarks_from_same_fresh_result(self):
        image = [SimpleNamespace(x=0, y=0, z=0)]
        world = [SimpleNamespace(x=1, y=2, z=3)]
        result = SimpleNamespace(
            pose_landmarks=[image],
            pose_world_landmarks=[world],
        )
        runtime = self.make_session()

        original_image_class = session_module.mp.Image
        try:
            session_module.mp.Image = lambda **kwargs: object()
            runtime._process_frame(FakeLandmarker(result), object())
        finally:
            session_module.mp.Image = original_image_class

        _, kwargs = runtime.active_engine.calls[0]
        self.assertIs(kwargs["world_landmarks"], world)
        self.assertIsInstance(kwargs["timestamp_ms"], int)

    def test_missing_world_is_none_and_cached_frame_does_not_repeat(self):
        image = [SimpleNamespace(x=0, y=0, z=0)]
        result = SimpleNamespace(pose_landmarks=[image])
        runtime = self.make_session()
        original_cadence = session_module.common.PROCESS_EVERY_N_FRAMES
        original_image_class = session_module.mp.Image
        try:
            session_module.common.PROCESS_EVERY_N_FRAMES = 3
            session_module.mp.Image = lambda **kwargs: object()
            landmarker = FakeLandmarker(result)
            runtime._process_frame(landmarker, object())
            runtime._process_frame(landmarker, object())
        finally:
            session_module.common.PROCESS_EVERY_N_FRAMES = original_cadence
            session_module.mp.Image = original_image_class

        self.assertEqual(landmarker.calls, 1)
        self.assertEqual(len(runtime.active_engine.calls), 1)
        _, kwargs = runtime.active_engine.calls[0]
        self.assertIsNone(kwargs["world_landmarks"])


if __name__ == "__main__":
    unittest.main()

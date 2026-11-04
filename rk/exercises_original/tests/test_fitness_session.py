import unittest

from ai_fitness_action_engine import RepetitionActionEngine
from ai_fitness_session import FitnessSession, SessionResources
from fitness_voice_control import ActionResult


def config(mode):
    return {
        "mode": mode,
        "compute_metric": lambda value: (value, ""),
        "target_op": "<",
        "target_threshold": 50,
        "finish_op": ">",
        "finish_threshold": 80,
        "range_type": "min",
        "range_threshold": 45,
    }


class FakeRuntime:
    def __init__(self):
        self.feedback = []

    def publish_feedback(self, mode, count, score, tip):
        self.feedback.append((mode, count, score, tip))


class FitnessSessionStateTests(unittest.TestCase):
    def setUp(self):
        self.created = []

        def loader(action):
            mode = "SQUAT" if action == "SQUAT" else "CURL"
            engine = RepetitionActionEngine(config(mode))
            self.created.append(action)
            spec = type("Spec", (), {"mode": mode})()
            return spec, engine

        self.runtime = FakeRuntime()
        self.session = FitnessSession(runtime=self.runtime, engine_loader=loader)

    def test_switch_restores_each_actions_completed_state(self):
        self.session.activate("SQUAT")
        self.session.active_engine.count = 4
        self.session.active_engine.score = 90

        self.session.handle_command(ActionResult("SWITCH", "CURL"))
        self.session.active_engine.count = 2
        self.session.handle_command(ActionResult("SWITCH", "SQUAT"))

        self.assertEqual(self.session.active_engine.snapshot().count, 4)
        self.assertEqual(self.session.active_engine.snapshot().score, 90)
        self.assertEqual(self.created, ["SQUAT", "CURL"])

    def test_reset_current_does_not_reset_other_action(self):
        self.session.activate("SQUAT")
        self.session.active_engine.count = 5
        self.session.activate("CURL")
        self.session.active_engine.count = 3

        self.session.reset_current()
        self.assertEqual(self.session.active_engine.snapshot().count, 0)
        self.session.activate("SQUAT")
        self.assertEqual(self.session.active_engine.snapshot().count, 5)

    def test_reset_all_resets_every_loaded_action(self):
        self.session.activate("SQUAT")
        self.session.active_engine.count = 5
        self.session.activate("CURL")
        self.session.active_engine.count = 3

        self.session.reset_all()
        self.assertEqual(self.session.engines["SQUAT"].snapshot().count, 0)
        self.assertEqual(self.session.engines["CURL"].snapshot().count, 0)

    def test_switch_immediately_publishes_restored_state(self):
        self.session.activate("SQUAT")
        self.session.active_engine.count = 6
        self.session.activate("CURL")
        self.session.activate("SQUAT")

        self.assertEqual(self.runtime.feedback[-1], ("SQUAT", 6, 100, "READY"))


class FitnessSessionResourceTests(unittest.TestCase):
    def test_switches_share_resources_and_cleanup_once(self):
        calls = []

        class FakeDisplay:
            def open(self):
                calls.append("display_open")

            def set_mouse_callback(self, callback):
                calls.append("mouse_callback")

            def show(self, frame):
                return 255

            def close(self):
                calls.append("display_close")

        class FakeCapture:
            def read(self):
                return False, None

            def release(self):
                calls.append("camera_release")

        class FakeLandmarker:
            def __enter__(self):
                calls.append("landmarker_open")
                return self

            def __exit__(self, *args):
                calls.append("landmarker_close")

        class Runtime(FakeRuntime):
            def __init__(self):
                super().__init__()
                self.commands = [
                    ActionResult("SWITCH", "CURL"),
                    ActionResult("SWITCH", "SQUAT"),
                    ActionResult("STOP"),
                ]

            def poll_control(self, active_action):
                return self.commands.pop(0)

        def loader(action):
            mode = "SQUAT" if action == "SQUAT" else "CURL"
            return type("Spec", (), {"mode": mode})(), RepetitionActionEngine(
                config(mode)
            )

        def reconnect(cap):
            if cap is not None:
                cap.release()
            return None

        capture = FakeCapture()
        resources = SessionResources(
            open_camera=lambda: calls.append("camera_open") or capture,
            reconnect_camera=reconnect,
            create_landmarker=lambda: FakeLandmarker(),
            open_imu=lambda: calls.append("imu_open") or True,
            read_imu=lambda handle, last: last,
            stop_imu=lambda: calls.append("imu_stop"),
            open_serial=lambda: None,
            display_factory=lambda: FakeDisplay(),
        )
        session = FitnessSession(
            runtime=Runtime(), engine_loader=loader, resources=resources
        )
        result = session.run("SQUAT")

        self.assertEqual(result.kind, "STOP")
        self.assertEqual(calls.count("camera_open"), 1)
        self.assertEqual(calls.count("landmarker_open"), 1)
        self.assertEqual(calls.count("display_open"), 1)
        self.assertEqual(calls.count("imu_open"), 1)
        self.assertEqual(calls.count("camera_release"), 1)
        self.assertEqual(calls.count("landmarker_close"), 1)
        self.assertEqual(calls.count("display_close"), 1)
        self.assertEqual(calls.count("imu_stop"), 1)

    def test_externally_managed_imu_is_not_stopped_by_session(self):
        calls = []

        class FakeDisplay:
            def open(self):
                pass

            def set_mouse_callback(self, callback):
                pass

            def close(self):
                pass

        class FakeCapture:
            def release(self):
                pass

        class FakeLandmarker:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

        class Runtime(FakeRuntime):
            def poll_control(self, active_action):
                return ActionResult("STOP")

        resources = SessionResources(
            open_camera=lambda: FakeCapture(),
            create_landmarker=lambda: FakeLandmarker(),
            open_imu=lambda: calls.append("imu_open") or True,
            read_imu=lambda handle, last: last,
            stop_imu=lambda: calls.append("imu_stop"),
            manage_imu_lifecycle=False,
            open_serial=lambda: None,
            display_factory=lambda: FakeDisplay(),
        )
        session = FitnessSession(runtime=Runtime(), resources=resources)
        result = session.run("SQUAT")

        self.assertEqual(result.kind, "STOP")
        self.assertEqual(calls.count("imu_open"), 1)
        self.assertEqual(calls.count("imu_stop"), 0)


if __name__ == "__main__":
    unittest.main()

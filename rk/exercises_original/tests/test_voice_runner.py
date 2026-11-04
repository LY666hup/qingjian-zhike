import unittest

from ai_fitness_action_registry import ActionSpec
from ai_fitness_voice_runner import (
    SupervisorState,
    VoiceActionRuntime,
    VoiceFitnessSupervisor,
    apply_idle_events,
)
from fitness_voice_control import ActionResult, VoiceController, VoiceEvent


class FakeTransport:
    def __init__(self):
        self.feedback = []

    def publish_feedback(self, mode, count, score, tip):
        self.feedback.append((mode, count, score, tip))


class VoiceRunnerTests(unittest.TestCase):
    def test_action_immediately_starts_and_selects(self):
        state = SupervisorState()
        apply_idle_events(state, [VoiceEvent(1, "ACTION", "PLANK")])
        self.assertEqual(state.selected_action, "PLANK")
        self.assertTrue(state.should_start)

    def test_start_uses_default_squat(self):
        state = SupervisorState()
        apply_idle_events(state, [VoiceEvent(1, "START")])
        self.assertEqual(state.selected_action, "SQUAT")
        self.assertTrue(state.should_start)

    def test_stop_wins_when_it_arrives_last(self):
        state = SupervisorState()
        apply_idle_events(
            state,
            [
                VoiceEvent(1, "ACTION", "CURL"),
                VoiceEvent(2, "STOP"),
            ],
        )
        self.assertEqual(state.selected_action, "CURL")
        self.assertFalse(state.should_start)

    def test_latest_action_wins_during_idle_cleanup(self):
        state = SupervisorState()
        apply_idle_events(
            state,
            [
                VoiceEvent(1, "ACTION", "CURL"),
                VoiceEvent(2, "ACTION", "SQUAT"),
            ],
        )
        self.assertEqual(state.selected_action, "SQUAT")
        self.assertTrue(state.should_start)

    def test_runtime_publishes_and_returns_switch(self):
        controller = VoiceController()
        transport = FakeTransport()
        runtime = VoiceActionRuntime(controller, transport)
        runtime.publish_feedback("CURL", 2, 90, "GOOD")
        controller.submit_line(b"VOICE,ACTION,PLANK\n")

        result = runtime.poll_control("CURL")
        self.assertEqual(transport.feedback, [("CURL", 2, 90, "GOOD")])
        self.assertEqual(result, ActionResult("SWITCH", "PLANK"))

    def test_start_is_ignored_while_action_is_active(self):
        controller = VoiceController()
        runtime = VoiceActionRuntime(controller, FakeTransport())
        controller.submit_line(b"VOICE,START\n")
        self.assertEqual(runtime.poll_control("SQUAT").kind, "CONTINUE")

    def test_supervisor_starts_imu_before_waiting_for_start(self):
        calls = []
        controller = VoiceController()

        class Transport(FakeTransport):
            def start(self):
                calls.append("transport_start")

            def close(self):
                calls.append("transport_close")

        supervisor = VoiceFitnessSupervisor(
            controller,
            Transport(),
            open_imu=lambda: calls.append("imu_open") or True,
            stop_imu=lambda: calls.append("imu_stop"),
        )

        def wait_and_shutdown(timeout=None):
            calls.append("idle_wait")
            supervisor.state.shutdown = True
            return []

        controller.wait_and_drain = wait_and_shutdown
        supervisor.run()

        self.assertEqual(
            calls,
            [
                "transport_start",
                "imu_open",
                "idle_wait",
                "imu_stop",
                "transport_close",
            ],
        )

    def test_supervisor_stops_imu_after_session_quit(self):
        calls = []
        controller = VoiceController()

        class Transport(FakeTransport):
            def start(self):
                calls.append("transport_start")

            def close(self):
                calls.append("transport_close")

        class Session:
            def run(self, initial_action):
                calls.append("session_run")
                return ActionResult("QUIT")

        controller.submit_line(b"VOICE,START\n")
        supervisor = VoiceFitnessSupervisor(
            controller,
            Transport(),
            session_factory=lambda runtime=None: Session(),
            open_imu=lambda: calls.append("imu_open") or True,
            stop_imu=lambda: calls.append("imu_stop"),
        )
        supervisor.run()

        self.assertEqual(calls.count("imu_open"), 1)
        self.assertEqual(calls.count("imu_stop"), 1)
        self.assertLess(calls.index("imu_open"), calls.index("session_run"))
        self.assertLess(calls.index("session_run"), calls.index("imu_stop"))
        self.assertEqual(calls[-1], "transport_close")

    def test_transport_closes_when_imu_start_raises(self):
        calls = []
        controller = VoiceController()

        class Transport(FakeTransport):
            def start(self):
                calls.append("transport_start")

            def close(self):
                calls.append("transport_close")

        def fail_open():
            calls.append("imu_open")
            raise RuntimeError("imu failed")

        supervisor = VoiceFitnessSupervisor(
            controller,
            Transport(),
            open_imu=fail_open,
            stop_imu=lambda: calls.append("imu_stop"),
        )

        with self.assertRaisesRegex(RuntimeError, "imu failed"):
            supervisor.run()

        self.assertEqual(
            calls,
            ["transport_start", "imu_open", "imu_stop", "transport_close"],
        )


if __name__ == "__main__":
    unittest.main()

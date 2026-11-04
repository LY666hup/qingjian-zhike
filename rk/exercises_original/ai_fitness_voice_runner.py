#!/home/pi/ESP32/.venv/bin/python
from __future__ import annotations

from ai_fitness_env import load_project_env

load_project_env()

import logging
import os
from dataclasses import dataclass
from typing import Callable

from ai_fitness_action_registry import get_action_spec, load_action_runner
from fitness_voice_control import (
    ActionResult,
    FeedbackTransport,
    VoiceController,
    resolve_active_events,
)


LOGGER = logging.getLogger(__name__)
DEFAULT_VOICE_ACTION = "SQUAT"
SIMULATION_SCENARIO = (
    b"VOICE,ACTION,CURL\n",
    b"VOICE,ACTION,PLANK\n",
    b"VOICE,STOP\n",
    b"VOICE,START\n",
    b"VOICE,ACTION,SQUAT\n",
)


class VoiceActionRuntime:
    def __init__(self, controller: VoiceController, transport: FeedbackTransport):
        self.controller = controller
        self.transport = transport

    def publish_feedback(self, mode: str, count: int, score: int, tip: str):
        self.transport.publish_feedback(mode, count, score, tip)

    def publish_heart_rate(self, bpm: int, valid: bool):
        self.transport.publish_heart_rate(bpm, valid)

    def poll_control(self, active_action: str):
        return resolve_active_events(self.controller.drain(), active_action)


@dataclass
class SupervisorState:
    selected_action: str = DEFAULT_VOICE_ACTION
    should_start: bool = False
    shutdown: bool = False


def apply_idle_events(state: SupervisorState, events):
    """Apply ordered events while no recognition loop owns the controls."""
    for event in events:
        if event.kind == "ACTION":
            state.selected_action = event.action
            state.should_start = True
        elif event.kind == "START":
            state.should_start = True
        elif event.kind == "STOP":
            state.should_start = False
        # RESET while idle simply leaves the next set in its initial state.
    return state


class VoiceFitnessSupervisor:
    def __init__(
        self,
        controller: VoiceController,
        transport: FeedbackTransport,
        runner_loader: Callable = load_action_runner,
        session_factory: Callable | None = None,
        open_imu: Callable | None = None,
        stop_imu: Callable | None = None,
    ):
        self.controller = controller
        self.transport = transport
        self.runner_loader = runner_loader
        self.session_factory = session_factory
        self.open_imu = open_imu
        self.stop_imu = stop_imu
        self.state = SupervisorState()
        self.runtime = VoiceActionRuntime(controller, transport)

    def run(self):
        self.transport.start()

        try:
            if self.open_imu is not None:
                self.open_imu()
                LOGGER.info("蓝牙 IMU 后台连接已启动")

            LOGGER.info("语音动作监督器已启动，默认动作：%s", DEFAULT_VOICE_ACTION)

            while not self.state.shutdown:
                if not self.state.should_start:
                    events = self.controller.wait_and_drain(timeout=0.25)
                    apply_idle_events(self.state, events)
                    continue

                # Collapse commands that arrived during the previous action's cleanup.
                apply_idle_events(self.state, self.controller.drain())
                if not self.state.should_start:
                    continue

                selected = self.state.selected_action
                self.state.should_start = False
                try:
                    if self.session_factory is None:
                        spec, runner = self.runner_loader(selected)
                        LOGGER.info("启动动作识别：%s (%s)", spec.mode, selected)
                        result = runner(runtime=self.runtime)
                    else:
                        session = self.session_factory(runtime=self.runtime)
                        spec = get_action_spec(selected)
                        LOGGER.info("启动统一动作会话：%s (%s)", spec.mode, selected)
                        result = session.run(initial_action=selected)
                except Exception:
                    LOGGER.exception("动作识别异常，已返回语音待命状态：%s", selected)
                    continue

                if result is None:
                    result = ActionResult("STOP")

                if result.kind == "SWITCH" and result.action:
                    if get_action_spec(result.action) is not None:
                        self.state.selected_action = result.action
                        self.state.should_start = True
                elif result.kind == "QUIT":
                    self.state.shutdown = True
                elif result.kind != "STOP":
                    LOGGER.warning("动作运行器返回未知结果：%s", result)
        except KeyboardInterrupt:
            LOGGER.info("收到终端中断，正在关闭语音动作监督器")
        finally:
            try:
                if self.stop_imu is not None:
                    self.stop_imu()
            finally:
                self.transport.close()


def run_simulation():
    """Drive the public supervisor entry point without camera or serial hardware."""
    from ai_fitness_display import resolve_display_enabled
    from fitness_voice_control import format_feedback_command

    state = SupervisorState()
    display_enabled = resolve_display_enabled()
    print("SIMULATION: voice supervisor headless mode")
    print(f"DISPLAY preview={'enabled' if display_enabled else 'headless'}")

    for line in SIMULATION_SCENARIO:
        from fitness_voice_control import parse_voice_line

        parsed = parse_voice_line(line)
        kind, action = parsed
        event = type("SimulationEvent", (), {"kind": kind, "action": action})()
        apply_idle_events(state, [event])
        print(
            f"RX {line.decode().strip()} -> selected={state.selected_action} "
            f"start={state.should_start}"
        )
        if state.should_start:
            spec = get_action_spec(state.selected_action)
            packet = format_feedback_command(spec.mode, 0, 100, "READY")
            print("TX " + packet.decode().strip())
            state.should_start = False

    # Exercise recovery at the real supervisor boundary with injected no-hardware runners.
    controller = VoiceController()

    class SimulationTransport:
        def start(self):
            print("TRANSPORT started")

        def close(self):
            print("TRANSPORT closed")

        def publish_feedback(self, mode, count, score, tip):
            packet = format_feedback_command(mode, count, score, tip)
            print("TX " + packet.decode().strip())

    calls = []

    def simulation_loader(action):
        spec = get_action_spec(action)

        def runner(runtime=None):
            calls.append(action)
            if len(calls) == 1:
                controller.submit_line(b"VOICE,START\n")
                raise RuntimeError("simulated action failure")
            controller.submit_line(b"VOICE,STOP\n")
            return ActionResult("STOP")

        return spec, runner

    transport = SimulationTransport()
    supervisor = VoiceFitnessSupervisor(controller, transport, simulation_loader)
    controller.submit_line(b"VOICE,START\n")

    original_wait = controller.wait_and_drain

    def wait_and_stop(timeout=None):
        events = original_wait(timeout=0)
        if not events and calls:
            supervisor.state.shutdown = True
        return events

    controller.wait_and_drain = wait_and_stop
    supervisor.run()
    print("RECOVERY action_exception -> supervisor remained available")
    print("SIMULATION: PASS")


def main():
    if os.getenv("FITNESS_SIMULATE_VOICE", "0").strip().lower() in {
        "1", "true", "yes", "on"
    }:
        run_simulation()
        return

    from ai_fitness_common import (
        BAUD,
        SERIAL_PORT,
        open_imu_serial,
        stop_imu_reader,
    )
    from ai_fitness_display import resolve_display_enabled
    from ai_fitness_session import FitnessSession, SessionResources

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    preview_enabled = resolve_display_enabled()
    LOGGER.info(
        "启动显示模式：%s",
        "摄像头预览" if preview_enabled else "无头模式",
    )
    controller = VoiceController()
    transport = FeedbackTransport(SERIAL_PORT, BAUD, controller)

    def session_factory(runtime=None):
        resources = SessionResources(manage_imu_lifecycle=False)
        return FitnessSession(runtime=runtime, resources=resources)

    VoiceFitnessSupervisor(
        controller,
        transport,
        session_factory=session_factory,
        open_imu=open_imu_serial,
        stop_imu=stop_imu_reader,
    ).run()


if __name__ == "__main__":
    main()

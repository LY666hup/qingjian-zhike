"""Unified nine-action entry point with optional buttons and voice control."""

from ai_fitness_env import load_project_env

load_project_env()

import logging
import os

from ai_fitness_voice_runner import VoiceActionRuntime, run_simulation


def main():
    if os.getenv("FITNESS_SIMULATE_VOICE", "0").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }:
        run_simulation()
        return

    from ai_fitness_common import BAUD, SERIAL_PORT
    from ai_fitness_session import FitnessSession
    from fitness_voice_control import FeedbackTransport, VoiceController

    logging.basicConfig(
        level=os.getenv("FITNESS_LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    controller = VoiceController()
    transport = FeedbackTransport(SERIAL_PORT, BAUD, controller)
    runtime = VoiceActionRuntime(controller, transport)
    transport.start()
    try:
        FitnessSession(runtime=runtime).run(initial_action="SQUAT")
    finally:
        transport.close()


if __name__ == "__main__":
    main()

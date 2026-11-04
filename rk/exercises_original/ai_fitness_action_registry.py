from __future__ import annotations

import importlib
from dataclasses import dataclass
from typing import Callable, Optional


@dataclass(frozen=True)
class ActionSpec:
    mode: str
    module: str
    kind: str = "repetition"


ACTION_SPECS = {
    "CURL": ActionSpec("CURL", "ai_fitness_curl2"),
    "LATERAL_RAISE": ActionSpec("LATERAL", "ai_fitness_lateral2"),
    "FRONT_RAISE": ActionSpec("FRONT", "ai_fitness_front2"),
    "OVERHEAD_PRESS": ActionSpec("PRESS", "ai_fitness_press2"),
    "BENT_OVER_ROW": ActionSpec("ROW", "ai_fitness_row"),
    "OVERHEAD_TRICEPS": ActionSpec("TRICEPS", "ai_fitness_triceps"),
    "PUSHUP": ActionSpec("PUSHUP", "ai_fitness_pushup2"),
    "SQUAT": ActionSpec("SQUAT", "ai_fitness_squat"),
    "PLANK": ActionSpec("PLANK", "ai_fitness_plank", kind="plank"),
}


ACTION_ORDER = tuple(ACTION_SPECS)
ACTION_BUTTONS = {
    "CURL": "Curl",
    "LATERAL_RAISE": "Lateral",
    "FRONT_RAISE": "Front",
    "OVERHEAD_PRESS": "Press",
    "BENT_OVER_ROW": "Row",
    "OVERHEAD_TRICEPS": "Triceps",
    "PUSHUP": "Push-up",
    "SQUAT": "Squat",
    "PLANK": "Plank",
}


def get_action_spec(voice_action: str) -> Optional[ActionSpec]:
    return ACTION_SPECS.get(voice_action)


def get_action_module(spec: ActionSpec, environ=None) -> str:
    del environ
    return spec.module


def load_action_engine(
    voice_action: str,
    environ=None,
    importer: Callable[[str], object] = importlib.import_module,
):
    """Lazy-load one persistent action engine without copying its thresholds."""
    spec = get_action_spec(voice_action)
    if spec is None:
        raise ValueError(f"unsupported voice action: {voice_action}")

    module_name = get_action_module(spec, environ=environ)
    module = importer(module_name)
    if spec.kind == "plank":
        engine = module.PlankActionEngine()
    else:
        engine_module = importer("ai_fitness_action_engine")
        engine = engine_module.RepetitionActionEngine(module.config)
    return spec, engine


def load_action_runner(
    voice_action: str,
    environ=None,
    importer: Callable[[str], object] = importlib.import_module,
):
    """Lazy-load one existing action implementation without changing its config."""
    spec = get_action_spec(voice_action)
    if spec is None:
        raise ValueError(f"unsupported voice action: {voice_action}")

    module_name = get_action_module(spec, environ=environ)
    module = importer(module_name)

    if spec.kind == "plank":
        action = module.run_plank_straight_action
    else:
        config = module.config
        common = importer("ai_fitness_common")
        action = lambda runtime=None: common.run_repetition_action(
            config, runtime=runtime
        )

    return spec, action

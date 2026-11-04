import unittest

from ai_fitness_action_registry import (
    ACTION_SPECS,
    get_action_module,
    get_action_spec,
    load_action_engine,
    load_action_runner,
)


class FakeModule:
    config = {"mode": "CURL"}

    @staticmethod
    def run_plank_straight_action(runtime=None):
        return runtime


class FakeCommon:
    @staticmethod
    def run_repetition_action(config, runtime=None):
        return config, runtime


class FakeEngineModule:
    class RepetitionActionEngine:
        def __init__(self, config):
            self.config = config


class FakePlankModule(FakeModule):
    class PlankActionEngine:
        pass


class ActionRegistryTests(unittest.TestCase):
    def test_maps_voice_names_to_canonical_modes(self):
        expected = {
            "CURL": "CURL",
            "LATERAL_RAISE": "LATERAL",
            "FRONT_RAISE": "FRONT",
            "OVERHEAD_PRESS": "PRESS",
            "BENT_OVER_ROW": "ROW",
            "OVERHEAD_TRICEPS": "TRICEPS",
            "PUSHUP": "PUSHUP",
            "SQUAT": "SQUAT",
            "PLANK": "PLANK",
        }
        self.assertEqual(
            {name: spec.mode for name, spec in ACTION_SPECS.items()}, expected
        )

    def test_registry_uses_reference_calibrations(self):
        expected = {
            "CURL": "ai_fitness_curl2",
            "LATERAL_RAISE": "ai_fitness_lateral2",
            "FRONT_RAISE": "ai_fitness_front2",
            "OVERHEAD_PRESS": "ai_fitness_press2",
            "PUSHUP": "ai_fitness_pushup2",
        }
        self.assertEqual(
            {name: get_action_module(get_action_spec(name)) for name in expected},
            expected,
        )

    def test_lazy_loader_imports_only_selected_action_and_common(self):
        imported = []

        def importer(name):
            imported.append(name)
            if name == "ai_fitness_common":
                return FakeCommon
            return FakeModule

        spec, runner = load_action_runner("CURL", importer=importer)
        marker = object()
        self.assertEqual(runner(marker), ({"mode": "CURL"}, marker))
        self.assertEqual(imported, ["ai_fitness_curl2", "ai_fitness_common"])
        self.assertEqual(spec.mode, "CURL")

    def test_engine_loader_preserves_selected_module_config(self):
        def importer(name):
            if name == "ai_fitness_action_engine":
                return FakeEngineModule
            return FakeModule

        spec, engine = load_action_engine("CURL", importer=importer)
        self.assertEqual(spec.mode, "CURL")
        self.assertEqual(engine.config, {"mode": "CURL"})

    def test_plank_engine_loader_uses_dedicated_engine(self):
        spec, engine = load_action_engine(
            "PLANK", importer=lambda name: FakePlankModule
        )
        self.assertEqual(spec.kind, "plank")
        self.assertIsInstance(engine, FakePlankModule.PlankActionEngine)

    def test_unknown_action_is_rejected(self):
        with self.assertRaises(ValueError):
            load_action_runner("DANCE")


if __name__ == "__main__":
    unittest.main()

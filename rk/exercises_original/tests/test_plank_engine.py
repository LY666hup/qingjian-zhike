import unittest

import ai_fitness_plank as plank


class FakePlankEngine(plank.PlankActionEngine):
    def __init__(self, metrics):
        self._metrics = iter(metrics)
        super().__init__()

    def process_metric(self, now):
        body_angle, line_error = next(self._metrics)
        original = plank.plank_metric
        plank.plank_metric = lambda landmarks: (body_angle, line_error, "metric")
        try:
            return self.process_fresh([], {}, now)
        finally:
            plank.plank_metric = original


class PlankActionEngineTests(unittest.TestCase):
    def test_valid_hold_accumulates_seconds(self):
        engine = FakePlankEngine([(170, 5), (170, 5)])
        engine.process_metric(10.0)
        update = engine.process_metric(12.2)

        self.assertEqual(update.snapshot.count, 2)
        self.assertEqual(update.snapshot.tip, "GOOD")

    def test_bad_form_pauses_without_losing_completed_time(self):
        engine = FakePlankEngine([(170, 5), (170, 5), (140, 20)])
        engine.process_metric(10.0)
        engine.process_metric(12.0)
        update = engine.process_metric(13.0)

        self.assertEqual(update.snapshot.count, 2)
        self.assertEqual(update.snapshot.tip, "BAD_FORM")
        self.assertEqual(update.snapshot.phase, "ADJUST")

    def test_switch_preserves_completed_hold_time(self):
        engine = FakePlankEngine([(170, 5), (170, 5)])
        engine.process_metric(10.0)
        engine.process_metric(13.0)
        engine.deactivate()
        engine.activate()

        self.assertEqual(engine.snapshot().count, 3)
        self.assertEqual(engine.snapshot().phase, "WAIT")


if __name__ == "__main__":
    unittest.main()

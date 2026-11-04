import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ai_fitness_display import (
    DisplaySession,
    NO_KEY,
    discover_desktop_environment,
    resolve_display_enabled,
)


class DisplayModeTests(unittest.TestCase):
    def test_discovers_same_user_wayland_session_for_ssh(self):
        with tempfile.TemporaryDirectory() as directory:
            socket_path = Path(directory) / "wayland-0"
            socket_path.touch()
            with patch("ai_fitness_display.stat.S_ISSOCK", return_value=True):
                detected = discover_desktop_environment(
                    {"XDG_RUNTIME_DIR": directory}
                )
        self.assertEqual(detected["WAYLAND_DISPLAY"], "wayland-0")
        self.assertEqual(detected["XDG_RUNTIME_DIR"], directory)

    def test_auto_without_desktop_is_headless_without_probe(self):
        calls = []
        enabled = resolve_display_enabled({}, lambda env: calls.append(env) or True)
        self.assertFalse(enabled)
        self.assertEqual(calls, [])

    def test_auto_uses_x11_or_wayland_hint_and_probe(self):
        self.assertTrue(
            resolve_display_enabled(
                {"DISPLAY": ":0"}, lambda env: env["DISPLAY"] == ":0"
            )
        )
        self.assertFalse(
            resolve_display_enabled(
                {"WAYLAND_DISPLAY": "wayland-0"}, lambda env: False
            )
        )

    def test_forced_off_never_probes(self):
        calls = []
        enabled = resolve_display_enabled(
            {"FITNESS_DISPLAY": "0", "DISPLAY": ":0"},
            lambda env: calls.append(env) or True,
        )
        self.assertFalse(enabled)
        self.assertEqual(calls, [])

    def test_forced_on_probes_without_desktop_hint_and_falls_back(self):
        calls = []
        enabled = resolve_display_enabled(
            {"FITNESS_DISPLAY": "1"},
            lambda env: calls.append(env) or False,
        )
        self.assertFalse(enabled)
        self.assertEqual(len(calls), 1)

    def test_invalid_mode_falls_back_to_auto(self):
        calls = []
        enabled = resolve_display_enabled(
            {"FITNESS_DISPLAY": "sometimes"},
            lambda env: calls.append(env) or True,
        )
        self.assertFalse(enabled)
        self.assertEqual(calls, [])


class FakeCv2:
    WINDOW_NORMAL = 0

    def __init__(self, fail_on=None):
        self.fail_on = fail_on
        self.calls = []

    def _call(self, name, *args):
        self.calls.append((name, *args))
        if self.fail_on == name:
            raise RuntimeError(f"{name} failed")

    def namedWindow(self, *args):
        self._call("namedWindow", *args)

    def resizeWindow(self, *args):
        self._call("resizeWindow", *args)

    def setMouseCallback(self, *args):
        self._call("setMouseCallback", *args)

    def imshow(self, *args):
        self._call("imshow", *args)

    def waitKey(self, *args):
        self._call("waitKey", *args)
        return ord("q")

    def destroyWindow(self, *args):
        self._call("destroyWindow", *args)


class DisplaySessionTests(unittest.TestCase):
    def test_window_lifecycle_and_key_reading(self):
        cv2 = FakeCv2()
        session = DisplaySession(
            cv2,
            "preview",
            environ={"FITNESS_DISPLAY": "1"},
            probe=lambda env: True,
        )
        self.assertTrue(session.open())
        self.assertEqual(session.show(object()), ord("q"))
        session.close()
        session.close()
        names = [call[0] for call in cv2.calls]
        self.assertEqual(
            names,
            ["namedWindow", "resizeWindow", "imshow", "waitKey", "destroyWindow"],
        )

    def test_mouse_callback_is_only_registered_for_open_window(self):
        cv2 = FakeCv2()
        session = DisplaySession(
            cv2,
            "preview",
            environ={"FITNESS_DISPLAY": "0"},
            probe=lambda env: True,
        )
        self.assertFalse(session.open())
        self.assertFalse(session.set_mouse_callback(lambda *args: None))
        self.assertNotIn("setMouseCallback", [call[0] for call in cv2.calls])

        visible = DisplaySession(
            cv2,
            "preview",
            environ={"FITNESS_DISPLAY": "1"},
            probe=lambda env: True,
        )
        self.assertTrue(visible.open())
        self.assertTrue(visible.set_mouse_callback(lambda *args: None))
        self.assertIn("setMouseCallback", [call[0] for call in cv2.calls])
        visible.close()

    def test_create_failure_downgrades_to_headless(self):
        cv2 = FakeCv2(fail_on="namedWindow")
        session = DisplaySession(
            cv2,
            "preview",
            environ={"FITNESS_DISPLAY": "1"},
            probe=lambda env: True,
        )
        self.assertFalse(session.open())
        self.assertEqual(session.show(object()), NO_KEY)
        session.close()

    def test_runtime_failure_downgrades_and_cleans_up_once(self):
        cv2 = FakeCv2(fail_on="imshow")
        session = DisplaySession(
            cv2,
            "preview",
            environ={"DISPLAY": ":0"},
            probe=lambda env: True,
        )
        self.assertTrue(session.open())
        self.assertEqual(session.show(object()), NO_KEY)
        self.assertFalse(session.enabled)
        self.assertEqual(session.show(object()), NO_KEY)
        self.assertEqual(
            [call[0] for call in cv2.calls].count("destroyWindow"), 1
        )


if __name__ == "__main__":
    unittest.main()

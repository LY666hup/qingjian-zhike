import unittest
from unittest.mock import patch

import ai_fitness_common


class FakeCapture:
    def __init__(self, events=None, opened=False):
        self.events = events if events is not None else []
        self.opened = opened
        self.released = False

    def open(self, *args):
        self.events.append(("capture_open", *args))
        return self.opened

    def isOpened(self):
        return self.opened

    def set(self, *args):
        self.events.append(("set", *args))
        return True

    def release(self):
        self.events.append("release")
        self.released = True


class CameraReconnectTests(unittest.TestCase):
    def test_open_retries_use_capped_exponential_backoff(self):
        captures = [FakeCapture() for _ in range(5)]

        with (
            patch("ai_fitness_common.cv2.VideoCapture", side_effect=captures),
            patch("ai_fitness_common.time.sleep") as sleep,
            patch("ai_fitness_common.CAMERA_MAX_RETRY_DELAY", 12),
        ):
            result = ai_fitness_common.open_camera_with_retries(
                "http://camera/stream",
                retries=5,
                retry_delay=2,
            )

        self.assertIsNone(result)
        self.assertEqual(
            [call.args[0] for call in sleep.call_args_list],
            [2, 4, 8, 12],
        )

    def test_releases_failed_capture_before_opening_replacement(self):
        events = []
        failed_capture = FakeCapture(events)
        replacement = FakeCapture()

        def open_replacement(stream_url):
            events.append(("open", stream_url))
            return replacement

        with patch(
            "ai_fitness_common.open_camera_with_retries",
            side_effect=open_replacement,
        ):
            result = ai_fitness_common.reconnect_camera(
                failed_capture,
                "http://camera/stream",
            )

        self.assertIs(result, replacement)
        self.assertEqual(
            events,
            ["release", ("open", "http://camera/stream")],
        )

    def test_returns_none_after_reconnect_attempts_are_exhausted(self):
        failed_capture = FakeCapture()

        with patch(
            "ai_fitness_common.open_camera_with_retries",
            return_value=None,
        ) as open_camera:
            result = ai_fitness_common.reconnect_camera(failed_capture)

        self.assertIsNone(result)
        self.assertTrue(failed_capture.released)
        open_camera.assert_called_once_with(ai_fitness_common.STREAM_URL)

    def test_accepts_missing_capture_during_cleanup(self):
        replacement = FakeCapture()

        with patch(
            "ai_fitness_common.open_camera_with_retries",
            return_value=replacement,
        ):
            result = ai_fitness_common.reconnect_camera(None)

        self.assertIs(result, replacement)


if __name__ == "__main__":
    unittest.main()

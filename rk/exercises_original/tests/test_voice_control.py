import queue
import threading
import time
import unittest

from fitness_voice_control import (
    FeedbackTransport,
    VoiceController,
    VoiceEvent,
    format_feedback_command,
    parse_voice_line,
    resolve_active_events,
)


class VoiceParserTests(unittest.TestCase):
    def test_parses_supported_commands(self):
        self.assertEqual(parse_voice_line(b"VOICE,START\r\n"), ("START", None))
        self.assertEqual(parse_voice_line("VOICE,RESET\n"), ("RESET", None))
        self.assertEqual(
            parse_voice_line(b"VOICE,ACTION,OVERHEAD_PRESS\n"),
            ("ACTION", "OVERHEAD_PRESS"),
        )
        self.assertEqual(
            parse_voice_line(b"PHONE,ACTION,SQUAT\n"),
            ("ACTION", "SQUAT"),
        )

    def test_ignores_noise_and_malformed_lines(self):
        bad_lines = [
            b"I (123) boot log\n",
            b"VOICE,AI_CHAT\n",
            b"VOICE,ACTION,UNKNOWN\n",
            b"VOICE,ACTION,SQUAT,EXTRA\n",
            b"VOICE,START,EXTRA\n",
            b"PHONE,START\n",
            b"PHONE,STOP\n",
            b"PHONE,RESET\n",
            b"PHONE,ACTION,UNKNOWN\n",
            b"PHONE,ACTION,SQUAT,EXTRA\n",
            b"\xff\xfe\n",
            b"VOICE," + (b"A" * 200),
        ]
        for line in bad_lines:
            with self.subTest(line=line):
                self.assertIsNone(parse_voice_line(line))

    def test_formats_exact_legacy_feedback(self):
        self.assertEqual(
            format_feedback_command("SQUAT", 3, 90, "GOOD"),
            b"CMD,SQUAT,3,90,GOOD\n",
        )

    def test_rejects_invalid_feedback_fields(self):
        for args in [
            ("SQUAT,BAD", 0, 100, "READY"),
            ("SQUAT", -1, 100, "READY"),
            ("SQUAT", 0, 101, "READY"),
            ("SQUAT", 0, 100, "bad"),
            ("深蹲", 0, 100, "READY"),
        ]:
            with self.subTest(args=args):
                with self.assertRaises(ValueError):
                    format_feedback_command(*args)

    def test_resolves_ordered_events(self):
        events = [
            VoiceEvent(1, "ACTION", "CURL"),
            VoiceEvent(2, "STOP"),
        ]
        self.assertEqual(resolve_active_events(events, "SQUAT").kind, "STOP")

        events = [
            VoiceEvent(1, "STOP"),
            VoiceEvent(2, "ACTION", "PLANK"),
        ]
        result = resolve_active_events(events, "SQUAT")
        self.assertEqual((result.kind, result.action), ("SWITCH", "PLANK"))


class FakeSerial:
    def __init__(self, **kwargs):
        self.lines = queue.Queue()
        self.writes = []
        self.closed = False

    def reset_input_buffer(self):
        pass

    def reset_output_buffer(self):
        pass

    def read_until(self, expected=b"\n", size=None):
        return self.readline()

    def readline(self):
        try:
            return self.lines.get(timeout=0.01)
        except queue.Empty:
            return b""

    def write(self, data):
        self.writes.append(data)
        return len(data)

    def flush(self):
        pass

    def close(self):
        self.closed = True


class FeedbackTransportTests(unittest.TestCase):
    def test_one_connection_reads_phone_action_and_writes_feedback(self):
        fake = FakeSerial()
        opened = []

        def factory(**kwargs):
            opened.append(kwargs)
            return fake

        controller = VoiceController()
        transport = FeedbackTransport(
            "/dev/fake",
            115200,
            controller,
            serial_factory=factory,
            reconnect_delay=0.01,
            startup_delay=0,
        )
        transport.start()
        fake.lines.put(b"PHONE,ACTION,SQUAT\n")
        transport.publish_feedback("SQUAT", 1, 100, "GOOD")

        deadline = time.monotonic() + 1
        while time.monotonic() < deadline:
            if fake.writes and controller.drain():
                break
            time.sleep(0.01)
        else:
            self.fail("duplex serial activity did not complete")

        transport.close()
        self.assertEqual(len(opened), 1)
        self.assertEqual(fake.writes[-1], b"CMD,SQUAT,1,100,GOOD\n")
        self.assertTrue(fake.closed)


if __name__ == "__main__":
    unittest.main()

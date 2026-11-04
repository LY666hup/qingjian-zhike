from __future__ import annotations

import logging
import string
import threading
from collections import deque
from dataclasses import dataclass
from typing import Callable, Iterable, Optional

from ai_fitness_heart_rate import (
    HEART_RATE_STATE,
    format_heart_rate_command,
    pending_heart_rate_sample,
)


LOGGER = logging.getLogger(__name__)

VOICE_ACTIONS = frozenset(
    {
        "CURL",
        "LATERAL_RAISE",
        "FRONT_RAISE",
        "OVERHEAD_PRESS",
        "BENT_OVER_ROW",
        "OVERHEAD_TRICEPS",
        "PUSHUP",
        "SQUAT",
        "PLANK",
    }
)

VOICE_EVENT_KINDS = frozenset({"START", "STOP", "RESET", "ACTION"})
MAX_VOICE_LINE_BYTES = 128


@dataclass(frozen=True)
class VoiceEvent:
    sequence: int
    kind: str
    action: Optional[str] = None


@dataclass(frozen=True)
class ActionResult:
    kind: str
    action: Optional[str] = None


CONTINUE = ActionResult("CONTINUE")
RESET = ActionResult("RESET")
STOP = ActionResult("STOP")
QUIT = ActionResult("QUIT")


def parse_voice_line(line: bytes | str, max_bytes: int = MAX_VOICE_LINE_BYTES):
    """Parse one ESP32 control line; non-control serial output is ignored."""
    if isinstance(line, bytes):
        if len(line) > max_bytes:
            return None
        try:
            text = line.decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            return None
    elif isinstance(line, str):
        if len(line.encode("utf-8")) > max_bytes:
            return None
        text = line
    else:
        return None

    parts = text.strip().split(",")
    if len(parts) == 2 and parts[0] in {"VOICE", "PHONE"} and parts[1] in {
        "START",
        "STOP",
        "RESET",
    }:
        return parts[1], None

    if (
        len(parts) == 3
        and parts[0] in {"VOICE", "PHONE"}
        and parts[1] == "ACTION"
        and parts[2] in VOICE_ACTIONS
    ):
        return "ACTION", parts[2]

    return None


def resolve_active_events(events: Iterable[VoiceEvent], active_action: str) -> ActionResult:
    """Resolve a batch while preserving ACTION/STOP arrival order."""
    directive = CONTINUE
    reset_requested = False

    for event in events:
        if event.kind == "ACTION":
            directive = ActionResult("SWITCH", event.action)
            reset_requested = False
        elif event.kind == "STOP":
            directive = STOP
            reset_requested = False
        elif event.kind == "RESET" and directive.kind == "CONTINUE":
            reset_requested = True
        # START is intentionally a no-op while recognition is active.

    if directive.kind != "CONTINUE":
        return directive
    if reset_requested:
        return RESET
    return CONTINUE


def _is_protocol_token(value: str) -> bool:
    return bool(value) and all(character in string.ascii_uppercase + string.digits + "_" for character in value)


def format_feedback_command(mode: str, count: int, score: int, tip: str) -> bytes:
    """Format the deployed five-field feedback protocol after validation."""
    if not _is_protocol_token(mode):
        raise ValueError("invalid feedback mode")
    if not _is_protocol_token(tip):
        raise ValueError("invalid feedback tip")
    if isinstance(count, bool) or not isinstance(count, int) or count < 0:
        raise ValueError("invalid feedback count")
    if isinstance(score, bool) or not isinstance(score, int) or not 0 <= score <= 100:
        raise ValueError("invalid feedback score")
    return f"CMD,{mode},{count},{score},{tip}\n".encode("ascii")


class VoiceController:
    def __init__(self, max_events: int = 64):
        self._events = deque(maxlen=max_events)
        self._condition = threading.Condition()
        self._sequence = 0

    def submit_line(self, line: bytes | str):
        parsed = parse_voice_line(line)
        if parsed is None:
            return None

        kind, action = parsed
        with self._condition:
            self._sequence += 1
            event = VoiceEvent(self._sequence, kind, action)
            self._events.append(event)
            self._condition.notify_all()
            return event

    def drain(self):
        with self._condition:
            events = list(self._events)
            self._events.clear()
            return events

    def wait_and_drain(self, timeout: Optional[float] = None):
        with self._condition:
            if not self._events:
                self._condition.wait(timeout)
            events = list(self._events)
            self._events.clear()
            return events

    def wake(self):
        with self._condition:
            self._condition.notify_all()


class FeedbackTransport:
    """Own one full-duplex serial connection in one background thread."""

    def __init__(
        self,
        port: str,
        baudrate: int,
        controller: VoiceController,
        serial_factory: Optional[Callable[..., object]] = None,
        reconnect_delay: float = 1.0,
        startup_delay: float = 1.2,
        heart_rate_snapshot_provider: Optional[Callable[[], object]] = None,
        auto_forward_heart_rate: bool = True,
    ):
        self.port = port
        self.baudrate = baudrate
        self.controller = controller
        self.serial_factory = serial_factory
        self.reconnect_delay = reconnect_delay
        self.startup_delay = startup_delay
        self.heart_rate_snapshot_provider = (
            HEART_RATE_STATE.snapshot
            if heart_rate_snapshot_provider is None
            else heart_rate_snapshot_provider
        )
        self.auto_forward_heart_rate = bool(auto_forward_heart_rate)
        self._last_auto_hr_timestamp = None
        self._stop_event = threading.Event()
        self._feedback_lock = threading.Lock()
        self._pending_feedback: Optional[bytes] = None
        self._pending_heart_rate: Optional[bytes] = None
        self._thread: Optional[threading.Thread] = None

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._worker,
            name="fitness-feedback-transport",
            daemon=True,
        )
        self._thread.start()

    def publish_feedback(self, mode: str, count: int, score: int, tip: str):
        message = format_feedback_command(mode, count, score, tip)
        with self._feedback_lock:
            self._pending_feedback = message

    def publish_heart_rate(self, bpm: int, valid: bool):
        message = format_heart_rate_command(bpm, valid)
        with self._feedback_lock:
            self._pending_heart_rate = message

    def close(self, timeout: float = 2.0):
        self._stop_event.set()
        self.controller.wake()
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(timeout)

    def _take_feedback(self):
        with self._feedback_lock:
            message = self._pending_feedback
            self._pending_feedback = None
            return message

    def _take_heart_rate(self):
        with self._feedback_lock:
            message = self._pending_heart_rate
            self._pending_heart_rate = None
            return message

    def _queue_latest_sensor_heart_rate(self):
        """Queue a newly received wristband HR sample independently of video loops.

        FeedbackTransport lives for the whole supervisor lifetime, including idle
        state.  Polling the thread-safe HR state here prevents a blocking camera
        read or slow MediaPipe inference from starving ESP32 heart-rate updates.
        """
        if not self.auto_forward_heart_rate:
            return
        try:
            snapshot = self.heart_rate_snapshot_provider()
            sample = pending_heart_rate_sample(
                snapshot, self._last_auto_hr_timestamp
            )
            if sample is None:
                return
            message = format_heart_rate_command(sample.bpm, sample.valid)
        except Exception as exc:
            LOGGER.warning("自动心率转发准备失败：%s", exc)
            return

        with self._feedback_lock:
            # Latest-wins semantics are intentional for real-time telemetry.
            self._pending_heart_rate = message
        self._last_auto_hr_timestamp = sample.timestamp

    def _take_pending_messages(self):
        """Drain one latest CMD followed by one latest HR message."""
        with self._feedback_lock:
            messages = [
                message
                for message in (self._pending_feedback, self._pending_heart_rate)
                if message is not None
            ]
            self._pending_feedback = None
            self._pending_heart_rate = None
            return messages

    def _restore_pending_messages(self, messages):
        with self._feedback_lock:
            for message in messages:
                if message.startswith(b"CMD,"):
                    if self._pending_feedback is None:
                        self._pending_feedback = message
                elif message.startswith(b"HR,"):
                    if self._pending_heart_rate is None:
                        self._pending_heart_rate = message

    def _restore_feedback(self, message: bytes):
        with self._feedback_lock:
            if self._pending_feedback is None:
                self._pending_feedback = message

    def _open_serial(self):
        factory = self.serial_factory
        if factory is None:
            import serial

            factory = serial.Serial
        return factory(
            port=self.port,
            baudrate=self.baudrate,
            timeout=0.1,
            write_timeout=1,
            rtscts=False,
            dsrdtr=False,
        )

    def _readline(self, connection):
        reader = getattr(connection, "read_until", None)
        if reader is not None:
            return reader(b"\n", MAX_VOICE_LINE_BYTES + 1)
        return connection.readline()

    def _worker(self):
        while not self._stop_event.is_set():
            connection = None
            discarding_overlong_line = False
            try:
                LOGGER.info("正在连接语音反馈串口：%s", self.port)
                connection = self._open_serial()
                LOGGER.info("语音反馈串口连接成功：%s", self.port)
                # 打开 USB 串口可能让 ESP32 自动复位，等待固件重新进入命令循环。
                if self._stop_event.wait(self.startup_delay):
                    break
                try:
                    connection.reset_input_buffer()
                    connection.reset_output_buffer()
                except (AttributeError, OSError):
                    pass

                while not self._stop_event.is_set():
                    self._queue_latest_sensor_heart_rate()
                    messages = self._take_pending_messages()
                    for index, message in enumerate(messages):
                        try:
                            connection.write(message)
                        except Exception:
                            self._restore_pending_messages(messages[index:])
                            raise
                        try:
                            connection.flush()
                        except Exception:
                            self._restore_pending_messages(messages[index + 1:])
                            raise

                    line = self._readline(connection)
                    if line:
                        if discarding_overlong_line:
                            if line.endswith(b"\n"):
                                discarding_overlong_line = False
                            continue
                        if len(line) > MAX_VOICE_LINE_BYTES:
                            discarding_overlong_line = not line.endswith(b"\n")
                            continue
                        event = self.controller.submit_line(line)
                        if event:
                            LOGGER.info(
                                "收到控制命令：%s%s",
                                event.kind,
                                f",{event.action}" if event.action else "",
                            )
            except Exception as exc:
                if not self._stop_event.is_set():
                    LOGGER.warning("语音反馈串口异常，准备重连：%s", exc)
                    self._stop_event.wait(self.reconnect_delay)
            finally:
                if connection is not None:
                    try:
                        connection.close()
                    except Exception:
                        pass

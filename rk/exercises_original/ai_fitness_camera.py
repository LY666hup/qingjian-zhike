"""Low-latency camera reader for live pose recognition.

The worker continuously consumes the network stream and keeps only the newest
successfully decoded frame.  There is deliberately no frame queue: when pose
inference is slower than the camera, intermediate frames are overwritten rather
than replayed later as a burst.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from typing import Callable, Optional


LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class LatestCameraFrame:
    sequence: int
    generation: int
    # This is the time the Pi finished reading/decoding the frame, not the
    # camera-side exposure timestamp.  Keep the name explicit to avoid treating
    # it as a true end-to-end latency measurement.
    received_at: float
    read_latency_ms: float
    frame: object


class LatestFrameCamera:
    """Own ``VideoCapture`` on one worker thread and expose only its latest frame.

    ``reconnect_camera`` is called after the worker has released the failed
    capture and should return a newly opened capture or ``None``.  Once
    ``start()`` is called, the worker owns all ``read/release/reconnect``
    operations.  The main thread only signals
    shutdown and waits for the worker, avoiding concurrent access to OpenCV's
    ``VideoCapture`` object.
    """

    def __init__(
        self,
        capture,
        reconnect_camera: Callable,
        *,
        clock: Callable[[], float] = time.monotonic,
        reconnect_delay: float = 0.25,
        slow_read_warn_ms: float = 250.0,
        name: str = "fitness-camera-reader",
    ):
        self._initial_capture = capture
        self._reconnect_camera = reconnect_camera
        self._clock = clock
        self._reconnect_delay = max(0.0, float(reconnect_delay))
        self._slow_read_warn_ms = max(0.0, float(slow_read_warn_ms))
        self._name = name

        self._condition = threading.Condition()
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._latest: LatestCameraFrame | None = None
        self._sequence = 0
        self._generation = 1 if capture is not None else 0

    def start(self):
        if self._thread is not None:
            if self._thread.is_alive():
                return self
            raise RuntimeError("LatestFrameCamera 是单次生命周期对象，关闭后请新建实例")

        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._worker,
            name=self._name,
            daemon=True,
        )
        self._thread.start()
        return self

    def wait_for_newer(
        self,
        last_sequence: int,
        timeout: float = 0.15,
    ) -> LatestCameraFrame | None:
        """Return the newest frame only when it is newer than ``last_sequence``."""
        deadline = self._clock() + max(0.0, float(timeout))
        with self._condition:
            while not self._stop_event.is_set():
                latest = self._latest
                if latest is not None and latest.sequence > last_sequence:
                    return latest

                remaining = deadline - self._clock()
                if remaining <= 0:
                    return None
                self._condition.wait(remaining)
        return None

    def close(self, timeout: float = 4.0) -> bool:
        """Request shutdown and wait for the owner thread to release the capture.

        The main thread intentionally does *not* call ``VideoCapture.release``.
        A read/reconnect attempt is bounded by the OpenCV timeouts configured by
        the caller, so the worker gets a chance to observe ``_stop_event`` and
        perform its own release in ``finally``.
        """
        self._stop_event.set()
        with self._condition:
            self._condition.notify_all()

        thread = self._thread
        if thread is None:
            capture = self._initial_capture
            self._initial_capture = None
            if capture is not None:
                try:
                    capture.release()
                except Exception:
                    pass
            return True

        if thread is not threading.current_thread():
            thread.join(max(0.0, float(timeout)))

        closed = not thread.is_alive()
        if not closed:
            LOGGER.error(
                "摄像头读取线程未在 %.1f 秒内退出；可能是 OpenCV 后端未遵守超时参数",
                timeout,
            )
        return closed

    def _worker(self):
        capture = self._initial_capture
        self._initial_capture = None
        generation = self._generation

        try:
            while not self._stop_event.is_set():
                if capture is None:
                    capture = self._try_reconnect(None)
                    if self._stop_event.is_set():
                        break
                    if capture is None:
                        self._stop_event.wait(self._reconnect_delay)
                        continue
                    generation += 1
                    self._generation = generation

                read_started = self._clock()
                try:
                    ok, frame = capture.read()
                except Exception as exc:
                    LOGGER.warning("摄像头读取异常，准备重连：%s", exc)
                    ok, frame = False, None
                read_finished = self._clock()
                read_latency_ms = max(0.0, (read_finished - read_started) * 1000.0)

                if self._stop_event.is_set():
                    break

                if (
                    self._slow_read_warn_ms > 0
                    and read_latency_ms >= self._slow_read_warn_ms
                ):
                    LOGGER.warning(
                        "摄像头单帧读取耗时 %.1f ms（网络流可能发生阻塞）",
                        read_latency_ms,
                    )

                if not ok or frame is None:
                    LOGGER.warning("摄像头视频流无有效帧，后台重新连接")
                    capture = self._try_reconnect(capture)
                    if self._stop_event.is_set():
                        break
                    if capture is not None:
                        generation += 1
                        self._generation = generation
                    else:
                        self._stop_event.wait(self._reconnect_delay)
                    continue

                with self._condition:
                    self._sequence += 1
                    self._latest = LatestCameraFrame(
                        sequence=self._sequence,
                        generation=generation,
                        received_at=read_finished,
                        read_latency_ms=read_latency_ms,
                        frame=frame,
                    )
                    # Single overwrite slot: there is no application-level backlog.
                    self._condition.notify_all()
        finally:
            if capture is not None:
                try:
                    capture.release()
                except Exception:
                    pass
            with self._condition:
                self._condition.notify_all()

    def _try_reconnect(self, capture):
        # This method executes only on the worker.  Release the failed capture
        # here before opening a replacement so ownership is unambiguous and a
        # reconnect callback cannot accidentally leak the previous object.
        if capture is not None:
            try:
                capture.release()
            except Exception:
                pass

        try:
            return self._reconnect_camera(None)
        except Exception as exc:
            LOGGER.warning("摄像头重连失败：%s", exc)
            return None

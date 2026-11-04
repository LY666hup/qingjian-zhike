from __future__ import annotations

import logging
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Callable, Optional

import mediapipe as mp
import numpy as np

import ai_fitness_common as common
from ai_fitness_camera import LatestFrameCamera
from ai_fitness_action_registry import (
    ACTION_BUTTONS,
    ACTION_ORDER,
    get_action_spec,
    load_action_engine,
)
from ai_fitness_display import DisplaySession
from fitness_voice_control import ActionResult, CONTINUE, QUIT, STOP
from pose_metrics.comparison import first_world_landmarks


LOGGER = logging.getLogger(__name__)
WINDOW_NAME = "AI Fitness Coach - All In One"
WINDOW_WIDTH = 1280
WINDOW_HEIGHT = 720
VIDEO_WIDTH = 940
PANEL_X = VIDEO_WIDTH


def open_initial_camera_once():
    """Make one bounded foreground camera attempt before the reader starts.

    A failed attempt is not fatal: ``LatestFrameCamera`` starts with ``None``
    and keeps reconnecting in its worker.  Keeping this first attempt short
    prevents VOICE/PHONE START from being blocked by the legacy 8-second open
    timeout when the ESP32 camera is temporarily unreachable.
    """
    return common.open_camera_with_retries(
        retries=1,
        retry_delay=0,
        open_timeout_msec=common.CAMERA_INITIAL_OPEN_TIMEOUT_MSEC,
        read_timeout_msec=common.CAMERA_READ_TIMEOUT_MSEC,
    )


def reconnect_camera_once(capture):
    # LatestFrameCamera 会在工作线程内先释放旧 capture；这里仅负责用较短
    # open timeout 建立替代连接。保留 capture 参数是为了兼容既有工厂接口。
    return common.open_camera_with_retries(
        retries=1,
        retry_delay=0,
        open_timeout_msec=common.CAMERA_RECONNECT_OPEN_TIMEOUT_MSEC,
        read_timeout_msec=common.CAMERA_READ_TIMEOUT_MSEC,
    )


@dataclass
class SessionResources:
    open_camera: Callable = open_initial_camera_once
    reconnect_camera: Callable = reconnect_camera_once
    create_landmarker: Callable = common.create_landmarker
    open_imu: Callable = common.open_imu_serial
    read_imu: Callable = common.read_imu
    stop_imu: Callable = common.stop_imu_reader
    manage_imu_lifecycle: bool = True
    open_serial: Callable = common.open_serial
    clock: Callable[[], float] = time.monotonic
    display_factory: Optional[Callable[[], object]] = None
    camera_stream_factory: Optional[Callable[..., object]] = None
    cv2_module: object = common.cv2


class FitnessSession:
    """Own shared recognition resources while actions switch in place."""

    def __init__(
        self,
        runtime=None,
        engine_loader: Callable = load_action_engine,
        resources: Optional[SessionResources] = None,
    ):
        self.runtime = runtime
        self.engine_loader = engine_loader
        self.resources = resources or SessionResources()
        self.engines = {}
        self.specs = {}
        self.active_action = None
        self.active_engine = None
        self._pending_commands = deque()
        self._button_regions = []
        self._last_draw_landmarks = None
        self._last_result = None
        self._frame_counter = 0
        self._last_timestamp_ms = -1
        self._last_imu = {
            "ax": 0,
            "ay": 0,
            "az": 0,
            "gx": 0,
            "gy": 0,
            "gz": 0,
            "pitch": 0,
            "gyro_mag": 0,
        }
        self._serial = None
        self._last_forwarded_hr_timestamp = None
        self._last_camera_sequence = 0
        self._last_camera_generation = None

    def activate(self, voice_action: str):
        if get_action_spec(voice_action) is None:
            raise ValueError(f"unsupported voice action: {voice_action}")

        if self.active_engine is not None:
            self.active_engine.deactivate()

        if voice_action not in self.engines:
            spec, engine = self.engine_loader(voice_action)
            self.specs[voice_action] = spec
            self.engines[voice_action] = engine

        self.active_action = voice_action
        self.active_engine = self.engines[voice_action]
        self.active_engine.activate()
        self._reset_inference_cache()
        self._publish_feedback(force=True)
        LOGGER.info(
            "已切换动作：%s (%s)",
            self.specs[voice_action].mode,
            voice_action,
        )
        return self.active_engine.snapshot()

    def reset_current(self):
        if self.active_engine is None:
            return None
        snapshot = self.active_engine.reset()
        self._reset_inference_cache()
        self._publish_feedback(force=True)
        return snapshot

    def reset_all(self):
        for engine in self.engines.values():
            engine.reset()
        self._reset_inference_cache()
        self._publish_feedback(force=True)
        return self.active_engine.snapshot() if self.active_engine else None

    def handle_command(self, command: ActionResult):
        if command.kind == "CONTINUE":
            return CONTINUE
        if command.kind == "SWITCH" and command.action:
            if get_action_spec(command.action) is None:
                LOGGER.warning("忽略未知动作切换：%s", command.action)
                return CONTINUE
            self.activate(command.action)
            common.speak_text(
                f"已切换到{common.get_action_name_cn(self.active_engine.mode)}"
            )
            return CONTINUE
        if command.kind == "RESET":
            self.reset_current()
            return CONTINUE
        if command.kind == "RESET_ALL":
            self.reset_all()
            return CONTINUE
        if command.kind == "AI_SUMMARY":
            self._start_ai_summary()
            return CONTINUE
        if command.kind in {"STOP", "QUIT"}:
            return command
        return CONTINUE

    def run(self, initial_action: str = "SQUAT"):
        display = self._make_display()
        camera_stream = None
        imu_handle = None
        result = STOP

        try:
            # Validate/create MediaPipe before touching serial or camera hardware.
            with self.resources.create_landmarker() as landmarker:
                common.init_ai_client()
                self._serial = (
                    None
                    if self.runtime is not None
                    else self.resources.open_serial()
                )
                imu_handle = self.resources.open_imu()
                capture = self.resources.open_camera()
                camera_stream = self._make_camera_stream(capture)
                camera_stream.start()
                display.open()
                display.set_mouse_callback(self._mouse_callback)
                self.activate(initial_action)

                while True:
                    command = self._poll_command()
                    handled = self.handle_command(command)
                    if handled.kind in {"STOP", "QUIT"}:
                        result = handled
                        break

                    self._last_imu = self.resources.read_imu(
                        imu_handle, self._last_imu
                    )

                    # Production VoiceActionRuntime uses FeedbackTransport's
                    # independent HR forwarding thread.  Custom/legacy runtimes
                    # retain the original per-loop fallback for compatibility.
                    transport = getattr(self.runtime, "transport", None)
                    hr_is_background_managed = bool(
                        getattr(transport, "auto_forward_heart_rate", False)
                    )
                    if not hr_is_background_managed:
                        self._last_forwarded_hr_timestamp = (
                            common.forward_pending_heart_rate(
                                self.runtime,
                                self._serial,
                                self._last_forwarded_hr_timestamp,
                            )
                        )

                    packet = camera_stream.wait_for_newer(
                        self._last_camera_sequence,
                        timeout=common.CAMERA_FRAME_WAIT_SEC,
                    )
                    if packet is None:
                        continue

                    self._last_camera_sequence = packet.sequence
                    if self._last_camera_generation is None:
                        self._last_camera_generation = packet.generation
                    elif packet.generation != self._last_camera_generation:
                        self._last_camera_generation = packet.generation
                        self.active_engine.recover_after_camera_reconnect()
                        self._reset_inference_cache()
                        self._publish_feedback(force=True)
                        LOGGER.info("摄像头重连完成，姿态状态已安全恢复")

                    # LatestFrameCamera 本身已经是单槽覆盖模型：主线程每次
                    # 只会拿到 reader 当前最新的一帧，不再额外按“帧年龄”丢帧。
                    # reader 的接收时间并不是 ESP32 摄像头真正的采集时间，
                    # 因此用它做端到端 stale 判定反而可能造成不必要的漏动作。

                    # Keep inference on the normalized source frame.  The old
                    # saturation boost is aesthetic only and is deferred to the
                    # preview path so headless recognition avoids two HSV copies.
                    frame = common.normalize_frame_channels(packet.frame)
                    frame = self.resources.cv2_module.flip(frame, 1)

                    process_started = self.resources.clock()
                    self._process_frame(landmarker, frame)
                    process_ms = (
                        self.resources.clock() - process_started
                    ) * 1000.0
                    if (
                        common.POSE_SLOW_WARN_MSEC > 0
                        and process_ms >= common.POSE_SLOW_WARN_MSEC
                    ):
                        LOGGER.warning(
                            "姿态处理耗时 %.1f ms（read=%.1f ms）",
                            process_ms,
                            packet.read_latency_ms,
                        )

                    # In headless mode drawing a 1280x720 UI is pure CPU waste.
                    if getattr(display, "enabled", True):
                        display_frame = common.enhance_frame_for_display(frame)
                        if self._last_draw_landmarks is not None:
                            common.draw_pose(
                                display_frame, self._last_draw_landmarks
                            )
                        key = display.show(self._draw_interface(display_frame))
                        self._queue_key_command(key)
        except KeyboardInterrupt:
            LOGGER.info("收到终端中断，正在关闭统一动作会话")
            result = QUIT if self.runtime is not None else STOP
        finally:
            if camera_stream is not None:
                try:
                    closed = camera_stream.close(
                        timeout=common.CAMERA_CLOSE_TIMEOUT_SEC
                    )
                except TypeError:
                    # 兼容测试/自定义 camera stream 的旧 close() 接口。
                    closed = camera_stream.close()
                if closed is False:
                    LOGGER.error(
                        "摄像头线程未能在关闭窗口内退出；请检查 OpenCV 后端超时支持"
                    )
            display.close()
            if imu_handle and self.resources.manage_imu_lifecycle:
                self.resources.stop_imu()
            if self._serial is not None:
                try:
                    self._serial.close()
                except Exception:
                    pass
            self._serial = None

        return result

    def _make_camera_stream(self, capture):
        if self.resources.camera_stream_factory is not None:
            return self.resources.camera_stream_factory(
                capture, self.resources.reconnect_camera, self.resources.clock
            )
        return LatestFrameCamera(
            capture,
            self.resources.reconnect_camera,
            clock=self.resources.clock,
            reconnect_delay=common.CAMERA_RECONNECT_IDLE_SEC,
            slow_read_warn_ms=common.CAMERA_SLOW_READ_WARN_MSEC,
        )

    def _process_frame(self, landmarker, frame):
        self._frame_counter += 1
        fresh = (
            self._frame_counter % common.PROCESS_EVERY_N_FRAMES == 0
            or self._last_result is None
        )
        if not fresh:
            return self.active_engine.snapshot()

        cv2_module = self.resources.cv2_module
        rgb = cv2_module.cvtColor(frame, cv2_module.COLOR_BGR2RGB)
        mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
        timestamp_ms = max(
            self._last_timestamp_ms + 1,
            int(self.resources.clock() * 1000),
        )
        self._last_timestamp_ms = timestamp_ms
        self._last_result = landmarker.detect_for_video(mp_image, timestamp_ms)
        now = self.resources.clock()

        if self._last_result.pose_landmarks:
            image_landmarks = self._last_result.pose_landmarks[0]
            if hasattr(self.active_engine, "metric_comparison"):
                world_landmarks = (
                    first_world_landmarks(self._last_result)
                    if self.active_engine.metric_comparison is not None
                    else None
                )
                update = self.active_engine.process_fresh(
                    image_landmarks,
                    self._last_imu,
                    now,
                    world_landmarks=world_landmarks,
                    timestamp_ms=timestamp_ms,
                )
            else:
                update = self.active_engine.process_fresh(
                    image_landmarks, self._last_imu, now
                )
            self._last_draw_landmarks = update.landmarks
        else:
            update = self.active_engine.no_person_fresh()
            self._last_draw_landmarks = None

        self._handle_action_update(update)
        return update.snapshot

    def _poll_command(self):
        if self.runtime is not None:
            command = self.runtime.poll_control(self.active_action)
            if command.kind != "CONTINUE":
                return command
        if self._pending_commands:
            return self._pending_commands.popleft()
        return CONTINUE

    def _queue_key_command(self, key):
        if key == ord("q"):
            self._pending_commands.append(
                QUIT if self.runtime is not None else STOP
            )
        elif key == ord("r"):
            self._pending_commands.append(ActionResult("RESET"))
        elif key == ord("a"):
            self._pending_commands.append(ActionResult("AI_SUMMARY"))

    def _handle_action_update(self, update):
        if update.completed:
            common.speak_count_tip(update.snapshot.count, update.snapshot.tip)
        self._publish_feedback()

    def _publish_feedback(self, force=False):
        if self.active_engine is None:
            return
        snapshot = self.active_engine.snapshot()
        if not force and (
            snapshot.count == self.active_engine.last_sent_count
            and snapshot.tip == self.active_engine.last_sent_tip
        ):
            return

        common.publish_action_feedback(
            self.runtime,
            self._serial,
            snapshot.mode,
            snapshot.count,
            snapshot.score,
            snapshot.tip,
        )
        self.active_engine.last_sent_count = snapshot.count
        self.active_engine.last_sent_tip = snapshot.tip

    def _start_ai_summary(self):
        if self.active_engine is None:
            return
        snapshot = self.active_engine.snapshot()
        threading.Thread(
            target=common.generate_and_speak_ai_summary,
            args=(
                snapshot.mode,
                snapshot.count,
                snapshot.score,
                dict(snapshot.stats),
            ),
            daemon=True,
        ).start()

    def _make_display(self):
        if self.resources.display_factory is not None:
            return self.resources.display_factory()
        return DisplaySession(
            self.resources.cv2_module,
            WINDOW_NAME,
            WINDOW_WIDTH,
            WINDOW_HEIGHT,
        )

    def _mouse_callback(self, event, x, y, flags, userdata):
        del flags, userdata
        if event != self.resources.cv2_module.EVENT_LBUTTONDOWN:
            return
        for x1, y1, x2, y2, command in self._button_regions:
            if x1 <= x <= x2 and y1 <= y <= y2:
                self._pending_commands.append(command)
                return

    def _draw_interface(self, frame):
        cv2_module = self.resources.cv2_module
        video = cv2_module.resize(frame, (VIDEO_WIDTH, WINDOW_HEIGHT))
        canvas = np.zeros((WINDOW_HEIGHT, WINDOW_WIDTH, 3), dtype=np.uint8)
        canvas[:, :VIDEO_WIDTH] = video
        canvas[:, PANEL_X:] = (32, 32, 32)

        snapshot = self.active_engine.snapshot()
        overlay = canvas.copy()
        cv2_module.rectangle(overlay, (15, 15), (410, 178), (0, 0, 0), -1)
        canvas = cv2_module.addWeighted(overlay, 0.58, canvas, 0.42, 0)
        lines = [
            f"Mode: {snapshot.mode}",
            f"Count/Time: {snapshot.count}",
            f"Score: {snapshot.score}",
            f"Tip: {snapshot.tip}",
            f"{snapshot.metric_name}: {snapshot.metric_value:.2f}",
            f"State: {snapshot.phase}",
        ]
        for index, line in enumerate(lines):
            cv2_module.putText(
                canvas,
                line,
                (30, 43 + index * 24),
                cv2_module.FONT_HERSHEY_SIMPLEX,
                0.62,
                (255, 255, 255),
                2,
                cv2_module.LINE_AA,
            )

        cv2_module.putText(
            canvas,
            snapshot.person_text,
            (25, WINDOW_HEIGHT - 55),
            cv2_module.FONT_HERSHEY_SIMPLEX,
            0.62,
            (255, 255, 255),
            2,
        )
        cv2_module.putText(
            canvas,
            f"IMU gyro: {self._last_imu['gyro_mag']:.2f}",
            (25, WINDOW_HEIGHT - 30),
            cv2_module.FONT_HERSHEY_SIMPLEX,
            0.58,
            (255, 255, 255),
            2,
        )
        if snapshot.aux_text:
            cv2_module.putText(
                canvas,
                snapshot.aux_text[:90],
                (420, WINDOW_HEIGHT - 30),
                cv2_module.FONT_HERSHEY_SIMPLEX,
                0.48,
                (255, 255, 255),
                1,
            )

        self._draw_control_panel(canvas)
        return canvas

    def _draw_control_panel(self, canvas):
        cv2_module = self.resources.cv2_module
        cv2_module.putText(
            canvas,
            "CLICK TO SWITCH",
            (PANEL_X + 27, 35),
            cv2_module.FONT_HERSHEY_SIMPLEX,
            0.70,
            (245, 245, 245),
            2,
        )
        self._button_regions = []
        for index, action in enumerate(ACTION_ORDER):
            row, column = divmod(index, 2)
            x1 = PANEL_X + 15 + column * 160
            y1 = 75 + row * 58
            x2 = x1 + 150
            y2 = y1 + 48
            self._draw_button(
                canvas,
                x1,
                y1,
                x2,
                y2,
                ACTION_BUTTONS[action],
                action == self.active_action,
            )
            self._button_regions.append(
                (x1, y1, x2, y2, ActionResult("SWITCH", action))
            )

        controls = (
            ("Reset current", ActionResult("RESET")),
            ("Reset all", ActionResult("RESET_ALL")),
            ("AI summary (A)", ActionResult("AI_SUMMARY")),
            ("Quit (Q)", QUIT if self.runtime is not None else STOP),
        )
        for index, (label, command) in enumerate(controls):
            x1, x2 = PANEL_X + 15, WINDOW_WIDTH - 15
            y1 = 400 + index * 58
            y2 = y1 + 46
            self._draw_button(canvas, x1, y1, x2, y2, label, False)
            self._button_regions.append((x1, y1, x2, y2, command))

    def _draw_button(self, canvas, x1, y1, x2, y2, label, active):
        cv2_module = self.resources.cv2_module
        fill = (210, 210, 210) if active else (75, 75, 75)
        text_color = (25, 25, 25) if active else (245, 245, 245)
        cv2_module.rectangle(canvas, (x1, y1), (x2, y2), fill, -1)
        cv2_module.rectangle(
            canvas, (x1, y1), (x2, y2), (180, 180, 180), 1
        )
        text_size, _ = cv2_module.getTextSize(
            label, cv2_module.FONT_HERSHEY_SIMPLEX, 0.55, 1
        )
        text_x = x1 + max(5, (x2 - x1 - text_size[0]) // 2)
        text_y = y1 + (y2 - y1 + text_size[1]) // 2
        cv2_module.putText(
            canvas,
            label,
            (text_x, text_y),
            cv2_module.FONT_HERSHEY_SIMPLEX,
            0.55,
            text_color,
            1,
            cv2_module.LINE_AA,
        )

    def _reset_inference_cache(self):
        self._last_result = None
        self._last_draw_landmarks = None
        self._frame_counter = 0

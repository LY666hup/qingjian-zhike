import cv2
import time
import math
import threading
from types import MappingProxyType

import mediapipe as mp

from ai_fitness_action_engine import ActionSnapshot, ActionUpdate, LandmarkSmoother

from ai_fitness_common import (
    PROCESS_EVERY_N_FRAMES,
    angle_3points,
    get_xy,
    draw_pose,
    draw_info_panel,
    create_landmarker,
    fix_frame_color,
    smooth_pose_landmarks,
    reset_pose_smoothing,
    open_camera_with_retries,
    reconnect_camera,
    open_serial,
    open_imu_serial,
    read_imu,
    stop_imu_reader,
    forward_pending_heart_rate,
    publish_action_feedback,
    poll_action_control,
    init_ai_client,
    speak_text,
    generate_and_speak_ai_summary,
)
from fitness_voice_control import QUIT, STOP
from ai_fitness_display import DisplaySession


GOOD_ANGLE_THRESHOLD = 160.0
GOOD_LINE_ERROR_THRESHOLD = 10.0


class PlankActionEngine:
    """Persistent straight-plank state kept separate from repetition actions."""

    mode = "PLANK"
    metric_name = "BodyAngle"

    def __init__(self):
        self.smoother = LandmarkSmoother(0.68)
        self.reset()

    def reset(self):
        self.count = 0
        self.score = 100
        self.stats = {
            "GOOD": 0,
            "TOO_FAST": 0,
            "RANGE_SMALL": 0,
            "SWING": 0,
            "BAD_FORM": 0,
        }
        self.last_sent_count = -1
        self.last_sent_tip = ""
        self.reset_transient()
        return self.snapshot()

    def reset_transient(self):
        self.tip = "READY"
        self.phase = "WAIT"
        self.hold_started_at = None
        self.hold_base_count = self.count
        self.last_good_second = self.count
        self.metric_value = 0.0
        self.line_error_percent = 999.0
        self.aux_text = ""
        self.person_text = "No person"
        self.smoother.reset()
        return self.snapshot()

    def activate(self):
        return self.reset_transient()

    def deactivate(self):
        return self.reset_transient()

    def recover_after_camera_reconnect(self):
        return self.reset_transient()

    def process_fresh(self, landmarks, imu_data, now):
        del imu_data
        smoothed = self.smoother.update(landmarks)
        body_angle, line_error_percent, aux_text = plank_metric(smoothed)
        self.metric_value = body_angle
        self.line_error_percent = line_error_percent
        self.aux_text = aux_text
        self.person_text = "Person detected"

        previous_tip = self.tip
        is_good = (
            body_angle >= GOOD_ANGLE_THRESHOLD
            and line_error_percent <= GOOD_LINE_ERROR_THRESHOLD
        )
        if is_good:
            self.tip = "GOOD"
            self.phase = "HOLD"
            if self.hold_started_at is None:
                self.hold_started_at = now
                self.hold_base_count = self.count
            self.count = self.hold_base_count + int(now - self.hold_started_at)
            if self.count > self.last_good_second:
                self.stats["GOOD"] += self.count - self.last_good_second
                self.last_good_second = self.count
        else:
            self.tip = "BAD_FORM"
            self.phase = "ADJUST"
            self._pause_hold()
            if previous_tip != "BAD_FORM":
                self.stats["BAD_FORM"] += 1

        return ActionUpdate(self.snapshot(), smoothed)

    def no_person_fresh(self):
        self.tip = "NO_PERSON"
        self.phase = "WAIT"
        self.metric_value = 0.0
        self.line_error_percent = 999.0
        self.aux_text = ""
        self.person_text = "No person"
        self._pause_hold()
        self.smoother.reset()
        return ActionUpdate(self.snapshot())

    def snapshot(self):
        return ActionSnapshot(
            mode=self.mode,
            count=self.count,
            score=self.score,
            tip=self.tip,
            phase=self.phase,
            metric_name=self.metric_name,
            metric_value=self.metric_value,
            aux_text=self.aux_text,
            person_text=self.person_text,
            stats=MappingProxyType(dict(self.stats)),
        )

    def _pause_hold(self):
        self.hold_started_at = None
        self.hold_base_count = self.count
        self.last_good_second = self.count


def point_line_distance(p, a, b):
    """
    计算点 p 到直线 ab 的距离，并用 ab 长度归一化。
    用来判断“肩-髋-脚踝”是否接近一条直线。
    """
    ax, ay = float(a[0]), float(a[1])
    bx, by = float(b[0]), float(b[1])
    px, py = float(p[0]), float(p[1])

    abx = bx - ax
    aby = by - ay
    apx = px - ax
    apy = py - ay

    ab_len = math.hypot(abx, aby) + 1e-6

    # 二维叉乘面积 / 底边长度 = 点到直线距离
    cross = abs(abx * apy - aby * apx)
    return cross / ab_len


def plank_metric(landmarks):
    # 平板支撑建议侧身拍摄。
    # 判断核心：肩膀-髋部-脚踝接近一条直线。

    left_shoulder = get_xy(landmarks, 11)
    left_hip = get_xy(landmarks, 23)
    left_ankle = get_xy(landmarks, 27)

    right_shoulder = get_xy(landmarks, 12)
    right_hip = get_xy(landmarks, 24)
    right_ankle = get_xy(landmarks, 28)

    left_angle = angle_3points(left_shoulder, left_hip, left_ankle)
    right_angle = angle_3points(right_shoulder, right_hip, right_ankle)

    left_line_error = point_line_distance(left_hip, left_shoulder, left_ankle)
    right_line_error = point_line_distance(right_hip, right_shoulder, right_ankle)

    # 哪一侧更接近直线，就用哪一侧；侧身拍摄时有一侧会更稳定。
    if left_angle >= right_angle:
        body_angle = left_angle
        line_error = left_line_error
    else:
        body_angle = right_angle
        line_error = right_line_error

    # 直线偏差换成百分数，方便观察
    line_error_percent = line_error * 100.0

    aux = (
        f"Angle L:{left_angle:.1f} R:{right_angle:.1f} "
        f"LineErr:{line_error_percent:.1f}"
    )

    return body_angle, line_error_percent, aux


def run_plank_straight_action(runtime=None):
    mode = "PLANK"
    window_name = "AI Fitness Coach - Plank Straight"

    # 这里是平板支撑标准阈值：
    # 角度越接近180，身体越直；直线偏差越小越好。
    # 阈值由模块常量与统一会话共用，保留现有校准值。

    count_seconds = 0
    score = 100
    tip = "READY"
    state = "WAIT"

    action_stats = {
        "GOOD": 0,
        "TOO_FAST": 0,
        "RANGE_SMALL": 0,
        "SWING": 0,
        "BAD_FORM": 0
    }

    good_start_time = None
    last_good_second = -1

    last_sent_count = -1
    last_sent_tip = ""
    last_forwarded_hr_timestamp = None

    frame_counter = 0
    last_result = None
    last_landmarks = None
    last_body_angle = 0.0
    last_aux_text = ""
    last_person_text = "No person"

    last_imu = {
        "ax": 0, "ay": 0, "az": 0,
        "gx": 0, "gy": 0, "gz": 0,
        "pitch": 0, "gyro_mag": 0
    }

    def reset_action_state():
        nonlocal count_seconds, score, tip, state
        nonlocal good_start_time, last_good_second
        nonlocal last_sent_count, last_sent_tip, last_result, frame_counter
        nonlocal last_landmarks, last_body_angle, last_aux_text, last_person_text

        count_seconds = 0
        score = 100
        tip = "READY"
        state = "WAIT"
        good_start_time = None
        last_good_second = -1
        last_result = None
        last_landmarks = None
        last_body_angle = 0.0
        last_aux_text = ""
        last_person_text = "No person"
        frame_counter = 0
        reset_pose_smoothing()
        for key in action_stats:
            action_stats[key] = 0
        publish_action_feedback(runtime, ser, mode, count_seconds, score, tip)
        last_sent_count = count_seconds
        last_sent_tip = tip

    def reset_recognition_after_reconnect():
        nonlocal count_seconds, tip, state, good_start_time, last_good_second
        nonlocal last_result, frame_counter
        nonlocal last_landmarks, last_body_angle, last_aux_text, last_person_text

        count_seconds = 0
        tip = "READY"
        state = "WAIT"
        good_start_time = None
        last_good_second = -1
        last_result = None
        last_landmarks = None
        last_body_angle = 0.0
        last_aux_text = ""
        last_person_text = "No person"
        frame_counter = 0
        reset_pose_smoothing()

    init_ai_client()

    ser = None if runtime is not None else open_serial()
    imu_ser = open_imu_serial()

    cap = open_camera_with_retries()
    if cap is None:
        print("摄像头暂未连接，将在动作运行期间继续重连")

    print("平板支撑直线版启动")
    print("标准：肩-髋-踝接近一条直线")
    print("按 R 重置计时")
    print("按 A 生成AI教练总结")
    print("按 Q 退出")

    action_result = STOP
    display = DisplaySession(cv2, window_name)
    try:
        display.open()

        publish_action_feedback(runtime, ser, mode, count_seconds, score, tip)

        with create_landmarker() as landmarker:
            while True:
                control = poll_action_control(runtime, mode)
                if control.kind == "RESET":
                    reset_action_state()
                elif control.kind in {"SWITCH", "STOP", "QUIT"}:
                    action_result = control
                    break

                last_imu = read_imu(imu_ser, last_imu)
                last_forwarded_hr_timestamp = forward_pending_heart_rate(
                    runtime, ser, last_forwarded_hr_timestamp
                )

                if cap is None:
                    cap = reconnect_camera(None)
                    if cap is None:
                        print("摄像头仍未恢复，将继续等待下一轮重连")
                        continue
                    reset_recognition_after_reconnect()
                    publish_action_feedback(
                        runtime, ser, mode, count_seconds, score, tip
                    )
                    last_sent_count = count_seconds
                    last_sent_tip = tip
                    continue

                ret, frame = cap.read()
                if not ret or frame is None:
                    cap = reconnect_camera(cap)
                    if cap is None:
                        print("摄像头重连失败，将继续等待下一轮重连")
                        continue
                    reset_recognition_after_reconnect()
                    publish_action_feedback(
                        runtime, ser, mode, count_seconds, score, tip
                    )
                    last_sent_count = count_seconds
                    last_sent_tip = tip
                    continue

                frame = fix_frame_color(frame)
                frame = cv2.flip(frame, 1)
                rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)

                frame_counter += 1
                fresh_result = (
                    frame_counter % PROCESS_EVERY_N_FRAMES == 0
                    or last_result is None
                )

                if fresh_result:
                    timestamp_ms = int(time.monotonic() * 1000)
                    result = landmarker.detect_for_video(mp_image, timestamp_ms)
                    last_result = result
                else:
                    result = last_result

                body_angle = last_body_angle
                line_error_percent = 999.0
                aux_text = last_aux_text
                person_text = last_person_text

                if result.pose_landmarks and fresh_result:
                    landmarks = smooth_pose_landmarks(result.pose_landmarks[0])
                    last_landmarks = landmarks
                    draw_pose(frame, landmarks)
                    person_text = "Person detected"
                    last_person_text = person_text

                    body_angle, line_error_percent, aux_text = plank_metric(landmarks)
                    last_body_angle = body_angle
                    last_aux_text = aux_text

                    now = time.monotonic()

                    is_good = (
                        body_angle >= GOOD_ANGLE_THRESHOLD
                        and line_error_percent <= GOOD_LINE_ERROR_THRESHOLD
                    )

                    if is_good:
                        tip = "GOOD"
                        state = "HOLD"

                        if good_start_time is None:
                            good_start_time = now
                            last_good_second = -1

                        count_seconds = int(now - good_start_time)

                        # 每增加1秒，统计一次GOOD，方便AI总结
                        if count_seconds > last_good_second:
                            last_good_second = count_seconds
                            action_stats["GOOD"] += 1

                    else:
                        tip = "BAD_FORM"
                        state = "ADJUST"
                        good_start_time = None
                        last_good_second = -1

                elif fresh_result:
                    tip = "NO_PERSON"
                    state = "WAIT"
                    last_landmarks = None
                    last_body_angle = 0.0
                    last_aux_text = ""
                    last_person_text = "No person"
                    body_angle = 0.0
                    aux_text = ""
                    person_text = "No person"
                    good_start_time = None
                    last_good_second = -1
                    reset_pose_smoothing()

                elif last_landmarks is not None:
                    draw_pose(frame, last_landmarks)

                if count_seconds != last_sent_count or tip != last_sent_tip:
                    publish_action_feedback(
                        runtime, ser, mode, count_seconds, score, tip
                    )

                    # BAD_FORM只在状态变化时播一次，避免一直说
                    if tip == "BAD_FORM" and last_sent_tip != "BAD_FORM":
                        speak_text("身体没有保持一条直线，收紧核心")
                        action_stats["BAD_FORM"] += 1

                    if tip == "GOOD" and last_sent_tip != "GOOD":
                        speak_text("身体保持直线，继续坚持")

                    last_sent_count = count_seconds
                    last_sent_tip = tip

                show = draw_info_panel(
                    frame,
                    mode,
                    count_seconds,
                    score,
                    tip,
                    "BodyAngle",
                    body_angle,
                    aux_text,
                    state,
                    person_text,
                    last_imu["gyro_mag"]
                )
                key = display.show(show)

                if key == ord("q"):
                    action_result = QUIT if runtime is not None else STOP
                    break

                elif key == ord("r"):
                    reset_action_state()

                elif key == ord("a"):
                    stats_copy = action_stats.copy()
                    threading.Thread(
                        target=generate_and_speak_ai_summary,
                        args=(mode, count_seconds, score, stats_copy),
                        daemon=True
                    ).start()

    finally:
        if cap is not None:
            cap.release()
        reset_pose_smoothing()
        display.close()

        if imu_ser and runtime is None:
            stop_imu_reader()

        if ser:
            ser.close()

    return action_result


if __name__ == "__main__":
    run_plank_straight_action()

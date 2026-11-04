"""
AI健身教练统一动作版

功能：
1. 程序只启动一次，摄像头、MediaPipe、反馈屏串口和蓝牙IMU不重复打开。
2. 在窗口右侧直接点击动作按钮即可切换识别动作。
3. 每个动作单独保存次数和分数；切换回来后继续显示原来的数据。
4. 支持重置当前动作、重置全部动作、按A生成当前动作AI总结。
5. 人声由电脑Python的pyttsx3播放，不使用ESP32扬声器播报。

需要和本文件放在同一文件夹：
- ai_fitness_common.py
- pose_landmarker_lite.task 或 pose_landmarker_full.task
- ai_coach_cloud.py（仅按A调用云端总结时需要）
"""

import math
import threading
import time
from collections import deque

import cv2
import mediapipe as mp
import numpy as np

import ai_fitness_common as common


WINDOW_NAME = "AI Fitness Coach - All In One"
WINDOW_WIDTH = 1280
WINDOW_HEIGHT = 720
VIDEO_WIDTH = 940
PANEL_X = VIDEO_WIDTH
PANEL_WIDTH = WINDOW_WIDTH - VIDEO_WIDTH


# ==================================================
# 各动作角度计算
# ==================================================

def curl_metric(landmarks):
    left_shoulder = common.get_xy(landmarks, 11)
    left_elbow = common.get_xy(landmarks, 13)
    left_wrist = common.get_xy(landmarks, 15)

    right_shoulder = common.get_xy(landmarks, 12)
    right_elbow = common.get_xy(landmarks, 14)
    right_wrist = common.get_xy(landmarks, 16)

    left_angle = common.angle_3points(left_shoulder, left_elbow, left_wrist)
    right_angle = common.angle_3points(right_shoulder, right_elbow, right_wrist)

    # 保持你原来代码的规则：默认用右臂。
    elbow_angle = right_angle
    return elbow_angle, f"L:{left_angle:.1f} R:{right_angle:.1f}"


def lateral_metric(landmarks):
    left_hip = common.get_xy(landmarks, 23)
    left_shoulder = common.get_xy(landmarks, 11)
    left_elbow = common.get_xy(landmarks, 13)

    right_hip = common.get_xy(landmarks, 24)
    right_shoulder = common.get_xy(landmarks, 12)
    right_elbow = common.get_xy(landmarks, 14)

    left_angle = common.angle_3points(left_hip, left_shoulder, left_elbow)
    right_angle = common.angle_3points(right_hip, right_shoulder, right_elbow)

    arm_angle = max(left_angle, right_angle)
    return arm_angle, f"L:{left_angle:.1f} R:{right_angle:.1f}"


def front_metric(landmarks):
    left_hip = common.get_xy(landmarks, 23)
    left_shoulder = common.get_xy(landmarks, 11)
    left_wrist = common.get_xy(landmarks, 15)

    right_hip = common.get_xy(landmarks, 24)
    right_shoulder = common.get_xy(landmarks, 12)
    right_wrist = common.get_xy(landmarks, 16)

    left_angle = common.angle_3points(left_hip, left_shoulder, left_wrist)
    right_angle = common.angle_3points(right_hip, right_shoulder, right_wrist)

    arm_angle = max(left_angle, right_angle)
    return arm_angle, f"L:{left_angle:.1f} R:{right_angle:.1f}"


def one_side_press_metric(shoulder, elbow, wrist):
    elbow_angle = common.angle_3points(shoulder, elbow, wrist)
    raise_score = (shoulder[1] - wrist[1]) * 100.0
    extension_score = elbow_angle - 80.0
    press_value = min(raise_score * 4.0, extension_score)
    return press_value, elbow_angle


def press_metric(landmarks):
    left_shoulder = common.get_xy(landmarks, 11)
    left_elbow = common.get_xy(landmarks, 13)
    left_wrist = common.get_xy(landmarks, 15)

    right_shoulder = common.get_xy(landmarks, 12)
    right_elbow = common.get_xy(landmarks, 14)
    right_wrist = common.get_xy(landmarks, 16)

    left_value, left_elbow_angle = one_side_press_metric(left_shoulder, left_elbow, left_wrist)
    right_value, right_elbow_angle = one_side_press_metric(right_shoulder, right_elbow, right_wrist)

    press_value = max(left_value, right_value)
    aux = (
        f"M L:{left_value:.1f} R:{right_value:.1f} "
        f"E L:{left_elbow_angle:.0f} R:{right_elbow_angle:.0f}"
    )
    return press_value, aux


def row_metric(landmarks):
    left_shoulder = common.get_xy(landmarks, 11)
    left_elbow = common.get_xy(landmarks, 13)
    left_wrist = common.get_xy(landmarks, 15)

    right_shoulder = common.get_xy(landmarks, 12)
    right_elbow = common.get_xy(landmarks, 14)
    right_wrist = common.get_xy(landmarks, 16)

    left_angle = common.angle_3points(left_shoulder, left_elbow, left_wrist)
    right_angle = common.angle_3points(right_shoulder, right_elbow, right_wrist)

    elbow_angle = min(left_angle, right_angle)
    return elbow_angle, f"L:{left_angle:.1f} R:{right_angle:.1f}"


def triceps_metric(landmarks):
    left_shoulder = common.get_xy(landmarks, 11)
    left_elbow = common.get_xy(landmarks, 13)
    left_wrist = common.get_xy(landmarks, 15)

    right_shoulder = common.get_xy(landmarks, 12)
    right_elbow = common.get_xy(landmarks, 14)
    right_wrist = common.get_xy(landmarks, 16)

    left_angle = common.angle_3points(left_shoulder, left_elbow, left_wrist)
    right_angle = common.angle_3points(right_shoulder, right_elbow, right_wrist)

    elbow_angle = max(left_angle, right_angle)
    return elbow_angle, f"L:{left_angle:.1f} R:{right_angle:.1f}"


def pushup_metric(landmarks):
    left_shoulder = common.get_xy(landmarks, 11)
    left_elbow = common.get_xy(landmarks, 13)
    left_wrist = common.get_xy(landmarks, 15)

    right_shoulder = common.get_xy(landmarks, 12)
    right_elbow = common.get_xy(landmarks, 14)
    right_wrist = common.get_xy(landmarks, 16)

    left_angle = common.angle_3points(left_shoulder, left_elbow, left_wrist)
    right_angle = common.angle_3points(right_shoulder, right_elbow, right_wrist)

    elbow_angle = min(left_angle, right_angle)
    return elbow_angle, f"L:{left_angle:.1f} R:{right_angle:.1f}"


def squat_metric(landmarks):
    left_hip = common.get_xy(landmarks, 23)
    left_knee = common.get_xy(landmarks, 25)
    left_ankle = common.get_xy(landmarks, 27)

    right_hip = common.get_xy(landmarks, 24)
    right_knee = common.get_xy(landmarks, 26)
    right_ankle = common.get_xy(landmarks, 28)

    left_angle = common.angle_3points(left_hip, left_knee, left_ankle)
    right_angle = common.angle_3points(right_hip, right_knee, right_ankle)

    knee_angle = min(left_angle, right_angle)
    return knee_angle, f"L:{left_angle:.1f} R:{right_angle:.1f}"


def point_line_distance(point, line_a, line_b):
    ax, ay = float(line_a[0]), float(line_a[1])
    bx, by = float(line_b[0]), float(line_b[1])
    px, py = float(point[0]), float(point[1])

    abx = bx - ax
    aby = by - ay
    apx = px - ax
    apy = py - ay

    ab_len = math.hypot(abx, aby) + 1e-6
    cross = abs(abx * apy - aby * apx)
    return cross / ab_len


def plank_metric(landmarks):
    left_shoulder = common.get_xy(landmarks, 11)
    left_hip = common.get_xy(landmarks, 23)
    left_ankle = common.get_xy(landmarks, 27)

    right_shoulder = common.get_xy(landmarks, 12)
    right_hip = common.get_xy(landmarks, 24)
    right_ankle = common.get_xy(landmarks, 28)

    left_angle = common.angle_3points(left_shoulder, left_hip, left_ankle)
    right_angle = common.angle_3points(right_shoulder, right_hip, right_ankle)

    left_error = point_line_distance(left_hip, left_shoulder, left_ankle)
    right_error = point_line_distance(right_hip, right_shoulder, right_ankle)

    if left_angle >= right_angle:
        body_angle = left_angle
        line_error = left_error
    else:
        body_angle = right_angle
        line_error = right_error

    line_error_percent = line_error * 100.0
    aux = f"Angle L:{left_angle:.1f} R:{right_angle:.1f} LineErr:{line_error_percent:.1f}"
    return body_angle, line_error_percent, aux


# ==================================================
# 动作配置：沿用你原来各个文件中的阈值
# ==================================================

ACTION_CONFIGS = {
    "CURL": {
        "mode": "CURL",
        "button": "Curl",
        "cn": "二头弯举",
        "metric_name": "Elbow",
        "compute_metric": curl_metric,
        "target_op": "<",
        "target_threshold": 150,
        "finish_op": ">",
        "finish_threshold": 155,
        "range_type": "min",
        "range_threshold": 120,
        "min_action_time": 0.65,
        "min_count_interval": 0.45,
        "target_hold_frames": 1,
        "finish_hold_frames": 1,
        "metric_history_len": 3,
        "landmark_smooth_alpha": 0.50,
        "tip_hold_time": 1.5,
        "swing_gyro_threshold": 1000.0,
    },
    "LATERAL": {
        "mode": "LATERAL",
        "button": "Lateral",
        "cn": "侧平举",
        "metric_name": "Arm",
        "compute_metric": lateral_metric,
        "target_op": ">",
        "target_threshold": 45,
        "finish_op": "<",
        "finish_threshold": 35,
        "range_type": "max",
        "range_threshold": 80,
        "min_action_time": 0.80,
        "min_count_interval": 0.50,
        "target_hold_frames": 1,
        "finish_hold_frames": 1,
        "metric_history_len": 3,
        "landmark_smooth_alpha": 0.55,
        "tip_hold_time": 1.5,
        "swing_gyro_threshold": 1000.0,
    },
    "FRONT": {
        "mode": "FRONT",
        "button": "Front",
        "cn": "前平举",
        "metric_name": "Arm",
        "compute_metric": front_metric,
        "target_op": ">",
        "target_threshold": 40,
        "finish_op": "<",
        "finish_threshold": 30,
        "range_type": "max",
        "range_threshold": 72,
        "min_action_time": 0.80,
        "min_count_interval": 0.50,
        "target_hold_frames": 1,
        "finish_hold_frames": 1,
        "metric_history_len": 3,
        "landmark_smooth_alpha": 0.55,
        "tip_hold_time": 1.5,
        "swing_gyro_threshold": 1000.0,
    },
    "PRESS": {
        "mode": "PRESS",
        "button": "Press",
        "cn": "推举",
        "metric_name": "Press",
        "compute_metric": press_metric,
        "target_op": ">",
        "target_threshold": 25,
        "finish_op": "<",
        "finish_threshold": 15,
        "range_type": "max",
        "range_threshold": 52,
        "min_action_time": 0.75,
        "min_count_interval": 0.50,
        "target_hold_frames": 1,
        "finish_hold_frames": 1,
        "metric_history_len": 3,
        "landmark_smooth_alpha": 0.55,
        "tip_hold_time": 1.5,
        "swing_gyro_threshold": 1000.0,
    },
    "ROW": {
        "mode": "ROW",
        "button": "Row",
        "cn": "俯身划船",
        "metric_name": "Elbow",
        "compute_metric": row_metric,
        "target_op": "<",
        "target_threshold": 130,
        "finish_op": ">",
        "finish_threshold": 150,
        "range_type": "min",
        "range_threshold": 110,
        "min_action_time": 0.70,
        "min_count_interval": 0.50,
        "target_hold_frames": 1,
        "finish_hold_frames": 1,
        "metric_history_len": 3,
        "landmark_smooth_alpha": 0.55,
        "tip_hold_time": 1.5,
        "swing_gyro_threshold": 1500.0,
    },
    "TRICEPS": {
        "mode": "TRICEPS",
        "button": "Triceps",
        "cn": "过头臂屈伸",
        "metric_name": "Elbow",
        "compute_metric": triceps_metric,
        "target_op": ">",
        "target_threshold": 135,
        "finish_op": "<",
        "finish_threshold": 120,
        "range_type": "max",
        "range_threshold": 155,
        "min_action_time": 0.70,
        "min_count_interval": 0.50,
        "target_hold_frames": 1,
        "finish_hold_frames": 1,
        "metric_history_len": 3,
        "landmark_smooth_alpha": 0.55,
        "tip_hold_time": 1.5,
        "swing_gyro_threshold": 1500.0,
    },
    "PUSHUP": {
        "mode": "PUSHUP",
        "button": "Push-up",
        "cn": "俯卧撑",
        "metric_name": "Elbow",
        "compute_metric": pushup_metric,
        "target_op": "<",
        "target_threshold": 135,
        "finish_op": ">",
        "finish_threshold": 155,
        "range_type": "min",
        "range_threshold": 110,
        "min_action_time": 0.70,
        "min_count_interval": 0.80,
        "target_hold_frames": 1,
        "finish_hold_frames": 1,
        "metric_history_len": 3,
        "landmark_smooth_alpha": 0.55,
        "tip_hold_time": 1.5,
        "swing_gyro_threshold": 1500.0,
    },
    "SQUAT": {
        "mode": "SQUAT",
        "button": "Squat",
        "cn": "深蹲",
        "metric_name": "Knee",
        "compute_metric": squat_metric,
        "target_op": "<",
        "target_threshold": 145,
        "finish_op": ">",
        "finish_threshold": 160,
        "range_type": "min",
        "range_threshold": 115,
        "min_action_time": 1.50,
        "min_count_interval": 1.80,
        "target_hold_frames": 3,
        "finish_hold_frames": 4,
        "metric_history_len": 7,
        "landmark_smooth_alpha": 0.68,
        "tip_hold_time": 1.5,
        "swing_gyro_threshold": 999999.0,
    },
    "PLANK": {
        "mode": "PLANK",
        "button": "Plank",
        "cn": "平板支撑",
        "metric_name": "BodyAngle",
        "compute_metric": plank_metric,
        "landmark_smooth_alpha": 0.68,
        "good_angle_threshold": 160.0,
        "good_line_error_threshold": 10.0,
    },
}

ACTION_ORDER = [
    "CURL",
    "LATERAL",
    "FRONT",
    "PRESS",
    "ROW",
    "TRICEPS",
    "PUSHUP",
    "SQUAT",
    "PLANK",
]


# ==================================================
# 状态管理
# ==================================================

def new_rep_state(config):
    return {
        "count": 0,
        "score": 100,
        "tip": "READY",
        "phase": "WAIT_TARGET",
        "stats": {
            "GOOD": 0,
            "TOO_FAST": 0,
            "RANGE_SMALL": 0,
            "SWING": 0,
        },
        "last_count_time": 0.0,
        "target_start_time": 0.0,
        "best_metric": None,
        "target_frame_count": 0,
        "finish_frame_count": 0,
        "tip_hold_until": 0.0,
        "metric_history": deque(maxlen=config.get("metric_history_len", 7)),
        "last_sent_count": -1,
        "last_sent_tip": "",
    }


def new_plank_state():
    return {
        "count": 0,
        "score": 100,
        "tip": "READY",
        "phase": "WAIT",
        "stats": {
            "GOOD": 0,
            "TOO_FAST": 0,
            "RANGE_SMALL": 0,
            "SWING": 0,
            "BAD_FORM": 0,
        },
        "hold_started_at": None,
        "hold_base_count": 0,
        "last_good_second": -1,
        "last_sent_count": -1,
        "last_sent_tip": "",
    }


def make_all_states():
    states = {}
    for mode, config in ACTION_CONFIGS.items():
        states[mode] = new_plank_state() if mode == "PLANK" else new_rep_state(config)
    return states


def reset_rep_transient(state):
    state["phase"] = "WAIT_TARGET"
    state["tip"] = "READY"
    state["last_count_time"] = 0.0
    state["target_start_time"] = 0.0
    state["best_metric"] = None
    state["target_frame_count"] = 0
    state["finish_frame_count"] = 0
    state["tip_hold_until"] = 0.0
    state["metric_history"].clear()


def pause_plank(state):
    state["hold_started_at"] = None
    state["hold_base_count"] = state["count"]
    state["last_good_second"] = state["count"]


def reset_current_action(states, mode):
    if mode == "PLANK":
        states[mode] = new_plank_state()
    else:
        states[mode] = new_rep_state(ACTION_CONFIGS[mode])
    common.reset_pose_smoothing()


def reset_all_actions(states):
    states.clear()
    states.update(make_all_states())
    common.reset_pose_smoothing()


# ==================================================
# 识别状态机
# ==================================================

def condition(value, operator, threshold):
    if operator == ">":
        return value > threshold
    if operator == "<":
        return value < threshold
    return False


def speak_rep_result(mode, count, tip):
    if tip == "GOOD":
        sentence = f"第{count}次，标准，请保持"
    elif tip == "TOO_FAST":
        sentence = f"第{count}次，动作太快，慢一点"
    elif tip == "RANGE_SMALL":
        sentence = f"第{count}次，幅度不够，再做到位一点"
    elif tip == "SWING":
        sentence = f"第{count}次，不要借力甩动"
    else:
        sentence = f"第{count}次"

    print(f"[{mode}] 人声报数：{sentence}")
    common.speak_text(sentence)


def update_repetition_action(config, state, landmarks, imu_data, now):
    metric_value, aux_text = config["compute_metric"](landmarks)

    state["metric_history"].append(metric_value)
    metric_value = sum(state["metric_history"]) / len(state["metric_history"])

    is_target = condition(metric_value, config["target_op"], config["target_threshold"])
    is_finish = condition(metric_value, config["finish_op"], config["finish_threshold"])

    if is_target:
        state["target_frame_count"] += 1
    else:
        state["target_frame_count"] = 0

    if is_finish:
        state["finish_frame_count"] += 1
    else:
        state["finish_frame_count"] = 0

    if state["phase"] == "WAIT_TARGET":
        enough_interval = now - state["last_count_time"] > config.get("min_count_interval", 1.5)
        enough_target_frames = state["target_frame_count"] >= config.get("target_hold_frames", 3)

        if enough_interval and enough_target_frames:
            state["phase"] = "WAIT_FINISH"
            state["target_start_time"] = now
            state["best_metric"] = metric_value
            state["tip"] = "UP"

    elif state["phase"] == "WAIT_FINISH":
        if config.get("range_type", "max") == "max":
            state["best_metric"] = max(state["best_metric"], metric_value)
        else:
            state["best_metric"] = min(state["best_metric"], metric_value)

        enough_interval = now - state["last_count_time"] > config.get("min_count_interval", 1.5)
        enough_finish_frames = state["finish_frame_count"] >= config.get("finish_hold_frames", 4)

        if enough_interval and enough_finish_frames:
            action_time = now - state["target_start_time"]

            state["count"] += 1
            state["last_count_time"] = now
            state["phase"] = "WAIT_TARGET"

            if action_time < config.get("min_action_time", 0.6):
                state["tip"] = "TOO_FAST"
                state["score"] -= 10

            elif config.get("range_type", "max") == "max" and state["best_metric"] < config["range_threshold"]:
                state["tip"] = "RANGE_SMALL"
                state["score"] -= 10

            elif config.get("range_type", "max") == "min" and state["best_metric"] > config["range_threshold"]:
                state["tip"] = "RANGE_SMALL"
                state["score"] -= 10

            elif imu_data["gyro_mag"] > config.get("swing_gyro_threshold", 7.0):
                state["tip"] = "SWING"
                state["score"] -= 10

            else:
                state["tip"] = "GOOD"

            state["score"] = max(0, state["score"])

            if state["tip"] in state["stats"]:
                state["stats"][state["tip"]] += 1

            state["tip_hold_until"] = now + config.get("tip_hold_time", 1.5)
            speak_rep_result(config["mode"], state["count"], state["tip"])

    if state["phase"] == "WAIT_TARGET":
        if state["tip"] in ("GOOD", "TOO_FAST", "RANGE_SMALL", "SWING"):
            if now > state["tip_hold_until"]:
                state["tip"] = "READY"

    return metric_value, aux_text


def update_plank_action(config, state, landmarks, now):
    body_angle, line_error_percent, aux_text = config["compute_metric"](landmarks)

    is_good = (
        body_angle >= config.get("good_angle_threshold", 160.0)
        and line_error_percent <= config.get("good_line_error_threshold", 10.0)
    )

    previous_tip = state["tip"]

    if is_good:
        state["tip"] = "GOOD"
        state["phase"] = "HOLD"

        if state["hold_started_at"] is None:
            state["hold_started_at"] = now
            state["hold_base_count"] = state["count"]

        state["count"] = state["hold_base_count"] + int(now - state["hold_started_at"])

        if state["count"] > state["last_good_second"]:
            state["last_good_second"] = state["count"]
            state["stats"]["GOOD"] += 1

        if previous_tip != "GOOD":
            common.speak_text("身体保持直线，继续坚持")

    else:
        state["tip"] = "BAD_FORM"
        state["phase"] = "ADJUST"
        pause_plank(state)

        if previous_tip != "BAD_FORM":
            state["stats"]["BAD_FORM"] += 1
            common.speak_text("身体没有保持一条直线，收紧核心")

    return body_angle, aux_text


# ==================================================
# 界面与鼠标按钮
# ==================================================

button_regions = []
pending_command = None


def draw_button(canvas, x1, y1, x2, y2, label, active=False):
    # 不指定固定主题色，使用灰阶，避免不同电脑显示差异。
    fill = (210, 210, 210) if active else (75, 75, 75)
    text_color = (25, 25, 25) if active else (245, 245, 245)

    cv2.rectangle(canvas, (x1, y1), (x2, y2), fill, -1)
    cv2.rectangle(canvas, (x1, y1), (x2, y2), (180, 180, 180), 1)

    text_size, _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.55, 1)
    text_x = x1 + max(5, (x2 - x1 - text_size[0]) // 2)
    text_y = y1 + (y2 - y1 + text_size[1]) // 2
    cv2.putText(canvas, label, (text_x, text_y), cv2.FONT_HERSHEY_SIMPLEX, 0.55, text_color, 1, cv2.LINE_AA)


def draw_interface(frame, active_mode, state, metric_name, metric_value, aux_text, person_text, imu_data):
    global button_regions

    video = cv2.resize(frame, (VIDEO_WIDTH, WINDOW_HEIGHT))
    canvas = np.zeros((WINDOW_HEIGHT, WINDOW_WIDTH, 3), dtype=np.uint8)
    canvas[:, :VIDEO_WIDTH] = video
    canvas[:, PANEL_X:] = (32, 32, 32)

    # 视频左上角状态
    overlay = canvas.copy()
    cv2.rectangle(overlay, (15, 15), (395, 178), (0, 0, 0), -1)
    canvas = cv2.addWeighted(overlay, 0.58, canvas, 0.42, 0)

    config = ACTION_CONFIGS[active_mode]
    info_lines = [
        f"Mode: {active_mode}",
        f"Count/Time: {state['count']}",
        f"Score: {state['score']}",
        f"Tip: {state['tip']}",
        f"{metric_name}: {metric_value:.2f}",
        f"State: {state['phase']}",
    ]

    for index, line in enumerate(info_lines):
        cv2.putText(
            canvas,
            line,
            (30, 43 + index * 24),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.62,
            (255, 255, 255),
            2,
            cv2.LINE_AA,
        )

    cv2.putText(canvas, person_text, (25, WINDOW_HEIGHT - 55), cv2.FONT_HERSHEY_SIMPLEX, 0.62, (255, 255, 255), 2)
    cv2.putText(canvas, f"IMU gyro: {imu_data['gyro_mag']:.2f}", (25, WINDOW_HEIGHT - 30), cv2.FONT_HERSHEY_SIMPLEX, 0.58, (255, 255, 255), 2)

    if aux_text:
        cv2.putText(canvas, aux_text[:90], (420, WINDOW_HEIGHT - 30), cv2.FONT_HERSHEY_SIMPLEX, 0.48, (255, 255, 255), 1)

    # 右侧控制区
    cv2.putText(canvas, "CLICK TO SWITCH", (PANEL_X + 27, 35), cv2.FONT_HERSHEY_SIMPLEX, 0.70, (245, 245, 245), 2)
    cv2.putText(canvas, f"Current: {config['button']}", (PANEL_X + 20, 65), cv2.FONT_HERSHEY_SIMPLEX, 0.60, (245, 245, 245), 2)

    button_regions = []
    margin_x = 15
    gap_x = 10
    button_width = 150
    button_height = 48
    start_y = 88
    gap_y = 10

    for index, mode in enumerate(ACTION_ORDER):
        row = index // 2
        col = index % 2
        x1 = PANEL_X + margin_x + col * (button_width + gap_x)
        y1 = start_y + row * (button_height + gap_y)
        x2 = x1 + button_width
        y2 = y1 + button_height

        draw_button(canvas, x1, y1, x2, y2, ACTION_CONFIGS[mode]["button"], active=(mode == active_mode))
        button_regions.append((x1, y1, x2, y2, ("ACTION", mode)))

    # 功能按钮
    control_y = 400
    full_x1 = PANEL_X + 15
    full_x2 = WINDOW_WIDTH - 15

    controls = [
        ("Reset current", "RESET_CURRENT"),
        ("Reset all", "RESET_ALL"),
        ("AI summary (A)", "AI_SUMMARY"),
        ("Quit (Q)", "QUIT"),
    ]

    for index, (label, command) in enumerate(controls):
        y1 = control_y + index * 58
        y2 = y1 + 46
        draw_button(canvas, full_x1, y1, full_x2, y2, label, active=False)
        button_regions.append((full_x1, y1, full_x2, y2, ("COMMAND", command)))

    cv2.putText(canvas, "R: reset current", (PANEL_X + 22, 654), cv2.FONT_HERSHEY_SIMPLEX, 0.52, (220, 220, 220), 1)
    cv2.putText(canvas, "A: AI summary", (PANEL_X + 22, 678), cv2.FONT_HERSHEY_SIMPLEX, 0.52, (220, 220, 220), 1)
    cv2.putText(canvas, "Q: quit", (PANEL_X + 22, 702), cv2.FONT_HERSHEY_SIMPLEX, 0.52, (220, 220, 220), 1)

    return canvas


def mouse_callback(event, x, y, flags, userdata):
    del flags, userdata
    global pending_command

    if event != cv2.EVENT_LBUTTONDOWN:
        return

    for x1, y1, x2, y2, command in button_regions:
        if x1 <= x <= x2 and y1 <= y <= y2:
            pending_command = command
            return


# ==================================================
# 主程序
# ==================================================

def main():
    global pending_command

    states = make_all_states()
    active_mode = "CURL"

    common.init_ai_client()

    feedback_serial = common.open_serial()
    imu_serial = common.open_imu_serial()

    print("正在打开摄像头视频流：", common.STREAM_URL)
    capture = cv2.VideoCapture(common.STREAM_URL)
    capture.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    capture.set(cv2.CAP_PROP_CONVERT_RGB, 1)

    try:
        capture.set(cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, 3000)
        capture.set(cv2.CAP_PROP_READ_TIMEOUT_MSEC, 3000)
    except Exception:
        pass

    if not capture.isOpened():
        print("无法打开摄像头视频流，请检查ai_fitness_common.py中的STREAM_URL。")
        common.speak_text("摄像头连接失败")
        if imu_serial:
            common.stop_imu_reader()
        if feedback_serial:
            feedback_serial.close()
        return

    cv2.namedWindow(WINDOW_NAME, cv2.WINDOW_NORMAL)
    cv2.resizeWindow(WINDOW_NAME, WINDOW_WIDTH, WINDOW_HEIGHT)
    cv2.setMouseCallback(WINDOW_NAME, mouse_callback)

    print("统一动作版启动成功。")
    print("右侧点击动作按钮即可切换，不需要退出程序。")
    print("按R重置当前动作，按A生成当前动作总结，按Q退出。")

    common.speak_text("统一动作识别启动，当前动作二头弯举")

    last_imu = {
        "ax": 0,
        "ay": 0,
        "az": 0,
        "gx": 0,
        "gy": 0,
        "gz": 0,
        "pitch": 0,
        "gyro_mag": 0,
    }

    frame_counter = 0
    last_result = None
    metric_value = 0.0
    aux_text = ""
    person_text = "No person"
    running = True

    # 第一次把当前动作状态发送给反馈屏。
    current_state = states[active_mode]
    common.send_cmd(
        feedback_serial,
        active_mode,
        current_state["count"],
        current_state["score"],
        current_state["tip"],
    )

    try:
        with common.create_landmarker() as landmarker:
            while running:
                last_imu = common.read_imu(imu_serial, last_imu)

                ok, frame = capture.read()
                if not ok or frame is None:
                    print("读取画面失败，正在重试；若持续失败，请关闭浏览器中的视频流并检查IP。")
                    cv2.waitKey(1)
                    time.sleep(0.05)
                    continue

                frame = common.fix_frame_color(frame)
                frame = cv2.flip(frame, 1)

                rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)

                frame_counter += 1
                if frame_counter % common.PROCESS_EVERY_N_FRAMES == 0 or last_result is None:
                    timestamp_ms = int(time.monotonic() * 1000)
                    last_result = landmarker.detect_for_video(mp_image, timestamp_ms)

                result = last_result
                config = ACTION_CONFIGS[active_mode]
                state = states[active_mode]
                common.current_landmark_smooth_alpha = config.get(
                    "landmark_smooth_alpha",
                    common.LANDMARK_SMOOTH_ALPHA,
                )

                metric_value = 0.0
                aux_text = ""
                person_text = "No person"

                if result is not None and result.pose_landmarks:
                    landmarks = common.smooth_pose_landmarks(result.pose_landmarks[0])
                    common.draw_pose(frame, landmarks)
                    person_text = "Person detected"
                    now = time.time()

                    if active_mode == "PLANK":
                        metric_value, aux_text = update_plank_action(config, state, landmarks, now)
                    else:
                        if state["tip"] == "NO_PERSON" and state["phase"] == "WAIT_TARGET":
                            state["tip"] = "READY"
                        metric_value, aux_text = update_repetition_action(config, state, landmarks, last_imu, now)

                else:
                    common.reset_pose_smoothing()
                    person_text = "No person"

                    if active_mode == "PLANK":
                        previous_tip = state["tip"]
                        state["tip"] = "NO_PERSON"
                        state["phase"] = "WAIT"
                        pause_plank(state)
                        if previous_tip not in ("NO_PERSON", "READY"):
                            pass
                    else:
                        state["tip"] = "NO_PERSON"
                        state["phase"] = "WAIT_TARGET"
                        state["metric_history"].clear()
                        state["target_frame_count"] = 0
                        state["finish_frame_count"] = 0
                        state["best_metric"] = None

                # 当前动作状态变化时，发送给反馈屏。
                if state["count"] != state["last_sent_count"] or state["tip"] != state["last_sent_tip"]:
                    common.send_cmd(
                        feedback_serial,
                        active_mode,
                        state["count"],
                        state["score"],
                        state["tip"],
                    )
                    state["last_sent_count"] = state["count"]
                    state["last_sent_tip"] = state["tip"]

                show = draw_interface(
                    frame,
                    active_mode,
                    state,
                    config.get("metric_name", "Value"),
                    metric_value,
                    aux_text,
                    person_text,
                    last_imu,
                )
                cv2.imshow(WINDOW_NAME, show)

                key = cv2.waitKey(1) & 0xFF

                if key == ord("q"):
                    pending_command = ("COMMAND", "QUIT")
                elif key == ord("r"):
                    pending_command = ("COMMAND", "RESET_CURRENT")
                elif key == ord("a"):
                    pending_command = ("COMMAND", "AI_SUMMARY")

                # 鼠标点击和快捷键统一在这里处理。
                command = pending_command
                pending_command = None

                if command is None:
                    continue

                command_type, command_value = command

                if command_type == "ACTION":
                    new_mode = command_value
                    if new_mode == active_mode:
                        continue

                    # 离开平板支撑时暂停计时，防止切换期间时间继续增加。
                    if active_mode == "PLANK":
                        pause_plank(states[active_mode])
                    else:
                        # 中途切换动作时，只丢弃未完成的半次动作，保留已完成次数。
                        reset_rep_transient(states[active_mode])

                    active_mode = new_mode
                    new_config = ACTION_CONFIGS[active_mode]
                    new_state = states[active_mode]

                    if active_mode == "PLANK":
                        pause_plank(new_state)
                        new_state["tip"] = "READY"
                        new_state["phase"] = "WAIT"
                    else:
                        reset_rep_transient(new_state)

                    common.current_landmark_smooth_alpha = new_config.get(
                        "landmark_smooth_alpha",
                        common.LANDMARK_SMOOTH_ALPHA,
                    )
                    common.reset_pose_smoothing()
                    metric_value = 0.0
                    aux_text = ""

                    # 强制反馈屏立即显示新动作。
                    common.send_cmd(
                        feedback_serial,
                        active_mode,
                        new_state["count"],
                        new_state["score"],
                        new_state["tip"],
                    )
                    new_state["last_sent_count"] = new_state["count"]
                    new_state["last_sent_tip"] = new_state["tip"]

                    print(f"已切换动作：{new_config['cn']} ({active_mode})")
                    common.speak_text(f"已切换到{new_config['cn']}")

                elif command_type == "COMMAND":
                    if command_value == "RESET_CURRENT":
                        reset_current_action(states, active_mode)
                        state = states[active_mode]
                        common.send_cmd(feedback_serial, active_mode, 0, 100, "READY")
                        state["last_sent_count"] = 0
                        state["last_sent_tip"] = "READY"
                        print(f"已重置当前动作：{ACTION_CONFIGS[active_mode]['cn']}")
                        common.speak_text("当前动作已重置")

                    elif command_value == "RESET_ALL":
                        reset_all_actions(states)
                        state = states[active_mode]
                        common.send_cmd(feedback_serial, active_mode, 0, 100, "READY")
                        state["last_sent_count"] = 0
                        state["last_sent_tip"] = "READY"
                        print("所有动作数据已重置。")
                        common.speak_text("所有动作已重置")

                    elif command_value == "AI_SUMMARY":
                        state = states[active_mode]
                        stats_copy = state["stats"].copy()
                        threading.Thread(
                            target=common.generate_and_speak_ai_summary,
                            args=(
                                active_mode,
                                state["count"],
                                state["score"],
                                stats_copy,
                            ),
                            daemon=True,
                        ).start()

                    elif command_value == "QUIT":
                        running = False

    except KeyboardInterrupt:
        print("收到键盘中断，程序退出。")

    except Exception as error:
        print("统一动作程序运行失败：", error)
        common.speak_text("程序运行出现错误")
        raise

    finally:
        capture.release()
        cv2.destroyAllWindows()

        if imu_serial:
            common.stop_imu_reader()

        if feedback_serial:
            try:
                feedback_serial.close()
            except Exception:
                pass

        print("统一动作程序已关闭。")


if __name__ == "__main__":
    main()

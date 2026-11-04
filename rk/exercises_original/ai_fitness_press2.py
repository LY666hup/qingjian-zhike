from ai_fitness_common import run_repetition_action, angle_3points, get_xy
from ai_fitness_imu_validation import load_action_imu_thresholds


def one_side_press_metric(shoulder, elbow, wrist):
    elbow_angle = angle_3points(shoulder, elbow, wrist)

    # y越小越靠上，所以 shoulder_y - wrist_y 越大，手举得越高
    raise_score = (shoulder[1] - wrist[1]) * 100.0

    # 肘角越大越接近伸直
    extension_score = elbow_angle - 80.0

    # 两个条件都要满足，所以取较小值
    press_metric = min(raise_score * 4.0, extension_score)

    return press_metric, elbow_angle, raise_score


def compute_metric(landmarks):
    left_shoulder = get_xy(landmarks, 11)
    left_elbow = get_xy(landmarks, 13)
    left_wrist = get_xy(landmarks, 15)

    right_shoulder = get_xy(landmarks, 12)
    right_elbow = get_xy(landmarks, 14)
    right_wrist = get_xy(landmarks, 16)

    left_metric, left_elbow_angle, left_raise = one_side_press_metric(left_shoulder, left_elbow, left_wrist)
    right_metric, right_elbow_angle, right_raise = one_side_press_metric(right_shoulder, right_elbow, right_wrist)

    press_metric = max(left_metric, right_metric)

    aux = (
        f"M L:{left_metric:.1f} R:{right_metric:.1f} "
        f"E L:{left_elbow_angle:.0f} R:{right_elbow_angle:.0f}"
    )
    return press_metric, aux


config = {
    "mode": "PRESS",
    "window_name": "AI Fitness Coach - Shoulder Press",
    "metric_name": "Press",
    "compute_metric": compute_metric,

    # 推举 metric 越大，越接近推到顶部
    # 先用 25 捕捉半程动作
    "target_op": ">",
    "target_threshold": 25,

    # 放回起始位置
    "finish_op": "<",
    "finish_threshold": 15,

    # 真正推到 52 以上才算标准
    # 半程推举会计数但提示 RANGE_SMALL
    "range_type": "max",
    "range_threshold": 52,

    "min_action_time": 1.20,
    "min_count_interval": 0.50,
    "target_hold_frames": 1,
    "finish_hold_frames": 1,
    "metric_history_len": 3,
    "landmark_smooth_alpha": 0.55,
    "tip_hold_time": 1.5,
    "swing_gyro_threshold": 1000.0,
    **load_action_imu_thresholds("OVERHEAD_PRESS"),
}


if __name__ == "__main__":
    run_repetition_action(config)

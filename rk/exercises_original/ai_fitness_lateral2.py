from ai_fitness_common import run_repetition_action, angle_3points, get_xy
from ai_fitness_imu_validation import load_action_imu_thresholds


def compute_metric(landmarks):
    left_hip = get_xy(landmarks, 23)
    left_shoulder = get_xy(landmarks, 11)
    left_elbow = get_xy(landmarks, 13)

    right_hip = get_xy(landmarks, 24)
    right_shoulder = get_xy(landmarks, 12)
    right_elbow = get_xy(landmarks, 14)

    left_angle = angle_3points(left_hip, left_shoulder, left_elbow)
    right_angle = angle_3points(right_hip, right_shoulder, right_elbow)

    arm_angle = max(left_angle, right_angle)

    aux = f"L:{left_angle:.1f} R:{right_angle:.1f}"
    return arm_angle, aux


config = {
    "mode": "LATERAL",
    "window_name": "AI Fitness Coach - Lateral Raise",
    "metric_name": "Arm",
    "compute_metric": compute_metric,

    # 侧平举角度越大，抬得越高
    # 先用较低阈值捕捉“抬起来”，方便半程也能计数并提示幅度不够
    "target_op": ">",
    "target_threshold": 45,

    # 放下到低位才算完成一次
    "finish_op": "<",
    "finish_threshold": 35,

    # 抬到 80° 以上才算幅度标准
    # 只抬到 50°~75°，会计数但提示 RANGE_SMALL
    "range_type": "max",
    "range_threshold": 80,

    "min_action_time": 1.20,
    "min_count_interval": 0.50,
    "target_hold_frames": 1,
    "finish_hold_frames": 1,
    "metric_history_len": 3,
    "landmark_smooth_alpha": 0.55,
    "tip_hold_time": 1.5,
    "swing_gyro_threshold": 1000.0,
    **load_action_imu_thresholds("LATERAL_RAISE"),
}


if __name__ == "__main__":
    run_repetition_action(config)

from ai_fitness_common import run_repetition_action, angle_3points, get_xy
from ai_fitness_imu_validation import load_action_imu_thresholds


def compute_metric(landmarks):
    left_hip = get_xy(landmarks, 23)
    left_shoulder = get_xy(landmarks, 11)
    left_wrist = get_xy(landmarks, 15)

    right_hip = get_xy(landmarks, 24)
    right_shoulder = get_xy(landmarks, 12)
    right_wrist = get_xy(landmarks, 16)

    left_angle = angle_3points(left_hip, left_shoulder, left_wrist)
    right_angle = angle_3points(right_hip, right_shoulder, right_wrist)

    arm_angle = max(left_angle, right_angle)

    aux = f"L:{left_angle:.1f} R:{right_angle:.1f}"
    return arm_angle, aux


config = {
    "mode": "FRONT",
    "window_name": "AI Fitness Coach - Front Raise",
    "metric_name": "Arm",
    "compute_metric": compute_metric,

    # 前平举建议侧身或45度面对摄像头
    # 角度越大，手臂抬得越高
    "target_op": ">",
    "target_threshold": 40,

    "finish_op": "<",
    "finish_threshold": 30,

    # 抬到 72° 以上算标准；不到位提示 RANGE_SMALL
    "range_type": "max",
    "range_threshold": 72,

    "min_action_time": 1.20,
    "min_count_interval": 0.50,
    "target_hold_frames": 1,
    "finish_hold_frames": 1,
    "metric_history_len": 3,
    "landmark_smooth_alpha": 0.55,
    "tip_hold_time": 1.5,
    "swing_gyro_threshold": 1000.0,
    **load_action_imu_thresholds("FRONT_RAISE"),
}


if __name__ == "__main__":
    run_repetition_action(config)

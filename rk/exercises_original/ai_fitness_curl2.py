from ai_fitness_common import run_repetition_action, angle_3points, get_xy
from ai_fitness_imu_validation import load_action_imu_thresholds


def compute_metric(landmarks):
    # 左臂：左肩11 左肘13 左腕15
    left_shoulder = get_xy(landmarks, 11)
    left_elbow = get_xy(landmarks, 13)
    left_wrist = get_xy(landmarks, 15)

    # 右臂：右肩12 右肘14 右腕16
    right_shoulder = get_xy(landmarks, 12)
    right_elbow = get_xy(landmarks, 14)
    right_wrist = get_xy(landmarks, 16)

    left_angle = angle_3points(left_shoulder, left_elbow, left_wrist)
    right_angle = angle_3points(right_shoulder, right_elbow, right_wrist)

    # 默认看右臂；如果展示时用左臂，把 right_angle 改成 left_angle
    # 如果想左右都能识别，可以改成：elbow_angle = min(left_angle, right_angle)
    elbow_angle = right_angle

    aux = f"L:{left_angle:.1f} R:{right_angle:.1f}"
    return elbow_angle, aux


config = {
    "mode": "CURL",
    "window_name": "AI Fitness Coach - Curl",
    "metric_name": "Elbow",
    "compute_metric": compute_metric,

    # 二头弯举是“角度越小，弯得越深”
    # target_threshold 是“进入一次动作”的阈值，必须放宽。
    # 只要弯到 150° 以下，就认为开始了一次动作。
    "target_op": "<",
    "target_threshold": 150,

    # 回到接近伸直位置才算完成一次
    "finish_op": ">",
    "finish_threshold": 155,

    # 真正弯到 120° 以下才算幅度标准
    # 如果只弯到 130°~145°，会计数，但提示 RANGE_SMALL / 幅度不够
    "range_type": "min",
    "range_threshold": 120,

    # 想看到“幅度不够”，动作不要太快，否则会先判 TOO_FAST
    "min_action_time": 1.2,

    "min_count_interval": 0.45,
    "target_hold_frames": 1,
    "finish_hold_frames": 1,
    "metric_history_len": 3,
    "landmark_smooth_alpha": 0.50,
    "tip_hold_time": 1.5,

    # 放宽甩动，避免先被 SWING 抢走
    "swing_gyro_threshold": 1000.0,
    **load_action_imu_thresholds("CURL"),
}


if __name__ == "__main__":
    run_repetition_action(config)

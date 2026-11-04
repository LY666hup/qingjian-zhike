from ai_fitness_common import run_repetition_action, angle_3points, get_xy
from ai_fitness_imu_validation import load_action_imu_thresholds


def compute_metric(landmarks):
    # 俯身划船：建议侧身或45度面对摄像头
    # 主要看肘关节角度：手臂下放时角度大，拉起时角度变小

    left_shoulder = get_xy(landmarks, 11)
    left_elbow = get_xy(landmarks, 13)
    left_wrist = get_xy(landmarks, 15)

    right_shoulder = get_xy(landmarks, 12)
    right_elbow = get_xy(landmarks, 14)
    right_wrist = get_xy(landmarks, 16)

    left_angle = angle_3points(left_shoulder, left_elbow, left_wrist)
    right_angle = angle_3points(right_shoulder, right_elbow, right_wrist)

    # 默认取更小的肘角，左右手都能识别；如果只想识别右手，改成 right_angle
    elbow_angle = min(left_angle, right_angle)

    aux = f"L:{left_angle:.1f} R:{right_angle:.1f}"
    return elbow_angle, aux


config = {
    "mode": "ROW",
    "window_name": "AI Fitness Coach - Bent Over Row",
    "metric_name": "Elbow",
    "compute_metric": compute_metric,

    # 俯身划船拉起时肘角变小
    # 只要小于130度，就认为进入一次划船动作
    "target_op": "<",
    "target_threshold": 130,

    # 手臂重新下放到接近伸直，完成一次计数
    "finish_op": ">",
    "finish_threshold": 150,

    # 真正拉到110度以下，才算幅度标准
    # 如果只拉到115~130度，会计数但提示 RANGE_SMALL
    "range_type": "min",
    "range_threshold": 110,

    # 想测试幅度不够，动作别太快，否则会先提示 TOO_FAST
    "min_action_time": 1.20,
    "min_count_interval": 0.50,

    "target_hold_frames": 1,
    "finish_hold_frames": 1,
    "metric_history_len": 3,
    "landmark_smooth_alpha": 0.55,
    "tip_hold_time": 1.5,

    # 先放宽甩动，避免正常演示被误判
    "swing_gyro_threshold": 1500.0,
    **load_action_imu_thresholds("BENT_OVER_ROW"),
}


if __name__ == "__main__":
    run_repetition_action(config)

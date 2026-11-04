from ai_fitness_common import run_repetition_action, angle_3points, get_xy
from ai_fitness_imu_validation import load_action_imu_thresholds


def compute_metric(landmarks):
    # 过头臂屈伸：建议侧身或45度面对摄像头
    # 主要看肘关节角度：屈肘时角度小，伸直时角度大

    left_shoulder = get_xy(landmarks, 11)
    left_elbow = get_xy(landmarks, 13)
    left_wrist = get_xy(landmarks, 15)

    right_shoulder = get_xy(landmarks, 12)
    right_elbow = get_xy(landmarks, 14)
    right_wrist = get_xy(landmarks, 16)

    left_angle = angle_3points(left_shoulder, left_elbow, left_wrist)
    right_angle = angle_3points(right_shoulder, right_elbow, right_wrist)

    # 过头臂屈伸一般双手一起做，取更大的肘角，方便识别伸直动作
    # 如果只用右手演示，可以改成 right_angle
    elbow_angle = max(left_angle, right_angle)

    aux = f"L:{left_angle:.1f} R:{right_angle:.1f}"
    return elbow_angle, aux


config = {
    "mode": "TRICEPS",
    "window_name": "AI Fitness Coach - Overhead Triceps Extension",
    "metric_name": "Elbow",
    "compute_metric": compute_metric,

    # 过头臂屈伸伸直时肘角变大
    # 超过135度，认为进入伸直阶段
    "target_op": ">",
    "target_threshold": 135,

    # 重新屈肘到120度以下，完成一次计数
    "finish_op": "<",
    "finish_threshold": 120,

    # 真正伸到155度以上才算幅度标准
    # 如果只伸到135~150度，会计数但提示 RANGE_SMALL
    "range_type": "max",
    "range_threshold": 155,

    "min_action_time": 1.20,
    "min_count_interval": 0.50,

    "target_hold_frames": 1,
    "finish_hold_frames": 1,
    "metric_history_len": 3,
    "landmark_smooth_alpha": 0.55,
    "tip_hold_time": 1.5,

    # 先放宽甩动，避免正常演示被误判
    "swing_gyro_threshold": 1500.0,
    **load_action_imu_thresholds("OVERHEAD_TRICEPS"),
}


if __name__ == "__main__":
    run_repetition_action(config)

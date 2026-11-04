from ai_fitness_common import run_repetition_action, angle_3points, get_xy


def compute_metric(landmarks):
    left_shoulder = get_xy(landmarks, 11)
    left_hip = get_xy(landmarks, 23)
    left_knee = get_xy(landmarks, 25)

    right_shoulder = get_xy(landmarks, 12)
    right_hip = get_xy(landmarks, 24)
    right_knee = get_xy(landmarks, 26)

    left_angle = angle_3points(left_shoulder, left_hip, left_knee)
    right_angle = angle_3points(right_shoulder, right_hip, right_knee)

    # 坐起来时髋部夹角变小
    torso_angle = min(left_angle, right_angle)
    aux = f"L:{left_angle:.1f} R:{right_angle:.1f}"
    return torso_angle, aux


config = {
    "mode": "SITUP",
    "window_name": "AI Fitness Coach - Sit Up",
    "metric_name": "Torso",
    "compute_metric": compute_metric,

    # 坐起：髋角变小
    "target_op": "<",
    "target_threshold": 120,

    # 躺回：髋角变大
    "finish_op": ">",
    "finish_threshold": 150,

    "range_type": "min",
    "range_threshold": 130,

    "min_action_time": 0.7,
    "min_count_interval": 1.6,
    "target_hold_frames": 3,
    "finish_hold_frames": 4,
    "tip_hold_time": 1.5,
    "swing_gyro_threshold": 9.0,
}


if __name__ == "__main__":
    run_repetition_action(config)

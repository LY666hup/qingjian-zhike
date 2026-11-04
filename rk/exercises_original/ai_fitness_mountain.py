from ai_fitness_common import run_repetition_action, get_xy, dist


def compute_metric(landmarks):
    left_shoulder = get_xy(landmarks, 11)
    left_hip = get_xy(landmarks, 23)
    left_knee = get_xy(landmarks, 25)

    right_shoulder = get_xy(landmarks, 12)
    right_hip = get_xy(landmarks, 24)
    right_knee = get_xy(landmarks, 26)

    # 简化版登山跑：看膝盖是否明显靠近胸口/肩膀区域
    # 距离越小，说明膝盖越靠前
    left_d = dist(left_knee, left_shoulder)
    right_d = dist(right_knee, right_shoulder)

    # 转成“靠近程度”：越大越靠近
    close_value = 1.0 - min(left_d, right_d)

    aux = f"Ld:{left_d:.2f} Rd:{right_d:.2f}"
    return close_value, aux


config = {
    "mode": "MOUNTAIN",
    "window_name": "AI Fitness Coach - Mountain Climber",
    "metric_name": "KneeClose",
    "compute_metric": compute_metric,

    # 膝盖靠近身体：close_value 变大
    "target_op": ">",
    "target_threshold": 0.55,

    # 膝盖伸回去：close_value 变小
    "finish_op": "<",
    "finish_threshold": 0.45,

    "range_type": "max",
    "range_threshold": 0.50,

    "min_action_time": 0.25,
    "min_count_interval": 0.5,
    "target_hold_frames": 1,
    "finish_hold_frames": 2,
    "tip_hold_time": 0.8,
    "swing_gyro_threshold": 20.0,
}


if __name__ == "__main__":
    run_repetition_action(config)

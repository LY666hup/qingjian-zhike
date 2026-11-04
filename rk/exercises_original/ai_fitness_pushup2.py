from ai_fitness_common import run_repetition_action, angle_3points, get_xy
from ai_fitness_imu_validation import load_action_imu_thresholds
from pose_metrics.comparison import JointAngleComparisonEstimator


def create_metric_comparison(**options):
    return JointAngleComparisonEstimator(
        (11, 13, 15), (12, 14, 16), **options
    )


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

    # 俯卧撑下降时肘角变小，取较小一侧更敏感
    elbow_angle = min(left_angle, right_angle)

    aux = f"L:{left_angle:.1f} R:{right_angle:.1f}"
    return elbow_angle, aux


config = {
    "mode": "PUSHUP",
    "window_name": "AI Fitness Coach - Push Up",
    "metric_name": "Elbow",
    "compute_metric": compute_metric,
    "metric_comparison_factory": create_metric_comparison,

    # 俯卧撑也是“角度越小，下降越深”
    # 下降到 135° 以下就认为进入一次动作
    "target_op": "<",
    "target_threshold": 135,

    # 推回接近伸直才算完成一次
    "finish_op": ">",
    "finish_threshold": 155,

    # 下降到 110° 以下才算幅度标准
    # 只下降到 120°~135°，会计数但提示 RANGE_SMALL
    "range_type": "min",
    "range_threshold": 110,

    "min_action_time": 1.20,
    "min_count_interval": 0.8,
    "target_hold_frames": 1,
    "finish_hold_frames": 1,
    "metric_history_len": 3,
    "landmark_smooth_alpha": 0.55,
    "tip_hold_time": 1.5,

    # 放宽甩动，避免正常俯卧撑被误判 SWING
    "swing_gyro_threshold": 1500.0,
    **load_action_imu_thresholds("PUSHUP"),

    # 3D world 影子计数器（诊断用，不影响正式计数/评分/协议）
    # 注意：俯卧撑 3D 阈值未校准，需根据实际 3DF 数据调整
    "shadow_3d": {
        "target_threshold_3d": 110,   # 3D肘角<110°视为下降到位（待校准）
        "finish_threshold_3d": 155,   # 3D肘角>155°视为推起恢复（待校准）
        "target_hold_frames": 1,
        "finish_hold_frames": 1,
        "min_count_interval": 0.8,
        "label": "3DS_PUSHUP",
        "debug": False,
    },
}


if __name__ == "__main__":
    run_repetition_action(config)

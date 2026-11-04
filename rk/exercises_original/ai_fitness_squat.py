from ai_fitness_common import run_repetition_action, angle_3points, get_xy
from ai_fitness_imu_validation import load_action_imu_thresholds
from pose_metrics.comparison import JointAngleComparisonEstimator


def create_metric_comparison(**options):
    return JointAngleComparisonEstimator(
        (23, 25, 27), (24, 26, 28), **options
    )


def compute_metric(landmarks):
    left_hip = get_xy(landmarks, 23)
    left_knee = get_xy(landmarks, 25)
    left_ankle = get_xy(landmarks, 27)

    right_hip = get_xy(landmarks, 24)
    right_knee = get_xy(landmarks, 26)
    right_ankle = get_xy(landmarks, 28)

    left_knee_angle = angle_3points(left_hip, left_knee, left_ankle)
    right_knee_angle = angle_3points(right_hip, right_knee, right_ankle)

    # 取较小一侧，蹲下时膝角会变小
    knee_angle = min(left_knee_angle, right_knee_angle)
    aux = f"L:{left_knee_angle:.1f} R:{right_knee_angle:.1f}"
    return knee_angle, aux


config = {
    "mode": "SQUAT",
    "window_name": "AI Fitness Coach - Squat",
    "metric_name": "Knee",
    "compute_metric": compute_metric,
    "metric_comparison_factory": create_metric_comparison,

    # 下蹲：膝角变小
    "target_op": "<",
    "target_threshold": 145,

    # 站起：膝角变大
    "finish_op": ">",
    "finish_threshold": 160,

    "range_type": "min",
    "range_threshold": 115,

    "min_action_time": 2.5,
    "min_count_interval": 1.8,
    "target_hold_frames": 3,
    "finish_hold_frames": 4,
    "tip_hold_time": 1.5,
    "swing_gyro_threshold": 999999,
    **load_action_imu_thresholds("SQUAT"),

    # 3D world 影子计数器（诊断用，不影响正式计数/评分/协议）
    "shadow_3d": {
        "target_threshold_3d": 105,   # 3D膝角<105°视为下蹲到位
        "finish_threshold_3d": 135,   # 3D膝角>135°视为站立恢复
        "target_hold_frames": 3,
        "finish_hold_frames": 4,
        "min_count_interval": 1.5,
        "label": "3DS_SQUAT",
        "debug": False,
    },
}


if __name__ == "__main__":
    run_repetition_action(config)

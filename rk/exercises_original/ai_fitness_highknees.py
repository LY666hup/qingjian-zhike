from ai_fitness_common import run_repetition_action, get_xy
import math


def dist(p1, p2):
    return math.hypot(p1[0] - p2[0], p1[1] - p2[1])


def compute_metric(landmarks):
    # 高抬腿快速跟随版：
    # 不看关节夹角，直接看“膝盖相对髋部抬高了多少”
    # y坐标越小越靠上，所以 hip_y - knee_y 越大，膝盖抬得越高

    left_shoulder = get_xy(landmarks, 11)
    right_shoulder = get_xy(landmarks, 12)
    left_hip = get_xy(landmarks, 23)
    right_hip = get_xy(landmarks, 24)
    left_knee = get_xy(landmarks, 25)
    right_knee = get_xy(landmarks, 26)

    shoulder_mid = (
        (left_shoulder[0] + right_shoulder[0]) / 2,
        (left_shoulder[1] + right_shoulder[1]) / 2,
    )
    hip_mid = (
        (left_hip[0] + right_hip[0]) / 2,
        (left_hip[1] + right_hip[1]) / 2,
    )

    # 用躯干长度归一化，避免人离摄像头远近影响判断
    torso_len = max(dist(shoulder_mid, hip_mid), 0.08)

    left_lift = (left_hip[1] - left_knee[1]) / torso_len * 100.0
    right_lift = (right_hip[1] - right_knee[1]) / torso_len * 100.0

    # 任意一边膝盖抬高都算一次高抬腿
    lift_metric = max(left_lift, right_lift)

    aux = f"L:{left_lift:.1f} R:{right_lift:.1f}"
    return lift_metric, aux


config = {
    "mode": "HIGHKNEE",
    "window_name": "AI Fitness Coach - High Knees Fast",
    "metric_name": "KneeLift",
    "compute_metric": compute_metric,

    # 高抬腿：metric越大，膝盖抬得越高
    # 抬到 22 以上就认为进入一次动作
    "target_op": ">",
    "target_threshold": 22,

    # 放下来到 10 以下，就马上认为动作结束
    # 这个值越高，越容易跟上“放下来了”
    "finish_op": "<",
    "finish_threshold": 10,

    # 抬到 38 以上才算幅度标准
    # 只抬一点点会提示 RANGE_SMALL
    "range_type": "max",
    "range_threshold": 38,

    # 高抬腿本来就是快动作，不要太容易报 TOO_FAST
    "min_action_time": 0.10,
    "min_count_interval": 0.18,

    # 快速跟随核心：只要1帧满足就切状态，减少“放下来了还没跟上”的延迟
    "target_hold_frames": 1,
    "finish_hold_frames": 1,

    # 快速跟随核心：几乎不做角度历史平滑
    "metric_history_len": 1,

    # 快速跟随核心：提高关键点跟随速度
    # 数值越大越跟手，但画面抖动也会更明显；0.85适合高抬腿
    "landmark_smooth_alpha": 0.85,

    "tip_hold_time": 0.8,

    # 高抬腿时手臂自然摆动，手环很容易误判SWING
    # 所以高抬腿里关闭SWING，不改common，不影响其他动作
    "swing_gyro_threshold": 999999.0,
}


if __name__ == "__main__":
    run_repetition_action(config)

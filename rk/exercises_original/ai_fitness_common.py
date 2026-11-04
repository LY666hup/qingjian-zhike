from ai_fitness_env import load_project_env

load_project_env()

import cv2
import mediapipe as mp
import numpy as np
import time
import os
import logging
import serial
import threading
import subprocess
import queue
from collections import deque
from dataclasses import dataclass
from pathlib import Path

from fitness_voice_control import CONTINUE, QUIT, STOP
from ai_fitness_display import DisplaySession
from ai_fitness_heart_rate import (
    HEART_RATE_STATE,
    format_heart_rate_command,
    parse_heart_rate_sample,
    pending_heart_rate_sample,
)
from ai_fitness_imu_validation import (
    IMU_STATE,
    IMU_VALIDATION_CONFIG,
    validate_imu_window,
    parse_imu_sample,
)
from pose_metrics.comparison import (
    append_comparison_text,
    create_metric_comparison_estimator,
    first_world_landmarks,
    pose_metric_debug_enabled,
)
from pose_metrics.types import PoseObservation


try:
    import pyttsx3
except Exception:
    pyttsx3 = None

try:
    from openai import OpenAI
except Exception:
    OpenAI = None


# ==================================================
# 基本配置：明天如果 IP 或端口变了，主要改这里
# ==================================================

STREAM_URL = os.getenv("FITNESS_STREAM_URL", "http://10.174.3.138/stream")

def _resolve_feedback_port():
    """返回反馈串口路径：有效的环境变量优先，否则自动发现。"""
    env = os.getenv("FITNESS_FEEDBACK_PORT", "").strip()
    if env:
        # Windows COM 名不是文件路径；Linux 路径必须实际存在，避免旧环境变量
        # 永久覆盖自动发现结果。
        if env.upper().startswith("COM") or os.path.exists(env):
            return env
        print(
            f"FITNESS_FEEDBACK_PORT 指向不存在的设备：{env}，"
            "将自动查找反馈串口。"
        )
    # 自动扫描 /dev/serial/by-id/、/dev/ttyUSB*、/dev/ttyACM*
    by_id_dir = Path("/dev/serial/by-id")
    if by_id_dir.is_dir():
        try:
            for entry in sorted(by_id_dir.iterdir()):
                return str(entry.resolve())
        except OSError:
            pass
    for pattern in ("/dev/ttyUSB*", "/dev/ttyACM*"):
        try:
            for path in sorted(Path("/dev").glob(pattern.split("/")[-1])):
                return str(path)
        except OSError:
            pass
    return "/dev/ttyUSB0"  # 兜底，让错误信息更清晰

SERIAL_PORT = _resolve_feedback_port()
# AI_IMU_BAND 使用经典蓝牙 SPP，经系统绑定后对应 /dev/rfcomm0。
IMU_SERIAL_PORT = os.getenv("FITNESS_IMU_PORT", "/dev/rfcomm0")
IMU_BAND_ADDRESS = os.getenv("FITNESS_IMU_BAND_ADDRESS", "B4:BF:E9:33:65:EA")
IMU_BAND_CHANNEL = int(os.getenv("FITNESS_IMU_BAND_CHANNEL", "1"))
IMU_REPAIR_ON_START = os.getenv(
    "FITNESS_IMU_REPAIR_ON_START", "1"
).strip().lower() in {"1", "true", "yes", "on"}
BAUD = int(os.getenv("FITNESS_BAUD", "115200"))
CAMERA_OPEN_RETRIES = max(1, int(os.getenv("FITNESS_CAMERA_OPEN_RETRIES", "8")))
CAMERA_RETRY_DELAY = max(0.0, float(os.getenv("FITNESS_CAMERA_RETRY_DELAY", "2")))
CAMERA_MAX_RETRY_DELAY = max(
    CAMERA_RETRY_DELAY,
    float(os.getenv("FITNESS_CAMERA_MAX_RETRY_DELAY", "12")),
)
CAMERA_OPEN_TIMEOUT_MSEC = max(
    1000,
    int(os.getenv("FITNESS_CAMERA_OPEN_TIMEOUT_MSEC", "8000")),
)
# FitnessSession uses a short first-attempt timeout so START cannot be held up
# for the legacy 8-second camera-open window.  If the first attempt fails, the
# LatestFrameCamera worker continues reconnecting in the background.
CAMERA_INITIAL_OPEN_TIMEOUT_MSEC = max(
    500,
    int(os.getenv("FITNESS_CAMERA_INITIAL_OPEN_TIMEOUT_MSEC", "1800")),
)
CAMERA_RECONNECT_OPEN_TIMEOUT_MSEC = max(
    500,
    int(os.getenv("FITNESS_CAMERA_RECONNECT_OPEN_TIMEOUT_MSEC", "1800")),
)
CAMERA_READ_TIMEOUT_MSEC = max(
    500,
    int(os.getenv("FITNESS_CAMERA_READ_TIMEOUT_MSEC", "1500")),
)
CAMERA_FRAME_WAIT_SEC = max(
    0.02,
    float(os.getenv("FITNESS_CAMERA_FRAME_WAIT_SEC", "0.12")),
)
CAMERA_RECONNECT_IDLE_SEC = max(
    0.0,
    float(os.getenv("FITNESS_CAMERA_RECONNECT_IDLE_SEC", "0.25")),
)
CAMERA_SLOW_READ_WARN_MSEC = max(
    0.0,
    float(os.getenv("FITNESS_CAMERA_SLOW_READ_WARN_MSEC", "250")),
)
CAMERA_CLOSE_TIMEOUT_SEC = max(
    1.0,
    float(os.getenv("FITNESS_CAMERA_CLOSE_TIMEOUT_SEC", "4.0")),
)
POSE_SLOW_WARN_MSEC = max(
    0.0,
    float(os.getenv("FITNESS_POSE_SLOW_WARN_MSEC", "180")),
)

# 蓝牙 IMU 调试开关：True 时控制台会每隔约1秒打印一次 IMU 数据
IMU_DEBUG_PRINT = True

# 反馈屏串口调试开关：True 时每次给屏幕发命令都会打印，方便排查屏幕不动
FEEDBACK_DEBUG_PRINT = True

# 摄像头画面颜色修正：
# 如果 Python 窗口看起来偏灰/发白，打开 COLOR_BOOST 会轻微增加饱和度。
COLOR_BOOST = True
COLOR_SATURATION_GAIN = 1.35

# 模型文件：默认使用 full；如果没有 full，就自动回退到 lite。
# 相对路径始终相对本文件目录，避免从其他工作目录启动时找不到模型。
def resolve_model_path(environ=None, source_dir=None):
    environ = os.environ if environ is None else environ
    source_dir = (
        Path(__file__).resolve().parent
        if source_dir is None
        else Path(source_dir).resolve()
    )
    override = environ.get("FITNESS_MODEL_PATH", "").strip()
    if override:
        path = Path(override).expanduser()
        if not path.is_absolute():
            path = source_dir / path
        return path.resolve()

    full_path = source_dir / "pose_landmarker_full.task"
    if full_path.is_file():
        return full_path
    return source_dir / "pose_landmarker_lite.task"


MODEL_PATH = str(resolve_model_path())

print("MediaPipe模型文件：", MODEL_PATH)

# 识别稳定性设置
# 数值越大，骨架越稳，但动作会稍微有一点延迟；0.60~0.80 都可以。
LANDMARK_SMOOTH_ALPHA = 0.68

# 推理频率；LatestFrameCamera 已负责在性能不足时自然跳过中间帧，
# 正式统一会话默认保持 1，避免再固定抽帧而降低快速动作采样率。
PROCESS_EVERY_N_FRAMES = max(
    1, int(os.getenv("FITNESS_PROCESS_EVERY_N_FRAMES", "1"))
)

# 当前动作实际使用的平滑系数，由每个动作 config 单独设置；默认等于 LANDMARK_SMOOTH_ALPHA
current_landmark_smooth_alpha = LANDMARK_SMOOTH_ALPHA


# ==================================================
# MediaPipe 配置
# ==================================================

BaseOptions = mp.tasks.BaseOptions
PoseLandmarker = mp.tasks.vision.PoseLandmarker
PoseLandmarkerOptions = mp.tasks.vision.PoseLandmarkerOptions
VisionRunningMode = mp.tasks.vision.RunningMode

POSE_CONNECTIONS = [
    (11, 12),
    (11, 13), (13, 15),
    (12, 14), (14, 16),
    (11, 23), (12, 24),
    (23, 24),
    (23, 25), (25, 27),
    (24, 26), (26, 28),
    (27, 31), (28, 32)
]


# ==================================================
# 可选：电脑语音 / AI 教练模块
# ==================================================
# 默认 False：不会调用 API，不会花钱。
# 想接入大模型时，把它改成 True，并在电脑环境变量里设置 OPENAI_API_KEY。
USE_AI_COACH = False

# 大模型名称：如果你账号不可用，就改成你平台里能用的模型。
AI_MODEL = "gpt-5.4-mini"

# 离线固定语音：不花 API 钱，只需要 pyttsx3。
USE_OFFLINE_SPEECH = True

LOGGER = logging.getLogger(__name__)

speech_queue = queue.Queue()
speech_thread_started = False
speech_lock = threading.Lock()
ai_client = None

last_speak_text = ""
last_speak_time = 0
last_ai_tip = ""
last_ai_count = -1
last_ai_time = 0

BASIC_TIP_TEXT = {
    "READY": "准备开始",
    "GOOD": "动作标准，继续保持",
    "TOO_FAST": "动作太快了，慢一点",
    "RANGE_SMALL": "幅度不够，再做到位一点",
    "SWING": "不要借力甩动",
    "NO_PERSON": "没有识别到人，请站到画面中间",
    "UP": "保持动作",
    "BAD_FORM": "姿势不标准，请调整身体",
    "NOT_EXTEND": "手臂没有完全伸展"
}

ACTION_NAME_CN = {
    "CURL": "二头弯举",
    "LATERAL": "侧平举",
    "FRONT": "前平举",
    "PRESS": "推举",
    "SQUAT": "深蹲",
    "PUSHUP": "俯卧撑",
    "SITUP": "仰卧起坐",
    "MOUNTAIN": "登山跑",
    "HIGHKNEES": "高抬腿",
    "PLANK": "平板支撑",
    "ROW": "俯身划船",
    "TRICEPS": "臂屈伸"
}


def get_action_name_cn(mode):
    return ACTION_NAME_CN.get(mode, mode)


def generate_and_speak_ai_summary(mode, count, score, action_stats):
    """
    按 A 键触发：
    把本组动作统计发给云端大模型，生成一句 AI 教练总结，并用电脑人声播报。
    """
    if count <= 0:
        speak_text("还没有完成动作，先做一组再总结。")
        return

    try:
        from ai_coach_cloud import get_ai_group_summary
    except Exception as e:
        print("没有找到 ai_coach_cloud.py 或导入失败：", e)
        speak_text("AI教练文件没有找到。")
        return

    action_name = get_action_name_cn(mode)
    good_count = action_stats.get("GOOD", 0)
    too_fast_count = action_stats.get("TOO_FAST", 0)
    range_small_count = action_stats.get("RANGE_SMALL", 0)
    swing_count = action_stats.get("SWING", 0)

    print("正在生成AI教练总结，请稍等...")
    speak_text("正在生成AI教练总结。")

    try:
        reply = get_ai_group_summary(
            action_name=action_name,
            total_count=count,
            good_count=good_count,
            too_fast_count=too_fast_count,
            range_small_count=range_small_count,
            swing_count=swing_count,
            score=score
        )

        print("AI教练总结：", reply)
        speak_text(reply)

    except Exception as e:
        print("云端AI总结失败：", e)
        speak_text("AI教练暂时连接失败，请继续保持动作标准。")




def init_speech_system():
    """
    语音初始化检查。
    现在不再用长期后台队列线程，避免 pyttsx3 队列卡死。
    每次播报时会单独开一个短线程，新建引擎播报。
    """
    global speech_thread_started

    if speech_thread_started:
        return

    if not USE_OFFLINE_SPEECH:
        print("电脑人声关闭：USE_OFFLINE_SPEECH = False")
        return

    if pyttsx3 is None:
        print("未安装 pyttsx3，跳过电脑语音。可运行：pip install pyttsx3")
        return

    speech_thread_started = True



def init_ai_client():
    global ai_client

    init_speech_system()
    speak_text("ai健身教练启动")

    if not USE_AI_COACH:
        return

    if OpenAI is None:
        print("未安装 openai，AI 大模型语音关闭。可运行：pip install openai")
        ai_client = None
        return

    try:
        ai_client = OpenAI()
        print("AI 大模型客户端初始化成功")
    except Exception as e:
        ai_client = None
        print("AI 大模型初始化失败，改用固定语音：", e)


def speak_text(text):
    """
    电脑人声播报。
    修正版：每一句都单独开短线程并新建 pyttsx3 引擎，避免只播“语音系统启动”后就不说话。
    """
    if not text:
        return

    if not USE_OFFLINE_SPEECH:
        print("语音未开启：USE_OFFLINE_SPEECH = False")
        return

    if pyttsx3 is None:
        print("语音库 pyttsx3 未安装，请运行：pip install pyttsx3")
        return

    def worker(sentence):
        with speech_lock:
            try:
                engine = pyttsx3.init()
                engine.setProperty("rate", 210)
                engine.setProperty("volume", 1.0)
                engine.say(sentence)
                engine.runAndWait()
                engine.stop()
            except Exception as e:
                print("电脑人声播报失败：", e)

    threading.Thread(target=worker, args=(text,), daemon=True).start()



def speak_count_tip(count, tip):
    """
    强制人声报数：
    只要 Python 端 count 增加，就直接播报。
    """
    if count <= 0:
        return

    if tip == "GOOD":
        sentence = f"第{count}次，标准，请保持"
    elif tip == "TOO_FAST":
        sentence = f"第{count}次，太快，放慢一点"
    elif tip == "RANGE_SMALL":
        sentence = f"第{count}次，幅度不够，收紧二头"
    elif tip == "SWING":
        sentence = f"第{count}次，不要甩，收紧核心"
    else:
        text = BASIC_TIP_TEXT.get(tip, tip)
        sentence = f"第{count}次，{text}"

    print("人声报数：", sentence)
    speak_text(sentence)



def get_ai_coach_sentence(mode, count, score, tip):
    fallback = BASIC_TIP_TEXT.get(tip, tip)

    # 离线固定语音：读出次数，更像人声教练
    if count > 0 and tip in ["GOOD", "TOO_FAST", "RANGE_SMALL", "SWING", "BAD_FORM", "NOT_EXTEND"]:
        fallback = f"第{count}次，{fallback}"

    if not USE_AI_COACH or ai_client is None:
        return fallback

    prompt = f"""
你是一名中文AI健身教练。
当前动作模式：{mode}
当前次数：{count}
当前得分：{score}
系统检测结果：{tip}

请只输出一句简短中文提示，20字以内。
要求：
1. 像真人教练一样自然
2. 不要解释原理
3. 不要加引号
4. 不要输出多句话
"""

    try:
        response = ai_client.responses.create(
            model=AI_MODEL,
            input=prompt
        )
        text = response.output_text.strip()
        if len(text) > 40:
            text = text[:40]
        return text
    except Exception as e:
        print("AI 生成失败，使用固定语音：", e)
        return fallback


def ai_speak_tip(mode, count, score, tip):
    """
    语音播报函数：
    - 默认不调用大模型，只用本地 pyttsx3 人声，不花 token。
    - 每次 count 变化都会播报。
    - 同一个提示会做简单防重复。
    """
    global last_ai_tip, last_ai_count, last_ai_time

    now = time.time()

    # READY 和 UP 太频繁，默认不播，避免一直说话
    if tip in ["READY", "UP"]:
        return

    # 同一个动作、同一个次数、同一个提示，短时间内不重复播
    if tip == last_ai_tip and count == last_ai_count and now - last_ai_time < 5:
        return

    # 普通提示至少间隔 0.8 秒
    if now - last_ai_time < 0.8:
        return

    last_ai_tip = tip
    last_ai_count = count
    last_ai_time = now

    # 先用固定语音，稳定、不花钱
    sentence = get_ai_coach_sentence(mode, count, score, tip)

    print("语音播报：", sentence)
    speak_text(sentence)



def create_landmarker(model_path=None):
    # VIDEO 模式会利用前后帧跟踪，比 IMAGE 每帧重新检测更不容易跳
    model_path = Path(MODEL_PATH if model_path is None else model_path)
    if not model_path.is_file():
        raise FileNotFoundError(f"MediaPipe模型文件不存在：{model_path}")
    options = PoseLandmarkerOptions(
        base_options=BaseOptions(model_asset_path=str(model_path)),
        running_mode=VisionRunningMode.VIDEO,
        num_poses=1,
        min_pose_detection_confidence=0.25,
        min_pose_presence_confidence=0.25,
        min_tracking_confidence=0.25
    )
    return PoseLandmarker.create_from_options(options)


def open_camera_with_retries(
    stream_url=STREAM_URL,
    retries=CAMERA_OPEN_RETRIES,
    retry_delay=CAMERA_RETRY_DELAY,
    *,
    open_timeout_msec=None,
    read_timeout_msec=None,
):
    """启动时多次尝试打开摄像头视频流。

    ``open_timeout_msec`` 可用于后台重连时采用更短的连接超时，避免
    STOP 后仍残留一个长时间阻塞在 ``VideoCapture.open`` 的旧线程。
    """
    resolved_open_timeout = (
        CAMERA_OPEN_TIMEOUT_MSEC
        if open_timeout_msec is None
        else max(500, int(open_timeout_msec))
    )
    resolved_read_timeout = (
        CAMERA_READ_TIMEOUT_MSEC
        if read_timeout_msec is None
        else max(500, int(read_timeout_msec))
    )
    for attempt in range(1, retries + 1):
        print(f"正在连接摄像头：{stream_url}（第 {attempt}/{retries} 次）")
        cap = cv2.VideoCapture()

        try:
            # 打开/读取超时需要在 open 时传入，避免摄像头未上线时单次连接等待太久。
            cap.open(
                stream_url,
                cv2.CAP_ANY,
                [
                    cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, resolved_open_timeout,
                    cv2.CAP_PROP_READ_TIMEOUT_MSEC, resolved_read_timeout,
                ],
            )
        except (TypeError, cv2.error):
            # 兼容不支持 open 参数列表的旧版 OpenCV。
            cap.open(stream_url)

        if cap.isOpened():
            buffer_size_ok = cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            convert_rgb_ok = cap.set(cv2.CAP_PROP_CONVERT_RGB, 1)
            print(
                "摄像头打开成功 "
                f"(buffer=1 {'OK' if buffer_size_ok else 'backend未确认'}, "
                f"convert_rgb {'OK' if convert_rgb_ok else 'backend未确认'})"
            )
            return cap

        cap.release()

        if attempt < retries:
            delay = min(retry_delay * (2 ** (attempt - 1)), CAMERA_MAX_RETRY_DELAY)
            print(f"连接失败，{delay:g} 秒后重试……")
            time.sleep(delay)

    print(f"连续尝试 {retries} 次仍无法连接摄像头，请检查摄像头供电、IP地址和 STREAM_URL。")
    return None


def reconnect_camera(cap, stream_url=STREAM_URL):
    """释放失效的视频流，并重新建立连接。"""
    print("摄像头视频流已断开，正在重新连接……")
    if cap is not None:
        cap.release()
    return open_camera_with_retries(stream_url)


# ==================================================
# 人体关键点平滑：解决骨架“一跳一跳”
# ==================================================

class SimpleLandmark:
    def __init__(self, x, y, z=0.0, visibility=0.0, presence=0.0):
        self.x = x
        self.y = y
        self.z = z
        self.visibility = visibility
        self.presence = presence


smoothed_landmarks_cache = None


def reset_pose_smoothing():
    global smoothed_landmarks_cache
    smoothed_landmarks_cache = None


def smooth_pose_landmarks(landmarks):
    """
    对 MediaPipe 输出的人体关键点做低通滤波。
    作用：
    - 骨架显示更稳
    - 角度计算更稳
    - 计数不容易因为关键点乱跳误判
    """
    global smoothed_landmarks_cache

    if landmarks is None:
        reset_pose_smoothing()
        return None

    if smoothed_landmarks_cache is None or len(smoothed_landmarks_cache) != len(landmarks):
        smoothed_landmarks_cache = [
            SimpleLandmark(
                lm.x,
                lm.y,
                getattr(lm, "z", 0.0),
                getattr(lm, "visibility", 0.0),
                getattr(lm, "presence", 0.0)
            )
            for lm in landmarks
        ]
        return smoothed_landmarks_cache

    a = current_landmark_smooth_alpha

    for i, lm in enumerate(landmarks):
        old = smoothed_landmarks_cache[i]
        old.x = a * old.x + (1 - a) * lm.x
        old.y = a * old.y + (1 - a) * lm.y
        old.z = a * old.z + (1 - a) * getattr(lm, "z", 0.0)
        old.visibility = getattr(lm, "visibility", 0.0)
        old.presence = getattr(lm, "presence", 0.0)

    return smoothed_landmarks_cache


def angle_3points(a, b, c):
    a = np.array(a)
    b = np.array(b)
    c = np.array(c)

    ba = a - b
    bc = c - b

    denominator = (np.linalg.norm(ba) * np.linalg.norm(bc)) + 1e-6
    cos_angle = np.dot(ba, bc) / denominator
    cos_angle = np.clip(cos_angle, -1.0, 1.0)

    return np.degrees(np.arccos(cos_angle))


def dist(a, b):
    a = np.array(a)
    b = np.array(b)
    return float(np.linalg.norm(a - b))


def get_xy(landmarks, index):
    lm = landmarks[index]
    return np.array([lm.x, lm.y])



def normalize_frame_channels(frame):
    """Normalize camera frames to 3-channel BGR without aesthetic processing."""
    if frame is None:
        return frame

    if len(frame.shape) == 2:
        return cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR)
    if len(frame.shape) == 3 and frame.shape[2] == 4:
        return cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR)
    return frame


def enhance_frame_for_display(frame):
    """Apply optional display-only saturation boost."""
    if frame is None or not COLOR_BOOST:
        return frame
    if len(frame.shape) != 3 or frame.shape[2] != 3:
        return frame
    try:
        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV).astype(np.float32)
        hsv[:, :, 1] = np.clip(
            hsv[:, :, 1] * COLOR_SATURATION_GAIN, 0, 255
        )
        return cv2.cvtColor(hsv.astype(np.uint8), cv2.COLOR_HSV2BGR)
    except Exception:
        return frame


def fix_frame_color(frame):
    """Backward-compatible channel normalization plus display enhancement."""
    return enhance_frame_for_display(normalize_frame_channels(frame))


def draw_pose(frame, landmarks):
    h, w, _ = frame.shape

    for lm in landmarks:
        x = int(lm.x * w)
        y = int(lm.y * h)
        if 0 <= x < w and 0 <= y < h:
            cv2.circle(frame, (x, y), 4, (0, 255, 0), -1)

    for a, b in POSE_CONNECTIONS:
        if a < len(landmarks) and b < len(landmarks):
            x1 = int(landmarks[a].x * w)
            y1 = int(landmarks[a].y * h)
            x2 = int(landmarks[b].x * w)
            y2 = int(landmarks[b].y * h)
            cv2.line(frame, (x1, y1), (x2, y2), (255, 255, 0), 2)


def _discover_serial_ports():
    """
    按优先级列出候选串口：
    1. 环境变量 FITNESS_FEEDBACK_PORT（精确设备路径）
    2. /dev/serial/by-id/ 下所有设备
    3. /dev/ttyUSB*（CP210x 等常见 USB 转串口芯片）
    4. /dev/ttyACM*（部分 ESP32 开发板的 USB CDC）
    5. Windows COM 端口（由环境变量覆盖）
    """
    ports = []

    # 如果用户明确指定了路径，始终排在第一位
    env_port = os.getenv("FITNESS_FEEDBACK_PORT", "").strip()
    if env_port:
        ports.append(env_port)

    # /dev/serial/by-id/ 下的稳定设备名
    by_id_dir = Path("/dev/serial/by-id")
    if by_id_dir.is_dir():
        try:
            for entry in sorted(by_id_dir.iterdir()):
                resolved = str(entry.resolve())
                if resolved not in ports:
                    ports.append(resolved)
        except OSError:
            pass

    # 按数字顺序排列 /dev/ttyUSB*
    try:
        for path in sorted(Path("/dev").glob("ttyUSB*"), key=lambda p: p.name):
            resolved = str(path)
            if resolved not in ports:
                ports.append(resolved)
    except OSError:
        pass

    # 按数字顺序排列 /dev/ttyACM*
    try:
        for path in sorted(Path("/dev").glob("ttyACM*"), key=lambda p: p.name):
            resolved = str(path)
            if resolved not in ports:
                ports.append(resolved)
    except OSError:
        pass

    return ports


def open_serial():
    """
    打开反馈板串口。
    自动发现 /dev/serial/by-id/、/dev/ttyUSB*、/dev/ttyACM* 中的设备。
    不会与蓝牙 IMU 的 RFCOMM 端口混淆（跳过 /dev/rfcomm*）。
    """
    port_candidates = _discover_serial_ports()
    if not port_candidates:
        print("反馈板串口：未找到任何候选串口设备，请连接 ESP32 反馈板。")
        return None

    imu_port = IMU_SERIAL_PORT

    for port in port_candidates:
        # 跳过与 IMU 相同的端口，避免抢蓝牙手环的串口
        if os.path.realpath(port) == os.path.realpath(imu_port) if os.path.exists(imu_port) else (port == imu_port):
            continue
        # Windows COM 端口适配
        actual_port = port
        if port.upper().startswith("COM") and len(port) > 4:
            actual_port = r"\\.\\" + port

        try:
            print("正在连接反馈板串口：", port)
            ser = serial.Serial(
                port=actual_port,
                baudrate=BAUD,
                timeout=1,
                write_timeout=1,
                rtscts=False,
                dsrdtr=False,
            )
            time.sleep(1.2)
            try:
                ser.reset_input_buffer()
                ser.reset_output_buffer()
            except Exception:
                pass
            print("反馈板串口连接成功：", port)
            return ser
        except Exception as e:
            print("反馈板串口连接失败：", port, e)

    print("反馈板串口未连接成功：程序会继续跑视觉识别，但屏幕不会更新。")
    print("请确认反馈板已连接、当前用户属于 dialout 组，且串口没有被其他程序占用。")
    return None




def send_cmd(ser, mode, count, score, tip):
    """
    给反馈屏 ESP32-S3-WROOM 发送显示命令。
    格式：
    CMD,动作,次数,分数,提示
    例如：
    CMD,CURL,3,90,GOOD
    """
    if ser is None:
        return

    try:
        cmd = f"CMD,{mode},{count},{score},{tip}\n"

        if FEEDBACK_DEBUG_PRINT:
            print("发送反馈屏：", cmd.strip())

        ser.write(cmd.encode("utf-8"))
        ser.flush()
        # 反馈板收到后会刷新屏幕；这里不等待OK，避免卡住视觉识别

    except Exception as e:
        print("发送反馈屏命令失败：", e)


# ==================================================
# 蓝牙 IMU / 心率后台读取模块
# ==================================================


@dataclass(frozen=True)
class SensorDispatchResult:
    """One classified sensor line and its parsed sample, if valid."""

    protocol: str
    sample: object | None


def dispatch_sensor_line(raw, timestamp):
    """Classify and strictly parse one complete RFCOMM sensor line."""
    if isinstance(raw, bytes):
        if raw.startswith(b"IMU,"):
            protocol = "IMU"
        elif raw.startswith(b"HR,"):
            protocol = "HR"
        else:
            return SensorDispatchResult("UNKNOWN", None)
        try:
            text = raw.decode("ascii")
        except UnicodeDecodeError:
            return SensorDispatchResult(protocol, None)
    elif isinstance(raw, str):
        if raw.startswith("IMU,"):
            protocol = "IMU"
        elif raw.startswith("HR,"):
            protocol = "HR"
        else:
            return SensorDispatchResult("UNKNOWN", None)
        try:
            raw.encode("ascii")
        except UnicodeEncodeError:
            return SensorDispatchResult(protocol, None)
        text = raw
    else:
        return SensorDispatchResult("UNKNOWN", None)

    if protocol == "IMU":
        sample = parse_imu_sample(
            text,
            timestamp=timestamp,
            input_mode=IMU_VALIDATION_CONFIG.accel_input_mode,
        )
        return SensorDispatchResult(protocol, sample)
    sample = parse_heart_rate_sample(text, timestamp=timestamp)
    return SensorDispatchResult(protocol, sample)


def _record_sensor_dispatch(result):
    if result.sample is None:
        return
    if result.protocol == "IMU":
        IMU_STATE.record_sample(result.sample)
    elif result.protocol == "HR":
        HEART_RATE_STATE.record_sample(result.sample)


def parse_imu_line(line):
    """
    解析蓝牙手环数据：
    IMU,ax,ay,az,gx,gy,gz,pitch
    """
    sample = parse_imu_sample(
        line,
        timestamp=time.monotonic(),
        input_mode=IMU_VALIDATION_CONFIG.accel_input_mode,
    )
    return None if sample is None else sample.to_legacy_dict()


def _ensure_rfcomm_binding():
    """
    确保 /dev/rfcomm0 已绑定到手环。
    如果设备节点不存在，通过 sudo rfcomm 创建绑定。
    返回 True 表示设备节点已就绪。
    """
    if os.path.exists(IMU_SERIAL_PORT):
        return True

    try:
        subprocess.run(
            ["sudo", "-n", "rfcomm", "release", "0"],
            capture_output=True, timeout=5,
        )
    except Exception:
        pass

    try:
        result = subprocess.run(
            [
                "sudo", "-n", "rfcomm", "bind", "0",
                IMU_BAND_ADDRESS, str(IMU_BAND_CHANNEL),
            ],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0:
            time.sleep(0.3)  # 等 udev 创建设备节点
            return os.path.exists(IMU_SERIAL_PORT)
        else:
            print(f"rfcomm bind 失败：{result.stderr.strip()}")
    except Exception as e:
        print(f"rfcomm bind 异常：{e}")

    return False


def _get_imu_bluetooth_properties():
    """读取 BlueZ 中手环的连接、配对和 bond 状态。"""
    try:
        result = subprocess.run(
            ["bluetoothctl", "info", IMU_BAND_ADDRESS],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return {}

    if result.returncode != 0:
        return {}

    properties = {}
    for line in result.stdout.splitlines():
        key, separator, value = line.strip().partition(":")
        if separator:
            properties[key] = value.strip()
    return properties


def _start_imu_scan():
    """启动持续存活的 BlueZ 扫描会话。"""
    scanner = subprocess.Popen(
        ["bluetoothctl"],
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    time.sleep(0.3)
    if scanner.poll() is not None or scanner.stdin is None:
        raise RuntimeError("无法启动蓝牙扫描会话")
    scanner.stdin.write("scan on\n")
    scanner.stdin.flush()
    time.sleep(0.5)
    return scanner


def _stop_imu_scan(scanner):
    if scanner is None:
        return
    try:
        if scanner.stdin is not None:
            scanner.stdin.write("scan off\nquit\n")
            scanner.stdin.flush()
        scanner.wait(timeout=2)
    except (BrokenPipeError, OSError, subprocess.SubprocessError):
        scanner.terminate()
        try:
            scanner.wait(timeout=2)
        except subprocess.SubprocessError:
            scanner.kill()


def _wait_for_imu_band_discovery(timeout=20.0, poll_interval=0.5):
    """等待 BlueZ 在扫描中重新发现被删除记录的手环。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        properties = _get_imu_bluetooth_properties()
        if properties.get("Name") == "AI_IMU_BAND":
            return True
        time.sleep(poll_interval)
    return False


def _start_imu_pairing_agent():
    """启动一个仅在本次手环配对期间存活的无输入 BlueZ 代理。"""
    agent = subprocess.Popen(
        ["bluetoothctl", "--agent", "NoInputNoOutput"],
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    time.sleep(0.5)
    if agent.poll() is not None or agent.stdin is None:
        raise RuntimeError("无法启动 NoInputNoOutput 配对代理")
    agent.stdin.write("default-agent\n")
    agent.stdin.flush()
    time.sleep(0.2)
    return agent


def _stop_imu_pairing_agent(agent):
    if agent is None:
        return
    try:
        if agent.stdin is not None:
            agent.stdin.write("quit\n")
            agent.stdin.flush()
        agent.wait(timeout=2)
    except (BrokenPipeError, OSError, subprocess.SubprocessError):
        agent.terminate()
        try:
            agent.wait(timeout=2)
        except subprocess.SubprocessError:
            agent.kill()


def _pair_imu_band():
    """删除失效密钥，并用临时无输入代理重新配对经典蓝牙手环。"""
    agent = None
    scanner = None
    try:
        subprocess.run(
            ["sudo", "-n", "rfcomm", "release", "0"],
            capture_output=True,
            timeout=5,
        )
        subprocess.run(
            ["bluetoothctl", "disconnect", IMU_BAND_ADDRESS],
            capture_output=True,
            timeout=5,
        )
        subprocess.run(
            ["bluetoothctl", "remove", IMU_BAND_ADDRESS],
            capture_output=True,
            timeout=5,
        )

        # 删除设备记录后保持同一个扫描客户端存活，直到重新发现手环。
        scanner = _start_imu_scan()
        if not _wait_for_imu_band_discovery():
            print(
                "蓝牙手环自动配对失败：扫描超时，"
                "请确认 AI_IMU_BAND 已上电且在附近"
            )
            return False

        # 保持代理进程存活，避免桌面面板接管并弹出 Pair 确认框。
        agent = _start_imu_pairing_agent()
        pair_result = subprocess.run(
            ["bluetoothctl", "--timeout", "25", "pair", IMU_BAND_ADDRESS],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if pair_result.returncode != 0 or "Pairing successful" not in pair_result.stdout:
            detail = (pair_result.stderr or pair_result.stdout).strip()
            print(f"蓝牙手环自动配对失败：{detail}")
            return False

        subprocess.run(
            ["bluetoothctl", "trust", IMU_BAND_ADDRESS],
            capture_output=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError, RuntimeError) as e:
        print(f"蓝牙手环自动配对异常：{e}")
        return False
    finally:
        _stop_imu_scan(scanner)
        _stop_imu_pairing_agent(agent)

    properties = _get_imu_bluetooth_properties()
    paired = properties.get("Paired") == "yes"
    bonded = properties.get("Bonded") == "yes"
    trusted = properties.get("Trusted") == "yes"
    if not (paired and bonded and trusted):
        print(
            "蓝牙手环自动配对未完成："
            f"paired={paired}, bonded={bonded}, trusted={trusted}"
        )
        return False

    print("蓝牙手环自动配对成功：", IMU_BAND_ADDRESS)
    return True


def _is_imu_bluetooth_connected():
    """通过 BlueZ 确认远端手环已建立蓝牙链路。"""
    return _get_imu_bluetooth_properties().get("Connected") == "yes"


def imu_reader_worker():
    """
    独立线程：自动绑定 RFCOMM、打开串口、持续读取 IMU 数据。
    断线后自动重绑并重连。
    """
    ser = None
    pairing_ready = not IMU_REPAIR_ON_START

    while IMU_STATE.running:
        try:
            IMU_STATE.mark_connecting()

            # 手环固件重启后可能丢失 link key。每个 worker 生命周期先完成一次
            # 干净配对；成功后普通断线只重连 RFCOMM，不反复删除 bond。
            if not pairing_ready:
                print("正在自动配对蓝牙手环：", IMU_BAND_ADDRESS)
                if not _pair_imu_band():
                    IMU_STATE.mark_disconnected()
                    print("蓝牙手环未就绪，3秒后重新配对")
                    time.sleep(3)
                    continue
                pairing_ready = True

            # 1. 确保 RFCOMM 绑定存在
            if not _ensure_rfcomm_binding():
                IMU_STATE.mark_disconnected()
                print("蓝牙IMU：无法创建 RFCOMM 绑定，3秒后重试")
                time.sleep(3)
                continue

            # 2. 打开本地串口会触发 SPP 连接，但打开成功不代表远端已连接。
            print("正在连接蓝牙IMU串口：", IMU_SERIAL_PORT)
            ser = serial.Serial(IMU_SERIAL_PORT, BAUD, timeout=1)
            time.sleep(1)

            if not _is_imu_bluetooth_connected():
                raise ConnectionError("手环未上电或蓝牙链路尚未建立")

            ser.reset_input_buffer()
            IMU_STATE.mark_connected()

            print("蓝牙手环连接成功：", IMU_BAND_ADDRESS)
            speak_text("蓝牙手环连接成功")

            last_debug_print = 0

            while IMU_STATE.running:
                raw = ser.readline().strip()

                if not raw:
                    continue

                result = dispatch_sensor_line(raw, time.monotonic())
                _record_sensor_dispatch(result)

                if result.protocol == "IMU" and result.sample is not None:
                    data = result.sample.to_legacy_dict()

                    if IMU_DEBUG_PRINT and time.time() - last_debug_print > 1.0:
                        last_debug_print = time.time()
                        print(
                            "蓝牙IMU：",
                            f"gyro={data['gyro_mag']:.2f}",
                            f"pitch={data['pitch']:.1f}",
                            f"raw={raw.decode('ascii')}"
                        )

        except Exception as e:
            IMU_STATE.mark_disconnected()
            print("蓝牙IMU读取失败/断开，3秒后重试：", e)
            time.sleep(3)

        finally:
            try:
                if ser:
                    ser.close()
            except Exception:
                pass
            ser = None
            IMU_STATE.mark_disconnected()

    IMU_STATE.mark_disconnected()


def open_imu_serial():
    """
    启动蓝牙 IMU 后台读取线程（自动绑定 RFCOMM 并连接）。
    返回 True 只是为了兼容主循环变量 imu_ser。
    """
    IMU_STATE.start_reader(imu_reader_worker)
    return True


def stop_imu_reader(join_timeout=3.0):
    IMU_STATE.stop_reader(join_timeout=join_timeout)


def get_imu_status_text():
    """返回 IMU 连接与数据状态：CONNECTING | CONNECTED | NO DATA | OK | OLD"""
    return IMU_STATE.status_text(now=time.monotonic(), old_after_sec=2.5)


def read_imu(ser, last_imu):
    """
    从后台线程拿最新 IMU 数据。
    ser 参数保留只是为了兼容原来的调用。
    """
    del ser
    return IMU_STATE.latest_legacy(last_imu)



def draw_info_panel(frame, mode, count, score, tip, metric_name, metric_value, aux_text, state, person_text, imu_value=0):
    show = cv2.resize(frame, (960, 720))

    panel_x1 = 650
    panel_y1 = 20
    panel_x2 = 945
    panel_y2 = 300

    overlay = show.copy()
    cv2.rectangle(overlay, (panel_x1, panel_y1), (panel_x2, panel_y2), (0, 0, 0), -1)
    show = cv2.addWeighted(overlay, 0.55, show, 0.45, 0)

    x = panel_x1 + 15
    y = panel_y1 + 35
    gap = 28

    lines = [
        f"Mode: {mode}",
        f"Count: {count}",
        f"Score: {score}",
        f"Tip: {tip}",
        f"{metric_name}: {metric_value:.2f}",
        aux_text,
        f"IMU: {imu_value:.2f} {get_imu_status_text()}",
        f"State: {state}",
    ]

    colors = [
        (255, 255, 255),
        (0, 255, 255),
        (0, 255, 0),
        (0, 0, 255),
        (255, 255, 0),
        (200, 200, 200),
        (200, 200, 200),
        (200, 200, 200),
    ]

    for i, line in enumerate(lines):
        cv2.putText(show, line, (x, y + gap * i),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.55 if i >= 4 else 0.65, colors[i], 2)

    cv2.putText(show, person_text, (25, 40),
                cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)

    cv2.putText(show, "Q: Quit    R: Reset    A: AI Coach", (25, 695),
                cv2.FONT_HERSHEY_SIMPLEX, 0.65, (255, 255, 255), 2)

    return show


def condition(value, op, threshold):
    if op == ">":
        return value > threshold
    if op == "<":
        return value < threshold
    return False


def forward_pending_heart_rate(runtime, ser, last_forwarded_timestamp):
    """Forward one newer HR snapshot without affecting action feedback state."""
    sample = pending_heart_rate_sample(
        HEART_RATE_STATE.snapshot(), last_forwarded_timestamp
    )
    if sample is None or (runtime is None and ser is None):
        return last_forwarded_timestamp

    try:
        if runtime is not None:
            runtime.publish_heart_rate(sample.bpm, sample.valid)
        else:
            ser.write(format_heart_rate_command(sample.bpm, sample.valid))
            ser.flush()
    except Exception as exc:
        print("心率转发失败：", exc)
        return last_forwarded_timestamp
    return sample.timestamp


def publish_action_feedback(runtime, ser, mode, count, score, tip):
    if runtime is not None:
        runtime.publish_feedback(mode, count, score, tip)
    else:
        send_cmd(ser, mode, count, score, tip)


def poll_action_control(runtime, mode):
    if runtime is None:
        return CONTINUE
    return runtime.poll_control(mode)


def run_repetition_action(config, runtime=None):
    global current_landmark_smooth_alpha
    """
    通用次数型动作程序。
    config 里面最重要的是：
    - mode
    - window_name
    - metric_name
    - compute_metric(landmarks)
    - target_op / target_threshold：进入动作顶点
    - finish_op / finish_threshold：回到起点，Count +1
    - range_type: "max" 或 "min"
    - range_threshold
    """

    mode = config["mode"]
    window_name = config["window_name"]
    metric_name = config.get("metric_name", "Value")
    compute_metric = config["compute_metric"]
    metric_comparison = create_metric_comparison_estimator(config)
    debug_metric_comparison = pose_metric_debug_enabled()
    last_debug_metric_time = 0.0

    target_op = config["target_op"]
    target_threshold = config["target_threshold"]
    finish_op = config["finish_op"]
    finish_threshold = config["finish_threshold"]

    range_type = config.get("range_type", "max")
    range_threshold = config.get("range_threshold", target_threshold)

    min_action_time = config.get("min_action_time", 0.6)
    min_count_interval = config.get("min_count_interval", 1.5)
    target_hold_frames = config.get("target_hold_frames", 3)
    finish_hold_frames = config.get("finish_hold_frames", 4)
    tip_hold_time = config.get("tip_hold_time", 1.5)
    swing_gyro_threshold = config.get("swing_gyro_threshold", 7.0)

    # 每个动作可以单独设置人体关键点平滑系数。
    # 默认 0.68；二头弯举这种快动作可以单独设 0.50，不影响其他动作。
    current_landmark_smooth_alpha = config.get("landmark_smooth_alpha", LANDMARK_SMOOTH_ALPHA)

    # 每个动作可以单独设置角度平滑帧数。
    # 数值越大越稳，但越容易漏掉很快的动作。
    metric_history_len = config.get("metric_history_len", 7)

    count = 0
    score = 100
    tip = "READY"
    state = "WAIT_TARGET"

    # 本组动作统计，用于按 A 生成 AI 教练总结
    action_stats = {
        "GOOD": 0,
        "TOO_FAST": 0,
        "RANGE_SMALL": 0,
        "SWING": 0
    }

    last_count_time = 0
    target_start_time = 0
    candidate_start = None
    best_metric = None

    last_sent_count = -1
    last_sent_tip = ""
    last_forwarded_hr_timestamp = None

    metric_history = deque(maxlen=metric_history_len)
    frame_counter = 0
    last_result = None
    last_timestamp_ms = -1
    last_landmarks = None
    last_metric_value = 0.0
    last_aux_text = ""
    last_person_text = "No person"
    target_frame_count = 0
    finish_frame_count = 0
    tip_hold_until = 0

    last_imu = {
        "ax": 0, "ay": 0, "az": 0,
        "gx": 0, "gy": 0, "gz": 0,
        "pitch": 0, "gyro_mag": 0
    }

    def reset_action_state():
        nonlocal count, score, tip, state
        nonlocal last_count_time, target_start_time, candidate_start, best_metric
        nonlocal target_frame_count, finish_frame_count, tip_hold_until
        nonlocal last_sent_count, last_sent_tip, last_result, frame_counter
        nonlocal last_timestamp_ms, last_debug_metric_time
        nonlocal last_landmarks, last_metric_value, last_aux_text, last_person_text
        count = 0
        score = 100
        tip = "READY"
        state = "WAIT_TARGET"
        last_count_time = 0
        target_start_time = 0
        candidate_start = None
        best_metric = None
        target_frame_count = 0
        finish_frame_count = 0
        tip_hold_until = 0
        last_result = None
        last_timestamp_ms = -1
        last_debug_metric_time = 0.0
        last_landmarks = None
        last_metric_value = 0.0
        last_aux_text = ""
        last_person_text = "No person"
        frame_counter = 0
        metric_history.clear()
        reset_pose_smoothing()
        if metric_comparison is not None:
            metric_comparison.reset()
        for key in action_stats:
            action_stats[key] = 0
        publish_action_feedback(runtime, ser, mode, count, score, tip)
        last_sent_count = count
        last_sent_tip = tip

    def reset_recognition_after_reconnect():
        nonlocal tip, state, target_start_time, candidate_start, best_metric
        nonlocal target_frame_count, finish_frame_count, tip_hold_until
        nonlocal last_result, frame_counter, last_timestamp_ms
        nonlocal last_debug_metric_time
        nonlocal last_landmarks, last_metric_value, last_aux_text, last_person_text

        tip = "READY"
        state = "WAIT_TARGET"
        target_start_time = 0
        candidate_start = None
        best_metric = None
        target_frame_count = 0
        finish_frame_count = 0
        tip_hold_until = 0
        last_result = None
        last_timestamp_ms = -1
        last_debug_metric_time = 0.0
        last_landmarks = None
        last_metric_value = 0.0
        last_aux_text = ""
        last_person_text = "No person"
        frame_counter = 0
        metric_history.clear()
        reset_pose_smoothing()
        if metric_comparison is not None:
            metric_comparison.reset()

    def reject_candidate():
        nonlocal state, tip, candidate_start, target_start_time, best_metric
        nonlocal target_frame_count, finish_frame_count
        state = "WAIT_TARGET"
        tip = "READY"
        candidate_start = None
        target_start_time = 0.0
        best_metric = None
        target_frame_count = 0
        finish_frame_count = 0

    def log_imu_validation(validation, now):
        motion_peak = (
            "NA" if validation.motion_peak_g is None
            else f"{validation.motion_peak_g:.3f}"
        )
        gyro_peak = (
            "NA" if validation.gyro_peak_dps is None
            else f"{validation.gyro_peak_dps:.3f}"
        )
        LOGGER.info(
            "IMU_VALIDATE action=%s result=%s samples=%d duration_sec=%.3f "
            "preroll_sec=%.3f motion_peak_g=%s min_g=%s max_g=%s "
            "min_enabled=%d gyro_peak_dps=%s",
            mode,
            validation.reason,
            validation.sample_count,
            now - target_start_time,
            IMU_VALIDATION_CONFIG.window_preroll_sec,
            motion_peak,
            config.get("imu_min_accel_g"),
            config.get("imu_max_accel_g"),
            int(bool(IMU_VALIDATION_CONFIG.use_min_threshold)),
            gyro_peak,
        )

    init_ai_client()

    ser = None if runtime is not None else open_serial()
    imu_ser = open_imu_serial()

    cap = open_camera_with_retries()
    if cap is None:
        print("摄像头暂未连接，将在动作运行期间继续重连")

    print(f"开始：AI {mode} 识别")
    print(f"反馈屏串口：{SERIAL_PORT}")
    print(f"蓝牙IMU串口：{IMU_SERIAL_PORT}")
    print("按 R 重置计数")
    print("按 A 生成AI教练总结")
    print("按 Q 退出")
    print("识别不卡版：低缓存读取 + 可配置推理频率 + lite模型优先")

    action_result = STOP
    display = DisplaySession(cv2, window_name)
    try:
        display.open()

        publish_action_feedback(runtime, ser, mode, count, score, tip)

        with create_landmarker() as landmarker:
            while True:
                control = poll_action_control(runtime, mode)
                if control.kind == "RESET":
                    reset_action_state()
                elif control.kind in {"SWITCH", "STOP", "QUIT"}:
                    action_result = control
                    break

                last_imu = read_imu(imu_ser, last_imu)
                last_forwarded_hr_timestamp = forward_pending_heart_rate(
                    runtime, ser, last_forwarded_hr_timestamp
                )

                if cap is None:
                    cap = reconnect_camera(None)
                    if cap is None:
                        print("摄像头仍未恢复，将继续等待下一轮重连")
                        continue
                    reset_recognition_after_reconnect()
                    publish_action_feedback(runtime, ser, mode, count, score, tip)
                    last_sent_count = count
                    last_sent_tip = tip
                    continue

                ret, frame = cap.read()
                if not ret or frame is None:
                    cap = reconnect_camera(cap)
                    if cap is None:
                        print("摄像头重连失败，将继续等待下一轮重连")
                        continue
                    reset_recognition_after_reconnect()
                    publish_action_feedback(runtime, ser, mode, count, score, tip)
                    last_sent_count = count
                    last_sent_tip = tip
                    continue

                frame = fix_frame_color(frame)
                frame = cv2.flip(frame, 1)
                rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)

                frame_counter += 1
                fresh_result = (
                    frame_counter % PROCESS_EVERY_N_FRAMES == 0
                    or last_result is None
                )
                if fresh_result:
                    timestamp_ms = max(
                        last_timestamp_ms + 1,
                        int(time.monotonic() * 1000),
                    )
                    last_timestamp_ms = timestamp_ms
                    result = landmarker.detect_for_video(mp_image, timestamp_ms)
                    last_result = result
                else:
                    result = last_result

                metric_value = last_metric_value
                aux_text = last_aux_text
                person_text = last_person_text

                if result.pose_landmarks and fresh_result:
                    landmarks = smooth_pose_landmarks(result.pose_landmarks[0])
                    last_landmarks = landmarks
                    draw_pose(frame, landmarks)
                    person_text = "Person detected"
                    last_person_text = person_text

                    if tip == "NO_PERSON" and state == "WAIT_TARGET":
                        tip = "READY"

                    now = time.monotonic()
                    metric_value, aux_text = compute_metric(landmarks)
                    if metric_comparison is not None:
                        comparison = metric_comparison.update(
                            PoseObservation(
                                image_landmarks=landmarks,
                                world_landmarks=first_world_landmarks(result),
                                timestamp_ms=timestamp_ms,
                            )
                        )
                        aux_text = append_comparison_text(aux_text, comparison)
                        if (
                            debug_metric_comparison
                            and now - last_debug_metric_time >= 1.0
                        ):
                            print(f"[{mode}] {comparison.aux_text}")
                            last_debug_metric_time = now

                    metric_history.append(metric_value)
                    metric_value = sum(metric_history) / len(metric_history)
                    last_metric_value = metric_value
                    last_aux_text = aux_text

                    is_target = condition(metric_value, target_op, target_threshold)
                    is_finish = condition(metric_value, finish_op, finish_threshold)

                    if is_target:
                        target_frame_count += 1
                    else:
                        target_frame_count = 0

                    if is_finish:
                        finish_frame_count += 1
                    else:
                        finish_frame_count = 0

                    if state == "WAIT_TARGET":
                        if target_frame_count >= target_hold_frames and now - last_count_time > min_count_interval:
                            state = "WAIT_FINISH"
                            target_start_time = now
                            candidate_start = now
                            best_metric = metric_value
                            tip = "UP"

                    elif state == "WAIT_FINISH":
                        if range_type == "max":
                            best_metric = max(best_metric, metric_value)
                        else:
                            best_metric = min(best_metric, metric_value)

                        if finish_frame_count >= finish_hold_frames and now - last_count_time > min_count_interval:
                            validation = validate_imu_window(
                                imu_snapshot=IMU_STATE.snapshot(),
                                now=now,
                                window_start=(
                                    candidate_start
                                    - IMU_VALIDATION_CONFIG.window_preroll_sec
                                ),
                                window_end=now,
                                min_accel_g=config.get("imu_min_accel_g"),
                                max_accel_g=config.get("imu_max_accel_g"),
                                use_min_threshold=(
                                    IMU_VALIDATION_CONFIG.use_min_threshold
                                ),
                                stale_sec=IMU_VALIDATION_CONFIG.stale_sec,
                                min_window_samples=(
                                    IMU_VALIDATION_CONFIG.min_window_samples
                                ),
                                config_valid=IMU_VALIDATION_CONFIG.is_valid,
                            )
                            log_imu_validation(validation, now)
                            if not validation.accepted:
                                reject_candidate()
                            else:
                                action_time = now - target_start_time
                                candidate_start = None
                                count += 1
                                last_count_time = now
                                state = "WAIT_TARGET"

                                if action_time < min_action_time:
                                    tip = "TOO_FAST"
                                    score -= 10

                                elif range_type == "max" and best_metric < range_threshold:
                                    tip = "RANGE_SMALL"
                                    score -= 10

                                elif range_type == "min" and best_metric > range_threshold:
                                    tip = "RANGE_SMALL"
                                    score -= 10

                                elif (
                                    not validation.bypassed
                                    and last_imu["gyro_mag"] > swing_gyro_threshold
                                ):
                                    tip = "SWING"
                                    score -= 10

                                else:
                                    tip = "GOOD"

                                if score < 0:
                                    score = 0

                                # 统计本组错误类型，后面按 A 给云端 AI 总结用
                                if tip in action_stats:
                                    action_stats[tip] += 1

                                tip_hold_until = now + tip_hold_time

                                # 动作刚完成时立刻人声报数，最稳定
                                speak_count_tip(count, tip)

                    if state == "WAIT_TARGET":
                        if tip in ["GOOD", "TOO_FAST", "RANGE_SMALL", "SWING"]:
                            if now > tip_hold_until:
                                tip = "READY"

                elif fresh_result:
                    tip = "NO_PERSON"
                    state = "WAIT_TARGET"
                    target_start_time = 0
                    candidate_start = None
                    best_metric = None
                    tip_hold_until = 0
                    last_landmarks = None
                    last_metric_value = 0.0
                    last_aux_text = ""
                    last_person_text = "No person"
                    metric_value = 0.0
                    aux_text = ""
                    person_text = "No person"
                    reset_pose_smoothing()
                    if metric_comparison is not None:
                        metric_comparison.reset()
                    metric_history.clear()
                    target_frame_count = 0
                    finish_frame_count = 0

                elif last_landmarks is not None:
                    draw_pose(frame, last_landmarks)

                if count != last_sent_count or tip != last_sent_tip:
                    publish_action_feedback(runtime, ser, mode, count, score, tip)
                    # 人声报数已经在动作完成瞬间触发，这里只负责屏幕/扬声器同步
                    last_sent_count = count
                    last_sent_tip = tip

                show = draw_info_panel(
                    frame, mode, count, score, tip,
                    metric_name, metric_value, aux_text, state, person_text,
                    last_imu["gyro_mag"]
                )
                key = display.show(show)

                if key == ord("q"):
                    action_result = QUIT if runtime is not None else STOP
                    break

                elif key == ord("a"):
                    # 不阻塞识别主循环，开线程生成AI总结
                    stats_copy = action_stats.copy()
                    threading.Thread(
                        target=generate_and_speak_ai_summary,
                        args=(mode, count, score, stats_copy),
                        daemon=True
                    ).start()

                elif key == ord("r"):
                    reset_action_state()
                    # 重置后不播READY，下一次动作完成后会继续播报

    finally:
        if cap is not None:
            cap.release()
        reset_pose_smoothing()
        display.close()

        if imu_ser and runtime is None:
            stop_imu_reader()

        if ser:
            ser.close()

    return action_result


def run_plank_action(config):
    global current_landmark_smooth_alpha
    mode = config.get("mode", "PLANK")
    window_name = config.get("window_name", "AI Fitness Coach - Plank")
    min_hold_seconds = config.get("min_hold_seconds", 20)

    count_seconds = 0
    score = 100
    tip = "READY"

    last_sent_count = -1
    last_sent_tip = ""
    last_forwarded_hr_timestamp = None

    good_start_time = None
    last_second_mark = -1
    frame_counter = 0
    last_result = None

    last_imu = {
        "ax": 0, "ay": 0, "az": 0,
        "gx": 0, "gy": 0, "gz": 0,
        "pitch": 0, "gyro_mag": 0
    }

    init_ai_client()

    ser = open_serial()
    imu_ser = open_imu_serial()

    cap = open_camera_with_retries()
    if cap is None:
        return

    display = DisplaySession(cv2, window_name)
    display.open()

    send_cmd(ser, mode, count_seconds, score, tip)

    try:
        with create_landmarker() as landmarker:
            while True:
                last_imu = read_imu(imu_ser, last_imu)
                last_forwarded_hr_timestamp = forward_pending_heart_rate(
                    None, ser, last_forwarded_hr_timestamp
                )

                ret, frame = cap.read()
                if not ret:
                    time.sleep(0.1)
                    continue

                frame = fix_frame_color(frame)
                frame = cv2.flip(frame, 1)
                rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)

                frame_counter += 1
                if frame_counter % PROCESS_EVERY_N_FRAMES == 0 or last_result is None:
                    timestamp_ms = int(time.monotonic() * 1000)
                    result = landmarker.detect_for_video(mp_image, timestamp_ms)
                    last_result = result
                else:
                    result = last_result

                body_angle = 0
                aux_text = ""
                person_text = "No person"

                if result.pose_landmarks:
                    landmarks = smooth_pose_landmarks(result.pose_landmarks[0])
                    draw_pose(frame, landmarks)
                    person_text = "Person detected"

                    l_sh = get_xy(landmarks, 11)
                    l_hip = get_xy(landmarks, 23)
                    l_ankle = get_xy(landmarks, 27)

                    r_sh = get_xy(landmarks, 12)
                    r_hip = get_xy(landmarks, 24)
                    r_ankle = get_xy(landmarks, 28)

                    left_body = angle_3points(l_sh, l_hip, l_ankle)
                    right_body = angle_3points(r_sh, r_hip, r_ankle)
                    body_angle = max(left_body, right_body)

                    aux_text = f"L:{left_body:.1f} R:{right_body:.1f}"

                    now = time.time()

                    if body_angle > 145:
                        tip = "GOOD"
                        if good_start_time is None:
                            good_start_time = now
                        count_seconds = int(now - good_start_time)
                    else:
                        tip = "BAD_FORM"
                        good_start_time = None

                else:
                    tip = "NO_PERSON"
                    reset_pose_smoothing()
                    good_start_time = None

                if count_seconds != last_sent_count or tip != last_sent_tip:
                    send_cmd(ser, mode, count_seconds, score, tip)
                    ai_speak_tip(mode, count_seconds, score, tip)
                    last_sent_count = count_seconds
                    last_sent_tip = tip

                show = draw_info_panel(
                    frame, mode, count_seconds, score, tip,
                    "Body", body_angle, aux_text, "HOLD", person_text,
                    last_imu["gyro_mag"]
                )

                key = display.show(show)

                if key == ord("q"):
                    break
                elif key == ord("r"):
                    count_seconds = 0
                    tip = "READY"
                    good_start_time = None
                    send_cmd(ser, mode, count_seconds, score, tip)
                    last_sent_count = count_seconds
                    last_sent_tip = tip

    finally:
        cap.release()
        reset_pose_smoothing()
        display.close()

        if imu_ser:
            stop_imu_reader()

        if ser:
            ser.close()

import cv2
import mediapipe as mp
import numpy as np
import time
import os
import serial
import threading
import queue
from collections import deque


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

STREAM_URL = "http://10.73.29.197/stream"
SERIAL_PORT = "COM12"     # 反馈屏 ESP32-S3-WROOM，现在你的反馈板是COM12
IMU_SERIAL_PORT = "COM9"  # 蓝牙手环 ESP32 NodeMCU
BAUD = 115200

# 蓝牙 IMU 调试开关：True 时控制台会每隔约1秒打印一次 IMU 数据
IMU_DEBUG_PRINT = True

# 反馈屏串口调试开关：True 时每次给屏幕发命令都会打印，方便排查屏幕不动
FEEDBACK_DEBUG_PRINT = True

# 摄像头画面颜色修正：
# 如果 Python 窗口看起来偏灰/发白，打开 COLOR_BOOST 会轻微增加饱和度。
COLOR_BOOST = True
COLOR_SATURATION_GAIN = 1.35

# 推荐用 full 模型，识别人更稳；模型文件必须和这些 py 文件放同一个文件夹
# 模型文件：优先使用 lite，明显更不卡；如果没有 lite，就自动用 full。
# 你文件夹里如果只有 pose_landmarker_full.task，也能继续跑。
MODEL_PATH = "pose_landmarker_lite.task" if os.path.exists("pose_landmarker_lite.task") else "pose_landmarker_full.task"

print("MediaPipe模型文件：", MODEL_PATH)

# 识别稳定性设置
# 数值越大，骨架越稳，但动作会稍微有一点延迟；0.60~0.80 都可以。
LANDMARK_SMOOTH_ALPHA = 0.68

# 如果画面太卡，可以把 PROCESS_EVERY_N_FRAMES 改成 2，表示隔一帧识别一次。
# 但一般先保持 1。
PROCESS_EVERY_N_FRAMES = 3

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
    "TRICEPS": "过头臂屈伸"
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
        sentence = f"第{count}次，幅度不够，再做到位一点"
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



def create_landmarker():
    # VIDEO 模式会利用前后帧跟踪，比 IMAGE 每帧重新检测更不容易跳
    options = PoseLandmarkerOptions(
        base_options=BaseOptions(model_asset_path=MODEL_PATH),
        running_mode=VisionRunningMode.VIDEO,
        num_poses=1,
        min_pose_detection_confidence=0.25,
        min_pose_presence_confidence=0.25,
        min_tracking_confidence=0.25
    )
    return PoseLandmarker.create_from_options(options)



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



def fix_frame_color(frame):
    """
    摄像头颜色修正：
    1. 保证 OpenCV 显示用的是 3通道 BGR。
    2. 如果画面偏灰，适当提高饱和度。
    注意：如果浏览器里的 /stream 本身就是黑白的，这里只能略微改善，不能真正恢复传感器没有输出的颜色。
    """
    if frame is None:
        return frame

    # 如果意外读成灰度图，转成 BGR，避免后续 cvtColor 出问题
    if len(frame.shape) == 2:
        frame = cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR)

    # 如果是 4 通道，转成 3 通道
    elif len(frame.shape) == 3 and frame.shape[2] == 4:
        frame = cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR)

    if COLOR_BOOST and len(frame.shape) == 3 and frame.shape[2] == 3:
        try:
            hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV).astype(np.float32)
            hsv[:, :, 1] = np.clip(hsv[:, :, 1] * COLOR_SATURATION_GAIN, 0, 255)
            frame = cv2.cvtColor(hsv.astype(np.uint8), cv2.COLOR_HSV2BGR)
        except Exception:
            pass

    return frame


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


def open_serial():
    """
    打开反馈板串口。
    你的反馈板现在是 COM12。
    注意：
    - SERIAL_PORT 只能填反馈板 WROOM 的端口，不能填摄像头 N16R8 的端口。
    - 如果填错到摄像头端口，会把摄像头板复位，导致视频流卡住。
    """
    # Windows 上 COM10 以上有时需要 \\.\COM12 这种写法；pyserial多数情况COM12也能用。
    port_candidates = [SERIAL_PORT]
    if SERIAL_PORT.upper().startswith("COM") and len(SERIAL_PORT) > 4:
        port_candidates.append(r"\\.\\" + SERIAL_PORT)

    for port in port_candidates:
        try:
            print("正在连接反馈板串口：", port)

            ser = serial.Serial(
                port=port,
                baudrate=BAUD,
                timeout=1,
                write_timeout=1,
                rtscts=False,
                dsrdtr=False
            )

            # ESP32 打开串口后可能会自动复位，稍微等一下，但不要等太久
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
    print("请确认：设备管理器里 WROOM 反馈板是 COM12，且 Arduino串口监视器/测试程序没有占用它。")
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
# 蓝牙 IMU 后台读取模块
# ==================================================

imu_lock = threading.Lock()
imu_running = False
imu_thread = None
imu_last_line_time = 0
imu_line_count = 0
imu_latest = {
    "ax": 0, "ay": 0, "az": 0,
    "gx": 0, "gy": 0, "gz": 0,
    "pitch": 0, "gyro_mag": 0
}


def parse_imu_line(line):
    """
    解析蓝牙手环数据：
    IMU,ax,ay,az,gx,gy,gz,pitch
    """
    parts = line.strip().split(",")

    if len(parts) != 8:
        return None

    if parts[0] != "IMU":
        return None

    try:
        ax = float(parts[1])
        ay = float(parts[2])
        az = float(parts[3])
        gx = float(parts[4])
        gy = float(parts[5])
        gz = float(parts[6])
        pitch = float(parts[7])
        gyro_mag = abs(gx) + abs(gy) + abs(gz)

        return {
            "ax": ax,
            "ay": ay,
            "az": az,
            "gx": gx,
            "gy": gy,
            "gz": gz,
            "pitch": pitch,
            "gyro_mag": gyro_mag
        }
    except Exception:
        return None


def imu_reader_worker():
    """
    独立线程持续读取 COM9。
    这样不会被视频识别循环影响，比每帧临时读串口更稳。
    """
    global imu_running, imu_last_line_time, imu_line_count, imu_latest

    ser = None

    while imu_running:
        try:
            print("正在连接蓝牙IMU串口：", IMU_SERIAL_PORT)
            ser = serial.Serial(IMU_SERIAL_PORT, BAUD, timeout=1)
            time.sleep(2)
            ser.reset_input_buffer()
            print("蓝牙IMU串口连接成功：", IMU_SERIAL_PORT)
            speak_text("蓝牙手环连接成功")

            last_debug_print = 0

            while imu_running:
                raw = ser.readline().decode("utf-8", errors="ignore").strip()

                if not raw:
                    continue

                data = parse_imu_line(raw)

                if data is not None:
                    with imu_lock:
                        imu_latest = data
                        imu_last_line_time = time.time()
                        imu_line_count += 1

                    if IMU_DEBUG_PRINT and time.time() - last_debug_print > 1.0:
                        last_debug_print = time.time()
                        print(
                            "蓝牙IMU：",
                            f"gyro={data['gyro_mag']:.2f}",
                            f"pitch={data['pitch']:.1f}",
                            f"raw={raw}"
                        )

        except Exception as e:
            print("蓝牙IMU读取失败/断开，2秒后重试：", e)
            time.sleep(2)

        finally:
            try:
                if ser:
                    ser.close()
            except Exception:
                pass


def open_imu_serial():
    """
    启动蓝牙 IMU 后台读取线程。
    返回 True 只是为了兼容主循环变量 imu_ser。
    """
    global imu_running, imu_thread

    if imu_thread is not None and imu_thread.is_alive():
        return True

    imu_running = True
    imu_thread = threading.Thread(target=imu_reader_worker, daemon=True)
    imu_thread.start()

    return True


def stop_imu_reader():
    global imu_running

    imu_running = False


def get_imu_status_text():
    with imu_lock:
        age = time.time() - imu_last_line_time if imu_last_line_time else 999
        count = imu_line_count

    if count == 0:
        return "NO DATA"

    if age > 2.5:
        return "OLD"

    return "OK"


def read_imu(ser, last_imu):
    """
    从后台线程拿最新 IMU 数据。
    ser 参数保留只是为了兼容原来的调用。
    """
    with imu_lock:
        if imu_line_count == 0:
            return last_imu

        return dict(imu_latest)



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


def run_repetition_action(config):
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
    best_metric = None

    last_sent_count = -1
    last_sent_tip = ""

    metric_history = deque(maxlen=metric_history_len)
    frame_counter = 0
    last_result = None
    target_frame_count = 0
    finish_frame_count = 0
    tip_hold_until = 0

    last_imu = {
        "ax": 0, "ay": 0, "az": 0,
        "gx": 0, "gy": 0, "gz": 0,
        "pitch": 0, "gyro_mag": 0
    }

    init_ai_client()

    ser = open_serial()
    imu_ser = open_imu_serial()

    print("正在打开摄像头视频流：", STREAM_URL)
    # 用和单独测试文件一样的 OpenCV 读取方式，并尽量降低缓存，避免窗口假死
    cap = cv2.VideoCapture(STREAM_URL)
    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    cap.set(cv2.CAP_PROP_CONVERT_RGB, 1)
    try:
        cap.set(cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, 3000)
        cap.set(cv2.CAP_PROP_READ_TIMEOUT_MSEC, 3000)
    except Exception:
        pass

    if not cap.isOpened():
        print("无法打开摄像头视频流，请检查 STREAM_URL")
        return

    print("摄像头打开成功")
    print(f"开始：AI {mode} 识别")
    print("COM12用于反馈屏，COM9用于蓝牙IMU手环；如果屏幕不动，请确认COM12没有被占用")
    print("按 R 重置计数")
    print("按 A 生成AI教练总结")
    print("按 Q 退出")
    print("识别不卡版：低缓存读取 + 隔帧识别 + lite模型优先")

    cv2.namedWindow(window_name, cv2.WINDOW_NORMAL)
    cv2.resizeWindow(window_name, 960, 720)

    send_cmd(ser, mode, count, score, tip)

    with create_landmarker() as landmarker:
        while True:
            last_imu = read_imu(imu_ser, last_imu)

            ret, frame = cap.read()
            if not ret or frame is None:
                print("读取画面失败，正在重试；如果一直失败，请关闭浏览器/检查IP")
                cv2.waitKey(1)
                time.sleep(0.05)
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

            metric_value = 0
            aux_text = ""
            person_text = "No person"

            if result.pose_landmarks:
                landmarks = smooth_pose_landmarks(result.pose_landmarks[0])
                draw_pose(frame, landmarks)
                person_text = "Person detected"

                if tip == "NO_PERSON" and state == "WAIT_TARGET":
                    tip = "READY"

                metric_value, aux_text = compute_metric(landmarks)

                metric_history.append(metric_value)
                metric_value = sum(metric_history) / len(metric_history)

                now = time.time()

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
                        best_metric = metric_value
                        tip = "UP"

                elif state == "WAIT_FINISH":
                    if range_type == "max":
                        best_metric = max(best_metric, metric_value)
                    else:
                        best_metric = min(best_metric, metric_value)

                    if finish_frame_count >= finish_hold_frames and now - last_count_time > min_count_interval:
                        action_time = now - target_start_time

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

                        elif last_imu["gyro_mag"] > swing_gyro_threshold:
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

            else:
                tip = "NO_PERSON"
                reset_pose_smoothing()
                metric_history.clear()
                target_frame_count = 0
                finish_frame_count = 0

            if count != last_sent_count or tip != last_sent_tip:
                send_cmd(ser, mode, count, score, tip)
                # 人声报数已经在动作完成瞬间触发，这里只负责屏幕/扬声器同步
                last_sent_count = count
                last_sent_tip = tip

            show = draw_info_panel(
                frame, mode, count, score, tip,
                metric_name, metric_value, aux_text, state, person_text,
                last_imu["gyro_mag"]
            )

            cv2.imshow(window_name, show)

            key = cv2.waitKey(1) & 0xFF

            if key == ord("q"):
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
                count = 0
                score = 100
                tip = "READY"
                state = "WAIT_TARGET"
                last_count_time = 0
                target_start_time = 0
                best_metric = None
                target_frame_count = 0
                finish_frame_count = 0
                tip_hold_until = 0
                metric_history.clear()
                reset_pose_smoothing()

                for k in action_stats:
                    action_stats[k] = 0

                send_cmd(ser, mode, count, score, tip)
                last_sent_count = count
                last_sent_tip = tip
                # 重置后不播READY，下一次动作完成后会继续播报

    cap.release()
    cv2.destroyAllWindows()

    if imu_ser:
        stop_imu_reader()

    if ser:
        ser.close()


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

    print("正在打开摄像头视频流：", STREAM_URL)
    # 用和单独测试文件一样的 OpenCV 读取方式，并尽量降低缓存，避免窗口假死
    cap = cv2.VideoCapture(STREAM_URL)
    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    cap.set(cv2.CAP_PROP_CONVERT_RGB, 1)
    try:
        cap.set(cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, 3000)
        cap.set(cv2.CAP_PROP_READ_TIMEOUT_MSEC, 3000)
    except Exception:
        pass

    if not cap.isOpened():
        print("无法打开摄像头视频流，请检查 STREAM_URL")
        return

    cv2.namedWindow(window_name, cv2.WINDOW_NORMAL)
    cv2.resizeWindow(window_name, 960, 720)

    send_cmd(ser, mode, count_seconds, score, tip)

    with create_landmarker() as landmarker:
        while True:
            last_imu = read_imu(imu_ser, last_imu)

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

            cv2.imshow(window_name, show)
            key = cv2.waitKey(1) & 0xFF

            if key == ord("q"):
                break
            elif key == ord("r"):
                count_seconds = 0
                tip = "READY"
                good_start_time = None
                send_cmd(ser, mode, count_seconds, score, tip)
                last_sent_count = count_seconds
                last_sent_tip = tip

    cap.release()
    cv2.destroyAllWindows()

    if imu_ser:
        stop_imu_reader()

    if ser:
        ser.close()

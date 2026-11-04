"""Receive RK analysis and display supplied counts. No Windows-side inference."""
import base64
import hashlib
import math
import time
from profiles import validate_identity, ConfigError
from connection import SessionReceiver, classify
from mjpeg_codec import decode_jpeg
from exercise_events import action_code

EDGES = {
 'mediapipe33': [(11,12),(11,13),(13,15),(12,14),(14,16),(11,23),(12,24),(23,24),(23,25),(25,27),(24,26),(26,28),(27,29),(29,31),(28,30),(30,32)],
 'coco17': [(5,6),(5,7),(7,9),(6,8),(8,10),(5,11),(6,12),(11,12),(11,13),(13,15),(12,14),(14,16)]}

def validate_pose(profile, raw, decoder=decode_jpeg):
    validate_identity(profile, raw)
    if raw.get('schema') != 'qjzc.analysis.v1':
        raise ConfigError('姿态协议必须为 qjzc.analysis.v1')
    if raw.get('kind') not in ('pose', 'absent'):
        raise ConfigError('需要姿态结果；检测框不能作为骨架')
    if type(raw.get('frame_id')) is not int or raw['frame_id'] < 0:
        raise ConfigError('姿态 frame_id 无效')
    people = raw.get('persons', [])
    if not isinstance(people, list) or len(people) > 32:
        raise ConfigError('姿态人数无效')
    if raw.get('kind') == 'absent' or raw.get('pose_present') is False:
        if people or any(raw.get(k) is not None for k in ('rep_count', 'quality_score')):
            raise ConfigError('无姿态时不能提供骨架或成绩')
    elif raw.get('pose_present') is not True:
        raise ConfigError('缺少 pose_present')
    seen = set()
    for person in people:
        if not isinstance(person, dict): raise ConfigError('个人结果格式错误')
        pid = person.get('person_id')
        if not isinstance(pid, str) or not 0 < len(pid) <= 128 or pid in seen:
            raise ConfigError('每人必须有独立、稳定的 person_id')
        seen.add(pid)
        n = {'mediapipe33': 33, 'coco17': 17}.get(person.get('layout'))
        points = person.get('keypoints')
        if not n or person.get('coordinate_space') != 'normalized_image' or not isinstance(points, list) or len(points) != n:
            raise ConfigError('关键点编号、数量或坐标系错误')
        for point in points:
            if (not isinstance(point, list) or len(point) != 3 or
                any(type(v) not in (int, float) or not math.isfinite(v) for v in point) or not 0 <= point[2] <= 1):
                raise ConfigError('关键点必须为有限的 [x,y,visibility]')
        for k in ('rep_count', 'correct_count', 'incorrect_count'):
            value = person.get(k)
            if value is not None and (type(value) is not int or value < 0):
                raise ConfigError('动作次数必须为非负整数或 null')
        counts = person.get('counts_by_action', {})
        if not isinstance(counts, dict) or len(counts)>64 or any(v is not None and (type(v) is not int or v < 0) for v in counts.values()):
            raise ConfigError('运动计数格式错误')
        for code in counts:action_code(code)
        durations=person.get('durations_ms',{})
        if not isinstance(durations,dict) or len(durations)>64 or any(type(v) is not int or v<0 for v in durations.values()):raise ConfigError('运动时长格式错误')
        for code in durations:action_code(code)
        if person.get('action_type') is not None:action_code(person['action_type'])
        score = person.get('quality_score')
        if score is not None and (type(score) not in (int,float) or not math.isfinite(score) or not 0 <= score <= 100):
            raise ConfigError('评分格式错误')
        profile_data = person.get('profile', {})
        if not isinstance(profile_data, dict): raise ConfigError('学生档案格式错误')
        for k in ('student_id', 'display_name', 'class_name'):
            if profile_data.get(k) is not None and (not isinstance(profile_data[k],str) or len(profile_data[k]) > 128):
                raise ConfigError('学生档案字段错误')
        if profile_data.get('identity_status') == 'confirmed' and not profile_data.get('student_id'):
            raise ConfigError('已绑定学生必须提供学号')
    image = None
    if raw.get('image_base64') is not None:
        encoded = raw['image_base64']
        if not isinstance(encoded, str) or len(encoded) > 4 * 1024 * 1024:
            raise ConfigError('姿态图像过大')
        try: payload = base64.b64decode(encoded, validate=True)
        except (ValueError, TypeError): raise ConfigError('姿态图像编码错误') from None
        if hashlib.sha256(payload).hexdigest() != raw.get('jpeg_sha256'):
            raise ConfigError('姿态输入图像 SHA 不一致')
        image = decoder(payload)
    return dict(raw), image

class PoseReceiver(SessionReceiver):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name += '-pose'; self.last_frame = -1
    def run(self):
        failures = 0
        try:
            while not self.stop_event.is_set():
                try:
                    self.check()
                    with self.lock: self.counts['attempts'] += 1
                    snap = self.read_status()
                    if snap['state'] in ('ARMED', 'ARMED_NO_CAPTURE'):
                        self.set_state('waiting', '等待板端启动'); self.wait(1); continue
                    raw = self.read_json(self.profile['analysis_url'], 'analysis', 5 * 1024 * 1024)
                    message, image = validate_pose(self.profile, raw, decoder=self.decoder)
                    if message['frame_id'] < self.last_frame:
                        raise ConfigError('姿态 frame_id 回退')
                    if message['frame_id'] != self.last_frame:
                        self.last_frame = message['frame_id']
                        with self.lock:
                            self.counts['received'] += 1; self.counts['decoded'] += 1
                            self.latest = message, image, time.monotonic()
                        self.event('POSE_RESULT', frame_id=self.last_frame, persons=len(message.get('persons', [])))
                    self.set_state('receiving', '正在接收姿态'); failures = 0; self.wait(.25)
                except Exception as e:
                    if self.user_stopped or self.stop_event.is_set(): break
                    category = classify(e); self.event('FAILURE', category=category, reason=str(e))
                    if category != 'retrying': self.set_state(category, str(e)); break
                    delay = self.backoff[min(failures,len(self.backoff)-1)]; failures += 1
                    with self.lock:
                        self.counts['retries'] += 1; self.next_retry_at = self.wall() + delay
                    self.set_state('retrying', str(e)); self.event('RETRY_SCHEDULED', seconds=delay, retry_at=self.next_retry_at)
                    try: self.wait(delay)
                    finally:
                        with self.lock: self.next_retry_at = None
        except Exception as e:
            if not self.user_stopped: self.set_state(classify(e), str(e))
        finally:
            self.shutdown()
            with self.lock:
                self.terminal = True; self.latest = None; self.next_retry_at = None
            self.event('STOPPED', counts=self.counts, state=self.state)

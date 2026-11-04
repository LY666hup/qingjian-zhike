"""Local validation only. Loading a profile never makes a network request."""
from datetime import datetime, timezone
from urllib.parse import urlsplit, parse_qs,parse_qsl
import hashlib
import json
import re

IDENTITY = ('session_id', 'boot_id', 'camera_id', 'processing_mode', 'fpga_bit_sha256', 'processor')
MODES = {'original': '原始视频', 'gray': '灰度', 'gaussian': '高斯滤波', 'histogram': '直方图均衡'}

class ConfigError(ValueError):
    pass

class IdentityError(ConfigError):
    pass

def deadline(profile):
    try:
        value = datetime.fromisoformat(profile['authorized_until'].replace('Z', '+00:00'))
        if value.tzinfo is None:
            raise ValueError()
        return value.timestamp()
    except (ValueError, KeyError, TypeError, AttributeError):
        raise ConfigError('截止时间必须是带时区的 ISO8601 时间') from None

def origin(value):
    try:
        p = urlsplit(value)
        if p.scheme not in ('http', 'https') or not p.hostname or p.username or p.password or p.fragment:
            raise ValueError()
        if any(ord(c) < 33 for c in value):
            raise ValueError()
        return p.scheme, p.hostname.lower(), p.port or (443 if p.scheme == 'https' else 80)
    except (TypeError, ValueError, AttributeError):
        raise ConfigError('地址必须是无凭据的 HTTP/HTTPS URL') from None

def validate_profile(p, now=None):
    if not isinstance(p, dict):
        raise ConfigError('每个视频来源必须是 JSON 对象')
    for key in IDENTITY:
        if not isinstance(p.get(key), str) or not 0 < len(p[key]) <= 128:
            raise ConfigError('缺少或无效的身份字段：' + key)
    if p['camera_id'] not in ('A', 'B') or p['processing_mode'] not in MODES:
        raise ConfigError('相机应为 A/B，模式应为 original/gray/gaussian/histogram')
    if p['processor'] not in ('fpga', 'rk', 'simulation'):
        raise ConfigError('processor 必须为 fpga/rk/simulation')
    if not re.fullmatch('[0-9a-fA-F]{64}', p['fpga_bit_sha256']):
        raise ConfigError('bit SHA 必须是完整的 64 位 SHA256')
    base = origin(p.get('status_url'))
    for key in ('status_url', 'video_url', 'analysis_url','events_url'):
        url = p.get(key)
        if key in ('analysis_url','events_url') and not url:
            continue
        if origin(url) != base:
            raise ConfigError('同一来源的状态、视频和分析 URL 必须同源')
        values = parse_qs(urlsplit(url).query).get('session', [])
        if values and any(v != p['session_id'] for v in values):
            raise ConfigError(key + ' 的 session 与配置不一致')
    if deadline(p) <= (datetime.now(timezone.utc).timestamp() if now is None else now):
        raise ConfigError('配置已到截止时间，请载入新配置')
    result = dict(p)
    result['fpga_bit_sha256'] = p['fpga_bit_sha256'].lower()
    return result

def validate_document(data, now=None):
    if not isinstance(data, dict) or data.get('schema') not in ('qjzc.windows.inputs.v1', 'qjzc.four_inputs.draft1'):
        raise ConfigError('不支持的配置格式；需要 qjzc.windows.inputs.v1 或 qjzc.four_inputs.draft1')
    items = data.get('inputs')
    if not isinstance(items, dict) or not 1 <= len(items) <= 8:
        raise ConfigError('配置应包含 1 至 8 个独立来源')
    result = {}; urls = set(); common = None
    old_slots = {'original': ('A', 'original'), 'gray': ('A', 'gray'),
                 'gaussian': ('A', 'gaussian'), 'histogram': ('A', 'histogram'), 'B': ('B', None)}
    for slot, raw in items.items():
        p = validate_profile(raw, now)
        if data['schema'] == 'qjzc.four_inputs.draft1':
            expected = old_slots.get(slot)
            if not expected or expected[0] != p['camera_id'] or expected[1] not in (None, p['processing_mode']):
                raise ConfigError('旧配置槽位与相机/模式不一致：' + slot)
        key = (p['camera_id'], p['processing_mode'])
        video_parts=urlsplit(p['video_url'])
        canonical=(origin(p['video_url']),video_parts.path or '/',tuple(sorted(parse_qsl(video_parts.query))))
        if key in result or canonical in urls:
            raise ConfigError('不能重复声明同一相机模式或视频 URL')
        current = origin(p['video_url'])
        if common is not None and common != current:
            raise ConfigError('所有视频来源必须同源')
        common = current; result[key] = p; urls.add(canonical)
    for camera in ('A','B'):
        for endpoint in ('analysis_url','events_url'):
            selected=[p for (c,m),p in result.items() if c==camera and p.get(endpoint)]
            if len(selected)>1:
                raise ConfigError(camera+'路的 '+endpoint+' 仅放在一个来源条目中，避免重复或歧义')
    return result

def load_profile_file(path):
    raw = path.read_bytes()
    if len(raw) > 256 * 1024:
        raise ConfigError('配置文件过大')
    try:
        data = json.loads(raw.decode('utf-8-sig'))
    except (UnicodeError, ValueError):
        raise ConfigError('配置不是有效的 UTF-8 JSON') from None
    return validate_document(data), hashlib.sha256(raw).hexdigest()

def validate_identity(profile, message):
    if not isinstance(message, dict):
        raise IdentityError('服务响应必须是 JSON 对象')
    for key in IDENTITY:
        actual = message.get(key)
        if key == 'fpga_bit_sha256' and isinstance(actual, str):
            actual = actual.lower()
        if actual != profile[key]:
            raise IdentityError(f'{key} 不匹配：期望 {profile[key]!r}，收到 {actual!r}')

def validate_status(profile, message):
    validate_identity(profile, message)
    if message.get('schema') not in ('qjzc.preview.v1', 'qjzc.preview.v2'):
        raise ConfigError('状态协议 schema 不匹配')
    if message.get('state') not in ('ARMED', 'ARMED_NO_CAPTURE', 'CAPTURING', 'ENDED'):
        raise ConfigError('无法识别板端状态：' + str(message.get('state')))
    return message

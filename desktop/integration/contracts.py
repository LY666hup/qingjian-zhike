"""Windows-side proposal; no inference or RK control. Explicit frame identity."""
from __future__ import annotations
import json, math

EDGES = {
    'mediapipe33': [(11,12),(11,13),(13,15),(12,14),(14,16),(11,23),(12,24),(23,24),(23,25),(25,27),(24,26),(26,28),(27,29),(29,31),(28,30),(30,32)],
    'coco17': [(5,6),(5,7),(7,9),(6,8),(8,10),(5,11),(6,12),(11,12),(11,13),(13,15),(12,14),(14,16)],
}
MODES={'original':'原始视频','gray':'灰度','gaussian':'高斯','histogram':'直方图均衡','unknown':'处理方式未知'}
def finite(v):
    return type(v) in (int,float) and math.isfinite(v)
def identity(m):
    keys=('stream_id','session_id','camera_id','frame_id')
    return tuple(m[k] for k in keys) if all(m.get(k) is not None for k in keys) else None
def validate_identity(m):
    if not isinstance(m,dict):raise ValueError('消息必须是 JSON 对象')
    if not all(isinstance(m.get(k),str) and 0<len(m[k])<=128 for k in ('stream_id','session_id','camera_id')):
        raise ValueError('缺少 stream/session/camera 标识')
    if type(m.get('frame_id')) is not int or not 0<=m['frame_id']<2**63:raise ValueError('frame_id 无效')
    if m.get('source') not in ('simulation','external','replay'):raise ValueError('来源标记无效')
    if m.get('source_timestamp_ns') is not None and (type(m['source_timestamp_ns']) is not int or m['source_timestamp_ns']<0):raise ValueError('源时间戳无效')
    if m.get('clock_domain') is not None and (not isinstance(m['clock_domain'],str) or len(m['clock_domain'])>128):raise ValueError('时钟域无效')
def parse_frame_header(data):
    if len(data)>65536:raise ValueError('帧元数据过大')
    m=json.loads(data);validate_identity(m)
    if m.get('schema')!='qjzc.frame.v1' or m.get('encoding')!='jpeg':raise ValueError('视频协议不匹配')
    if type(m.get('payload_bytes')) is not int or not 0<m['payload_bytes']<=16*1024*1024:raise ValueError('图像长度无效')
    if any(type(m.get(k)) is not int or not 0<m[k]<=4096 for k in ('width','height')):raise ValueError('图像尺寸无效')
    if m['width']*m['height']>4096*2160:raise ValueError('图像尺寸超限')
    if m.get('processing_mode') not in MODES:raise ValueError('处理模式无效')
    if m.get('processor') not in ('fpga','rk','simulation','unknown'):raise ValueError('处理来源无效')
    return m
def parse_analysis(data):
    if len(data)>65536:raise ValueError('分析消息过大')
    m=json.loads(data);validate_identity(m)
    if m.get('schema')!='qjzc.analysis.v1':raise ValueError('分析协议不匹配')
    for k in ('rep_count','correct_count','incorrect_count'):
        if m.get(k) is not None and (type(m[k]) is not int or m[k]<0):raise ValueError(k+' 无效')
    if m.get('quality_score') is not None and (not finite(m['quality_score']) or not 0<=m['quality_score']<=100):raise ValueError('评分无效')
    for k in ('action_type','action_phase','subject','pose_state'):
        if m.get(k) is not None and (not isinstance(m[k],str) or len(m[k])>256):raise ValueError(k+' 无效')
    if not isinstance(m.get('error_flags',[]),list) or any(not isinstance(x,str) or len(x)>256 for x in m.get('error_flags',[])):raise ValueError('纠错信息无效')
    persons=m.get('persons',[])
    if not isinstance(persons,list) or len(persons)>32:raise ValueError('人数超限')
    seen=set()
    for p in persons:
        if not isinstance(p,dict):raise ValueError('人体数据必须是对象')
        pid=p.get('person_id')
        if pid is not None:
            if not isinstance(pid,str) or not 0<len(pid)<=128 or pid in seen:raise ValueError('个人跟踪标识无效或重复')
            seen.add(pid)
        for k in ('rep_count','correct_count','incorrect_count'):
            if p.get(k) is not None and (type(p[k]) is not int or p[k]<0):raise ValueError('个人'+k+'无效')
        if p.get('quality_score') is not None and (not finite(p['quality_score']) or not 0<=p['quality_score']<=100):raise ValueError('个人评分无效')
        for k in ('action_type','action_phase'):
            if p.get(k) is not None and (not isinstance(p[k],str) or len(p[k])>256):raise ValueError('个人动作字段无效')
        if not isinstance(p.get('error_flags',[]),list) or len(p.get('error_flags',[]))>32 or any(not isinstance(e,str) or len(e)>256 for e in p.get('error_flags',[])):raise ValueError('个人纠错字段无效')
        profile=p.get('profile')
        if profile is not None:
            if not isinstance(profile,dict):raise ValueError('档案必须是对象')
            for k in ('student_id','display_name','class_name'):
                if profile.get(k) is not None and (not isinstance(profile[k],str) or not 0<len(profile[k])<=128):raise ValueError('档案字段无效')
            if profile.get('identity_status','unknown') not in ('unknown','unconfirmed','confirmed'):raise ValueError('身份状态无效')
            if profile.get('identity_status')=='confirmed' and not profile.get('student_id'):raise ValueError('已绑定身份缺少档案号')
        layout=p.get('layout');n={'mediapipe33':33,'coco17':17}.get(layout)
        if n is None or p.get('coordinate_space')!='normalized_image':raise ValueError('关键点编号/坐标系不支持')
        points=p.get('keypoints',[])
        if not isinstance(points,list) or len(points)!=n:raise ValueError('关键点数量不符')
        for q in points:
            if not isinstance(q,list) or len(q)!=3 or not all(finite(v) for v in q) or not 0<=q[2]<=1:raise ValueError('关键点无效')
    return m

"""RK decides when a repetition completes. Windows only applies idempotent messages."""
from pathlib import Path
import hashlib
import json
import math
import re
import sqlite3
import threading
import time
from urllib.parse import urlsplit,parse_qsl,urlencode,urlunsplit
from profiles import IDENTITY,validate_identity,ConfigError
from connection import SessionReceiver,classify

ACTION_NAMES={'CURL':'二头弯举','LATERAL_RAISE':'侧平举','FRONT_RAISE':'前平举','OVERHEAD_PRESS':'推举',
 'BENT_OVER_ROW':'俯身划船','OVERHEAD_TRICEPS':'臂屈伸','PUSHUP':'俯卧撑','SQUAT':'深蹲','PLANK':'平板支撑',
 'SITUP':'仰卧起坐','MOUNTAIN':'登山跑','HIGHKNEES':'高抬腿'}
ALIASES={'LATERAL':'LATERAL_RAISE','FRONT':'FRONT_RAISE','PRESS':'OVERHEAD_PRESS',
 'ROW':'BENT_OVER_ROW','TRICEPS':'OVERHEAD_TRICEPS','HIGHKNEE':'HIGHKNEES'}

def action_code(value):
    if not isinstance(value,str) or not re.fullmatch('[A-Za-z][A-Za-z0-9_]{0,63}',value):
        raise ConfigError('运动项目编号无效')
    return ALIASES.get(value.upper(),value.upper())

def valid_id(value,name):
    if not isinstance(value,str) or not 0<len(value)<=128:raise ConfigError(name+' 无效')
    return value

def validate_student_profile(value):
    if not isinstance(value,dict):raise ConfigError('学生档案必须是对象')
    for key in ('student_id','display_name','class_name'):
        if value.get(key) is not None and (not isinstance(value[key],str) or not 0<len(value[key])<=128):
            raise ConfigError('学生档案字段无效：'+key)
    if value.get('identity_status') not in (None,'unknown','unbound','unconfirmed','confirmed'):
        raise ConfigError('学生身份状态无效')
    if value.get('identity_status')=='confirmed' and not value.get('student_id'):
        raise ConfigError('已绑定学生缺少学号')
    return value

class EventStore:
    def __init__(self,path):
        self.path=Path(path);self.path.parent.mkdir(parents=True,exist_ok=True);self.lock=threading.RLock()
        self.db=sqlite3.connect(str(self.path),check_same_thread=False)
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.executescript('''
          CREATE TABLE IF NOT EXISTS scopes(scope TEXT PRIMARY KEY,cursor INTEGER NOT NULL);
          CREATE TABLE IF NOT EXISTS events(scope TEXT,event_id TEXT,event_seq INTEGER,digest TEXT,
            PRIMARY KEY(scope,event_id),UNIQUE(scope,event_seq));
          CREATE TABLE IF NOT EXISTS totals(scope TEXT,person TEXT,action TEXT,count INTEGER,duration_ms INTEGER,profile TEXT,
            PRIMARY KEY(scope,person,action));
        ''');self.db.commit()
    def scope(self,profile,training_id):
        valid_id(training_id,'training_id')
        return json.dumps([profile[k] for k in IDENTITY]+[training_id],separators=(',',':'))
    def cursor(self,profile,training_id):
        if not training_id:return 0
        with self.lock:
            row=self.db.execute('SELECT cursor FROM scopes WHERE scope=?',(self.scope(profile,training_id),)).fetchone()
            return row[0] if row else 0
    def apply(self,profile,message):
        validate_identity(profile,message)
        if message.get('schema')!='qjzc.exercise.events.v1':raise ConfigError('动作事件协议错误')
        training=valid_id(message.get('training_id'),'training_id');scope=self.scope(profile,training)
        events=message.get('events',[])
        if not isinstance(events,list) or len(events)>500:raise ConfigError('每页最多 500 条动作事件')
        with self.lock,self.db:
            self.db.execute('INSERT OR IGNORE INTO scopes VALUES (?,0)',(scope,))
            cursor=self.db.execute('SELECT cursor FROM scopes WHERE scope=?',(scope,)).fetchone()[0]
            snapshot=message.get('snapshot')
            if snapshot is not None:
                if not isinstance(snapshot,dict) or type(snapshot.get('through_seq')) is not int or snapshot['through_seq']<0:
                    raise ConfigError('动作快照 through_seq 无效')
                people=snapshot.get('persons')
                if not isinstance(people,list) or len(people)>32:raise ConfigError('动作快照人数无效')
                if snapshot['through_seq']>=cursor:
                    for person in people:
                        if not isinstance(person,dict):raise ConfigError('动作快照个人格式错误')
                        pid=valid_id(person.get('person_id'),'person_id');profile_data=person.get('profile',{})
                        validate_student_profile(profile_data)
                        for kind,field in [('count','counts_by_action'),('duration_ms','durations_ms')]:
                            values=person.get(field,{})
                            if not isinstance(values,dict) or len(values)>64:raise ConfigError('动作快照格式错误')
                            for action,value in values.items():
                                action=action_code(action)
                                if type(value) is not int or value<0:raise ConfigError('累计值必须是非负整数')
                                self._ensure(scope,pid,action,profile_data)
                                old=self.db.execute(f'SELECT {kind} FROM totals WHERE scope=? AND person=? AND action=?',(scope,pid,action)).fetchone()[0]
                                if old is not None and value<old:raise ConfigError('累计值回退；新训练请更换 training_id')
                                self.db.execute(f'UPDATE totals SET {kind}=? WHERE scope=? AND person=? AND action=?',(value,scope,pid,action))
                    cursor=snapshot['through_seq']
            applied=0
            for event in events:
                if not isinstance(event,dict):raise ConfigError('动作事件必须是对象')
                eid=valid_id(event.get('event_id'),'event_id');pid=valid_id(event.get('person_id'),'person_id')
                seq=event.get('event_seq');action=action_code(event.get('action_type'))
                if type(seq) is not int or seq<=0:raise ConfigError('event_seq 必须是正整数')
                digest=hashlib.sha256(json.dumps(event,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()
                old=self.db.execute('SELECT event_seq,digest FROM events WHERE scope=? AND event_id=?',(scope,eid)).fetchone()
                if old:
                    if old!=(seq,digest):raise ConfigError('同一 event_id 的内容发生变化')
                    continue
                if seq<=cursor:continue  # Already covered by an authoritative RK cumulative snapshot.
                if seq!=cursor+1:raise ConfigError('动作事件序号有缺口；需要 RK 重放缺失事件或提供累计快照')
                profile_data=event.get('profile',{})
                validate_student_profile(profile_data)
                self._ensure(scope,pid,action,profile_data)
                if event.get('event_type')=='rep_completed':
                    if type(event.get('delta')) is not int or event['delta']!=1:raise ConfigError('一次完成事件 delta 必须为 1')
                    self.db.execute('UPDATE totals SET count=COALESCE(count,0)+1 WHERE scope=? AND person=? AND action=?',(scope,pid,action))
                elif event.get('event_type')=='duration_updated':
                    value=event.get('duration_ms')
                    if type(value) is not int or value<0:raise ConfigError('持续时长无效')
                    old_duration=self.db.execute('SELECT duration_ms FROM totals WHERE scope=? AND person=? AND action=?',(scope,pid,action)).fetchone()[0]
                    if old_duration is not None and value<old_duration:raise ConfigError('时长回退；新训练请更换 training_id')
                    self.db.execute('UPDATE totals SET duration_ms=? WHERE scope=? AND person=? AND action=?',(value,scope,pid,action))
                else:raise ConfigError('无法识别的动作事件类型')
                self.db.execute('INSERT INTO events VALUES (?,?,?,?)',(scope,eid,seq,digest));cursor=seq;applied+=1
            self.db.execute('UPDATE scopes SET cursor=? WHERE scope=?',(cursor,scope))
            rows=self.db.execute('SELECT person,action,count,duration_ms,profile FROM totals WHERE scope=? ORDER BY person,action',(scope,)).fetchall()
            people={}
            for pid,action,count,duration,pdata in rows:
                person=people.setdefault(pid,dict(person_id=pid,counts_by_action={},durations_ms={},profile=json.loads(pdata)))
                if count is not None:person['counts_by_action'][action]=count
                if duration is not None:person['durations_ms'][action]=duration
            return dict(persons=list(people.values()),training_id=training,event_seq=cursor,applied_events=applied)
    def _ensure(self,scope,pid,action,pdata):
        self.db.execute('INSERT OR IGNORE INTO totals VALUES (?,?,?,NULL,NULL,?)',(scope,pid,action,json.dumps(pdata,ensure_ascii=False)))
        if pdata:self.db.execute('UPDATE totals SET profile=? WHERE scope=? AND person=? AND action=?',(json.dumps(pdata,ensure_ascii=False),scope,pid,action))
    def close(self):
        with self.lock:self.db.close()

class ExerciseReceiver(SessionReceiver):
    def __init__(self,profile,log,store,**kwargs):
        super().__init__(profile,log,**kwargs);self.store=store;self.training=None;self.name+='-exercise-events'
    def run(self):
        failures=0
        try:
            while not self.stop_event.is_set():
                try:
                    self.check()
                    with self.lock:self.counts['attempts']+=1
                    snap=self.read_status()
                    if snap['state'] in ('ARMED','ARMED_NO_CAPTURE'):
                        self.set_state('waiting','等待板端启动');self.wait(1);continue
                    url=urlsplit(self.profile['events_url']);query=dict(parse_qsl(url.query))
                    query['after']=str(self.store.cursor(self.profile,self.training))
                    if self.training:query['training_id']=self.training
                    target=urlunsplit((url.scheme,url.netloc,url.path,urlencode(query),''))
                    raw=self.read_json(target,'exercise',1024*1024)
                    value=self.store.apply(self.profile,raw);self.training=value['training_id']
                    with self.lock:
                        self.counts['received']+=len(raw.get('events',[]));self.counts['decoded']+=value['applied_events']
                        self.latest=value,None,time.monotonic()
                    self.event('EXERCISE_RESULT',training_id=self.training,cursor=value['event_seq'],applied=value['applied_events'])
                    self.set_state('receiving','正在接收运动记录');failures=0;self.wait(.3)
                except Exception as e:
                    if self.user_stopped or self.stop_event.is_set():break
                    category=classify(e);self.event('FAILURE',category=category,reason=str(e))
                    if category!='retrying':self.set_state(category,str(e));break
                    delay=self.backoff[min(failures,len(self.backoff)-1)];failures+=1
                    with self.lock:self.counts['retries']+=1;self.next_retry_at=self.wall()+delay
                    self.set_state('retrying',str(e));self.event('RETRY_SCHEDULED',seconds=delay,retry_at=self.next_retry_at)
                    try:self.wait(delay)
                    finally:
                        with self.lock:self.next_retry_at=None
        except Exception as e:
            if not self.user_stopped:self.set_state(classify(e),str(e))
        finally:
            self.shutdown()
            with self.lock:self.terminal=True;self.latest=None;self.next_retry_at=None
            self.event('STOPPED',counts=self.counts,state=self.state)

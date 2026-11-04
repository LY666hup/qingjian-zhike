"""Per-local-track ORIGINAL action engines. No original file or threshold changes."""
from pathlib import Path
from types import SimpleNamespace
from concurrent.futures import ThreadPoolExecutor
import sys,time,math,hashlib
from local_tracks import LocalTracks,crop_roi,project_landmarks,pose_belongs_to_box

POSE_ROOT=Path('/root/ly_debug_tools/pose_nine_20261103_r1')
ACTIONS=('PUSHUP','SQUAT','CURL','LATERAL_RAISE','FRONT_RAISE','OVERHEAD_PRESS','BENT_OVER_ROW','OVERHEAD_TRICEPS','PLANK')

class PersonRuntime:
    def __init__(self,key,common,registry,mp,cv2,model):
        self.key=key;self.common=common;self.registry=registry;self.mp=mp;self.cv2=cv2
        self.landmarker=common.create_landmarker(str(model));self.engines={};self.specs={};self.action=None
        self.last_timestamp=-1;self.geometry_mode=None

    def select(self,token):
        if token not in ACTIONS:raise ValueError('Unknown original action')
        if token==self.action:return
        if self.action:self.engines[self.action].deactivate()
        if token not in self.engines:self.specs[token],self.engines[token]=self.registry.load_action_engine(token)
        self.action=token;self.engines[token].activate()

    def pause(self):
        for engine in self.engines.values():engine.recover_after_camera_reconnect()

    def process(self,frame,track,token,timestamp,mode):
        self.select(token);engine=self.engines[token];h,w=frame.shape[:2]
        if timestamp<=self.last_timestamp:
            return self.packet(track,None,engine.snapshot(),False,'duplicate_frame',[0,0,w,h],mode,0)
        if self.last_timestamp>=0 and timestamp-self.last_timestamp>1200:self.pause()
        if self.geometry_mode is not None and self.geometry_mode!=mode:self.pause()
        self.geometry_mode=mode
        roi=[0,0,w,h]if mode=='full_frame'else crop_roi(track.box,w,h)
        x1,y1,x2,y2=roi;crop=frame[y1:y2,x1:x2];rgb=self.cv2.cvtColor(crop,self.cv2.COLOR_BGR2RGB)
        started=time.monotonic();result=self.landmarker.detect_for_video(self.mp.Image(image_format=self.mp.ImageFormat.SRGB,data=rgb),timestamp)
        ms=(time.monotonic()-started)*1000;self.last_timestamp=timestamp
        pts=result.pose_landmarks[0]if result.pose_landmarks else None
        points=project_landmarks(pts,roi,w,h)if pts else None
        if points and not pose_belongs_to_box(points,track.box,w,h):points=None
        eligible=bool(track.eligible and points)
        if eligible:snapshot=engine.process_fresh(points,{},timestamp/1000).snapshot
        else:snapshot=engine.no_person_fresh().snapshot
        reason='ok'if eligible else'pose_absent'if not points else'track_warmup'
        return self.packet(track,points,snapshot,eligible,reason,roi,mode,ms)

    def packet(self,track,points,snapshot,eligible,reason,roi,mode,ms):
        action={k:getattr(snapshot,k)for k in ('mode','count','score','tip','phase','metric_name','metric_value','aux_text','person_text','stats')}
        action['stats']=dict(action['stats'])
        for key in ('count','score','metric_value'):
            if not isinstance(action[key],(int,float))or not math.isfinite(action[key]):raise ValueError('Nonfinite original action snapshot')
        return dict(track_id=track.key,student_id=None,identity_status='local_track_only',continuity='unambiguous_local_iou'if track.eligible else'initializing',
                    box=list(track.box),pose_present=bool(points),keypoints=[dict(index=i,x=p.x,y=p.y,z=p.z,visibility=p.visibility,presence=p.presence)for i,p in enumerate(points or [])],
                    rep_count=action['count']if eligible else None,training_count_eligible=eligible,score_eligible=False,count_reason=reason,
                    action=action,all_counts={a:e.snapshot().count for a,e in self.engines.items()},
                    geometry=dict(mode=mode,roi=list(roi),coordinate_space='image_normalized',z_scale='roi_width/full_width',clipped=False),inference_ms=ms)

    def close(self):self.landmarker.close()

class MultiRuntime:
    def __init__(self,max_people=2):
        sys.path.insert(0,str(POSE_ROOT/'pi_algo'))
        import cv2,mediapipe as mp,numpy as np
        import ai_fitness_common as common,ai_fitness_action_registry as registry
        self.cv2=cv2;self.mp=mp;self.np=np;self.common=common;self.registry=registry
        self.model=POSE_ROOT/'models/pose_landmarker_full.task'
        if hashlib.sha256(self.model.read_bytes()).hexdigest()!='4eaa5eb7a98365221087693fcc286334cf0858e2eb6e15b506aa4a7ecdcec4ad':raise ValueError('Original full model SHA differs')
        probe=common.create_landmarker(str(self.model));probe.close()
        self.tracker=LocalTracks(max_people);self.people={}
        self.pool=ThreadPoolExecutor(max_workers=max_people);self.last_record=None;self.last_source=None

    def process(self,record,jpeg,yolo,action,revision):
        token=(tuple(record[k]for k in ('session_id','boot_id','camera_id','processing_mode','fpga_bit_sha256','processor')),record['input_record_id'],record['jpeg_sha256'])
        if token==self.last_record:return None
        self.last_record=token
        if record.get('observed_frame_id')is not None and self.last_source==(token[0],record['observed_frame_id']):return None
        self.last_source=(token[0],record.get('observed_frame_id'))
        data=self.np.frombuffer(jpeg,self.np.uint8);frame=self.cv2.imdecode(data,self.cv2.IMREAD_COLOR)
        if frame is None or list(frame.shape[1::-1])!=record['preview_size']:raise ValueError('Actual JPEG size')
        boxes=[d['xyxy']for d in yolo['detections']if d['class_id']==0]
        tracks=self.tracker.update(boxes,record['timestamp_ms']);active={t.key for t in tracks}
        for key in list(self.people):
            if key not in active:self.people.pop(key).close()
        jobs=[];mode='full_frame'if len(boxes)==1 else'person_roi'
        for t in tracks:
            if t.key not in self.people:self.people[t.key]=PersonRuntime(t.key,self.common,self.registry,self.mp,self.cv2,self.model)
            jobs.append(self.pool.submit(self.people[t.key].process,frame,t,action,record['timestamp_ms'],mode))
        result=[f.result()for f in jobs]
        return dict(persons=result,tracking_reason=self.tracker.reason,detected_people=len(boxes),analysis_limit=self.tracker.limit,action_token=action,action_revision=revision)

    def close(self):
        self.pool.shutdown(wait=True)
        for p in self.people.values():p.close()

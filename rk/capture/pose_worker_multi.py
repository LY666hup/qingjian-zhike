"""Independent latest-frame pose consumer; no wait inserted into YOLO publication."""
from pathlib import Path
import argparse,json,sys,base64,time,signal,threading,hashlib
from control import read,atomic,IDENTITY
from action_control import desired,matches_action
from multi_runtime import MultiRuntime,POSE_ROOT
from change_only_ack import ChangedACK

def main():
 p=argparse.ArgumentParser();p.add_argument('--window',required=True);a=p.parse_args()
 c=read(a.window);root=Path(c['run_dir'])
 if c.get('approval_status')!='APPROVED_BY_USER':raise SystemExit('Approval absent')
 if Path('/proc/sys/kernel/random/boot_id').read_text().strip()!=c['boot_id']:raise SystemExit('Boot changed')
 runtime=MultiRuntime(2);stop=threading.Event();last=None;count=0;last_binding=None;ack=ChangedACK(root/'ai/action_ack.json')
 for sig in(signal.SIGINT,signal.SIGTERM):signal.signal(sig,lambda *_:stop.set())
 print('POSE_WORKER_READY_ORIGINAL_NINE_MAX2',flush=True)
 try:
  while not stop.is_set()and time.monotonic()*1000<c['expires_monotonic_ms']:
   if Path('/proc/sys/kernel/random/boot_id').read_text().strip()!=c['boot_id']:raise ValueError('Boot changed')
   if read(root/'run.json',{}).get('state')=='ENDED':break
   action=desired(c);ack.update(action)
   obj=read(root/'ai/pose_pending.json')
   if not obj:stop.wait(.03);continue
   request=read(root/'ai/request.json')
   if request and request.get('binding_id')!=last_binding:
    runtime.close();runtime=MultiRuntime(2);last_binding=request['binding_id'];last=None
   if request!=obj.get('binding'):stop.wait(.03);continue
   submitted_action=obj.get('action',{})
   if any(submitted_action.get(k)!=action.get(k)for k in ('action_token','action_revision')):stop.wait(.03);continue
   record=obj['record'];jpeg=base64.b64decode(obj['jpeg'],validate=True);identity={k:record[k]for k in IDENTITY}
   if request is None:stop.wait(.03);continue
   y=obj['yolo']
   if y.get('identity')!=identity or y.get('binding_id')!=request['binding_id'] or any(y.get(k)!=record.get(k)for k in ('input_record_id','jpeg_sha256','timestamp_ms','time_source','preview_size')):raise ValueError('YOLO/frame binding differs')
   if identity!=request['identity']or hashlib.sha256(jpeg).hexdigest()!=record['jpeg_sha256']:raise ValueError('Frozen input differs')
   if not record.get('ready')or not 0<=time.monotonic()*1000-record['timestamp_ms']<=1500:stop.wait(.03);continue
   token=(request['binding_id'],record['input_record_id'],record['jpeg_sha256'],action['action_revision'])
   if token==last:stop.wait(.03);continue
   last=token;started=time.monotonic()
   result=runtime.process(record,jpeg,obj['yolo'],action['action_token'],action['action_revision'])
   if result is None:stop.wait(.01);continue
   panel=dict(schema='qjzc.pose.multi_action.v1',**identity,identity=identity,input_record_id=record['input_record_id'],jpeg_sha256=record['jpeg_sha256'],preview_size=record['preview_size'],preview_sequence=record.get('preview_sequence'),
    timing=dict(timestamp_ms=record['timestamp_ms'],time_source=record['time_source']),backend='mediapipe_cpu',inference_device='rk-cpu',model_sha256='4eaa5eb7a98365221087693fcc286334cf0858e2eb6e15b506aa4a7ecdcec4ad',
    pose_present=any(p['pose_present']for p in result['persons']),**result,binding_id=request['binding_id'],pipeline_age_ms=time.monotonic()*1000-record['timestamp_ms'],pose_batch_ms=(time.monotonic()-started)*1000,formal_grades_available=False)
   from fusion_contract import verify_pair
   verify_pair(record,jpeg,panel,obj['yolo'])
   if not matches_action(c,panel)or read(root/'ai/request.json')!=request:continue
   if read(root/'run.json',{}).get('state')!='CAPTURING':continue
   atomic(root/'ai/pose_bundle.json',dict(panel=panel,yolo=obj['yolo'],completed_ms=time.monotonic()*1000));count+=1
   with(root/'pose_results_r2.jsonl').open('a')as f:
    if f.tell()>128*1024**2:raise ValueError('Pose log budget')
    f.write(json.dumps(panel,allow_nan=False)+'\n')
   atomic(root/'ai/pose_health.json',dict(state='ACTIVE',processed=count,heartbeat_ms=time.monotonic()*1000,action_revision=action['action_revision']))
 finally:runtime.close();atomic(root/'ai/pose_health.json',dict(state='ENDED',processed=count))
if __name__=='__main__':main()

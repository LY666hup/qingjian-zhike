"""Explicit single-window continuous video + RK NPU YOLO + original CPU pose."""
from pathlib import Path
import os,sys,json,time,signal,subprocess,urllib.request,hashlib,argparse
from control import read,atomic
from devices import owners
ROOT=Path(__file__).resolve().parent
POSE=Path('/root/ly_debug_tools/pose_nine_20261103_r1')
MODEL=Path('/root/ly_debug_evidence/qjzc_realtime_35d63b99a286/model/yolov8n_float.rknn')
SITE='/root/ly_debug_tools/yolo_vendor_feae90c3/site'

def main():
 parser=argparse.ArgumentParser();parser.add_argument('--decoder-proof',type=Path);parser.add_argument('--action',default='SQUAT',choices=['PUSHUP','SQUAT','CURL','LATERAL_RAISE','FRONT_RAISE','OVERHEAD_PRESS','BENT_OVER_ROW','OVERHEAD_TRICEPS','PLANK']);args=parser.parse_args()
 c=read(ROOT/'window.approved.json');run=Path(c['run_dir']);jobs=[]
 if Path('/proc/sys/kernel/random/boot_id').read_text().strip()!=c['boot_id']:raise SystemExit('Boot changed')
 seconds=min(3500,int((c['expires_monotonic_ms']-time.monotonic_ns()//1000000)/1000)-90)
 if seconds<30:raise SystemExit('Window too short; no start')
 request=read(run/'ai/request.json')
 if not request or request['source_key']!='B_original':raise SystemExit('Windows: select B original and enable YOLO first')
 if (run/'DEMO_ONCE.token').exists()or(run/'CAPTURE_ONCE.token').exists():raise SystemExit('Already attempted; no automatic retry')
 if not(POSE/'venv/bin/python').is_file():raise SystemExit('Pose environment missing')
 if hashlib.sha256(MODEL.read_bytes()).hexdigest()!='c9f43d33ca07413bee582baf3bfd76a91b87c240482ff9e7a91e47f3a54b495b':raise SystemExit('YOLO model SHA')
 if args.decoder_proof:
  from verify_decoder import verify_proof
  verify_proof(args.decoder_proof)
 owners(require_idle=True)
 print('Continuous B video + NPU YOLO + original CPU MediaPipe. Ctrl+C stops owned workers.',flush=True)
 print('No20s countdown. Window limit '+str(seconds)+'s. No restart/retry. Initial pose action: '+args.action+'.',flush=True)
 if input('Confirm bit/power/wiring and approve camera+NPU+CPU; type RUN_DEMO: ').strip()!='RUN_DEMO':raise SystemExit('Cancelled')
 with(run/'DEMO_ONCE.token').open('x')as f:f.write(c['session_id'])
 c['approval_status']='APPROVED_BY_USER';c['capture_seconds']=seconds;c['pose_action']=args.action;atomic(ROOT/'window.approved.json',c);atomic(run/'capture_approval.json',c)
 clean=dict(os.environ);clean.pop('PYTHONPATH',None);clean.pop('PYTHONHOME',None);clean['PYTHONNOUSERSITE']='1';clean['MPLCONFIGDIR']=str(POSE/'logs/mplconfig')
 def start(name,args,env):
  with(run/(name+'.stdout.log')).open('xb')as so,(run/(name+'.stderr.log')).open('xb')as se:
   p=subprocess.Popen(args,stdout=so,stderr=se,env=env,start_new_session=True)
  jobs.append((name,p));return p
 cap=None
 try:
  cpu=start('pose',[str(POSE/'venv/bin/python'),'-B',str(ROOT/'pose_worker_multi.py'),'--window',str(ROOT/'window.approved.json')],clean)
  until=time.monotonic()+25
  while time.monotonic()<until:
   if cpu.poll()is not None:raise RuntimeError('Pose worker failed; see pose.stderr.log')
   if 'POSE_WORKER_READY_ORIGINAL_NINE_MAX2' in (run/'pose.stdout.log').read_text(errors='replace'):break
   time.sleep(.1)
  else:raise RuntimeError('Pose worker startup timeout; camera not started')
  np_env=dict(clean,PYTHONPATH=SITE)
  npu_args=['/usr/bin/python3','-B',str(ROOT/'persistent_yolo.py'),'--window',str(ROOT/'window.approved.json'),'--model',str(MODEL),'--max-seconds',str(seconds+30),'--execute-approved']
  if args.decoder_proof:npu_args+=['--decoder-proof',str(args.decoder_proof.resolve())]
  npu=start('npu',npu_args,np_env)
  until=time.monotonic()+20
  while time.monotonic()<until:
   if npu.poll()is not None:raise RuntimeError('NPU failed; see npu.stderr.log; camera not started')
   h=read(run/'ai/health.json',{})
   if h.get('state')=='WAITING_SOURCE'and h.get('binding_id')==request['binding_id']:break
   time.sleep(.1)
  else:raise RuntimeError('NPU startup timeout; camera not started')
  print('YOLO_AND_POSE_READY; STARTING_CONTINUOUS_VIDEO_NOW. Ctrl+C stops.',flush=True)
  cap=start('capture',['timeout','--signal=INT','--kill-after=1',str(seconds+3),'/usr/bin/python3','-B',str(ROOT/'qj5v_capture.py'),'--window',str(ROOT/'window.approved.json'),'--protocol',str(ROOT/'protocol.template.json'),'--execute-approved'],clean)
  while cap.poll()is None:
   if cpu.poll()is not None or npu.poll()is not None:raise RuntimeError('AI worker stopped; stop owned capture')
   time.sleep(.2)
  print('CAPTURE_EXIT='+str(cap.returncode),flush=True)
 except KeyboardInterrupt:print('USER_STOP_REQUESTED',flush=True)
 finally:
  for name,p in reversed(jobs):
   if p.poll()is None:
    os.killpg(p.pid,signal.SIGINT if name=='capture' else signal.SIGTERM)
    try:p.wait(timeout=3 if name=='capture' else 5)
    except subprocess.TimeoutExpired:os.killpg(p.pid,signal.SIGKILL);p.wait();print('FORCED_STOP='+name,flush=True)
  release=owners();atomic(run/'demo_device_release.json',release)
  print('DEVICE_RELEASE='+json.dumps(release),flush=True);print('RUN_DIR='+str(run),flush=True);print('DEMO_ENDED_NO_RETRY',flush=True)
if __name__=='__main__':main()

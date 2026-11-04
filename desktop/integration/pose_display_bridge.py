"""Offline V6 handoff adapter; keeps raw evidence and does not run inference."""
from pathlib import Path
import sys,json,copy,hashlib
BASE=Path(__file__).resolve().parent.parent/'five_input_showcase_20261031'
sys.path.insert(0,str(BASE))
from contracts import parse_analysis

def prepare(raw):
 if raw.get('source')!='replay' or raw.get('pose_backend')!='mediapipe_cpu':raise ValueError('This adapter is for explicit CPU replay only')
 if 'source_frame_id' not in raw or raw['source_frame_id'] is not None:raise ValueError('Expected explicit null sensor frame identity')
 candidate=copy.deepcopy(raw);changes=[]
 for i,p in enumerate(candidate.get('persons',[])):
  if p.get('layout')!='mediapipe33':raise ValueError('V6 expects 33-point MediaPipe layout')
  profile=p.get('profile',{})
  if profile.get('identity_status')=='unbound':profile['identity_status']='unknown';changes.append({'field':f'persons[{i}].profile.identity_status','raw':'unbound','validation_value':'unknown','meaning':'not bound to a confirmed student'})
 parse_analysis(json.dumps(candidate,ensure_ascii=False).encode())
 absent=not candidate.get('persons')
 if absent and any(candidate.get(k) is not None for k in ('rep_count','correct_count','incorrect_count','quality_score')):raise ValueError('Absent pose must not claim counts or scores')
 return candidate,changes

def display_class():
 from sprint_app import SprintMonitor
 class PoseMonitor(SprintMonitor):
  def present_pose(self,data):
   raw=json.loads(data);candidate,changes=prepare(raw)
   super().present_pose(json.dumps(candidate,ensure_ascii=False).encode())
   self.pose_json.setPlainText(json.dumps(raw,ensure_ascii=False,indent=2))
   value=lambda k:'--' if raw.get(k) is None else str(raw[k])
   self.pose_counts.setText('动作次数 '+value('rep_count')+' · 正确 '+value('correct_count')+' · 错误 '+value('incorrect_count')+' · 评分 '+value('quality_score'))
   self.pose_state.setText('mediapipe_cpu · 离线回放 · '+('33点已载入' if raw.get('persons') else '未检出姿态')+' · preview-only／未严格同帧')
   self.last_pose_provenance={'raw_sha256':hashlib.sha256(data).hexdigest(),'frame_id':raw['frame_id'],'session_id':raw['session_id'],'source_frame_id':None,'pose_backend':'mediapipe_cpu','transport':'offline_replay','validation_adaptations':changes,'no_sensor_overlay':True}
 return PoseMonitor

if __name__=='__main__':
 import sprint_app
 sprint_app.SprintMonitor=display_class()
 raise SystemExit(sprint_app.main())

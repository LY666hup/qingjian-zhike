"""Actual GUI offline checks. No network endpoints, camera or fake live display."""
from pathlib import Path
import sys,json,socket,time,hashlib
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QTimer
from PySide6.QtNetwork import QNetworkAccessManager
from sprint_app import SprintMonitor,ROOT
from input_contract import validate_profiles,validate_status

E=ROOT/('sprint_validation_'+str(time.time_ns()));E.mkdir(exist_ok=False);calls=[];checks=[]
def blocked(*a,**k):calls.append(str(a));raise AssertionError('NETWORK BLOCKED')
socket.socket.connect=blocked;socket.socket.connect_ex=blocked;socket.create_connection=blocked;QNetworkAccessManager.get=blocked
app=QApplication([]);app.setQuitOnLastWindowClosed(False);w=SprintMonitor();w.resize(1600,900)
cfg=json.loads((ROOT/'display_config_demo_snapshots.json').read_bytes());cfg['views']['gray']['kind']='none';cfg['views']['gray']['location']='';cfg['analysis']['rk_ai_url']=''
path=E/'offline_config.json';path.write_text(json.dumps(cfg),encoding='utf8');w.config_path=path;w.load_config();w.show()
def check(name,value):checks.append({'name':name,'pass':bool(value)});print(json.dumps(checks[-1]),flush=True)
def later():
 try:
  w.refresh_sprint_labels();check('four fixed positions',len(w.slots)==4 and all(t.isVisible() for t in w.slots.values()))
  check('B explicit not connected','无 B 服务' in w.slots['B'].sprint_label.text())
  check('historical labels',all('非实时' in w.slots[k].sprint_label.text() for k in ('gaussian','histogram')))
  check('two real diagnostic snapshots loaded',all(not w.slots[k].last_image.isNull() for k in ('gaussian','histogram')))
  check('gray offline not fake',w.slots['gray'].last_image.isNull());check('pose backend not connected','未接入' in w.pose_state.text())
  w.grab().save(str(E/'FOUR_SLOTS_OFFLINE.png'));width=w.slots['gray'].width();w.single_slot('gray')
  QTimer.singleShot(200,lambda:after_full(width))
 except Exception as e:check('exception '+repr(e),False);finish()
def after_full(width):
 try:
  check('single view expands',w.slots['gray'].width()>width*1.5 and not w.ai_panel.isVisible());w.grab().save(str(E/'SINGLE_GRAY_OFFLINE.png'));w.single_slot('gray')
  check('restore four',all(t.isVisible() for t in w.slots.values()))
  tabs=w.diag_dock.widget();tabs.setCurrentIndex(tabs.count()-2);w.diag_dock.show();w.grab().save(str(E/'POSE_BACKEND_NOT_CONNECTED.png'))
  check('RK panel retained',hasattr(w,'rk_ai_people'));check('per slot stats',set(w.metrics_export())=={'gray','gaussian','histogram','B'})
  # Four independent same-origin profiles; these are data fixtures only.
  ps={k:dict(camera_id='B' if k=='B' else 'A',processing_mode='gray' if k=='B' else k,session_id='SYNTHETIC_'+k,boot_id='SYNTHETIC_BOOT',fpga_bit_sha256='a'*64,processor='fpga',video_url='http://offline.invalid/'+k+'/stream.mjpg',status_url='http://offline.invalid/'+k+'/status.json',authorized_until='2099-01-01T00:00:00+08:00') for k in w.slots}
  check('four profiles valid',len(validate_profiles({'schema':'qjzc.four_inputs.draft1','inputs':ps}))==4)
  for field in ('camera_id','session_id','boot_id','fpga_bit_sha256','processing_mode'):
   s={**ps['gray'],'state':'CAPTURING',field:'wrong'}
   try:validate_status(ps['gray'],s);ok=False
   except ValueError:ok=True
   check('reject wrong '+field,ok)
  bad=json.loads(json.dumps(ps));bad['B']['video_url']='http://other.invalid/b'
  try:validate_profiles({'schema':'qjzc.four_inputs.draft1','inputs':bad});ok=False
  except ValueError:ok=True
  check('reject cross-origin',ok)
  try:w.present_pose(json.dumps({'device':'rk3576-npu','detections':[]}).encode());ok=False
  except Exception:ok=True
  check('YOLO boxes not accepted as pose',ok)
  # No synthetic pose is displayed on the screenshot; the backend stays unconnected.
 except Exception as e:check('exception '+repr(e),False)
 finish()
def finish():
 check('network requests zero',not calls);(E/'metrics_offline.json').write_text(json.dumps(w.metrics_export(),ensure_ascii=False,indent=2),encoding='utf8');w.close();QTimer.singleShot(500,app.quit)
QTimer.singleShot(1800,later);QTimer.singleShot(10000,app.quit);rc=app.exec()
(E/'result.json').write_text(json.dumps({'scope':'OFFLINE actual GUI and synthetic profile checks only','checks':checks,'network_attempts':calls,'app_exit_rc':rc,'pose_backend':'NOT_CONNECTED','new_live':'NOT_TESTED'},ensure_ascii=False,indent=2),encoding='utf8')
assert checks and all(c['pass'] for c in checks) and rc==0

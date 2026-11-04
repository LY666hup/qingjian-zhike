"""Independent sprint presentation; frozen app.py remains unchanged."""
from pathlib import Path
import json,sys,time,hashlib,base64
from datetime import datetime,timezone
from PySide6.QtCore import QTimer,Qt
from PySide6.QtWidgets import QApplication,QWidget,QVBoxLayout,QLabel,QPlainTextEdit,QPushButton,QFileDialog
from PySide6.QtGui import QImage,QPixmap
from app import Monitor
from video_sources import SourceSpec
from input_contract import validate_profiles,validate_status
from contracts import parse_analysis
import receivers,http_mjpeg as H

ROOT=Path(__file__).resolve().parent
class StrictFrames(H.HttpMjpegFrames):
 def __init__(self,spec,profile):super().__init__(spec,allow_start=False);self.profile=profile;self.video_gets=0
 def request(self,url,kind):
  if datetime.now(timezone.utc).timestamp()>=datetime.fromisoformat(self.profile['authorized_until']).timestamp():raise ValueError('Window expired')
  if kind=='video':
   if self.video_gets:raise ValueError('No reconnect')
   if self.snapshot.get('state')!='CAPTURING':raise ValueError('Do not start capture via GET')
   validate_status(self.profile,self.snapshot);self.video_gets+=1
  return super().request(url,kind)
 def read_status(self):
  c=r=None
  try:
   c,r=self.request(self.spec.status_url,'status')
   if r.status!=200:raise ValueError('Status HTTP '+str(r.status))
   raw=r.read(H.MAX_STATUS+1);s=H.parse_preview_status(raw);validate_status(self.profile,s)
   with self.lock:self.snapshot=s
   if s['state']=='ENDED':self.ended('同会话ENDED · 保留末帧，非实时')
   return s
  except Exception:
   with self.lock:self.terminal=True;self.status=('blocked','身份/状态核对失败 · 禁止重连')
   self.shutdown('video');raise
  finally:
   if r:r.close()
   if c:c.close()
 def run(self):
  try:super().run()
  finally:self.terminal=True

class SprintMonitor(Monitor):
 def __init__(self):
  self.sprint_ready=False;self.profiles={};self.started_inputs=set();super().__init__();self.sprint_ready=True
  self.slots={'original':self.camera_tiles['A']['original'],'gray':self.camera_tiles['A']['gray'],'gaussian':self.camera_tiles['A']['gaussian'],'histogram':self.camera_tiles['A']['histogram'],'B':self.camera_tiles['B']['original']}
  self.slots['original'].title.setText('A 原始视频')
  self.slots['gray'].title.setText('A 灰度');self.slots['B'].title.setText('B 路未接入')
  self.camera_combo.setEnabled(False);self.ai_panel.swap_button.setEnabled(False)
  self.comparison_heading.layout().itemAt(0).widget().setText('FPGA输出与诊断快照')
  self.camera_hint.setText('仅A灰度有实流基线 · 快照非实时 · B未接入')
  self.banner=QLabel('五个独立来源位置；当前并非五路实时。中间仅放大所选来源。');self.banner.setWordWrap(True);self.comparison_heading.layout().addWidget(self.banner)
  for i,(key,tile) in enumerate(self.slots.items(),1):
   tile.findChild(QLabel,'cameraIndex').setText(f'{i:02d}')
   tile.select.disconnect();tile.full.disconnect();tile.canvas.double_clicked.disconnect()
   tile.select.connect(lambda _,k=key:self.choose(k));tile.full.connect(lambda _,k=key:self.single_slot(k));tile.canvas.double_clicked.connect(lambda k=key:self.single_slot(k))
   label=QLabel('');label.setWordWrap(True);label.setStyleSheet('font-size:13px;font-weight:600;color:#ffd166;');tile.layout().insertWidget(1,label);tile.sprint_label=label
  b=self.slots['B'];b.canvas.setMinimumHeight(60);b.canvas.setMaximumHeight(85);b.display_source.hide();b.playback_metrics.hide();b.content_metrics.hide()
  self.ai_panel.title.setText('A路预览 · 分析结果独立显示');self.ai_panel.viewport.pip.hide()
  self.setup_pose_panel();self.setup_inputs_panel();self.layout_video_views(None);self.choose('gray')
  self.sprint_timer=QTimer(self);self.sprint_timer.setInterval(500);self.sprint_timer.timeout.connect(self.refresh_sprint_labels);self.sprint_timer.start();self.refresh_sprint_labels()
  btn=QPushButton('载入新窗口配置（不自动连接）');btn.clicked.connect(self.load_profiles_dialog);self.controls.layout().insertWidget(0,btn)
 def layout_video_views(self,single):
  if not getattr(self,'sprint_ready',False):return super().layout_video_views(single)
  for tile in self.all_tiles():self.grid.removeWidget(tile);tile.hide()
  for row in range(3):self.grid.setRowStretch(row,0 if single else (2 if row<2 else 1))
  for col in range(2):self.grid.setColumnStretch(col,0 if single else 1)
  if single:
   if self.sprint_selected=='B':self.slots['B'].canvas.setMaximumHeight(16777215)
   tile=self.slots[self.sprint_selected];self.grid.addWidget(tile,0,0,3,2);tile.show();self.body.setStretch(0,1);self.body.setStretch(1,0);self.body.setStretch(2,0);self.ai_panel.hide();self.roster.hide()
  else:
   self.slots['B'].canvas.setMaximumHeight(85)
   for i,(key,tile) in enumerate(self.slots.items()):
    if key=='B':self.grid.addWidget(tile,2,0,1,2)
    else:self.grid.addWidget(tile,i//2,i%2)
    tile.show()
   self.ai_panel.show();self.roster.show();self.body.setStretch(0,4);self.body.setStretch(1,5);self.body.setStretch(2,1)
 def choose(self,key):
  self.sprint_selected=key;tile=self.slots[key];self.active_camera=tile.camera_slot;self.tiles=self.camera_tiles[tile.camera_slot];self.mode_integration=self.camera_modes[tile.camera_slot]
  super().select(tile.key)
 def single_slot(self,key):self.choose(key);self.single=None if self.single else key;self.layout_video_views(self.single)
 def toggle_single(self,key):
  if not getattr(self,'sprint_ready',False):return super().toggle_single(key)
  self.single_slot(self.sprint_selected)
 def refresh_sprint_labels(self):
  for key,tile in self.slots.items():
   if key=='B' and key not in self.profiles:tile.sprint_label.setText('B 路未接入（无独立 sensor、无 B 服务、无 B session）')
   elif key in ('gaussian','histogram') and tile.spec.kind=='snapshot':tile.sprint_label.setText('非实时 · 历史板端诊断快照')
   elif key=='original':tile.sprint_label.setText('实时 FPGA 原始输出' if tile.receiver.status=='connected' and tile.spec.kind=='mjpeg' else 'A 原始独立待接入 · 不复制灰度画面')
   else:tile.sprint_label.setText('实时 FPGA 输出' if tile.receiver.status=='connected' and tile.spec.kind=='mjpeg' else '未连接／已结束 · 非实时；仅已有实流基线')
  self.ai_panel.viewport.pip.hide()
  tile=self.slots['gray']
  if tile.last_image.isNull():tile.canvas.clear('等待本次获准 A 灰度窗口\n已有真实实流基线 · 当前未连接')
  self.input_summary.setPlainText(json.dumps(self.metrics_export(),ensure_ascii=False,indent=2,default=str))
 def can_open(self,tile,spec):
  if spec.kind=='snapshot':return True
  if not getattr(self,'sprint_ready',False):return spec.kind=='none'
  key=next((k for k,t in self.slots.items() if t is tile),None);p=self.profiles.get(key)
  if spec.kind!='mjpeg' or not p or spec.location!=p['video_url'] or spec.status_url!=p['status_url']:
   self.log_message('未提供对应新窗口契约，禁止网络连接');return False
  return super().can_open(tile,spec)
 def load_profiles(self,path):
  profiles=validate_profiles(json.loads(Path(path).read_text(encoding='utf-8-sig')))
  for tile in self.slots.values():
   if tile.receiver.active:raise ValueError('请先断开现有输入再载入配置')
  self.profiles=profiles
  for key,p in profiles.items():
   tile=self.slots[key];tile.spec=SourceSpec('mjpeg',p['video_url'],960,540,15,False,status_url=p['status_url'],provenance='external',expected_processing_mode=p['processing_mode']);tile.allow_network=True;tile.expected_camera_id=p['camera_id'];tile.sprint_mode=p['processing_mode']
  self.log_message('窗口配置已载入，尚未连接；每路需单独选中再选源确认')
 def load_profiles_dialog(self):
  name,_=QFileDialog.getOpenFileName(self,'载入新窗口配置','','JSON (*.json)')
  if name:
   try:self.load_profiles(name)
   except Exception as e:self.log_message(str(e))
 def frame_for(self,camera,mode,image,meta):
  key=next((k for k,t in self.slots.items() if t.camera_slot==camera and t.key==mode),None)
  if meta.get('source')=='external':
   try:validate_status(self.profiles[key],meta.get('status_snapshot',{}))
   except Exception:self.slots[key].disconnect();self.log_message('已拒绝跨路/错误身份画面');return
  return super().frame_for(camera,mode,image,meta)
 def setup_pose_panel(self):
  page=QWidget();v=QVBoxLayout(page);self.pose_state=QLabel('姿态后端未接入 · 独立结果，非当前视频同帧');v.addWidget(self.pose_state)
  self.pose_image=QLabel('等待姿态分析输入帧');self.pose_image.setAlignment(Qt.AlignmentFlag.AlignCenter);v.addWidget(self.pose_image)
  self.pose_counts=QLabel('动作次数 -- · 正确 -- · 错误 -- · 评分 --');v.addWidget(self.pose_counts)
  self.pose_json=QPlainTextEdit();self.pose_json.setReadOnly(True);v.addWidget(self.pose_json)
  button=QPushButton('载入姿态JSON（离线回放，非实时）');button.clicked.connect(self.pose_file);v.addWidget(button)
  self.diag_dock.widget().addTab(page,'独立姿态（待接入）')
 def pose_file(self):
  name,_=QFileDialog.getOpenFileName(self,'载入独立姿态结果','','JSON (*.json)')
  if name:
   try:self.present_pose(Path(name).read_bytes())
   except Exception as e:self.pose_state.setText('姿态数据已拒绝：'+str(e))
 def present_pose(self,data):
  raw=json.loads(data)
  # Existing analysis contract validates 17/33 layouts. Do not relabel YOLO boxes.
  small={k:v for k,v in raw.items() if k!='image_base64'};parse_analysis(json.dumps(small).encode())
  if raw.get('source_frame_id') is not None:raise ValueError('Sensor source_frame_id must remain null in this demonstration')
  image=None
  if raw.get('image_base64') is not None:
   if len(raw['image_base64'])>4*1024*1024:raise ValueError('Pose image too large')
   payload=base64.b64decode(raw['image_base64'],validate=True)
   if hashlib.sha256(payload).hexdigest()!=raw.get('jpeg_sha256'):raise ValueError('Pose input JPEG SHA mismatch')
   image=QImage.fromData(payload,'JPEG')
   if image.isNull():raise ValueError('Invalid pose input JPEG')
  self.pose_json.setPlainText(json.dumps(small,ensure_ascii=False,indent=2))
  self.pose_counts.setText('动作次数 '+str(raw.get('rep_count','--'))+' · 正确 '+str(raw.get('correct_count','--'))+' · 错误 '+str(raw.get('incorrect_count','--'))+' · 评分 '+str(raw.get('quality_score','--')))
  self.pose_state.setText('独立姿态JSON离线回放 · source_frame_id=null · 非当前视频同帧')
  if image:self.pose_image.setPixmap(QPixmap.fromImage(image).scaled(420,240,Qt.AspectRatioMode.KeepAspectRatio,Qt.TransformationMode.SmoothTransformation))
  else:self.pose_image.clear();self.pose_image.setText('未提供姿态分析输入帧；不复制预览图')
 def metrics_export(self):
  return {k:{'camera_slot':t.camera_slot,'mode':self.profiles[k]['processing_mode'] if k in self.profiles else t.key,'declared_scope':self.profiles.get(k),'status':t.receiver.status,'metrics':dict(t.metrics),'source_metadata':dict(t.meta)} for k,t in self.slots.items()}
 def setup_inputs_panel(self):
  page=QWidget();layout=QVBoxLayout(page);self.input_summary=QPlainTextEdit();self.input_summary.setReadOnly(True);layout.addWidget(self.input_summary)
  button=QPushButton('保存四路身份和帧统计');button.clicked.connect(self.save_input_metrics);layout.addWidget(button);self.diag_dock.widget().addTab(page,'四输入身份/统计')
 def save_input_metrics(self):
  path,_=QFileDialog.getSaveFileName(self,'保存四输入统计','four_input_metrics.json','JSON (*.json)')
  if path:Path(path).write_text(json.dumps(self.metrics_export(),ensure_ascii=False,indent=2,default=str),encoding='utf8')

def main():
 app=QApplication([]);win=SprintMonitor()
 # Only known historical snapshot assets autoload. Gray and B remain offline.
 cfg=json.loads((ROOT/'display_config_demo_snapshots.json').read_bytes());cfg['views']['gray']['kind']='none';cfg['views']['gray']['location']='';cfg['analysis']['rk_ai_url']=''
 path=ROOT/'sprint_offline_config.json';path.write_text(json.dumps(cfg,ensure_ascii=False,indent=2),encoding='utf8');win.config_path=path;win.load_config();win.show()
 def factory(spec,*a,**kw):
  pair=next(((k,p) for k,p in win.profiles.items() if p['video_url']==spec.location),None)
  if pair is None:raise ValueError('No authorized source profile')
  k,p=pair;identity=(k,p['session_id'],p['boot_id'])
  if identity in win.started_inputs:raise ValueError('One video GET per source window; no reconnect')
  win.started_inputs.add(identity);return StrictFrames(spec,p)
 receivers.HttpMjpegFrames=factory
 return app.exec()
if __name__=='__main__':raise SystemExit(main())

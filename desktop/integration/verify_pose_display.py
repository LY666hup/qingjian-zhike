"""Replay supplied JSON only into the separate panel, with network prohibited."""
from pathlib import Path
import json,socket,sys,hashlib
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QTimer
from PySide6.QtNetwork import QNetworkAccessManager
from pose_display_bridge import display_class,prepare
ROOT=Path(__file__).resolve().parent;OUT=ROOT/'display_validation';OUT.mkdir(exist_ok=False)
calls=[];checks=[];records=[line.encode() for line in (ROOT/'pose_replay_qjzc_analysis.jsonl').read_text(encoding='utf-8-sig').splitlines() if line.strip()]
def block(*a,**k):calls.append(str(a));raise AssertionError('NO NETWORK')
socket.socket.connect=block;socket.socket.connect_ex=block;socket.create_connection=block;QNetworkAccessManager.get=block
app=QApplication([]);app.setQuitOnLastWindowClosed(False);w=display_class()();w.show();index=[0];rows=[]
def step():
 try:
  if index[0]>=len(records):finish();return
  data=records[index[0]];raw=json.loads(data);w.present_pose(data)
  row={**w.last_pose_provenance,'persons':len(raw['persons']),'counts_label':w.pose_counts.text(),'state_label':w.pose_state.text(),'raw_index':index[0]}
  assert '--' in row['counts_label'] and 'None' not in row['counts_label']
  assert ('未检出姿态' in row['state_label'])== (not raw['persons'])
  assert json.loads(w.pose_json.toPlainText())==raw
  tabs=w.diag_dock.widget();tabs.setCurrentIndex(tabs.count()-2);w.diag_dock.show()
  if index[0] in (0,5,9):
   name=f'POSE_REPLAY_{index[0]}.png';w.grab().save(str(OUT/name));row['screenshot']=name;row['screenshot_sha256']=hashlib.sha256((OUT/name).read_bytes()).hexdigest()
  rows.append(row);index[0]+=1
 except Exception as e:checks.append({'failure':repr(e)});finish()
def finish():
 timer.stop();w.close();QTimer.singleShot(300,app.quit)
timer=QTimer();timer.setInterval(150);timer.timeout.connect(step);QTimer.singleShot(400,timer.start);rc=app.exec()
assert len(rows)==10 and not checks and not calls and rc==0,(len(rows),checks,calls,rc)
assert sum(r['persons']>0 for r in rows)==5
(OUT/'result.json').write_text(json.dumps({'scope':'supplied CPU offline replay, not board pose inference','records':rows,'present':5,'absent':5,'network_attempts':calls,'exit_rc':rc,'result':'PASS','input_JPEG':'NOT PROVIDED; no camera image copied','counts':'unverified, all missing values rendered --'},ensure_ascii=False,indent=2),encoding='utf8')
print('PASS actual GUI: 10 CPU replay JSON, 5 present/5 absent; no counts invented; raw preserved; no network; normal exit')

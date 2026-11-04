"""Five video tiles, independent pose image and per-student exercise counts."""
from pathlib import Path
from datetime import datetime
import hashlib
import json
import math
import time
from PySide6.QtCore import Qt, QTimer, QRectF, QPointF, Signal
from PySide6.QtGui import QImage, QPainter, QColor, QPen, QFont, QFontDatabase
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QFrame, QLabel, QPushButton,
 QVBoxLayout, QHBoxLayout, QGridLayout, QComboBox, QFileDialog, QMessageBox, QDialog,
 QPlainTextEdit, QScrollArea, QSizePolicy)
from profiles import MODES, load_profile_file, ConfigError, deadline
from connection import SessionReceiver, EventLog
from pose_input import PoseReceiver, EDGES
from exercise_events import ExerciseReceiver, EventStore, ACTION_NAMES, action_code

ROOT = Path(__file__).resolve().parent
STATE_TEXT = {'offline':'离线','loaded':'配置已载入','waiting':'等待板端启动','connecting':'正在连接',
 'receiving':'正在接收','retrying':'连接中断','ended':'会话结束','mismatch':'配置或身份不匹配',
 'permission':'本机权限错误','error':'程序错误'}
COLORS = ('#5ce0c8','#78baff','#ffca78','#c69bff','#ff91ba')
STYLE = '''
* {font-family:Microsoft YaHei UI;font-size:13px;color:#dbeaff;}
QMainWindow,QDialog {background:#06172a;}
QFrame#panel {background:#091f33;border:1px solid #224d6b;border-radius:12px;}
QFrame#tile {background:#081c2e;border:1px solid #28516d;border-radius:9px;}
QFrame#card {background:#0d2c43;border:1px solid #24516b;border-radius:9px;}
QLabel#heading {font-size:21px;font-weight:700;color:#f1f7ff;}
QLabel#brand {font-size:32px;font-weight:700;color:#e2f1ff;}
QLabel#muted {font-size:12px;color:#86a9c3;}
QLabel#state {font-size:12px;color:#62d9dc;}
QLabel#number {font-size:25px;font-weight:700;color:#5ce0c8;}
QLabel#badge {font-size:11px;color:#ffce82;}
QPushButton,QComboBox {background:#10354e;border:1px solid #2b607f;border-radius:5px;padding:7px 11px;}
QPushButton:hover {background:#194b68;border-color:#57bcd9;}
QPushButton:disabled {color:#506d82;background:#0b2438;}
QPushButton#primary {background:#0c77bd;border-color:#2499de;font-weight:700;}
QScrollArea {border:0;background:transparent;}
QScrollArea>QWidget>QWidget {background:transparent;}
QPlainTextEdit {background:#061421;border:1px solid #24475d;font-family:Consolas;font-size:12px;}
QToolTip {background:#10354e;color:#e2f1ff;border:1px solid #2b607f;}
'''

def label(text='', name=None):
    w = QLabel(text); w.setTextFormat(Qt.TextFormat.PlainText); w.setMinimumWidth(0)
    if name: w.setObjectName(name)
    return w

class VideoCanvas(QWidget):
    double_clicked = Signal()
    clicked = Signal()
    def __init__(self):
        super().__init__(); self.image = QImage(); self.people = []; self.stopped = False
        self.placeholder = '等待视频'; self.highlight = None; self.setMinimumSize(80,55)
    def paintEvent(self, event):
        p = QPainter(self); p.fillRect(self.rect(), QColor('#041422'))
        if self.image.isNull():
            p.setPen(QColor('#6b91ad')); p.drawText(self.rect(),Qt.AlignmentFlag.AlignCenter,self.placeholder); return
        scale = min(self.width()/self.image.width(), self.height()/self.image.height())
        w,h = self.image.width()*scale,self.image.height()*scale
        r = QRectF((self.width()-w)/2,(self.height()-h)/2,w,h); p.drawImage(r,self.image)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        for i,person in enumerate(self.people):
            color=QColor(COLORS[i%len(COLORS)])
            if self.highlight and self.highlight != person['person_id']: color.setAlpha(75)
            p.setPen(QPen(color,3)); points=person['keypoints']
            def usable(n): return points[n][2]>=.5 and 0<=points[n][0]<=1 and 0<=points[n][1]<=1
            def point(n): return QPointF(r.x()+points[n][0]*w,r.y()+points[n][1]*h)
            for a,b in EDGES[person['layout']]:
                if usable(a) and usable(b): p.drawLine(point(a),point(b))
            p.setBrush(color)
            for n in range(len(points)):
                if usable(n): p.drawEllipse(point(n),3,3)
            visible=[n for n in range(len(points)) if usable(n)]
            if visible:
                name=display_name(person,i); first=point(min(visible,key=lambda n:points[n][1]))
                p.drawText(QPointF(max(r.x()+5,min(r.right()-110,first.x())),max(r.y()+20,first.y()-12)),name)
        if self.stopped:
            p.fillRect(0,self.height()-27,self.width(),27,QColor(2,12,22,220)); p.setPen(QColor('#ffce82'))
            p.drawText(QRectF(8,self.height()-27,self.width()-16,27),Qt.AlignmentFlag.AlignVCenter,'画面已停止更新')
    def mouseDoubleClickEvent(self,e): self.double_clicked.emit()
    def mousePressEvent(self,e): self.clicked.emit()

def display_name(person, index):
    profile=person.get('profile') or {}
    if profile.get('identity_status')=='confirmed' and profile.get('student_id'):
        return profile.get('display_name') or profile['student_id']
    return '学员 '+str(person.get('person_id',index+1))

class Tile(QFrame):
    def __init__(self,title):
        super().__init__(); self.setObjectName('tile'); v=QVBoxLayout(self);v.setContentsMargins(9,8,9,7);v.setSpacing(5)
        row=QHBoxLayout(); self.title=label(title);self.title.setStyleSheet('font-size:15px;font-weight:700;')
        self.state=label('离线','state');row.addWidget(self.title,1);row.addWidget(self.state);v.addLayout(row)
        self.canvas=VideoCanvas();v.addWidget(self.canvas,1)
        self.metrics=label('','muted');v.addWidget(self.metrics)

class Monitor(QMainWindow):
    def __init__(self):
        super().__init__();self.setWindowTitle('青健智课 · 智慧体育 · 五画面 vNext');self.resize(1640,940)
        if not QFontDatabase.families():
            import os
            for filename in ('msyh.ttc','msyhbd.ttc'):
                path=Path(os.environ.get('WINDIR','C:/Windows'))/'Fonts'/filename
                if path.is_file():QFontDatabase.addApplicationFont(str(path))
        self.setMinimumSize(1150,720);self.setStyleSheet(STYLE)
        self.profiles={};self.workers={};self.pose_workers={};self.event_workers={};self.images={};self.pose_data={};self.event_data={};self.primary='A'
        self.event_store=EventStore(ROOT/'state'/'exercise_events.sqlite3')
        self.log_dir=ROOT/'logs'/datetime.now().strftime('%Y%m%d_%H%M%S_%f');self.log=EventLog(self.log_dir/'events.jsonl')
        self.config_sha='';self.last_ui={};self.card_widgets={};self.full_dialogs=[];self.requested=False
        self.paused=False;self.record_dir=None;self.record_index=0;self.ui_error='';self.selected_tile=None
        self.root=QWidget();self.setCentralWidget(self.root);v=QVBoxLayout(self.root);v.setContentsMargins(20,14,20,12);v.setSpacing(12)
        header=QHBoxLayout();header.addWidget(label('青健智课 · 智慧体育','brand'),1)
        right=QVBoxLayout();self.overall=label('离线','state');self.clock=label('','muted');right.addWidget(self.overall,alignment=Qt.AlignmentFlag.AlignRight);right.addWidget(self.clock);header.addLayout(right);v.addLayout(header)
        body=QHBoxLayout();body.setSpacing(12);v.addLayout(body,1)
        self.left=QFrame();self.left.setObjectName('panel');lv=QVBoxLayout(self.left);lv.setContentsMargins(12,12,12,12)
        h=QHBoxLayout();h.addWidget(label('视频画面','heading'),1);self.camera_combo=QComboBox();self.camera_combo.addItems(['A路','B路']);self.camera_combo.currentIndexChanged.connect(self.swap_camera);h.addWidget(self.camera_combo);lv.addLayout(h)
        grid=QGridLayout();grid.setSpacing(9);self.tiles=[]
        for i,mode in enumerate(MODES):
            t=Tile('A路 · '+MODES[mode]);grid.addWidget(t,i//2,i%2);self.tiles.append(t)
            t.canvas.double_clicked.connect(lambda t=t:self.open_full(t))
            t.canvas.clicked.connect(lambda t=t:setattr(self,'selected_tile',t))
        self.other_tile=Tile('B路 · 原始视频');self.other_tile.canvas.double_clicked.connect(lambda:self.open_full(self.other_tile))
        self.other_tile.canvas.clicked.connect(lambda:setattr(self,'selected_tile',self.other_tile));self.selected_tile=self.tiles[0]
        grid.addWidget(self.other_tile,2,0,1,2);grid.setRowStretch(0,2);grid.setRowStretch(1,2);grid.setRowStretch(2,1)
        grid.setColumnStretch(0,1);grid.setColumnStretch(1,1);lv.addLayout(grid,1);body.addWidget(self.left,4)
        center=QFrame();center.setObjectName('panel');cv=QVBoxLayout(center);cv.setContentsMargins(14,12,14,12)
        h=QHBoxLayout();h.addWidget(label('人体姿态','heading'),1);self.pose_state=label('等待姿态数据','state');h.addWidget(self.pose_state);cv.addLayout(h)
        self.pose_camera=label('A路','muted');cv.addWidget(self.pose_camera)
        self.pose_canvas=VideoCanvas();self.pose_canvas.placeholder='等待姿态画面';cv.addWidget(self.pose_canvas,1)
        h=QHBoxLayout();self.people=label('人数 --','state');h.addWidget(self.people,1);all_button=QPushButton('查看全班');all_button.clicked.connect(lambda:self.focus_person(None));h.addWidget(all_button);cv.addLayout(h);body.addWidget(center,5)
        roster=QFrame();roster.setObjectName('panel');roster.setMinimumWidth(185);rv=QVBoxLayout(roster);rv.setContentsMargins(10,12,10,10)
        rv.addWidget(label('学生档案','heading'));self.roster_status=label('等待数据','muted');rv.addWidget(self.roster_status)
        scroll=QScrollArea();scroll.setWidgetResizable(True);scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        content=QWidget();self.cards=QVBoxLayout(content);self.cards.setContentsMargins(0,0,0,0);self.cards.setSpacing(10)
        self.empty=label('暂无运动记录','muted');self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter);self.cards.addWidget(self.empty);self.cards.addStretch();scroll.setWidget(content);rv.addWidget(scroll,1);body.addWidget(roster,1)
        controls=QFrame();controls.setObjectName('panel');row=QHBoxLayout(controls)
        self.load_btn=QPushButton('载入配置');self.load_btn.clicked.connect(self.load_dialog);row.addWidget(self.load_btn)
        self.connect_btn=QPushButton('连接');self.connect_btn.setObjectName('primary');self.connect_btn.clicked.connect(self.connect_sources);row.addWidget(self.connect_btn)
        self.disconnect_btn=QPushButton('断开');self.disconnect_btn.clicked.connect(self.disconnect_sources);row.addWidget(self.disconnect_btn)
        self.pause_btn=QPushButton('暂停');self.pause_btn.clicked.connect(self.toggle_pause);row.addWidget(self.pause_btn)
        self.config_label=label('未载入配置','muted');row.addWidget(self.config_label,1)
        full=QPushButton('单画面');full.clicked.connect(lambda:self.open_full(self.selected_tile));row.addWidget(full)
        shot=QPushButton('截图');shot.clicked.connect(self.save_screenshot);row.addWidget(shot)
        self.record_btn=QPushButton('录像');self.record_btn.clicked.connect(self.toggle_record);self.record_btn.setToolTip('保存各路接收帧与来源记录');row.addWidget(self.record_btn)
        diag=QPushButton('连接日志');diag.clicked.connect(self.show_logs);row.addWidget(diag);v.addWidget(controls)
        self.bottom=label('离线','muted');self.bottom.setWordWrap(True);v.addWidget(self.bottom)
        self.timer=QTimer(self);self.timer.timeout.connect(self.refresh);self.timer.start(80)
        self.log.write('APP_STARTED', version='20261101-vNext', mode='offline');self.refresh()

    def load_dialog(self):
        path,_=QFileDialog.getOpenFileName(self,'载入 Ly 提供的新配置','','JSON 配置 (*.json)')
        if path:
            try:self.load_profiles(Path(path))
            except Exception as e:
                self.ui_error='配置或身份不匹配：'+str(e)
                self.bottom.setText('配置或身份不匹配：'+str(e));self.overall.setText('配置或身份不匹配')
                self.log.write('CONFIG_REJECTED',reason=str(e));QMessageBox.warning(self,'配置无法载入',str(e))

    def all_workers(self): return list(self.workers.values())+list(self.pose_workers.values())+list(self.event_workers.values())

    def load_profiles(self,path):
        if any(w.is_alive() for w in self.all_workers()): raise ConfigError('请先断开，等待连接关闭后再载入配置')
        profiles,sha=load_profile_file(path)
        self.profiles=profiles;self.config_sha=sha;self.workers={};self.pose_workers={};self.event_workers={};self.images={};self.pose_data={};self.event_data={};self.requested=False;self.ui_error=''
        self.clear_cards();self.last_ui={};self.config_label.setText(path.name+' · 配置已载入')
        self.config_label.setToolTip('SHA256: '+sha);self.log.write('CONFIG_LOADED',path=str(path),sha256=sha,inputs=[list(k) for k in profiles]);self.refresh()

    def connect_sources(self):
        if not self.profiles:
            self.bottom.setText('请先载入 Ly 提供的新配置');return
        if any(w.is_alive() for w in self.all_workers()): return
        # A stopped/ended/error session requires an explicit reload; repeated clicks cannot rearm it.
        if self.requested:
            self.ui_error='连接已停止，请处理错误并重新载入有效配置';self.bottom.setText(self.ui_error);return
        try:
            if any(deadline(p)<=time.time() for p in self.profiles.values()): raise ConfigError('配置已过期，请载入新配置')
            self.requested=True
            for key,p in self.profiles.items():
                w=SessionReceiver(p,self.log);self.workers[key]=w;w.start()
            for camera in ('A','B'):
                candidates=[p for (c,m),p in self.profiles.items() if c==camera and p.get('analysis_url')]
                # One analysis poller per camera; do not multiply requests for four processing modes.
                if candidates:
                    p=candidates[0];w=PoseReceiver(p,self.log);self.pose_workers[camera]=w;w.start()
                events=[p for (c,m),p in self.profiles.items() if c==camera and p.get('events_url')]
                if events:
                    w=ExerciseReceiver(events[0],self.log,self.event_store);self.event_workers[camera]=w;w.start()
        except Exception as e:
            self.disconnect_sources();self.bottom.setText(str(e));self.log.write('CONNECT_FAILED',reason=str(e))
        self.refresh()

    def disconnect_sources(self):
        for worker in self.all_workers(): worker.stop()
        self.log.write('USER_DISCONNECT');self.refresh()

    def swap_camera(self,index):
        self.primary='AB'[index];self.clear_cards();self.last_ui={};self.pose_canvas.highlight=None;self.refresh()

    def keys(self):
        return [(self.primary,m) for m in MODES]+[('B' if self.primary=='A' else 'A','original')]

    def clear_cards(self):
        for card in self.card_widgets.values(): self.cards.removeWidget(card);card.deleteLater()
        self.card_widgets={};self.empty.show()

    def update_cards(self,message):
        people=message.get('persons',[]);ids=[p['person_id'] for p in people]
        for pid in list(self.card_widgets):
            if pid not in ids:
                card=self.card_widgets.pop(pid);self.cards.removeWidget(card);card.deleteLater()
        for i,p in enumerate(people):
            pid=p['person_id']
            if pid not in self.card_widgets:
                card=QFrame();card.setObjectName('card');layout=QVBoxLayout(card);layout.setContentsMargins(9,10,9,10)
                card.name=label();card.name.setStyleSheet('font-size:16px;font-weight:700;');layout.addWidget(card.name)
                card.values=QVBoxLayout();layout.addLayout(card.values);card.value_labels={}
                card.phase=label('','muted');layout.addWidget(card.phase)
                b=QPushButton('关注');b.clicked.connect(lambda _,pid=pid:self.focus_person(pid));layout.addWidget(b)
                self.card_widgets[pid]=card;self.cards.insertWidget(i,card)
            card=self.card_widgets[pid];card.name.setText(display_name(p,i))
            counts={action_code(k):v for k,v in p.get('counts_by_action',{}).items()};action=p.get('action_type')
            if action:
                action=action_code(action)
                if action not in counts:counts[action]=p.get('rep_count')
            durations={action_code(k):v for k,v in p.get('durations_ms',{}).items()}
            values={k:('--' if value is None else str(value)+' 次') for k,value in counts.items()}
            values.update({k:f'{value/1000:.1f} 秒' for k,value in durations.items()})
            if not values:values={'SQUAT':'--','PUSHUP':'--'}
            for k in list(card.value_labels):
                if k not in values:
                    w=card.value_labels.pop(k);card.values.removeWidget(w);w.deleteLater()
            for k,value in values.items():
                if k not in card.value_labels:
                    w=label('','number');w.setWordWrap(True);w.setStyleSheet('font-size:18px;');card.values.addWidget(w);card.value_labels[k]=w
                card.value_labels[k].setText(ACTION_NAMES.get(k,k)+'  '+value)
            card.squat=card.value_labels.get('SQUAT');card.pushup=card.value_labels.get('PUSHUP')
            phase={'standing':'站立','descending':'下行','bottom':'最低点','ascending':'起身','unknown':'等待动作','up':'支撑','down':'下压'}.get(p.get('action_phase'),'')
            card.phase.setText(phase)
        self.empty.setVisible(not people);self.roster_status.setText(self.primary+f'路 · {len(people)}人')

    def focus_person(self,pid): self.pose_canvas.highlight=pid;self.pose_canvas.update()

    def toggle_pause(self):
        self.paused=not self.paused;self.pause_btn.setText('继续' if self.paused else '暂停');self.refresh()

    def toggle_record(self):
        if self.record_dir:
            self.log.write('RECORDING_STOPPED',path=str(self.record_dir));self.record_dir=None;self.record_btn.setText('录像');return
        directory=QFileDialog.getExistingDirectory(self,'选择录像保存目录')
        if directory:
            self.record_dir=Path(directory)/('青健智课_'+datetime.now().strftime('%Y%m%d_%H%M%S_%f'))
            self.record_dir.mkdir(parents=True);self.record_index=0;self.record_btn.setText('停止录像')
            self.log.write('RECORDING_STARTED',path=str(self.record_dir))

    def record_frame(self,key,image,metadata):
        if not self.record_dir:return
        try:
            self.record_index+=1;name=f'{self.record_index:08d}_{key[0]}_{key[1]}.jpg'
            if not image.save(str(self.record_dir/name),'JPEG',90):raise OSError('无法保存录像帧')
            with (self.record_dir/'frames.jsonl').open('a',encoding='utf-8') as f:
                f.write(json.dumps(dict(file=name,saved_at=datetime.now().astimezone().isoformat(),metadata=metadata),ensure_ascii=False)+'\n')
        except OSError as e:
            self.log.write('RECORDING_ERROR',reason=str(e));self.ui_error='录像保存失败：'+str(e);self.record_dir=None;self.record_btn.setText('录像')

    def refresh(self):
        self.clock.setText(datetime.now().strftime('%Y.%m.%d  %H:%M:%S'))
        for key,w in self.workers.items():
            packet=w.take()
            if packet:
                self.record_frame(key,*packet)
                if not self.paused:self.images[key]=packet
        for camera,w in self.pose_workers.items():
            packet=w.take()
            if packet and not self.paused:self.pose_data[camera]=packet
        for camera,w in self.event_workers.items():
            packet=w.take()
            if packet and not self.paused:self.event_data[camera]=packet
        for tile,key in zip(self.tiles+[self.other_tile],self.keys()):
            tile.title.setText(key[0]+'路 · '+MODES[key[1]])
            worker=self.workers.get(key);info=worker.info() if worker else None
            tile.state.setText(self.state_text(info) if info else '配置已载入' if key in self.profiles else '离线')
            packet=self.images.get(key)
            tile.canvas.image=packet[0] if packet else QImage();tile.canvas.people=[]
            tile.canvas.stopped=bool(packet) and (self.paused or not info or info['state']!='receiving')
            tile.canvas.placeholder='等待视频';tile.canvas.update()
            tile.metrics.setText((str(packet[0].width())+'×'+str(packet[0].height())+' · '+str(info['counts']['decoded'])+' 帧') if packet and info else '')
            tile.state.setToolTip(info['detail'] if info else '')
        self.refresh_pose()
        infos=[w.info() for w in self.all_workers()]
        busy=any(w.is_alive() for w in self.all_workers());self.load_btn.setEnabled(not busy)
        self.connect_btn.setEnabled(bool(self.profiles) and not busy and not self.requested)
        self.disconnect_btn.setEnabled(busy)
        if infos:
            priority=('permission','mismatch','error','retrying','waiting','connecting','receiving','ended','offline')
            info=next((i for s in priority for i in infos if i['state']==s),infos[0])
            self.overall.setText(self.state_text(info));self.bottom.setText(self.state_text(info)+(('：'+info['detail']) if info['state'] in ('permission','mismatch','error','retrying') else ''))
        else:
            state='配置已载入' if self.profiles else '离线';self.overall.setText(state);self.bottom.setText(state)
        if self.ui_error:self.bottom.setText(self.ui_error)
        if self.paused:self.bottom.setText('显示已暂停')

    def state_text(self,info):
        if info['state']=='retrying' and info['next_retry_at'] is not None:
            return '连接中断，'+str(max(0,math.ceil(info['next_retry_at']-time.time())))+'秒后重试'
        return STATE_TEXT.get(info['state'],info['state'])

    def refresh_pose(self):
        self.pose_camera.setText(self.primary+'路');packet=self.pose_data.get(self.primary)
        worker=self.pose_workers.get(self.primary);info=worker.info() if worker else None
        fresh=bool(packet) and not self.paused and time.monotonic()-packet[2]<3 and info and info['state']=='receiving'
        image=None;people=[]
        if packet:
            message,image,at=packet;people=message.get('persons',[])
            if image is None:
                match=next((p for k,p in self.images.items() if k[0]==self.primary and p[1]['jpeg_sha256']==message.get('jpeg_sha256') and p[1]['session_id']==message['session_id']),None)
                if match: image=match[0]
        if image is None:
            raw=self.images.get((self.primary,'original'))
            if raw: image=raw[0]
            # Unpaired preview is never overlaid with another frame's skeleton.
            people=[]
        self.pose_canvas.image=image if image is not None else QImage();self.pose_canvas.people=people
        self.pose_canvas.stopped=not fresh and image is not None;self.pose_canvas.update()
        self.people.setText('人数 '+str(len(packet[0].get('persons',[]))) if fresh else '人数 --')
        self.pose_state.setText('姿态识别中' if fresh and people else '等待对应图像' if fresh else self.state_text(info) if info and info['state']!='receiving' else '等待姿态数据')
        result=self.event_data.get(self.primary) if self.primary in self.event_workers else packet
        result_worker=self.event_workers.get(self.primary) if self.primary in self.event_workers else worker
        if result:
            value=result[0];result_info=result_worker.info() if result_worker else None
            result_fresh=not self.paused and time.monotonic()-result[2]<3 and result_info and result_info['state']=='receiving'
            signature=(self.primary,value.get('training_id'),value.get('session_id'),value.get('event_seq',value.get('frame_id')),result_fresh)
            if signature!=self.last_ui.get('results'):
                self.update_cards(value);self.last_ui['results']=signature
            if not result_fresh:self.roster_status.setText(self.primary+'路 · 数据已停止更新')
        else:
            if self.card_widgets:self.clear_cards()
            self.roster_status.setText('等待数据')

    def show_logs(self):
        d=QDialog(self);d.setWindowTitle('连接日志');d.resize(950,640);v=QVBoxLayout(d)
        v.addWidget(label(str(self.log_dir),'muted'));text=QPlainTextEdit();text.setReadOnly(True);v.addWidget(text)
        def update():
            stats={str(k):w.info() for k,w in self.workers.items()};stats.update({'pose_'+c:w.info() for c,w in self.pose_workers.items()});stats.update({'events_'+c:w.info() for c,w in self.event_workers.items()})
            lines=(self.log_dir/'events.jsonl').read_text(encoding='utf-8').splitlines()[-100:]
            text.setPlainText(json.dumps(stats,ensure_ascii=False,indent=2)+'\n\n'+'\n'.join(lines))
        update();timer=QTimer(d);timer.timeout.connect(update);timer.start(1000);d.exec()

    def save_screenshot(self):
        path,_=QFileDialog.getSaveFileName(self,'保存界面截图',str(ROOT/'课堂截图.png'),'PNG 图片 (*.png)')
        if path:
            self.grab().save(path,'PNG');self.log.write('SCREENSHOT_SAVED',path=path)

    def open_full(self,tile):
        d=QDialog(self);d.setWindowTitle(tile.title.text());d.resize(1000,650);v=QVBoxLayout(d);canvas=VideoCanvas();v.addWidget(canvas)
        def update(): canvas.image=tile.canvas.image;canvas.stopped=tile.canvas.stopped;canvas.update()
        timer=QTimer(d);timer.timeout.connect(update);timer.start(80);update();d.exec()

    def closeEvent(self,event):
        self.timer.stop()
        for worker in self.all_workers():worker.stop()
        for worker in self.all_workers():worker.join(2.5)
        if not any(worker.is_alive() for worker in self.all_workers()):self.event_store.close()
        self.log.write('APP_CLOSED');event.accept()

def main():
    app=QApplication.instance() or QApplication([]);font=QFont('Microsoft YaHei UI');font.setPointSize(10);app.setFont(font)
    win=Monitor();win.show();return app.exec()

if __name__=='__main__': raise SystemExit(main())

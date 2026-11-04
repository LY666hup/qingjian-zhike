"""Receive-only Windows monitor. No inference, camera control or board access."""
from __future__ import annotations
import argparse,json,sys,time
from collections import deque
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from PySide6.QtCore import Qt,QTimer,QRectF,QPointF,QSize,Signal
from PySide6.QtGui import QColor,QPainter,QPen,QImage,QFont,QLinearGradient,QShortcut,QKeySequence
from PySide6.QtWidgets import (QApplication,QMainWindow,QWidget,QFrame,QLabel,QPushButton,QVBoxLayout,QHBoxLayout,QGridLayout,QComboBox,QLineEdit,QSpinBox,QDoubleSpinBox,QCheckBox,QDialog,QDialogButtonBox,QFormLayout,QFileDialog,QPlainTextEdit,QScrollArea,QToolButton,QDockWidget,QTabWidget,QStackedWidget,QMenu)
from video_sources import SourceSpec
from receivers import Receiver,safe_url
from analysis_receiver import AnalysisReceiver
from recording import Recorder,snapshot
from contracts import EDGES,MODES,identity
from showcase_widgets import Header,StatusChip,MetricCard,icon,draw_icon,STYLE
from classroom_widgets import AnalysisStage,StudentRoster,CLASSROOM_STYLE,COLORS,person_key,person_name

ROOT=Path(__file__).resolve().parent
STATE={'disconnected':'未连接','connecting':'连接中','connected':'已接收','buffering':'缓冲中','interrupted':'视频中断','reconnecting':'自动重连','paused':'显示暂停','ended':'本次结束 · 保留末帧','armed':'等待新获准窗口','blocked':'配置不匹配'}

class Canvas(QWidget):
    clicked=Signal();double_clicked=Signal()
    def __init__(self):
        super().__init__();self.image=QImage();self.meta={};self.pose=None;self.highlight_person=None;self.placeholder='等待选择视频源';self.setMinimumSize(60,60)
        self.present_seq=0;self.painted_seq=0;self.paint_times=deque();self.paint_delay_ms=None
    def clear(self,text):self.image=QImage();self.pose=None;self.placeholder=text;self.paint_times.clear();self.paint_delay_ms=None;self.update()
    def paintEvent(self,event):
        p=QPainter(self);gradient=QLinearGradient(0,0,self.width(),self.height());gradient.setColorAt(0,QColor('#071b30'));gradient.setColorAt(1,QColor('#031020'));p.fillRect(self.rect(),gradient)
        if self.image.isNull():
            p.save();p.translate(self.width()/2-22,self.height()/2-48);p.scale(1.8,1.8);draw_icon(p,'video','#285172');p.restore()
            font=QFont('Microsoft YaHei');font.setPixelSize(13 if self.width()<300 else 16);p.setFont(font);p.setPen(QColor('#819db9'));p.drawText(QRectF(8,self.height()/2+6,self.width()-16,65),Qt.AlignmentFlag.AlignHCenter|Qt.AlignmentFlag.AlignTop|Qt.TextFlag.TextWordWrap,self.placeholder);return
        s=self.image.size();scale=min(self.width()/s.width(),self.height()/s.height());w=s.width()*scale;h=s.height()*scale
        r=QRectF((self.width()-w)/2,(self.height()-h)/2,w,h);self.last_target_rect=r;p.drawImage(r,self.image)
        if self.present_seq!=self.painted_seq:
            now=time.monotonic();self.paint_times.append(now);self.painted_seq=self.present_seq
            received=self.meta.get('received_monotonic');self.paint_delay_ms=(now-received)*1000 if received is not None else None
        if self.pose:
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            for index,person in enumerate(self.pose.get('persons',[])):
                key=person_key(person,index);color=QColor(COLORS[index%len(COLORS)])
                if self.highlight_person and self.highlight_person!=key:color.setAlpha(65)
                p.setPen(QPen(color,3 if key==self.highlight_person else 2))
                pts=person['keypoints']
                def usable(i):return pts[i][2]>=.5 and 0<=pts[i][0]<=1 and 0<=pts[i][1]<=1
                for a,b in EDGES[person['layout']]:
                    if usable(a) and usable(b):p.drawLine(QPointF(r.x()+pts[a][0]*w,r.y()+pts[a][1]*h),QPointF(r.x()+pts[b][0]*w,r.y()+pts[b][1]*h))
                visible=[q for q in pts if q[2]>=.5 and 0<=q[0]<=1 and 0<=q[1]<=1]
                if visible:
                    font=QFont('Microsoft YaHei');font.setPixelSize(13);p.setFont(font)
                    x=max(r.x()+4,min(r.right()-130,r.x()+min(q[0] for q in visible)*w));y=max(r.y()+38,r.y()+min(q[1] for q in visible)*h-10)
                    p.drawText(QPointF(x,y),person_name(person,index))
            p.fillRect(0,0,min(190,self.width()),26,QColor(4,20,30,210));p.setPen(QColor('#8df5cf'));p.drawText(10,18,'骨架 · 同帧分析数据')
    def mousePressEvent(self,e):self.clicked.emit();super().mousePressEvent(e)
    def mouseDoubleClickEvent(self,e):self.double_clicked.emit();super().mouseDoubleClickEvent(e)

class SourceDialog(QDialog):
    def __init__(self,spec,parent):
        super().__init__(parent);self.setWindowTitle('配置显示源');self.resize(600,260);f=QFormLayout(self)
        self.kind=QComboBox()
        for label,value in [('离线占位','none'),('本地视频回放','mp4'),('YUYV 原始回放','yuyv'),('HTTP MJPEG诊断预览','mjpeg'),('RTSP 网络候选','rtsp'),('HTTP 通用媒体','http'),('参考 TCP JPEG 接口','tcpjpeg')]:self.kind.addItem(label,value)
        self.kind.setCurrentIndex(max(0,self.kind.findData(spec.kind)));f.addRow('输入适配器',self.kind)
        row=QHBoxLayout();self.path=QLineEdit(spec.location);row.addWidget(self.path);browse=QPushButton('选文件');row.addWidget(browse);browse.clicked.connect(self.browse);f.addRow('地址 / 路径',row)
        self.width=QSpinBox();self.width.setRange(2,4096);self.width.setValue(spec.width)
        self.height=QSpinBox();self.height.setRange(1,2160);self.height.setValue(spec.height)
        self.fps=QDoubleSpinBox();self.fps.setRange(.1,120);self.fps.setValue(spec.fps)
        raw=QHBoxLayout();raw.addWidget(self.width);raw.addWidget(QLabel('×'));raw.addWidget(self.height);raw.addWidget(self.fps);f.addRow('仅 YUYV：宽 / 高 / FPS',raw)
        self.loop=QCheckBox('本地文件循环');self.loop.setChecked(spec.loop);f.addRow(self.loop)
        self.status_url=QLineEdit(spec.status_url);self.status_url.setPlaceholderText('仅MJPEG：同源status.json，先查状态再请求视频');f.addRow('状态URL',self.status_url)
        self.provenance=QComboBox()
        for text,val in [('外部网络（来源待核验）','external'),('本机模拟服务','simulation'),('保存帧回放服务','replay')]:self.provenance.addItem(text,val)
        self.provenance.setCurrentIndex(max(0,self.provenance.findData(spec.provenance)));f.addRow('MJPEG来源声明',self.provenance)
        self.expected_mode=QLineEdit(spec.expected_processing_mode);self.expected_mode.setPlaceholderText('如 A3_camera_output；必须与状态页一致');f.addRow('预期处理模式',self.expected_mode)
        note=QLabel('HTTP视频URL会启动一次采集：仅在Ly通知新获准窗口后连接。程序先查状态页；其它显示区是否可用以正式模式映射为准。');note.setWordWrap(True);f.addRow(note)
        b=QDialogButtonBox(QDialogButtonBox.StandardButton.Ok|QDialogButtonBox.StandardButton.Cancel);b.accepted.connect(self.accept);b.rejected.connect(self.reject);f.addRow(b)
    def browse(self):
        name,_=QFileDialog.getOpenFileName(self,'视频文件','','视频 (*.mp4 *.mkv *.mov *.yuyv *.raw)')
        if name:self.path.setText(name)
    def spec(self):return SourceSpec(self.kind.currentData(),self.path.text().strip(),self.width.value(),self.height.value(),self.fps.value(),self.loop.isChecked(),status_url=self.status_url.text().strip(),provenance=self.provenance.currentData(),expected_processing_mode=self.expected_mode.text().strip())

class Tile(QFrame):
    select=Signal(str);full=Signal(str);notice=Signal(str);new_frame=Signal(str,QImage,dict)
    def __init__(self,key,title):
        super().__init__();self.key=key;self.setObjectName('videoTile');self.receiver=Receiver(self);self.spec=SourceSpec();self.last_image=QImage();self.meta={};self.metrics={};self.allow_network=key=='original'
        v=QVBoxLayout(self);v.setContentsMargins(7,8,7,5);v.setSpacing(5);h=QHBoxLayout();h.setContentsMargins(3,0,3,0);h.setSpacing(11)
        number=QLabel({'original':'01','gray':'02','gaussian':'03','histogram':'04'}[key]);number.setObjectName('cameraIndex');number.setAlignment(Qt.AlignmentFlag.AlignCenter);number.setFixedWidth(48);h.addWidget(number)
        self.title=QPushButton(title);self.title.setObjectName('cameraTitle');self.title.clicked.connect(lambda:self.select.emit(key));h.addWidget(self.title,1);self.status=StatusChip('未连接' if key=='original' else '待接入');h.addWidget(self.status);v.addLayout(h)
        self.canvas=Canvas();self.canvas.clicked.connect(lambda:self.select.emit(key));self.canvas.double_clicked.connect(lambda:self.full.emit(key));v.addWidget(self.canvas,1)
        self.display_source=QLabel('尚无视频源' if key=='original' else 'FPGA模式待验证');self.display_source.setObjectName('tileSource');v.addWidget(self.display_source)
        self.display_source.setMinimumWidth(0);self.display_source.setMaximumHeight(30);self.display_source.setWordWrap(True)
        # Existing engineering widgets move into the dock, keeping their data and API.
        self.diagnostics=QWidget();d=QVBoxLayout(self.diagnostics);d.setContentsMargins(10,10,10,10)
        self.provenance=QLabel('无视频');self.provenance.setObjectName('provenance');self.provenance.setWordWrap(True);d.addWidget(self.provenance)
        self.source_label=QLabel('视频源：未选择');self.source_label.setObjectName('muted');self.source_label.setWordWrap(True);d.addWidget(self.source_label)
        self.stats=QLabel('接收 -- FPS · 绘制 -- FPS · 分辨率 --');self.stats.setObjectName('muted');d.addWidget(self.stats)
        self.extra=QLabel('序号缺口 -- · 显示丢弃 0 · 端到端延迟 --');self.extra.setObjectName('muted');self.extra.setWordWrap(True);d.addWidget(self.extra)
        self.preview_info=QLabel('传感器更新率 -- · 实际模式待核验');self.preview_info.setObjectName('muted');self.preview_info.setWordWrap(True);d.addWidget(self.preview_info);d.addStretch()
        self.receiver.frame.connect(self.on_frame);self.receiver.state.connect(self.on_state);self.receiver.stats.connect(self.on_stats)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu);self.customContextMenuRequested.connect(self.context_menu)
        if key!='original':self.canvas.clear('FPGA模式未集成 / 待验证');self.provenance.setText('FPGA模式未集成 / 待验证')
    def context_menu(self,pos):
        menu=QMenu(self)
        for text,fn in [('选择视频源',self.configure),('暂停 / 继续',self.pause),('断开',self.disconnect),('单画面全屏',lambda:self.full.emit(self.key)),('保存截图',self.capture)]:menu.addAction(text).triggered.connect(fn)
        menu.exec(self.mapToGlobal(pos))
    def configure(self):
        d=SourceDialog(self.spec,self)
        if d.exec():self.play(d.spec())
    def play(self,spec):
        if spec.kind in ('mjpeg','rtsp','http','tcpjpeg') and not self.allow_network:
            self.notice.emit('该FPGA模式未集成 / 待验证：尚未启用网络源映射');return
        self.spec=spec;self.canvas.clear('正在连接…');self.last_image=QImage();self.meta={}
        source=safe_url(spec.location);self.source_label.setText('视频源：'+(Path(source).name if spec.kind in ('mp4','yuyv') else source[:90] or '离线'));self.source_label.setToolTip(source)
        try:self.receiver.start(spec);self.notice.emit(f'{self.key} 打开 {safe_url(spec.location)}')
        except Exception as e:self.disconnect();self.notice.emit(f'{self.key}：{e}')
    def disconnect(self):
        self.receiver.disconnect();self.last_image=QImage();self.meta={};self.canvas.clear('未连接视频源' if self.allow_network else 'FPGA模式未集成 / 待验证');self.provenance.setText('无视频');self.display_source.setText('尚无视频源' if self.allow_network else 'FPGA模式待验证')
    def pause(self):self.receiver.pause(not self.receiver.paused)
    def on_state(self,s,d):
        state_text='回放结束' if s=='ended' and self.spec.kind in ('mp4','yuyv') else STATE.get(s,s)
        chip={'connected':'已连接','connecting':'连接中','buffering':'缓冲中','interrupted':'已中断','reconnecting':'重连中','paused':'已暂停','ended':'已结束','armed':'待授权','blocked':'未验证','disconnected':'未连接' if self.allow_network else '待接入'}.get(s,state_text)
        tone='demo' if s=='connected' and self.meta.get('source') in ('simulation','replay') else 'success' if s=='connected' else 'warning' if s in ('ended','armed','blocked','interrupted','reconnecting','paused') else 'offline'
        self.status.show_status(chip,tone);self.status.setToolTip(d)
        preserve=self.spec.kind=='mjpeg' and s in ('ended','armed','blocked','interrupted','reconnecting')
        if preserve:
            self.canvas.pose=None
            if self.last_image.isNull():self.canvas.clear(state_text)
            origin={'simulation':'模拟服务','replay':'保存帧回放','external':'外部网络（声明）'}.get(self.meta.get('source'),'来源未知')
            self.provenance.setText(origin+' · '+('本次诊断结束' if s=='ended' else state_text)+' · '+('保留末帧（非实时）' if not self.last_image.isNull() else '尚无有效帧'))
            self.display_source.setText(('模拟来源 · ' if self.meta.get('source')=='simulation' else '回放来源 · ' if self.meta.get('source')=='replay' else '')+('诊断结束 · 保留末帧，非实时' if s=='ended' and not self.last_image.isNull() else state_text))
        elif s in ('interrupted','reconnecting','disconnected'):self.canvas.clear(STATE.get(s,s));self.last_image=QImage();self.meta={}
        self.notice.emit(f'{self.key} {STATE.get(s,s)}：{d}')
    def on_frame(self,image,meta):
        self.last_image=image;self.meta=meta;self.canvas.image=image;self.canvas.meta=meta;self.canvas.pose=None;self.canvas.present_seq+=1
        src={'simulation':'模拟网络测试','replay':'本地 / 录制回放','external':'外部网络 · 来源按发送端声明'}.get(meta.get('source'),'来源未知')
        processor={'fpga':'发送端声明 FPGA','rk':'发送端声明 RK','simulation':'模拟','unknown':'未核验'}.get(meta.get('processor'),'未核验')
        self.provenance.setText(f"{src} | {MODES.get(meta.get('processing_mode'),'未知')} · {processor}")
        if meta.get('diagnostic_snapshot'):
            mode=MODES.get(meta.get('processing_mode'),'未知')
            self.provenance.setText(f'板端诊断快照 | {mode} · 非实时 · 未连接板端')
            self.display_source.setText('板端诊断快照 · 非实时')
        elif meta.get('transport')=='http-mjpeg':
            declared=meta.get('declared_processing_mode') or '模式未知'
            encoder='RK软件JPEG网传（声明）' if 'rk' in (meta.get('encoder') or '').lower() else 'JPEG编码来源待核验'
            self.provenance.setText(f'{src} | {declared} · {encoder}')
        concise=('板端诊断快照 · 非实时' if meta.get('diagnostic_snapshot') else
                 {'simulation':'演示来源 · 模拟视频与数据','replay':'演示来源 · 本地回放，非实时FPGA效果','external':'外部视频 · 来源按发送端声明'}.get(meta.get('source'),'视频来源未核验'))
        self.display_source.setText(concise);self.display_source.setToolTip(self.provenance.text())
        self.status.show_status('演示' if meta.get('source') in ('simulation','replay') else '已连接','demo' if meta.get('source') in ('simulation','replay') else 'success')
        self.canvas.update();self.new_frame.emit(self.key,image,meta)

    def on_stats(self,m):
        now=time.monotonic()
        while self.canvas.paint_times and self.canvas.paint_times[0]<now-2:self.canvas.paint_times.popleft()
        m=dict(m,paint_fps=len(self.canvas.paint_times)/2,postdecode_paint_ms=self.canvas.paint_delay_ms if self.canvas.paint_times and now-self.canvas.paint_times[-1]<.2 else None)
        self.metrics=m;size=f'{self.last_image.width()}×{self.last_image.height()}' if not self.last_image.isNull() else '--'
        if self.spec.kind=='yuyv' and not self.last_image.isNull():size=f'{self.spec.width}×{self.spec.height}（预览 {size}）'
        self.stats.setText(f"接收 {m['receive_fps']:.1f} FPS · 绘制 {m['paint_fps']:.1f} FPS · {size}")
        gap=m['sequence_gaps'] if m['sequence_gaps'] is not None else '--';local=f"{m['postdecode_paint_ms']:.1f} ms" if m['postdecode_paint_ms'] is not None else '--'
        self.extra.setText(f"序号缺口 {gap} · 显示丢弃 {m['display_discarded']} · 本机滞留 {local}")
        self.extra.setToolTip('接收 FPS 为解码交付；绘制 FPS 为实际 paintEvent 的新帧。均不代表独立新画面。本机滞留为解码完成至绘制调用，不含显示器扫描。序号缺口不是网络丢包。端到端延迟未知。')
        preview=m.get('preview',{});snap=preview.get('status_snapshot',{})
        if self.spec.kind=='mjpeg':
            self.preview_info.setText(f"相机 {snap.get('camera_id','--')} · 预览序号 {preview.get('preview_sequence','--')} · 最近状态编码数 {snap.get('encoded_preview_frames','--')} · 源帧号 --")
            self.preview_info.setToolTip(json.dumps(preview,ensure_ascii=False,indent=2)+'\n预览序号不是传感器曝光ID；画质及独立图像更新率未验收。')
    def capture(self):
        if self.last_image.isNull():self.notice.emit('尚无可保存的视频帧');return
        try:self.notice.emit('截图已保存：'+str(snapshot(ROOT/'captures',self.key,self.last_image,self.meta)))
        except Exception as e:self.notice.emit(str(e))

class Monitor(QMainWindow):
    def __init__(self):
        super().__init__();self.setWindowTitle('青健智课 · 智慧体育展示平台')
        available=QApplication.primaryScreen().availableGeometry();height=min(900,available.height()-55);width=min(1600,available.width()-35,int(height*16/9));self.resize(width,int(width*9/16));self.setMinimumSize(1024,600)
        self.analysis=AnalysisReceiver(self);self.recorder=None;self.saved_recorder=None;self.selected='original';self.single=None;self.closing=False;self.mode_integration={'original':True,'gray':False,'gaussian':False,'histogram':False}
        self.config_path=ROOT/'display_config.json';self.root=QWidget();self.root.setObjectName('showcaseRoot');self.setCentralWidget(self.root);v=QVBoxLayout(self.root);v.setContentsMargins(16,5,16,12);v.setSpacing(12)
        self.header=Header();self.mode=self.header.mode;self.clock=self.header.clock;v.addWidget(self.header)
        body=QHBoxLayout();self.body=body;body.setSpacing(10)
        self.comparison_panel=QFrame();self.comparison_panel.setObjectName('comparisonPanel');left=QVBoxLayout(self.comparison_panel);left.setContentsMargins(10,12,10,10);left.setSpacing(9)
        self.comparison_heading=QWidget();heading=QVBoxLayout(self.comparison_heading);heading.setContentsMargins(0,0,0,0);heading.setSpacing(4)
        label=QLabel('FPGA图像对比');label.setObjectName('sectionTitle');heading.addWidget(label)
        label=QLabel('选择画面 · 双击或“单画面”放大');label.setObjectName('smallMuted');heading.addWidget(label);left.addWidget(self.comparison_heading)
        self.grid_widget=QWidget();self.grid=QGridLayout(self.grid_widget);self.grid.setContentsMargins(0,0,0,0);self.grid.setSpacing(8);self.tiles={}
        for i,(key,title) in enumerate([('original','原始视频'),('gray','灰度处理'),('gaussian','高斯滤波'),('histogram','直方图均衡')]):
            tile=Tile(key,title);self.tiles[key]=tile;self.grid.addWidget(tile,i//2,i%2);tile.notice.connect(self.log_message);tile.select.connect(self.select);tile.full.connect(self.toggle_single);tile.new_frame.connect(self.frame)
            tile.findChild(QLabel,'cameraIndex').setFixedWidth(28);tile.status.setMinimumWidth(42)
            tile.layout().itemAt(0).layout().setSpacing(5)
        self.grid.setRowStretch(0,1);self.grid.setRowStretch(1,1);self.grid.setColumnStretch(0,1);self.grid.setColumnStretch(1,1)
        left.addWidget(self.grid_widget,1);body.addWidget(self.comparison_panel,4)
        self.ai_key='original';self.ai_last_stamp=None;self.ai_canvas=Canvas();self.ai_canvas.clear('等待分析视频\n选择左侧视频源后可在这里展示')
        self.ai_panel=AnalysisStage(self.ai_canvas,MODES);body.addWidget(self.ai_panel,5)
        self.roster=StudentRoster();self.sidebar_scroll=self.roster.scroll;body.addWidget(self.roster,1);v.addLayout(body,1)
        self.ai_panel.source_combo.currentIndexChanged.connect(self.change_analysis_source)
        self.ai_panel.all_button.clicked.connect(lambda:self.roster.select_person(''))
        self.roster.selected.connect(self.focus_person)
        self.legacy_results=QWidget(self);self.legacy_results.hide()
        self.analysis_source=QLabel('等待分析数据',self.legacy_results)
        self.values={};self.metric_cards={}
        for key,title,name,color,number in [('action_type','当前运动项目','run','#76ceff',False),('rep_count','动作次数','count','#43dfd5',True),('correct_count','正确次数','check','#4ce6ad',True),('incorrect_count','错误次数','cross','#ff7183',True),('action_phase','当前动作阶段','phase','#74c7ff',False),('quality_score','综合评分','score','#ffd067',True)]:
            self.values[key]=QLabel('--',self.legacy_results)
        self.run_card=MetricCard('运行状态','signal','#68d49c');self.run_card.setParent(self.legacy_results);self.run_card.value.setText('等待输入')
        self.controls=QFrame();self.controls.setObjectName('controlBar');bar=QHBoxLayout(self.controls);bar.setContentsMargins(9,3,9,3);bar.setSpacing(4);self.view_combo=QComboBox();self.view_combo.setFixedWidth(145)
        for key in self.tiles:self.view_combo.addItem(MODES[key],key)
        self.view_combo.currentIndexChanged.connect(lambda i:self.select(self.view_combo.itemData(i)));bar.addWidget(self.view_combo);self.tools={}
        specs=[('source','选源','source',lambda:self.tiles[self.selected].configure(),'Ctrl+O'),('pause','暂停','pause',lambda:self.tiles[self.selected].pause(),'Ctrl+P'),('disconnect','断开','disconnect',lambda:self.tiles[self.selected].disconnect(),''),('fullscreen','单画面','fullscreen',lambda:self.toggle_single(self.selected),'Ctrl+F'),('snapshot','截图','snapshot',lambda:self.tiles[self.selected].capture(),'Ctrl+Shift+S'),('record','录像','record',self.toggle_record,'Ctrl+R')]
        for key,text,name,fn,shortcut in specs:
            b=self.tool(text,name,fn,shortcut);self.tools[key]=b;bar.addWidget(b)
        self.record_btn=self.tools['record'];self.record_btn.setObjectName('recordButton');bar.addStretch();b=self.tool('技术诊断','diagnostics',self.toggle_diagnostics,'Ctrl+D');b.setCheckable(True);self.tools['diagnostics']=b;bar.addWidget(b);v.addWidget(self.controls)
        self.footer=QFrame();self.footer.setObjectName('statusStrip');foot=QHBoxLayout(self.footer);foot.setContentsMargins(11,4,11,4);self.footer_dot=QLabel('●');self.footer_dot.setObjectName('footerDot');foot.addWidget(self.footer_dot);self.footer_text=QLabel('离线准备 · 选择视频源后开始展示');self.footer_text.setObjectName('footerText');foot.addWidget(self.footer_text,1);self.footer_metrics=QLabel('原始视频 · --');self.footer_metrics.setObjectName('footerText');foot.addWidget(self.footer_metrics);v.addWidget(self.footer)
        self.build_diagnostics();self.analysis.message.connect(self.on_analysis);self.analysis.status.connect(self.analysis_status.setText)
        self.timer=QTimer(self);self.timer.setInterval(250);self.timer.timeout.connect(self.tick);self.timer.start();self.load_config();self.setStyleSheet(STYLE+CLASSROOM_STYLE);self.select('original')
        shortcut=QShortcut(QKeySequence('F11'),self);shortcut.activated.connect(lambda:self.showNormal() if self.isFullScreen() else self.showFullScreen())
    def tool(self,text,name,fn,shortcut=''):
        b=QToolButton();b.setText(text);b.setIcon(icon(name));b.setIconSize(QSize(21,21));b.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon);b.setAccessibleName(text);b.setToolTip(text+(' · '+shortcut if shortcut else ''));b.clicked.connect(fn)
        if shortcut:sc=QShortcut(QKeySequence(shortcut),self);sc.activated.connect(fn)
        return b
    def build_diagnostics(self):
        self.diag_dock=QDockWidget('技术诊断 · 仅显示端',self);self.diag_dock.setAllowedAreas(Qt.DockWidgetArea.BottomDockWidgetArea|Qt.DockWidgetArea.RightDockWidgetArea);self.diag_dock.setFeatures(QDockWidget.DockWidgetFeature.DockWidgetClosable|QDockWidget.DockWidgetFeature.DockWidgetMovable|QDockWidget.DockWidgetFeature.DockWidgetFloatable);tabs=QTabWidget();self.diag_tabs=tabs
        video=QWidget();h=QHBoxLayout(video);self.diag_stack=QStackedWidget()
        for tile in self.tiles.values():self.diag_stack.addWidget(tile.diagnostics)
        h.addWidget(self.diag_stack,1);self.metadata=QPlainTextEdit();self.metadata.setReadOnly(True);self.metadata.setPlaceholderText('当前帧及状态元数据');h.addWidget(self.metadata,1);tabs.addTab(video,'视频 / 来源 / FPS')
        analysis=QWidget();f=QFormLayout(analysis);self.selection=QLabel('分析面板 · 原始视频');f.addRow(self.selection);self.diag_analysis_source=QLabel('等待数据');f.addRow('来源与帧身份',self.diag_analysis_source)
        for key,title in [('subject','训练对象'),('pose_state','识别状态'),('error_flags','纠错信息')]:value=QLabel('--');value.setWordWrap(True);self.values[key]=value;f.addRow(title,value)
        f.addRow(QLabel('旧接口全局结果 · 不自动归属给任何学生'))
        for key,title in [('action_type','项目'),('rep_count','次数'),('correct_count','正确'),('incorrect_count','错误'),('action_phase','阶段'),('quality_score','评分')]:f.addRow(title,self.values[key])
        self.host=QLineEdit('127.0.0.1');self.port=QSpinBox();self.port.setRange(1,65535);self.port.setValue(8766);f.addRow('分析主机',self.host);f.addRow('分析端口',self.port);h=QHBoxLayout();b=QPushButton('连接JSON分析');b.clicked.connect(lambda:self.analysis.configure(self.host.text().strip(),self.port.value()));h.addWidget(b);b=QPushButton('断开分析');b.clicked.connect(self.analysis.stop);h.addWidget(b);f.addRow(h);self.analysis_status=QLabel('未连接');f.addRow('连接状态',self.analysis_status);tabs.addTab(analysis,'动作 JSON')
        logs=QWidget();v=QVBoxLayout(logs);self.record_status=QLabel('录像保存为PNG帧序列及时间戳JSON');v.addWidget(self.record_status);self.log=QPlainTextEdit();self.log.setReadOnly(True);self.log.setMaximumBlockCount(300);v.addWidget(self.log);h=QHBoxLayout();b=QPushButton('保存源配置');b.clicked.connect(self.save_config);h.addWidget(b);b=QPushButton('导出当前诊断');b.clicked.connect(self.export_diagnostics);h.addWidget(b);h.addStretch();v.addLayout(h);tabs.addTab(logs,'日志 / 保存')
        self.diag_dock.setWidget(tabs);self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea,self.diag_dock);self.diag_dock.setFloating(True);self.diag_dock.resize(900,370);self.diag_dock.hide();self.diag_dock.visibilityChanged.connect(lambda visible:self.tools['diagnostics'].setChecked(visible))
    def toggle_diagnostics(self):self.diag_dock.setVisible(not self.diag_dock.isVisible());self.update_metadata()
    def update_metadata(self):
        if not self.diag_dock.isVisible():return
        tile=self.tiles[self.selected];text=json.dumps({'view':self.selected,'state':tile.receiver.status,'frame':tile.meta,'metrics':tile.metrics},ensure_ascii=False,indent=2)
        if self.metadata.toPlainText()!=text:self.metadata.setPlainText(text)
    def export_diagnostics(self):
        name,_=QFileDialog.getSaveFileName(self,'导出显示端诊断',str(ROOT/'display_diagnostics.json'),'JSON (*.json)')
        if not name:return
        tile=self.tiles[self.selected];Path(name).write_text(json.dumps({'view':self.selected,'state':tile.receiver.status,'frame':tile.meta,'metrics':tile.metrics,'log':self.log.toPlainText()},ensure_ascii=False,indent=2),encoding='utf8');self.log_message('诊断已保存：'+name)
    def resizeEvent(self,e):
        super().resizeEvent(e)
        if hasattr(self,'metric_cards'):
            compact=self.height()<800 or self.width()<1280
            for card in self.metric_cards.values():card.set_compact(compact)
            QTimer.singleShot(50,self.fit_analysis_cards)
    def fit_analysis_cards(self):
        # Student cards intentionally scroll; never shrink a whole class to fit.
        pass
    def log_message(self,text):
        if hasattr(self,'log'):self.log.appendPlainText(datetime.now().strftime('%H:%M:%S')+'  '+text)
    def select(self,key):
        if key not in self.tiles:return
        self.selected=key;self.selection.setText('分析面板 · '+MODES[key]);index=list(self.tiles).index(key);self.diag_stack.setCurrentIndex(index);self.view_combo.blockSignals(True);self.view_combo.setCurrentIndex(index);self.view_combo.blockSignals(False)
        for k,tile in self.tiles.items():tile.setProperty('selected',k==key);tile.style().unpolish(tile);tile.style().polish(tile)
        if self.single:
            self.layout_video_views(key)
        self.refresh_panel();self.update_metadata();self.refresh_presentation()
    def frame(self,key,image,meta):
        # Comparison views show the supplied pixels; skeletons belong to the AI stage.
        self.tiles[key].canvas.pose=None
        if self.recorder:self.recorder.frame(key,image,meta)
        if key==self.ai_key:self.refresh_classroom()
    def on_analysis(self,msg):
        if self.recorder:self.recorder.analysis(msg)
        self.refresh_panel();self.refresh_classroom()
    def change_analysis_source(self,index):
        self.ai_key=self.ai_panel.source_combo.itemData(index) or 'original';self.ai_last_stamp=None;self.roster.clear();self.focus_person('');self.refresh_classroom()
    def focus_person(self,key):
        self.ai_canvas.highlight_person=key or None
        card=self.roster.cards.get(key);self.ai_panel.focus.setText('当前关注：'+(card.name.text() if card else '全班'));self.ai_canvas.update()
    def refresh_classroom(self):
        tile=self.tiles[self.ai_key];canvas=self.ai_canvas
        stamp=(self.ai_key,tile.canvas.present_seq,tile.receiver.status)
        if tile.last_image.isNull():
            if self.ai_last_stamp!=stamp:canvas.clear('等待分析视频\n在左侧配置视频源后选择对应画面')
        elif self.ai_last_stamp!=stamp:
            canvas.image=tile.last_image;canvas.meta=tile.meta;canvas.present_seq+=1;canvas.update()
        self.ai_last_stamp=stamp
        active=tile.receiver.status in ('connected','paused')
        pose=self.analysis.matching(tile.meta) if active and time.monotonic()-self.analysis.last_at<2 else None
        if canvas.pose is not pose:canvas.pose=pose;canvas.update()
        m=self.analysis.latest;ident=identity(tile.meta)
        fresh=m is not None and time.monotonic()-self.analysis.last_at<2
        compatible=active and fresh and ident is not None and identity(m) is not None and ident[:3]==identity(m)[:3] and tile.meta.get('source')==m['source']
        if compatible:
            self.roster.update_people(m);self.ai_panel.people.setText(f"人数 {len(m.get('persons',[]))}")
            self.focus_person(self.roster.current or '')
            same=pose is not None
            self.ai_panel.status.show_status('模拟分析' if m['source']=='simulation' else '同帧分析' if same else '等待同帧','demo' if m['source'] in ('simulation','replay') else 'success' if same else 'warning')
            self.ai_panel.notice.setText('模拟分析 · 不代表板端识别结果' if m['source']=='simulation' else '回放分析 · 非实时' if m['source']=='replay' else '外部分析结果 · 身份绑定按来源声明')
            if not same:self.ai_panel.notice.setText(self.ai_panel.notice.text()+' · 尚无同帧骨架')
        else:
            reason='等待分析数据' if not fresh else '等待匹配的视频 / 分析数据'
            if self.roster.cards or self.roster.source.text()!=reason:self.roster.clear(reason)
            self.ai_panel.people.setText('人数 --');self.focus_person('')
            self.ai_panel.status.show_status('待接入' if tile.last_image.isNull() else '等待分析','offline')
            self.ai_panel.notice.setText('暂未接入有效多人分析 · 不生成成绩')
        self.ai_panel.source.setText(MODES[self.ai_key]+' · '+tile.display_source.text())
    def refresh_panel(self):
        meta=self.tiles[self.selected].meta;m=self.analysis.latest
        fresh=m is not None and time.monotonic()-self.analysis.last_at<2
        matching_stream=fresh and identity(meta) is not None and identity(meta)[:3]==identity(m)[:3] and meta.get('source')==m['source']
        if not matching_stream:
            self.analysis_source.setText('等待匹配的分析数据' if fresh else '等待分析数据');self.diag_analysis_source.setText('等待匹配的视频 / 分析数据' if fresh else '等待数据 / 数据已过期')
            for x in self.values.values():x.setText('--')
            return
        source='模拟数据' if m['source']=='simulation' else '回放分析数据' if m['source']=='replay' else '外部分析 · 声明来源'
        self.analysis_source.setText(source);self.diag_analysis_source.setText(source+f" · 帧 {m['frame_id']} · 骨架仅匹配同帧")
        for k,label in self.values.items():
            value=m.get(k);label.setText('、'.join(value) or '无' if isinstance(value,list) else str(value) if value is not None else '--')
    def tick(self):
        self.clock.setText(datetime.now().strftime('%Y.%m.%d  %H:%M:%S'));self.refresh_panel();self.refresh_classroom();self.refresh_presentation();self.update_metadata()
        for tile in self.tiles.values():
            if tile.canvas.pose and not self.analysis.matching(tile.meta):tile.canvas.pose=None;tile.canvas.update()
        r=self.recorder or self.saved_recorder
        if r:self.record_status.setText(f"已存 {r.written} 帧 · 录制队列丢弃 {r.dropped}"+(' · '+r.error if r.error else ''))
    def refresh_presentation(self):
        tile=self.tiles[self.selected];state=tile.receiver.status;source=tile.meta.get('source');r=tile.receiver
        if state=='connected':
            label='模拟演示中' if source=='simulation' else '本地回放中' if source=='replay' else '视频接收中';mode='演示模式' if source in ('simulation','replay') else '外部视频预览'
        elif state=='ended':label='本次已结束';mode='保留末帧 · 非实时' if not tile.last_image.isNull() else '本次诊断已结束'
        elif state=='paused':label='显示已暂停';mode='暂停展示'
        elif state in ('connecting','buffering','reconnecting'):label='正在连接' if state=='connecting' else '正在缓冲' if state=='buffering' else '等待重连';mode='连接中'
        elif state=='armed':label='等待获准窗口';mode='尚未开流'
        elif state=='blocked':label='配置待核验';mode='已停止接收'
        elif state=='interrupted':label='视频已中断';mode='保留末帧' if not tile.last_image.isNull() else '等待输入'
        else:label='等待输入';mode='离线准备'
        self.run_card.value.setText(label);self.mode.setText(mode)
        truth='模拟数据 · 不代表板端结果' if source=='simulation' else '本地回放 · 非实时采集' if source=='replay' else '来源与处理方式以接口声明为准' if source=='external' else '选择视频源后开始展示'
        if state=='ended':truth='窗口已结束 · 不会自动重开'
        elif state=='armed':truth='等待新的获准采集窗口'
        elif state=='blocked':truth='配置或模式不匹配 · 请查看技术诊断'
        self.footer_text.setText(label+'  |  '+truth)
        size=f'{tile.last_image.width()}×{tile.last_image.height()}' if not tile.last_image.isNull() else '--'
        m=tile.metrics;rate=f"接收 {m.get('receive_fps',0):.1f} / 绘制 {m.get('paint_fps',0):.1f} FPS" if not tile.last_image.isNull() else '未接收视频'
        self.footer_metrics.setText(MODES[self.selected]+'  ·  '+size+'  ·  '+rate)
        self.tools['pause'].setText('继续' if r.paused else '暂停');self.tools['pause'].setIcon(icon('play' if r.paused else 'pause'))
    def toggle_record(self):
        if self.recorder:
            self.recorder.stop();self.saved_recorder=self.recorder;self.recorder=None;self.record_btn.setText('录像');self.record_btn.setProperty('recording',False);self.record_btn.style().unpolish(self.record_btn);self.record_btn.style().polish(self.record_btn);return
        if self.saved_recorder and self.saved_recorder.thread.is_alive():self.log_message('正在保存上一段录制');return
        try:self.recorder=Recorder(ROOT/'recordings'/datetime.now().strftime('%Y%m%d_%H%M%S_%f'));self.record_btn.setText('停止录像');self.record_btn.setProperty('recording',True);self.record_btn.style().unpolish(self.record_btn);self.record_btn.style().polish(self.record_btn);self.log_message('录制到 '+str(self.recorder.directory))
        except Exception as e:self.log_message(str(e))
    def layout_video_views(self,single):
        self.single=single
        # Hiding three widgets retains the 2x2 grid's empty stretch tracks.
        # Rebuild the grid so the selected tile occupies the full viewport.
        for tile in self.tiles.values():self.grid.removeWidget(tile)
        for i,(key,tile) in enumerate(self.tiles.items()):
            tile.setVisible(single is None or key==single)
            if single is None:self.grid.addWidget(tile,i//2,i%2)
            elif key==single:self.grid.addWidget(tile,0,0)
        self.grid.setRowStretch(0,1);self.grid.setRowStretch(1,1 if single is None else 0)
        self.grid.setColumnStretch(0,1);self.grid.setColumnStretch(1,1 if single is None else 0)
        self.header.setVisible(single is None);self.roster.setVisible(single is None);self.ai_panel.setVisible(single is None);self.comparison_heading.setVisible(single is None)
        self.comparison_panel.layout().setContentsMargins(10,12,10,10) if single is None else self.comparison_panel.layout().setContentsMargins(0,0,0,0)
        for i,stretch in enumerate((4,5,1) if single is None else (1,0,0)):self.body.setStretch(i,stretch)
        layout=self.root.layout()
        if single is None:layout.setContentsMargins(16,5,16,12)
        else:layout.setContentsMargins(8,4,8,6)
        layout.setSpacing(12 if single is None else 6)
        b=self.tools['fullscreen'];b.setText('返回四画面' if single else '单画面');b.setAccessibleName(b.text());b.setToolTip('返回四画面 · Esc / Ctrl+F' if single else '单画面 · Ctrl+F')
        self.grid.invalidate();layout.activate()
    def toggle_single(self,key):
        if key not in self.tiles:return
        was_single=self.single;leaving=was_single==key
        if was_single is None:
            self.single_previous_fullscreen=self.isFullScreen();self.single_previous_maximized=self.isMaximized()
        self.select(key);self.layout_video_views(None if leaving else key)
        if self.single:self.showFullScreen()
        elif self.single_previous_fullscreen:self.showFullScreen()
        elif self.single_previous_maximized:self.showMaximized()
        else:self.showNormal()
    def keyPressEvent(self,e):
        if e.key()==Qt.Key.Key_Escape and self.single:self.toggle_single(self.single)
        elif e.key()==Qt.Key.Key_Escape and self.isFullScreen():self.showNormal()
        else:super().keyPressEvent(e)
    def save_config(self):
        d={'views':{k:asdict(t.spec) for k,t in self.tiles.items()},'analysis':{'host':self.host.text(),'port':self.port.value()},'mode_integration':self.mode_integration,'presentation':{'analysis_view':self.ai_key}}
        # Never persist credentials or URL query tokens.
        for x in d['views'].values():x['location']=safe_url(x['location']);x['status_url']=safe_url(x['status_url'])
        self.config_path.write_text(json.dumps(d,ensure_ascii=False,indent=2),encoding='utf8');self.log_message('配置已保存；启动时不会自动连接')
    def load_config(self):
        if not self.config_path.exists():return
        try:
            d=json.loads(self.config_path.read_text(encoding='utf8'))
            for k,enabled in d.get('mode_integration',{}).items():
                if k in self.tiles and type(enabled) is bool:self.mode_integration[k]=enabled;self.tiles[k].allow_network=enabled
            for k,s in d.get('views',{}).items():
                if k in self.tiles:
                    tile=self.tiles[k];tile.spec=SourceSpec(**s)
                    if tile.allow_network and tile.spec.kind!='none' and tile.receiver.status=='disconnected':
                        tile.canvas.clear('视频源已配置 · 等待连接');tile.status.show_status('未连接','offline');tile.display_source.setText('已配置视频源 · 尚未接收画面');tile.source_label.setText('视频源：'+safe_url(tile.spec.location))
            self.host.setText(d.get('analysis',{}).get('host','127.0.0.1'));self.port.setValue(d.get('analysis',{}).get('port',8766))
            key=d.get('presentation',{}).get('analysis_view','original');index=self.ai_panel.source_combo.findData(key)
            if index>=0:self.ai_panel.source_combo.setCurrentIndex(index)
        except Exception as e:self.log_message('配置读取失败：'+str(e))
    def closeEvent(self,e):
        if not self.closing:
            self.closing=True;self.close_deadline=time.monotonic()+5;self.analysis.close();self.diag_dock.hide()
            for t in self.tiles.values():t.receiver.close()
            if self.recorder:self.recorder.stop();self.saved_recorder=self.recorder;self.recorder=None
        pending_receivers=any(x.is_alive() for t in self.tiles.values() for x in t.receiver.retired)
        if (self.saved_recorder and self.saved_recorder.thread.is_alive()) or (pending_receivers and time.monotonic()<self.close_deadline):
            e.ignore();QTimer.singleShot(100,self.close);self.record_status.setText('正在停止接收并保存数据…');return
        self.timer.stop();e.accept()

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--config',type=Path,help='载入配置，不会自动连接');inputs=parser.add_mutually_exclusive_group();inputs.add_argument('--local',type=Path);inputs.add_argument('--demo',action='store_true',help='本机模拟视频与分析数据，非RK实流');args=parser.parse_args()
    app=QApplication.instance() or QApplication(sys.argv[:1]);win=Monitor();win.show()
    if args.config:
        win.config_path=args.config.resolve();win.load_config();enabled=[k for k,tile in win.tiles.items() if tile.allow_network and tile.spec.kind!='none']
        if len(enabled)==1:win.select(enabled[0])
    if args.demo:
        from local_test_service import LocalService
        for key,tile in win.tiles.items():tile.allow_network=key=='original';win.mode_integration[key]=key=='original'
        win.select('original')
        win.demo_service=LocalService(0,0);app.aboutToQuit.connect(win.demo_service.close)
        def connect_demo():
            win.tiles['original'].play(SourceSpec('tcpjpeg',f'tcpjpeg://127.0.0.1:{win.demo_service.video_port}'))
            win.analysis.configure('127.0.0.1',win.demo_service.analysis_port)
        QTimer.singleShot(0,connect_demo)
    elif args.local:QTimer.singleShot(0,lambda:win.tiles['original'].play(SourceSpec('mp4',str(args.local.resolve()),loop=True)))
    return app.exec()
if __name__=='__main__':raise SystemExit(main())

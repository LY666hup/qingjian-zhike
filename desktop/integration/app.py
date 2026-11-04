"""Independent dual-view extension of the preserved classroom UI."""
from __future__ import annotations
import argparse,json,sys,time
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from PySide6.QtCore import Qt,QTimer
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication,QComboBox,QLabel,QLineEdit
from legacy_app import Monitor as LegacyMonitor,Tile,Canvas,SourceDialog,ROOT
from contracts import identity,MODES
from video_sources import SourceSpec
from receivers import safe_url
from recording import snapshot
from dual_widgets import DualAnalysisStage,DUAL_STYLE
from image_metrics import preview_metrics
from rk_ai_receiver import RkAiReceiver
from PySide6.QtWidgets import QWidget,QFormLayout,QHBoxLayout,QPushButton,QPlainTextEdit

CAMERAS=('A','B');NETWORK=('mjpeg','tcpjpeg','rtsp','http')

class CameraTile(Tile):
    def __init__(self,camera,mode,title,owner):
        super().__init__(mode,title);self.camera_slot=camera;self.owner=owner;self.expected_camera_id=''
        self.findChild(QLabel,'cameraIndex').setFixedWidth(32)
        self.layout().itemAt(0).layout().setSpacing(5)
        self.playback_metrics=QLabel('接收 -- / 绘制 -- FPS · --×--');self.playback_metrics.setObjectName('tileMetrics')
        self.content_metrics=QLabel('亮度 -- · 对比 -- · 边缘 --');self.content_metrics.setObjectName('tileMetrics')
        for label in (self.playback_metrics,self.content_metrics):label.setWordWrap(True);label.setMinimumWidth(0);self.layout().addWidget(label)
        self.playback_metrics.setToolTip('接收为解码交付FPS；绘制为paintEvent中新帧FPS。2秒滑窗，不代表传感器独立更新率；丢弃及延迟详见技术诊断。')
        self.content_metrics.setToolTip('显示端只统计解码预览，不修改图像。固定160×90抽样；亮度=编码RGB加权平均，对比=亮度标准差，边缘=相邻亮度绝对差均值，数值范围0..255。受曝光/画面/编码影响，无好坏评分；跨模式须先确认同一相机、session、frame，不能视为FPGA处理耗时。')
        self.metric_at=0;self.metric_values=None
    def configure(self):
        d=SourceDialog(self.spec,self);d.resize(680,500);d.setWindowTitle(self.camera_slot+'路 · '+MODES[self.key]+'显示源')
        expected=QLineEdit(self.expected_camera_id);expected.setPlaceholderText('可选：发送端实际camera_id；不把A/B显示位当实物证据')
        d.layout().insertRow(d.layout().rowCount()-2,'预期相机ID',expected)
        if d.exec():self.expected_camera_id=expected.text().strip();self.play(d.spec())
    def play(self,spec):
        if not self.owner.can_open(self,spec):return
        super().play(spec)
    def disconnect(self):
        super().disconnect()
        if hasattr(self,'content_metrics'):
            self.metric_values=None;self.content_metrics.setText('亮度 -- · 对比 -- · 边缘 --');self.playback_metrics.setText('接收 -- / 绘制 -- FPS · --×--')
        if self.owner.dual_ready:self.owner.refresh_classroom()
    def capture(self):
        if self.last_image.isNull():self.notice.emit('尚无可保存的视频帧');return
        try:self.notice.emit('截图已保存：'+str(snapshot(ROOT/'captures',self.camera_slot+'_'+self.key,self.last_image,dict(self.meta,display_camera_slot=self.camera_slot))))
        except Exception as e:self.notice.emit(str(e))
    def on_frame(self,image,meta):
        actual=meta.get('camera_id')
        if self.expected_camera_id and str(actual)!=self.expected_camera_id:
            self.disconnect();self.status.show_status('身份不符','warning')
            self.display_source.setText('相机身份不符 · 已拒绝画面');self.notice.emit('预期camera_id='+self.expected_camera_id+'，实际='+str(actual));return
        mode=meta.get('processing_mode')
        if meta.get('source')=='external' and mode in MODES and mode not in (getattr(self,'sprint_mode',self.key),'unknown'):
            self.disconnect();self.status.show_status('模式不符','warning');self.display_source.setText('发送模式与显示位不符 · 已拒绝画面');return
        super().on_frame(image,meta)
        self.display_source.setText(self.camera_slot+'路 · '+self.display_source.text())
        now=time.monotonic()
        if now-self.metric_at>=.5 or self.metric_values is None:
            self.metric_at=now;self.metric_values=preview_metrics(image)
            if self.metric_values:
                v=self.metric_values;self.content_metrics.setText(f"亮度 {v['mean']:.1f} · 对比 {v['contrast']:.1f} · 边缘 {v['edge']:.1f}")
    def on_stats(self,metrics):
        super().on_stats(metrics)
        if hasattr(self,'playback_metrics'):
            size=f'{self.last_image.width()}×{self.last_image.height()}' if not self.last_image.isNull() else '--×--'
            rate=f"接收 {self.metrics.get('receive_fps',0):.1f} / 绘制 {self.metrics.get('paint_fps',0):.1f} FPS" if not self.last_image.isNull() else '接收 -- / 绘制 -- FPS'
            self.playback_metrics.setText(('静态快照 · 非实时 · '+size) if self.spec.kind=='snapshot' else (rate+' · '+size))
    def on_state(self,state,detail):
        super().on_state(state,detail)
        if hasattr(self,'content_metrics') and self.last_image.isNull():
            self.metric_values=None;self.content_metrics.setText('亮度 -- · 对比 -- · 边缘 --')

class Monitor(LegacyMonitor):
    def __init__(self):
        self.dual_ready=False;super().__init__()
        self.setWindowTitle('青健智课 · 智慧体育展示平台 · 双视角')
        old_tiles=self.tiles;old_stage=self.ai_panel
        for tile in old_tiles.values():self.grid.removeWidget(tile);tile.receiver.close();self.diag_stack.removeWidget(tile.diagnostics);tile.deleteLater()
        self.body.removeWidget(old_stage);old_stage.deleteLater()
        self.camera_tiles={};self.camera_modes={};self.active_camera='A';self.primary_camera='A';self.result_camera='A'
        self.analysis_views={'A':'original','B':'original'};self.camera_stamps={}
        for camera in CAMERAS:
            self.camera_modes[camera]={k:k=='original' for k in MODES if k!='unknown'};self.camera_tiles[camera]={}
            for mode,title in [('original','原始视频'),('gray','灰度处理'),('gaussian','高斯滤波'),('histogram','直方图均衡')]:
                tile=CameraTile(camera,mode,title,self);self.camera_tiles[camera][mode]=tile
                tile.notice.connect(lambda text,c=camera,m=mode:self.log_message(c+'/'+m+' '+text))
                tile.select.connect(lambda key,c=camera:self.select_camera_tile(c,key))
                tile.full.connect(lambda key,c=camera:self.full_camera_tile(c,key))
                tile.new_frame.connect(lambda key,img,meta,c=camera:self.frame_for(c,key,img,meta))
                self.diag_stack.addWidget(tile.diagnostics);tile.hide()
        self.tiles=self.camera_tiles['A'];self.mode_integration=self.camera_modes['A']
        self.analysis_canvases={c:Canvas() for c in CAMERAS}
        self.ai_panel=DualAnalysisStage(self.analysis_canvases,MODES);self.ai_canvas=self.analysis_canvases['A']
        self.body.insertWidget(1,self.ai_panel,5)
        self.ai_panel.source_combo.currentIndexChanged.connect(self.change_analysis_source)
        self.ai_panel.swap_requested.connect(self.swap_primary)
        self.ai_panel.all_button.clicked.connect(lambda:self.roster.select_person(''))
        self.camera_combo=QComboBox();self.camera_combo.setObjectName('cameraSelector')
        for camera in CAMERAS:self.camera_combo.addItem(camera+'路视角',camera)
        self.controls.layout().insertWidget(0,self.camera_combo);self.camera_combo.currentIndexChanged.connect(lambda i:self.set_camera(self.camera_combo.itemData(i)))
        self.camera_hint=QLabel('A / B分别选源 · 画面统计不是质量评分');self.camera_hint.setObjectName('cameraHint');self.comparison_heading.layout().addWidget(self.camera_hint)
        self.result_combo=QComboBox();self.result_combo.addItem('结果来源 A路','A');self.result_combo.addItem('结果来源 B路','B')
        self.roster.layout().insertWidget(1,self.result_combo);self.result_combo.currentIndexChanged.connect(lambda i:self.set_result_camera(self.result_combo.itemData(i)))
        self.result_policy=QLabel('按视角独立统计\n未自动合并或累加');self.result_policy.setWordWrap(True);self.result_policy.setObjectName('resultPolicy');self.roster.layout().addWidget(self.result_policy)
        self.setStyleSheet(self.styleSheet()+DUAL_STYLE);self.dual_ready=True
        self.load_config();self.layout_video_views(None);self.select('original');self.refresh_classroom();self.setup_rk_ai()
    def setup_rk_ai(self):
        self.rk_ai=RkAiReceiver(self)
        page=QWidget();form=QFormLayout(page)
        self.rk_ai_url=QLineEdit('http://169.254.67.238:8090')
        form.addRow('RK只读结果基址',self.rk_ai_url)
        row=QHBoxLayout();connect=QPushButton('连接 RK 检测（只读 GET）');disconnect=QPushButton('断开 RK 检测')
        connect.clicked.connect(self.connect_rk_ai);disconnect.clicked.connect(self.rk_ai.stop);row.addWidget(connect);row.addWidget(disconnect);form.addRow(row)
        self.rk_ai_status=QLabel('未连接 · 等待本次灰度有效窗口');self.rk_ai_people=QLabel('--');self.rk_ai_ms=QLabel('--')
        form.addRow('连接/模型状态',self.rk_ai_status);form.addRow('RK检测人数',self.rk_ai_people);form.addRow('RK报告NPU耗时(ms)',self.rk_ai_ms)
        note=QLabel('独立RK YOLO结果，非MediaPipe骨架；不绑定学生、不生成成绩。未证明当前视频同帧时不叠框。');note.setWordWrap(True);form.addRow(note)
        self.rk_ai_details=QPlainTextEdit();self.rk_ai_details.setReadOnly(True);form.addRow('会话/模型/检测框',self.rk_ai_details)
        self.diag_dock.widget().addTab(page,'RK只读检测');self.rk_ai.message.connect(self.on_rk_panel)
        self.rk_ai.status.connect(self.rk_ai_status.setText);self.rk_ai.status.connect(self.refresh_rk_status)
        self.rk_ui_timer=QTimer(self);self.rk_ui_timer.setInterval(500);self.rk_ui_timer.timeout.connect(self.refresh_rk_status);self.rk_ui_timer.start()
    def connect_rk_ai(self):
        tile=self.camera_tiles['A']['gray'];snap=tile.metrics.get('preview',{}).get('status_snapshot',{})
        if tile.receiver.status!='connected' or snap.get('state')!='CAPTURING' or not snap.get('session_id') or snap.get('processing_mode')!='gray':
            self.rk_ai_status.setText('先确认并接收本次有效灰度窗口；未发起AI GET');return
        self.rk_ai_scope={k:snap.get(k) for k in ('session_id','boot_id','fpga_bit_sha256','processing_mode')}
        self.rk_ai.configure(self.rk_ai_url.text())
    def on_rk_panel(self,panel):
        self.refresh_rk_status()
    def refresh_rk_status(self,*args):
        panel=self.rk_ai.latest
        if not panel or time.monotonic()-self.rk_ai.last_at>=2:
            self.rk_ai_people.setText('--');self.rk_ai_ms.setText('--');self.rk_ai_details.clear();return
        pin=getattr(self,'rk_ai_scope',None);snap=self.rk_ai.snapshot or {}
        if not pin or any(snap.get(k)!=v for k,v in pin.items()):
            self.rk_ai.stop();self.rk_ai_status.setText('AI会话身份变化，已停止只读接收');return
        self.rk_ai_people.setText(str(panel.get('persons','--')))
        self.rk_ai_ms.setText(str(panel.get('npu_ms')) if panel.get('npu_ms') is not None else '--')
        visible={k:v for k,v in panel.items() if k!='image_base64'}
        self.rk_ai_details.setPlainText(json.dumps(visible,ensure_ascii=False,indent=2))
    def all_tiles(self):return [t for group in self.camera_tiles.values() for t in group.values()]
    def can_open(self,tile,spec):
        if spec.kind in NETWORK:
            for other in self.all_tiles():
                if other is not tile and other.spec.kind in NETWORK and other.receiver.active and other.spec.location==spec.location:
                    self.log_message('该网络源已被'+other.camera_slot+'路/'+other.key+'使用，未重复开流');return False
        return True
    def set_camera(self,camera):
        if not self.dual_ready or camera not in CAMERAS:return
        self.active_camera=camera;self.tiles=self.camera_tiles[camera];self.mode_integration=self.camera_modes[camera]
        self.camera_combo.blockSignals(True);self.camera_combo.setCurrentIndex(CAMERAS.index(camera));self.camera_combo.blockSignals(False)
        self.layout_video_views(self.single);self.select(self.selected)
    def select_camera_tile(self,camera,mode):
        if camera!=self.active_camera:self.set_camera(camera)
        self.select(mode)
    def full_camera_tile(self,camera,mode):
        if camera!=self.active_camera:self.set_camera(camera)
        self.toggle_single(mode)
    def select(self,key):
        if not self.dual_ready:return super().select(key)
        if key not in self.tiles:return
        self.selected=key;self.selection.setText(self.active_camera+'路 · '+MODES[key]);self.diag_stack.setCurrentIndex(CAMERAS.index(self.active_camera)*4+list(self.tiles).index(key))
        self.view_combo.blockSignals(True);self.view_combo.setCurrentIndex(list(self.tiles).index(key));self.view_combo.blockSignals(False)
        for tile in self.all_tiles():
            tile.setProperty('selected',tile is self.tiles[key]);tile.style().unpolish(tile);tile.style().polish(tile)
        if self.single:self.layout_video_views(key)
        self.refresh_panel();self.refresh_presentation();self.update_metadata()
    def frame_for(self,camera,mode,image,meta):
        self.camera_tiles[camera][mode].canvas.pose=None
        if self.recorder:self.recorder.frame(camera+'_'+mode,image,dict(meta,display_camera_slot=camera))
        if mode==self.analysis_views[camera]:self.refresh_classroom()
    def change_analysis_source(self,index):
        if not self.dual_ready:return super().change_analysis_source(index)
        self.analysis_views[self.primary_camera]=self.ai_panel.source_combo.itemData(index) or 'original'
        self.camera_stamps.pop(self.primary_camera,None);self.refresh_classroom()
    def set_primary(self,camera):
        self.primary_camera=camera;self.ai_canvas=self.analysis_canvases[camera];self.ai_key=self.analysis_views[camera]
        self.ai_panel.set_primary(camera);self.ai_panel.source_combo.blockSignals(True)
        self.ai_panel.source_combo.setCurrentIndex(self.ai_panel.source_combo.findData(self.ai_key));self.ai_panel.source_combo.blockSignals(False)
        self.refresh_classroom()
    def swap_primary(self):self.set_primary('B' if self.primary_camera=='A' else 'A')
    def set_result_camera(self,camera):
        if camera not in CAMERAS:return
        self.result_camera=camera;self.roster.clear('等待'+camera+'路个人结果');self.refresh_classroom()
    def _message_for(self,tile,exact=False):
        if tile.receiver.status not in ('connected','paused') or identity(tile.meta) is None:return None
        if exact:return self.analysis.matching(tile.meta)
        now=time.monotonic();ident=identity(tile.meta)
        for key,(msg,at) in reversed(self.analysis.cache.items()):
            if key is not None and key[:3]==ident[:3] and msg.get('source')==tile.meta.get('source') and now-at<2:return msg
        return None
    def refresh_classroom(self):
        if not self.dual_ready:return super().refresh_classroom()
        descriptions=[]
        for camera in CAMERAS:
            mode=self.analysis_views[camera];tile=self.camera_tiles[camera][mode];canvas=self.analysis_canvases[camera]
            stamp=(mode,tile.canvas.present_seq,tile.last_image.isNull())
            if self.camera_stamps.get(camera)!=stamp:
                if tile.last_image.isNull():canvas.clear(camera+'路等待视频\n只展示已配置的对应来源')
                else:canvas.image=tile.last_image;canvas.meta=tile.meta;canvas.present_seq+=1;canvas.update()
            self.camera_stamps[camera]=stamp;pose=self._message_for(tile,True)
            if canvas.pose is not pose:canvas.pose=pose;canvas.update()
            now=time.monotonic()
            while canvas.paint_times and canvas.paint_times[0]<now-2:canvas.paint_times.popleft()
            origin='待接入' if tile.last_image.isNull() else '模拟' if tile.meta.get('source')=='simulation' else '回放' if tile.meta.get('source')=='replay' else '外部来源'
            if not tile.last_image.isNull() and tile.receiver.status=='ended':origin+=' · 已结束/保留末帧'
            elif not tile.last_image.isNull() and tile.receiver.status not in ('connected','paused'):origin+=' · 非实时'
            rate=f" · 接收{tile.metrics.get('receive_fps',0):.1f}/分析窗{len(canvas.paint_times)/2:.1f} FPS" if not tile.last_image.isNull() else ''
            descriptions.append(camera+'路 '+MODES[mode]+' · '+origin+rate)
        result_tile=self.camera_tiles[self.result_camera][self.analysis_views[self.result_camera]];m=self._message_for(result_tile)
        if m:
            self.roster.update_people(m);self.ai_panel.people.setText(self.result_camera+f"路 {len(m.get('persons',[]))}人")
            same=self._message_for(result_tile,True) is not None
            self.ai_panel.status.show_status('模拟分析' if m['source']=='simulation' else '同帧分析' if same else '结果已到','demo' if m['source'] in ('simulation','replay') else 'success')
            self.ai_panel.notice.setText(('模拟数据 · 不代表板端识别' if m['source']=='simulation' else '回放分析 · 非实时' if m['source']=='replay' else '外部分析 · 身份按来源声明')+f"；个人结果来自{self.result_camera}路帧{m['frame_id']}，不累加另一路")
        else:
            if self.roster.cards:self.roster.clear('等待'+self.result_camera+'路有效分析')
            self.ai_panel.people.setText(self.result_camera+'路人数 --');self.ai_panel.status.show_status('等待分析','offline')
            self.ai_panel.notice.setText('无有效个人分析 · 不生成成绩 · 不自动跨摄合并')
        self._apply_focus();self.ai_panel.source.setText('  |  '.join(descriptions))
        a=self.camera_tiles['A'][self.analysis_views['A']].meta;b=self.camera_tiles['B'][self.analysis_views['B']].meta
        if a.get('camera_id') is not None and a.get('camera_id')==b.get('camera_id') and a.get('session_id')==b.get('session_id'):
            self.ai_panel.notice.setText(self.ai_panel.notice.text()+' · 相机身份重复，双视角来源待核验')
        visible=[t for t in self.tiles.values() if not t.last_image.isNull()]
        ids=[identity(t.meta) for t in visible]
        paired=len(visible)==4 and all(i is not None for i in ids) and len(set((i[1],i[2],i[3]) for i in ids))==1
        self.camera_hint.setText(self.active_camera+'路 · '+('四模式同帧ID（来源声明）' if paired else '非同帧对照 · 仅描述各自画面'))
    def _apply_focus(self):
        key=self.roster.current
        for camera,canvas in self.analysis_canvases.items():
            canvas.highlight_person=key if camera==self.result_camera else None;canvas.update()
        card=self.roster.cards.get(key);self.ai_panel.focus.setText('当前关注：'+(self.result_camera+'路 '+card.name.text() if card else '全班'))
    def focus_person(self,key):
        if not self.dual_ready:return super().focus_person(key)
        if key and self.primary_camera!=self.result_camera:self.set_primary(self.result_camera)
        self._apply_focus()
    def refresh_panel(self):
        if not self.dual_ready:return super().refresh_panel()
        m=self._message_for(self.tiles[self.selected]);self.analysis_source.setText('等待匹配分析' if m is None else self.active_camera+'路 · '+m['source'])
        self.diag_analysis_source.setText('等待数据' if m is None else self.active_camera+f"路 帧{m['frame_id']} · 骨架只匹配同帧")
        for key,label in self.values.items():
            value=m.get(key) if m else None;label.setText('、'.join(value) or '无' if isinstance(value,list) else str(value) if value is not None else '--')
    def refresh_presentation(self):
        if not self.dual_ready:return super().refresh_presentation()
        super().refresh_presentation();self.footer_metrics.setText(self.active_camera+'路 · '+self.footer_metrics.text())
    def layout_video_views(self,single):
        if not self.dual_ready:return super().layout_video_views(single)
        for tile in self.all_tiles():self.grid.removeWidget(tile);tile.hide()
        super().layout_video_views(single)
    def save_config(self):
        if not self.dual_ready:return super().save_config()
        cameras={}
        for camera,group in self.camera_tiles.items():
            views={mode:asdict(t.spec) for mode,t in group.items()}
            for item in views.values():item['location']=safe_url(item['location']);item['status_url']=safe_url(item['status_url'])
            cameras[camera]=dict(views=views,mode_integration=self.camera_modes[camera],expected_camera_ids={mode:t.expected_camera_id for mode,t in group.items()})
        d=dict(schema='qjzc.display.dual.v1',cameras=cameras,analysis=dict(host=self.host.text(),port=self.port.value()),presentation=dict(comparison_camera=self.active_camera,primary_camera=self.primary_camera,result_camera=self.result_camera,analysis_views=self.analysis_views))
        self.config_path.write_text(json.dumps(d,ensure_ascii=False,indent=2),encoding='utf8');self.log_message('双路配置已保存；启动不会自动连接')
    def load_config(self):
        if not self.dual_ready:return super().load_config()
        if not self.config_path.exists():return
        try:
            d=json.loads(self.config_path.read_text(encoding='utf8'));groups=d.get('cameras') if d.get('schema')=='qjzc.display.dual.v1' else {'A':d}
            for camera,g in groups.items():
                if camera not in CAMERAS:continue
                for mode,enabled in g.get('mode_integration',{}).items():
                    if mode in self.camera_tiles[camera] and type(enabled) is bool:self.camera_modes[camera][mode]=enabled;self.camera_tiles[camera][mode].allow_network=enabled
                for mode,s in g.get('views',{}).items():
                    if mode in self.camera_tiles[camera]:
                        t=self.camera_tiles[camera][mode];t.spec=SourceSpec(**s);t.expected_camera_id=g.get('expected_camera_ids',{}).get(mode,'')
                        if t.spec.kind=='snapshot':
                            t.spec=SourceSpec(**dict(s,location=str((ROOT/s['location']).resolve())));t.play(t.spec)
                        elif t.spec.kind!='none':t.canvas.clear(camera+'路已配置 · 等待手动连接');t.display_source.setText('已配置，尚未接收')
            self.host.setText(d.get('analysis',{}).get('host','127.0.0.1'));self.port.setValue(d.get('analysis',{}).get('port',8766))
            p=d.get('presentation',{});self.analysis_views.update({k:v for k,v in p.get('analysis_views',{}).items() if k in CAMERAS and v in self.camera_tiles[k]})
            if 'analysis_view' in p:self.analysis_views['A']=p['analysis_view'] if p['analysis_view'] in self.camera_tiles['A'] else 'original'
            self.set_camera(p.get('comparison_camera','A'));self.set_primary(p.get('primary_camera','A') if p.get('primary_camera','A') in CAMERAS else 'A')
            self.result_combo.setCurrentIndex(CAMERAS.index(p.get('result_camera','A')) if p.get('result_camera','A') in CAMERAS else 0)
        except Exception as e:self.log_message('双路配置读取失败：'+str(e))
    def closeEvent(self,e):
        if not self.dual_ready:return super().closeEvent(e)
        if not self.closing:
            self.closing=True;self.close_deadline=time.monotonic()+5;self.analysis.close();self.diag_dock.hide()
            if hasattr(self,'rk_ai'):self.rk_ui_timer.stop();self.rk_ai.close()
            for tile in self.all_tiles():tile.receiver.close()
            if self.recorder:self.recorder.stop();self.saved_recorder=self.recorder;self.recorder=None
        pending=any(x.is_alive() for tile in self.all_tiles() for x in tile.receiver.retired)
        if (self.saved_recorder and self.saved_recorder.thread.is_alive()) or (pending and time.monotonic()<self.close_deadline):e.ignore();QTimer.singleShot(100,self.close);return
        self.timer.stop();e.accept()

def main():
    p=argparse.ArgumentParser();p.add_argument('--config',type=Path);p.add_argument('--local',type=Path);p.add_argument('--local-a',type=Path);p.add_argument('--local-b',type=Path);p.add_argument('--demo',action='store_true');a=p.parse_args()
    app=QApplication.instance() or QApplication(sys.argv[:1]);w=Monitor()
    if a.config:w.config_path=a.config.resolve();w.load_config()
    w.show()
    if a.demo:
        from dual_demo import DualDemo
        w.demo=DualDemo(w);app.aboutToQuit.connect(w.demo.close);QTimer.singleShot(0,w.demo.connect)
    else:
        for camera,path in [('A',a.local_a or a.local),('B',a.local_b)]:
            if path:QTimer.singleShot(0,lambda c=camera,f=path:w.camera_tiles[c]['original'].play(SourceSpec('mp4',str(f.resolve()),loop=True)))
    return app.exec()
if __name__=='__main__':raise SystemExit(main())


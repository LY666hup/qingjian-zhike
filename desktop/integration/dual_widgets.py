"""Presentation-only dual viewport. No receiver, inference or camera control."""
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QWidget,QFrame,QLabel,QPushButton,QComboBox,QVBoxLayout,QHBoxLayout
from classroom_widgets import text_label
from showcase_widgets import StatusChip

class DualViewport(QWidget):
    def __init__(self,canvases):
        super().__init__();self.setMinimumSize(200,180);self.canvases=canvases
        self.main=QWidget(self);self.main_layout=QVBoxLayout(self.main);self.main_layout.setContentsMargins(0,0,0,0)
        self.pip=QFrame(self);self.pip.setObjectName('secondaryView')
        layout=QVBoxLayout(self.pip);layout.setContentsMargins(4,4,4,4);layout.setSpacing(3)
        self.pip_title=text_label('B路 · 辅助视角','pipTitle');layout.addWidget(self.pip_title)
        self.pip_layout=layout;self.primary='A';self.set_primary('A')
    def set_primary(self,camera):
        self.primary=camera;other='B' if camera=='A' else 'A'
        for canvas in self.canvases.values():
            self.main_layout.removeWidget(canvas);self.pip_layout.removeWidget(canvas)
        self.main_layout.addWidget(self.canvases[camera]);self.pip_layout.addWidget(self.canvases[other],1)
        self.pip_title.setText(other+'路 · 辅助视角');self.place()
        for canvas in self.canvases.values():canvas.show()
    def place(self):
        self.main.setGeometry(self.rect())
        width=min(300,max(140,int(self.width()*.34)))
        height=min(self.height()-12,int(width*9/16)+32)
        self.pip.setGeometry(max(4,self.width()-width-8),max(4,self.height()-height-8),width,max(80,height));self.pip.raise_()
    def resizeEvent(self,event):self.place();super().resizeEvent(event)

class DualAnalysisStage(QFrame):
    swap_requested=Signal()
    def __init__(self,canvases,modes):
        super().__init__();self.setObjectName('analysisStage');self.setMinimumWidth(260)
        v=QVBoxLayout(self);v.setContentsMargins(12,12,12,10);v.setSpacing(8)
        h=QHBoxLayout();self.title=text_label('多人运动分析','stageTitle');h.addWidget(self.title,1)
        self.status=StatusChip('待接入');h.addWidget(self.status);v.addLayout(h)
        h=QHBoxLayout();self.primary_label=text_label('A路 · 主视角','stageFocus');h.addWidget(self.primary_label)
        self.source_combo=QComboBox();self.source_combo.setMinimumWidth(70)
        for key,name in modes.items():
            if key!='unknown':self.source_combo.addItem(name,key)
        h.addWidget(self.source_combo,1);self.swap_button=QPushButton('交换主次');self.swap_button.setToolTip('只交换显示，不改变采集、推理或计数来源')
        self.swap_button.clicked.connect(self.swap_requested);h.addWidget(self.swap_button);v.addLayout(h)
        self.viewport=DualViewport(canvases);v.addWidget(self.viewport,1)
        self.source=text_label('A / B 两路均等待视频','stageSource',True);v.addWidget(self.source)
        h=QHBoxLayout();self.focus=text_label('当前关注：全班','stageFocus');h.addWidget(self.focus,1)
        self.people=text_label('人数 --','stagePeople');h.addWidget(self.people)
        self.all_button=QPushButton('关注全班');self.all_button.setToolTip('取消当前结果来源的个人高亮');h.addWidget(self.all_button);v.addLayout(h)
        self.notice=text_label('骨架由外部同帧JSON提供 · 不在Windows推理','smallMuted',True);v.addWidget(self.notice)
    def set_primary(self,camera):
        self.viewport.set_primary(camera);self.primary_label.setText(camera+'路 · 主视角')

DUAL_STYLE='''
QFrame#secondaryView{background:#06182c;border:2px solid #2c86ad;border-radius:8px;}
QLabel#pipTitle{font-size:11px;font-weight:600;color:#9edcf1;}
QComboBox#cameraSelector{font-size:13px;font-weight:600;min-width:65px;}
QLabel#cameraHint{font-size:10px;color:#90adc4;}
QLabel#resultPolicy{font-size:10px;color:#a8b6c7;}
QLabel#tileMetrics{font-size:10px;color:#8cbdce;}
'''

"""Presentation widgets for the existing monitor; no video processing or I/O."""
from PySide6.QtCore import Qt,QPointF,QRectF
from PySide6.QtGui import QColor,QPainter,QPen,QPainterPath,QLinearGradient,QFont,QPixmap,QIcon,QPolygonF
from PySide6.QtWidgets import QFrame,QLabel,QHBoxLayout,QVBoxLayout,QWidget,QGraphicsDropShadowEffect

def draw_icon(p,name,color):
    p.setRenderHint(QPainter.RenderHint.Antialiasing);p.setPen(QPen(QColor(color),1.8,Qt.PenStyle.SolidLine,Qt.PenCapStyle.RoundCap,Qt.PenJoinStyle.RoundJoin));p.setBrush(Qt.BrushStyle.NoBrush)
    def line(a,b):p.drawLine(QPointF(*a),QPointF(*b))
    if name in ('camera','video'):
        p.drawRoundedRect(QRectF(3,6,13,12),2,2);p.drawPolygon(QPolygonF([QPointF(16,10),QPointF(21,7),QPointF(21,17),QPointF(16,14)]))
    elif name=='source':
        p.drawRoundedRect(QRectF(3,6,18,15),2,2);line((3,10),(21,10));line((4,6),(10,6));line((10,6),(12,3));line((12,3),(18,3))
    elif name=='pause':line((8,5),(8,19));line((16,5),(16,19))
    elif name=='play':p.drawPolygon(QPolygonF([QPointF(7,4),QPointF(20,12),QPointF(7,20)]))
    elif name=='disconnect':
        p.drawArc(QRectF(4,4,16,16),35*16,290*16);line((4,4),(20,20))
    elif name=='fullscreen':
        for x,y,sx,sy in [(4,4,1,1),(20,4,-1,1),(4,20,1,-1),(20,20,-1,-1)]:line((x,y),(x+5*sx,y));line((x,y),(x,y+5*sy))
    elif name=='snapshot':
        p.drawRoundedRect(QRectF(2,6,20,14),2,2);p.drawEllipse(QRectF(8,9,8,8));line((7,6),(9,3));line((9,3),(15,3));line((15,3),(17,6))
    elif name=='record':p.setBrush(QColor(color));p.drawEllipse(QRectF(6,6,12,12))
    elif name=='diagnostics':
        p.drawRoundedRect(QRectF(3,3,18,18),3,3)
        for x,y in [(7,10),(12,15),(17,8)]:line((x,6),(x,18));p.setBrush(QColor('#0b203a'));p.drawEllipse(QRectF(x-2,y-2,4,4))
    elif name=='check':line((5,12),(10,17));line((10,17),(20,6))
    elif name=='cross':line((6,6),(18,18));line((18,6),(6,18))
    elif name in ('count','chart'):
        p.setBrush(QColor(color));p.setPen(Qt.PenStyle.NoPen)
        for x,h in [(4,7),(10,13),(16,18)]:p.drawRoundedRect(QRectF(x,21-h,4,h),1,1)
    elif name=='phase':
        p.drawRoundedRect(QRectF(5,3,14,18),2,2);line((8,8),(16,8));line((8,12),(16,12));line((8,16),(13,16))
    elif name=='score':
        import math
        points=[]
        for i in range(10):
            r=10 if i%2==0 else 4.5;a=-math.pi/2+i*math.pi/5;points.append(QPointF(12+r*math.cos(a),12+r*math.sin(a)))
        p.setBrush(QColor(color));p.drawPolygon(QPolygonF(points))
    elif name=='signal':
        for r in (5,9,13):p.drawArc(QRectF(12-r,17-r,2*r,2*r),40*16,100*16)
        p.setBrush(QColor(color));p.drawEllipse(QRectF(10.5,16.5,3,3))
    elif name=='run':
        p.drawEllipse(QRectF(12,2,4,4));line((13,8),(10,13));line((10,13),(17,17));line((17,17),(20,17));line((10,13),(7,20));line((12,9),(17,11));line((17,11),(20,8));line((11,9),(6,9));line((6,9),(3,13))

def icon(name,color='#8acbff'):
    image=QPixmap(48,48);image.fill(Qt.GlobalColor.transparent);p=QPainter(image);p.scale(2,2);draw_icon(p,name,color);p.end();return QIcon(image)

class IconWidget(QWidget):
    def __init__(self,name,color,parent=None):super().__init__(parent);self.name=name;self.color=color;self.setFixedSize(48,48)
    def paintEvent(self,e):
        p=QPainter(self);p.setRenderHint(QPainter.RenderHint.Antialiasing);c=QColor(self.color);c.setAlpha(20);p.setPen(Qt.PenStyle.NoPen);p.setBrush(c);p.drawRoundedRect(QRectF(0,0,self.width(),self.height()),10,10);p.translate(self.width()*.18,self.height()*.18);scale=self.width()/48*1.25;p.scale(scale,scale);draw_icon(p,self.name,self.color)

class StatusChip(QLabel):
    def __init__(self,text='待接入'):super().__init__(text);self.setObjectName('statusChip');self.setAlignment(Qt.AlignmentFlag.AlignCenter);self.setMinimumWidth(66);self.tone='offline';self.setProperty('tone',self.tone)
    def show_status(self,text,tone):
        self.setText(text)
        if self.tone!=tone:self.tone=tone;self.setProperty('tone',tone);self.style().unpolish(self);self.style().polish(self)

class Header(QFrame):
    def __init__(self):
        super().__init__();self.setObjectName('hero');self.setMinimumHeight(84)
        layout=QHBoxLayout(self);layout.setContentsMargins(16,8,18,14);self.title=QLabel('青健智课 · 智慧体育展示平台');self.title.setObjectName('projectTitle');layout.addWidget(self.title,1)
        glow=QGraphicsDropShadowEffect(self);glow.setColor(QColor(0,102,255,135));glow.setBlurRadius(18);glow.setOffset(0,2);self.title.setGraphicsEffect(glow)
        right=QVBoxLayout();right.setSpacing(4);self.mode=QLabel('离线准备');self.mode.setObjectName('headerMode');self.mode.setAlignment(Qt.AlignmentFlag.AlignRight);self.clock=QLabel();self.clock.setObjectName('clock');self.clock.setAlignment(Qt.AlignmentFlag.AlignRight);right.addWidget(self.mode);right.addWidget(self.clock);layout.addLayout(right)
    def resizeEvent(self,e):
        size=max(25,min(42,int(self.width()*.028)));self.title.setStyleSheet(f'font-size:{size}px;font-weight:700;color:#edfaff;');super().resizeEvent(e)
    def paintEvent(self,e):
        super().paintEvent(e);p=QPainter(self);p.setRenderHint(QPainter.RenderHint.Antialiasing);y=self.height()-4
        gradient=QLinearGradient(0,0,self.width(),0);gradient.setColorAt(0,QColor('#137bff'));gradient.setColorAt(.68,QColor('#137bff'));gradient.setColorAt(1,QColor('#082846'));p.setPen(QPen(gradient,1.3));p.drawLine(QPointF(4,y-15),QPointF(22,y));p.drawLine(QPointF(22,y),QPointF(self.width()-10,y))
        p.setPen(QPen(QColor('#1688ff'),3))
        for x in range(self.width()-64,self.width()-14,16):p.drawLine(QPointF(x,y-7),QPointF(x+9,y-15))

class MetricCard(QFrame):
    def __init__(self,title,name,color,number=False):
        super().__init__();self.setObjectName('metricCard');self.number=number;self.color=color;self.setMinimumHeight(64)
        h=QHBoxLayout(self);self.row=h;h.setContentsMargins(14,9,12,9);h.setSpacing(13);self.symbol=IconWidget(name,color);h.addWidget(self.symbol)
        text=QVBoxLayout();self.text_layout=text;text.setSpacing(3);label=QLabel(title);label.setObjectName('metricLabel');self.label=label;text.addWidget(label);self.value=QLabel('--');self.value.setObjectName('metricValue');self.value.setWordWrap(True);self.value.setStyleSheet('color:'+color+';');text.addWidget(self.value);h.addLayout(text,1);self.set_compact(False)
    def set_compact(self,value):
        self.setMinimumHeight(46 if value else 64);self.symbol.setFixedSize(30 if value else 44,30 if value else 44);self.row.setContentsMargins(10,3,10,3) if value else self.row.setContentsMargins(14,9,12,9);self.text_layout.setSpacing(1 if value else 3);self.label.setStyleSheet('font-size:12px;' if value else 'font-size:13px;')
        size=(24 if value else 34) if self.number else (18 if value else 24);self.value.setStyleSheet(f'color:{self.color};font-size:{size}px;font-weight:600;')

STYLE='''
QWidget{color:#eaf4ff;font-family:"Microsoft YaHei";font-size:13px;}
QWidget#showcaseRoot{background:qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 #041125,stop:.6 #071d37,stop:1 #041020);}
QLabel{background:transparent;border:none;} QFrame#hero{background:transparent;border:none;}
QLabel#projectTitle{color:#edfaff;} QLabel#headerMode{color:#85c9ff;font-size:14px;} QLabel#clock{color:#83a5c8;font-size:12px;}
QFrame#videoTile{background:qlineargradient(x1:0,y1:0,x2:0,y2:1,stop:0 #092744,stop:1 #07182d);border:1px solid #155a9b;border-radius:12px;}
QFrame#videoTile[selected="true"]{border:2px solid #2693ff;}
QLabel#cameraIndex{background:qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 #1673ff,stop:1 #073e96);color:white;border-radius:7px;font-size:24px;font-weight:700;padding:3px 7px;}
QPushButton#cameraTitle{background:transparent;border:none;color:#f0f7ff;font-size:21px;font-weight:700;text-align:left;padding:0;}
QLabel#statusChip{background:#112b44;color:#8eaecb;border:1px solid #264968;border-radius:7px;padding:4px 7px;font-size:12px;}
QLabel#statusChip[tone="success"]{color:#50edb5;background:#073b35;border-color:#107c62;}
QLabel#statusChip[tone="demo"]{color:#7bceff;background:#0b3558;border-color:#1872ad;}
QLabel#statusChip[tone="warning"]{color:#ffd17b;background:#3c2e1b;border-color:#8b6630;}
QLabel#tileSource{color:#8da9c5;font-size:11px;padding:0 4px;}
QFrame#analysisPanel{background:qlineargradient(x1:0,y1:0,x2:0,y2:1,stop:0 #082441,stop:1 #06172c);border:1px solid #1765a8;border-radius:12px;}
QLabel#analysisTitle{font-size:24px;font-weight:700;color:#f0f7ff;}
QLabel#analysisSource{color:#f2c16e;font-size:12px;}
QFrame#metricCard{background:qlineargradient(x1:0,y1:0,x2:1,y2:0,stop:0 #0b2946,stop:1 #081c33);border:1px solid #113e66;border-radius:10px;}
QLabel#metricLabel{color:#a2c0db;font-size:13px;} QLabel#metricValue{background:transparent;}
QFrame#controlBar{background:#08203b;border:1px solid #154a78;border-radius:8px;}
QToolButton{background:transparent;border:1px solid transparent;border-radius:6px;padding:7px 8px;color:#bddfff;font-size:13px;}
QToolButton:hover{background:#123a5f;border-color:#2370a5;} QToolButton:checked{background:#124578;border-color:#248eff;}
QToolButton#recordButton[recording="true"]{color:#ff9ca9;background:#4c2035;border:1px solid #ad3f5d;}
QFrame#statusStrip{background:#061b32;border:1px solid #103b63;border-radius:6px;}
QLabel#footerText{color:#97b4ce;font-size:12px;} QLabel#footerDot{color:#4a87b2;font-size:17px;}
QPushButton{background:#123b60;border:1px solid #24638c;border-radius:5px;padding:6px 10px;color:#d9edff;}
QPushButton:hover{background:#1a5481;} QComboBox,QLineEdit,QSpinBox,QDoubleSpinBox{background:#091c32;border:1px solid #235579;border-radius:5px;padding:5px;color:#d4e9fb;min-height:22px;}
QComboBox QAbstractItemView{background:#092139;color:#ecf6ff;selection-background-color:#146baf;}
QDialog,QDockWidget{background:#071b30;} QDockWidget::title{background:#123858;padding:6px;color:#dbedff;}
QTabWidget::pane{border:1px solid #1b4d74;background:#071c31;} QTabBar::tab{background:#0b2945;color:#a9cce7;padding:7px 18px;} QTabBar::tab:selected{background:#155181;color:white;}
QPlainTextEdit{background:#041326;color:#b2d2e9;border:1px solid #1b4668;border-radius:5px;padding:5px;font-family:Consolas,"Microsoft YaHei";font-size:12px;}
QScrollArea{background:transparent;border:none;} QScrollArea>QWidget>QWidget{background:transparent;}
QScrollBar:vertical{background:#081e35;width:8px;} QScrollBar::handle:vertical{background:#23547a;min-height:25px;border-radius:3px;} QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical{height:0;}
QLabel#muted{color:#9bb6ce;font-size:12px;} QLabel#provenance{color:#edc17f;font-size:12px;} QLabel#section{font-weight:700;font-size:15px;}
'''

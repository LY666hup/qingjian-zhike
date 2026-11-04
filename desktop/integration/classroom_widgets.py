"""Teacher-facing presentation only; no inference, face matching or board control."""
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QWidget, QFrame, QLabel, QPushButton, QVBoxLayout,
                              QHBoxLayout, QComboBox, QScrollArea, QDialog,
                              QDialogButtonBox, QFormLayout, QSizePolicy)
from showcase_widgets import StatusChip, icon

COLORS = ('#58d9d2', '#80b9ff', '#edbd73', '#bb9aff', '#ee91ae')

def text_label(text='', name='', wrap=False):
    label = QLabel(text)
    label.setTextFormat(Qt.TextFormat.PlainText)
    if name: label.setObjectName(name)
    label.setWordWrap(wrap)
    label.setMinimumWidth(0)
    return label

def person_key(person, index):
    # Missing tracking IDs are valid legacy messages, but are frame-local only.
    return 'track:'+str(person['person_id']) if person.get('person_id') is not None else f'frame-person-{index}'

def person_name(person, index):
    profile = person.get('profile') or {}
    if profile.get('identity_status') == 'confirmed' and profile.get('student_id'):
        return profile.get('display_name') or profile['student_id']
    return f'临时对象 {index + 1:02d}'

class AnalysisStage(QFrame):
    def __init__(self, canvas, modes):
        super().__init__(); self.setObjectName('analysisStage'); self.setMinimumWidth(160)
        v = QVBoxLayout(self); v.setContentsMargins(12, 12, 12, 10); v.setSpacing(9)
        h = QHBoxLayout(); title = text_label('多人运动分析', 'stageTitle')
        h.addWidget(title, 1); self.status = StatusChip('待接入'); h.addWidget(self.status); v.addLayout(h)
        h = QHBoxLayout(); label = text_label('分析画面', 'smallMuted'); h.addWidget(label)
        self.source_combo = QComboBox(); self.source_combo.setMinimumWidth(80)
        for key, name in modes.items():
            if key != 'unknown': self.source_combo.addItem(name, key)
        h.addWidget(self.source_combo, 1)
        self.all_button = QPushButton('关注全班'); self.all_button.setToolTip('取消个人高亮，显示全部同帧骨架')
        h.addWidget(self.all_button); v.addLayout(h)
        self.canvas = canvas; self.canvas.setMinimumSize(100, 100); v.addWidget(canvas, 1)
        self.source = text_label('等待视频与同帧分析数据', 'stageSource', True); v.addWidget(self.source)
        h = QHBoxLayout(); self.focus = text_label('当前关注：全班', 'stageFocus'); h.addWidget(self.focus, 1)
        self.people = text_label('人数 --', 'stagePeople'); h.addWidget(self.people); v.addLayout(h)
        self.notice = text_label('骨架与成绩由外部分析接口提供', 'smallMuted', True); v.addWidget(self.notice)

class StudentCard(QFrame):
    selected = Signal(str)
    def __init__(self, key, index):
        super().__init__(); self.key = key; self.index = index; self.person = {}; self.setObjectName('studentCard')
        self.setCursor(Qt.CursorShape.PointingHandCursor); self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        v = QVBoxLayout(self); v.setContentsMargins(9, 10, 9, 9); v.setSpacing(6)
        self.name = text_label('', 'studentName', True); v.addWidget(self.name)
        self.identity = text_label('', 'identityLabel', True); v.addWidget(self.identity)
        self.action = text_label('--', 'studentAction', True); v.addWidget(self.action)
        h = QHBoxLayout(); h.setSpacing(4)
        col = QVBoxLayout(); col.setSpacing(0); col.addWidget(text_label('次数', 'smallMuted'))
        self.reps = text_label('--', 'studentNumber'); col.addWidget(self.reps); h.addLayout(col, 1)
        col = QVBoxLayout(); col.setSpacing(0); col.addWidget(text_label('评分', 'smallMuted'))
        self.score = text_label('--', 'studentScore'); col.addWidget(self.score); h.addLayout(col, 1); v.addLayout(h)
        self.counts = text_label('正确 -- / 错误 --', 'studentCounts', True); v.addWidget(self.counts)
        self.phase = text_label('阶段：--', 'smallMuted', True); v.addWidget(self.phase)
        self.feedback = text_label('等待个人分析', 'studentFeedback', True); v.addWidget(self.feedback)
        self.details = QPushButton('档案 / 详情'); self.details.setObjectName('detailButton')
        self.details.clicked.connect(self.show_details); v.addWidget(self.details)
    def update_person(self, person, index, source):
        self.person = person; self.index = index; self.source = source
        profile = person.get('profile') or {}; self.name.setText(person_name(person, index))
        confirmed = profile.get('identity_status') == 'confirmed' and profile.get('student_id')
        self.identity.setText('档案绑定 · 来源声明' if confirmed else '身份待确认')
        self.action.setText(person.get('action_type') or '等待运动项目')
        for key, label in [('rep_count', self.reps), ('quality_score', self.score)]:
            val = person.get(key); label.setText(f'{val:g}' if type(val) in (int, float) else '--')
        self.counts.setText(f"正确 {person.get('correct_count', '--')} / 错误 {person.get('incorrect_count', '--')}")
        self.phase.setText('阶段：' + (person.get('action_phase') or '--'))
        errors = person.get('error_flags', [])
        self.feedback.setText('、'.join(errors) if errors else '暂无纠错信息')
        self.setToolTip(f"{source}\n跟踪标识：{person.get('person_id', '仅本帧序号')}\n点击关注，详情查看档案与个人结果")
    def set_selected(self, selected):
        if self.property('selected') != selected:
            self.setProperty('selected', selected); self.style().unpolish(self); self.style().polish(self)
    def mousePressEvent(self, event):
        self.selected.emit(self.key); super().mousePressEvent(event)
    def show_details(self):
        self.selected.emit(self.key)
        d = QDialog(self); d.setWindowTitle('个人档案与运动结果'); d.resize(420, 480)
        layout = QVBoxLayout(d); layout.addWidget(text_label(self.source, 'stageSource', True))
        form = QFormLayout(); profile = self.person.get('profile') or {}
        rows = [('显示对象', self.name.text()), ('身份状态', self.identity.text()),
                ('学生档案号', profile.get('student_id', '--')), ('班级', profile.get('class_name', '--')),
                ('跟踪标识', self.person.get('person_id', '未提供，不作跨帧身份绑定')),
                ('运动项目', self.person.get('action_type', '--')), ('动作次数', self.reps.text()),
                ('正确次数', self.person.get('correct_count', '--')), ('错误次数', self.person.get('incorrect_count', '--')),
                ('动作阶段', self.person.get('action_phase', '--')), ('综合评分', self.score.text()),
                ('纠错信息', self.feedback.text())]
        for key, value in rows: form.addRow(key, text_label(str(value), wrap=True))
        layout.addLayout(form); note = text_label('档案绑定由外部系统声明；显示端不执行人脸识别。', 'smallMuted', True); layout.addWidget(note)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close); buttons.rejected.connect(d.reject); layout.addWidget(buttons); d.exec()

class StudentRoster(QFrame):
    selected = Signal(str)
    def __init__(self):
        super().__init__(); self.setObjectName('rosterPanel'); self.setMinimumWidth(108)
        v = QVBoxLayout(self); v.setContentsMargins(8, 12, 8, 8); v.setSpacing(8)
        v.addWidget(text_label('学生档案', 'rosterTitle')); self.count = text_label('个人分析 · --', 'smallMuted'); v.addWidget(self.count)
        self.source = text_label('等待数据', 'rosterSource', True); v.addWidget(self.source)
        self.scroll = QScrollArea(); self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        content = QWidget(); content.setMinimumWidth(0); self.items = QVBoxLayout(content)
        self.items.setContentsMargins(0, 0, 0, 0); self.items.setSpacing(8)
        self.empty = text_label('等待多人分析\n\n每人一份档案\n每人一份结果', 'emptyRoster', True)
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter); self.items.addWidget(self.empty); self.items.addStretch()
        self.scroll.setWidget(content); v.addWidget(self.scroll, 1); self.cards = {}; self.current = None; self.scope = None
    def clear(self, reason='等待分析数据'):
        for key, card in list(self.cards.items()): self.items.removeWidget(card); card.deleteLater()
        self.cards.clear(); self.current = None; self.scope = None; self.empty.show(); self.count.setText('个人分析 · --'); self.source.setText(reason)
    def update_people(self, message):
        scope = tuple(message.get(k) for k in ('stream_id', 'session_id', 'camera_id'))
        if any(p.get('person_id') is None for p in message.get('persons', [])):
            scope += (message.get('frame_id'),)
        if self.scope != scope: self.clear(); self.scope = scope
        persons = message.get('persons', []); keys = [person_key(p, i) for i, p in enumerate(persons)]
        for key in list(self.cards):
            if key not in keys:
                card = self.cards.pop(key); self.items.removeWidget(card); card.deleteLater()
        if self.current not in keys: self.current = None
        source = {'simulation': '模拟数据 · 界面测试', 'replay': '回放分析 · 非实时', 'external': '外部分析 · 来源声明'}[message['source']]
        self.source.setText(source); self.count.setText(f'个人分析 · {len(persons)}人'); self.empty.setVisible(not persons)
        for i, person in enumerate(persons):
            key = keys[i]
            if key not in self.cards:
                card = StudentCard(key, i); card.selected.connect(self.select_person); self.cards[key] = card
            card = self.cards[key]
            if self.items.indexOf(card) != i:
                self.items.removeWidget(card); self.items.insertWidget(i, card)
            card.update_person(person, i, source); card.set_selected(key == self.current)
    def select_person(self, key):
        self.current = key or None
        for k, card in self.cards.items(): card.set_selected(k == self.current)
        self.selected.emit(key)

CLASSROOM_STYLE = '''
QFrame#analysisStage,QFrame#rosterPanel,QFrame#comparisonPanel{background:#071d33;border:1px solid #1b4d73;border-radius:12px;}
QLabel#stageTitle{font-size:24px;font-weight:700;color:#f0f8ff;}
QLabel#sectionTitle{font-size:20px;font-weight:700;color:#e8f4ff;}
QLabel#rosterTitle{font-size:17px;font-weight:700;color:#e8f4ff;}
QLabel#smallMuted{font-size:11px;color:#8da9c2;}
QLabel#stageSource{font-size:12px;color:#8cbfdf;}
QLabel#stageFocus{font-size:13px;color:#b9d4e6;}
QLabel#stagePeople{font-size:14px;color:#67d9ce;font-weight:600;}
QLabel#rosterSource{font-size:10px;color:#edc77e;}
QLabel#emptyRoster{font-size:12px;color:#6f91af;line-height:1.6;}
QFrame#studentCard{background:#0b2a43;border:1px solid #19445f;border-radius:9px;}
QFrame#studentCard[selected="true"]{border:1px solid #59d8cd;background:#0c3748;}
QLabel#studentName{font-size:14px;font-weight:700;color:#ddecfa;}
QLabel#identityLabel{font-size:10px;color:#91afc6;}
QLabel#studentAction{font-size:12px;color:#8ac8ed;}
QLabel#studentNumber{font-size:24px;font-weight:700;color:#59ded1;}
QLabel#studentScore{font-size:24px;font-weight:700;color:#f0ca7d;}
QLabel#studentCounts{font-size:10px;color:#a0c8d6;}
QLabel#studentFeedback{font-size:11px;color:#e6c588;}
QPushButton#detailButton{font-size:10px;padding:4px 2px;background:#11374e;border:1px solid #24536a;}
QFrame#comparisonPanel QFrame#videoTile{border-radius:8px;}
QFrame#comparisonPanel QLabel#cameraIndex{font-size:14px;padding:3px 4px;}
QFrame#comparisonPanel QPushButton#cameraTitle{font-size:15px;}
QFrame#comparisonPanel QLabel#statusChip{font-size:10px;padding:3px 4px;}
QFrame#comparisonPanel QLabel#tileSource{font-size:10px;}
'''

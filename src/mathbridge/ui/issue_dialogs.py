"""Focused repairs for a located dubbing problem."""
from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QPlainTextEdit,
    QPushButton, QVBoxLayout, QComboBox, QListWidget, QListWidgetItem,
)
from PySide6.QtCore import Qt


class DubbingReviewDialog(QDialog):
    """One listening queue for residual problems and optional spot checks."""
    def __init__(self,project,report,listen,edit,parent=None):
        super().__init__(parent)
        self.setWindowTitle("配音抽查 · 自动处理后只看剩余问题")
        self.resize(880,650)
        self.segments = {s["id"]:s for s in project["segments"]}
        self.report,self.listen,self.edit = report,listen,edit
        self.problems = {i["segment_id"]:i for i in report.get("issues",[])}
        layout=QVBoxLayout(self)
        summary=QLabel(f"已测量 {report.get('measured_count',0)} 句，剩余 {len(self.problems)} 句需处理。自动处理结果可抽查，无需逐句确认。")
        summary.setWordWrap(True);layout.addWidget(summary)
        self.filter=QComboBox()
        for text,value in (("仅剩余问题","problems"),("已自动调整，供抽查","adjusted"),("全部已生成配音","all")):
            self.filter.addItem(text,value)
        layout.addWidget(self.filter)
        self.listing=QListWidget();layout.addWidget(self.listing,1)
        self.context=QPlainTextEdit();self.context.setReadOnly(True);self.context.setMaximumHeight(175)
        layout.addWidget(self.context)
        row=QHBoxLayout()
        self.controls=[]
        for title,kind in (("听原声","original"),("听自然译声","natural"),("听调整后译声","matched")):
            control=QPushButton(title);control.clicked.connect(lambda _=False,k=kind:self._listen(k))
            row.addWidget(control);self.controls.append(control)
        self.modify=QPushButton("修改这句读法");self.modify.clicked.connect(self._edit)
        row.addWidget(self.modify);layout.addLayout(row)
        nav=QHBoxLayout()
        for title,step in (("上一句",-1),("下一句",1)):
            control=QPushButton(title);control.clicked.connect(lambda _=False,d=step:self.listing.setCurrentRow(max(0,min(self.listing.count()-1,self.listing.currentRow()+d))))
            nav.addWidget(control)
        close=QPushButton("关闭");close.clicked.connect(self.accept);nav.addWidget(close);layout.addLayout(nav)
        self.filter.currentIndexChanged.connect(self._load)
        self.listing.currentItemChanged.connect(self._selected)
        self.filter.setCurrentIndex(0 if self.problems else 1)
        self._load()

    def _load(self,*_):
        self.listing.clear()
        mode=self.filter.currentData()
        for plan in self.report.get("plans",[]):
            identifier=plan["segment_id"]
            adjusted=plan.get("pause_saved_ms",0)>20 or abs(plan.get("shift_ms",0))>20 or plan["speed"]>1.005
            if (mode=="problems" and identifier not in self.problems) or (mode=="adjusted" and not adjusted):continue
            segment=self.segments[identifier]
            title=f"第 {plan['index']} 句 · {segment['subtitle_plain'][:70]}"
            item=QListWidgetItem(title);item.setData(Qt.ItemDataRole.UserRole,plan);self.listing.addItem(item)
        self.listing.setCurrentRow(0)
        self._selected()

    def _selected(self,*_):
        item=self.listing.currentItem()
        for control in self.controls+[self.modify]:control.setEnabled(bool(item))
        if not item:
            self.context.setPlainText("这个队列没有需要检查的句子。");return
        plan=item.data(Qt.ItemDataRole.UserRole);segment=self.segments[plan["segment_id"]]
        issue=self.problems.get(segment["id"])
        text=(f"原文：{segment['source_text']}\n字幕：{segment['subtitle_plain']}\n配音读法：{segment['tts_text']}\n"
              f"自然译声 {plan.get('original_speech_ms',plan['speech_ms'])/1000:.2f} 秒 → 停顿处理后 {plan['speech_ms']/1000:.2f} 秒；"
              f"局部语速 {plan['speed']:.2f} 倍，起点移动 {plan.get('shift_ms',0)/1000:+.2f} 秒。")
        if issue:text+='\n'+issue['message']
        self.context.setPlainText(text)

    def _listen(self,kind):
        item=self.listing.currentItem()
        if item:self.listen(item.data(Qt.ItemDataRole.UserRole)["segment_id"],kind)

    def _edit(self):
        item=self.listing.currentItem()
        if item:
            identifier=item.data(Qt.ItemDataRole.UserRole)["segment_id"]
            issue=self.problems.get(identifier,dict(segment_id=identifier,message="可选修订；字幕保持原样。"))
            self.accept();self.edit(issue)


class SpokenTextDialog(QDialog):
    def __init__(self, segment, issue, *, previous=None, following=None, replay=None, parent=None):
        super().__init__(parent)
        self.action = "save"
        self.setWindowTitle("处理配音时长 · " + issue.get("location", "当前句"))
        self.resize(680, 480)
        layout = QVBoxLayout(self)
        summary = QLabel(issue.get("message", "缩短配音读法，或将被切碎的相邻语句合并。"))
        summary.setWordWrap(True)
        summary.setObjectName("notice")
        layout.addWidget(summary)
        context = QLabel("原文：" + segment["source_text"] + "\n字幕：" + segment["subtitle_plain"])
        context.setWordWrap(True)
        layout.addWidget(context)
        layout.addWidget(QLabel("配音实际朗读的文字（此处修改不会改动字幕）"))
        self.spoken_text = QPlainTextEdit(segment["tts_text"])
        layout.addWidget(self.spoken_text, 1)
        hint = QLabel("如果原文的一句话被拆成了很短的两段，可以合并相邻句。合并会保留两句文字与公式来源，并使用两句的完整时间范围。修改后重新生成配音，未改变的句子会复用缓存。")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        row = QHBoxLayout()
        if replay:
            listen = QPushButton("回听这句原音")
            listen.clicked.connect(replay)
            row.addWidget(listen)
        row.addStretch()
        self.merge_previous = QPushButton("与前一句合并")
        self.merge_next = QPushButton("与后一句合并")
        for control, neighbor, action in ((self.merge_previous, previous, "merge_previous"), (self.merge_next, following, "merge_next")):
            control.setEnabled(bool(neighbor and neighbor["speaker_id"] == segment["speaker_id"]))
            control.setToolTip((neighbor or {}).get("source_text", "没有同一说话人的相邻句"))
            control.clicked.connect(lambda _checked=False, choice=action: self._choose(choice))
            row.addWidget(control)
        layout.addLayout(row)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("保存配音读法")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _choose(self, action):
        self.action = action
        self.accept()

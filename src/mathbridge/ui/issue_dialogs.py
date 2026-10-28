"""Focused repairs for a located dubbing problem."""
from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QPlainTextEdit,
    QPushButton, QVBoxLayout,
)


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

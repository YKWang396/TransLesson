from __future__ import annotations

from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFormLayout,
    QFrame, QLabel, QHBoxLayout, QLineEdit, QMessageBox, QPlainTextEdit,
    QPushButton, QRubberBand, QScrollArea, QVBoxLayout, QWidget,
)

REVIEW_LABELS = {"needs_review": "待校对", "confirmed": "已确认", "preserve_source": "保留原始证据"}


def review_combo(value="needs_review"):
    combo = QComboBox()
    for key, label in REVIEW_LABELS.items():
        combo.addItem(label, key)
    combo.setCurrentIndex(max(0, combo.findData(value)))
    return combo


class FormulaDialog(QDialog):
    def __init__(self, formula, parent=None):
        super().__init__(parent)
        self.setWindowTitle("公式校对 · " + formula["id"])
        self.setMinimumWidth(620)
        layout = QVBoxLayout(self)
        description = QLabel("将原音或原画面与候选表达逐项核对。可排版不代表数学含义正确。")
        description.setWordWrap(True)
        description.setObjectName("notice")
        layout.addWidget(description)
        form = QFormLayout()
        layout.addLayout(form)
        self.fields = {}
        for key, label in [("raw", "原始表达"), ("latex", "LaTeX 候选"), ("spoken_source", "源语言读法"), ("spoken_target", "目标语言读法")]:
            field = QPlainTextEdit(formula.get(key) or "")
            field.setMaximumHeight(85)
            if key == "latex":
                field.setStyleSheet("font-family: Consolas; font-size: 12pt;")
            form.addRow(label, field)
            self.fields[key] = field
        self.review = review_combo(formula.get("review"))
        form.addRow("校对状态", self.review)
        evidence = QLabel("来源：" + " · ".join(
            f"{x.get('kind', '')} {x.get('start_ms', 0)/1000:.2f}s"
            for x in formula.get("evidence", [])
        ))
        evidence.setWordWrap(True)
        layout.addWidget(evidence)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("保存校对")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def changes(self):
        values = {key: field.toPlainText().strip() for key, field in self.fields.items()}
        values["latex"] = values["latex"] or None
        values["review"] = self.review.currentData()
        return values


class TermDialog(QDialog):
    def __init__(self, term, parent=None):
        super().__init__(parent)
        self.setWindowTitle("课程术语 · " + term["id"])
        self.setMinimumWidth(510)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        layout.addLayout(form)
        self.fields = {}
        for key, label in [("source", "原文术语"), ("target", "课程译法"), ("context", "概念上下文")]:
            field = QLineEdit(term.get(key, ""))
            form.addRow(label, field)
            self.fields[key] = field
        self.locked = QCheckBox("锁定本课程译法（后续翻译继续使用）")
        self.locked.setChecked(term.get("locked", False))
        form.addRow(self.locked)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("保存术语")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def changes(self):
        return {**{k: v.text().strip() for k, v in self.fields.items()}, "locked": self.locked.isChecked()}


class SpeakerDialog(QDialog):
    def __init__(self, speaker, source_language="en", duration_ms=0,
                 default_reference=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("说话人与声音 · " + speaker.get("label", speaker["id"]))
        self.setMinimumWidth(590)
        self.resize(680, 660)
        layout = QVBoxLayout(self)
        content = QWidget()
        form = QFormLayout(content)
        form.setFormAlignment(Qt.AlignmentFlag.AlignTop)
        self.speaker_label = QLineEdit(speaker.get("label", ""))
        form.addRow("显示名称", self.speaker_label)
        self.provider = QComboBox()
        self.provider.addItem("Windows 系统声音", "windows")
        self.provider.addItem("参考原声音色 · 本地 GPT-SoVITS", "gpt_sovits")
        profile = speaker.get("voice_profile") or {}
        provider = profile.get("provider", "gpt_sovits" if speaker.get("voice_mode") == "reference" else "windows")
        self.provider.setCurrentIndex(max(0, self.provider.findData(provider)))
        form.addRow("配音方式", self.provider)
        self.windows_group = QWidget()
        windows_form = QFormLayout(self.windows_group)
        windows_form.setContentsMargins(0, 0, 0, 0)
        self.voice_name = QLineEdit(profile.get("voice_name", ""))
        self.voice_name.setPlaceholderText("留空使用处理设置中的声音，或自动选择目标语言声音")
        windows_form.addRow("声音名称 / ID", self.voice_name)
        form.addRow(self.windows_group)
        self.reference_group = QWidget()
        reference_form = QFormLayout(self.reference_group)
        reference_form.setContentsMargins(0, 0, 0, 0)
        reference_form.setFormAlignment(Qt.AlignmentFlag.AlignTop)
        hint = QLabel("从原视频选择 3–10 秒清晰、仅该人讲话的片段，并准确填写这段原话。参考声音会交给已启动的本机 GPT-SoVITS 服务，需另行准备其模型。")
        hint.setWordWrap(True)
        hint.setMaximumHeight(100)
        hint.setObjectName("notice")
        reference_form.addRow(hint)
        evidence = speaker.get("reference_evidence") or {}
        default_start, default_end = default_reference or (0, min(6000, duration_ms or 6000))
        self.reference_start = QDoubleSpinBox()
        self.reference_end = QDoubleSpinBox()
        for spin in (self.reference_start, self.reference_end):
            spin.setDecimals(3)
            spin.setRange(0, duration_ms / 1000 if duration_ms else 999999)
            spin.setSingleStep(0.1)
            spin.setSuffix(" s")
        self.reference_start.setValue(profile.get("reference_start_ms", evidence.get("start_ms", default_start)) / 1000)
        self.reference_end.setValue(profile.get("reference_end_ms", evidence.get("end_ms", default_end)) / 1000)
        reference_form.addRow("参考原声起点", self.reference_start)
        reference_form.addRow("参考原声终点", self.reference_end)
        self.reference_language = QComboBox()
        self.reference_language.addItem("英语", "en")
        self.reference_language.addItem("中文", "zh")
        reference_language = str(profile.get("reference_language", source_language)).split("-")[0]
        self.reference_language.setCurrentIndex(max(0, self.reference_language.findData(reference_language)))
        reference_form.addRow("参考原声语言", self.reference_language)
        self.clone_speed = QDoubleSpinBox()
        self.clone_speed.setRange(0.5, 2.0)
        self.clone_speed.setDecimals(2)
        self.clone_speed.setSingleStep(0.05)
        self.clone_speed.setValue(float(profile.get("speed", 1.0)))
        self.clone_speed.setToolTip("1 为自然合成基准；配音页会自动匹配时间，通常无需调整此项。")
        speed_advanced=QCheckBox("高级：整体语速偏好（通常无需修改）")
        speed_row=QWidget()
        speed_layout=QHBoxLayout(speed_row);speed_layout.setContentsMargins(0,0,0,0)
        speed_layout.addWidget(QLabel("自然语速基准（1 = 默认）"));speed_layout.addWidget(self.clone_speed)
        reference_form.addRow(speed_advanced)
        reference_form.addRow(speed_row)
        speed_advanced.toggled.connect(speed_row.setVisible)
        speed_advanced.setChecked(self.clone_speed.value()!=1)
        speed_row.setVisible(speed_advanced.isChecked())
        self.reference_text = QPlainTextEdit(profile.get("reference_text", ""))
        self.reference_text.setPlaceholderText("填写参考片段中实际说出的原话，不是译文；不要包含公式 ID 占位符。")
        self.reference_text.setFixedHeight(100)
        reference_form.addRow("参考原话", self.reference_text)
        self.authorized = QCheckBox("已取得该声音的使用授权")
        self.authorized.setChecked(bool(speaker.get("voice_authorized", False)))
        reference_form.addRow(self.authorized)
        form.addRow(self.reference_group)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(content)
        layout.addWidget(scroll)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("保存说话人")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.provider.currentIndexChanged.connect(self._provider_changed)
        self._provider_changed()

    def _provider_changed(self, *_):
        cloning = self.provider.currentData() == "gpt_sovits"
        self.windows_group.setVisible(not cloning)
        self.reference_group.setVisible(cloning)

    def accept(self):
        if not self.speaker_label.text().strip():
            QMessageBox.warning(self, "说话人名称", "请填写说话人的显示名称。")
            return
        if self.provider.currentData() == "gpt_sovits":
            duration_ms = round(self.reference_end.value() * 1000) - round(self.reference_start.value() * 1000)
            if not 3000 <= duration_ms <= 10000:
                QMessageBox.warning(self, "参考原声", "参考片段长度应为 3–10 秒，且终点晚于起点。")
                return
            if not self.reference_text.toPlainText().strip():
                QMessageBox.warning(self, "参考原话", "请填写参考片段中实际说出的原话。")
                return
            if not self.authorized.isChecked():
                QMessageBox.warning(self, "声音授权", "请在取得该声音使用授权后勾选授权项。")
                return
        super().accept()

    def changes(self):
        cloning = self.provider.currentData() == "gpt_sovits"
        profile = {"provider": self.provider.currentData()}
        if cloning:
            profile.update(
                reference_start_ms=round(self.reference_start.value() * 1000),
                reference_end_ms=round(self.reference_end.value() * 1000),
                reference_text=self.reference_text.toPlainText().strip(),
                reference_language=self.reference_language.currentData(),
                speed=self.clone_speed.value(),
            )
        else:
            profile["voice_name"] = self.voice_name.text().strip()
        return {"label": self.speaker_label.text().strip(),
                "voice_mode": "reference" if cloning else "standard",
                "voice_authorized": self.authorized.isChecked() if cloning else False,
                "voice_profile": profile}


class CropLabel(QLabel):
    def __init__(self, path, parent=None):
        super().__init__(parent)
        pixmap = QPixmap(str(path)).scaled(900, 490, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
        self.setPixmap(pixmap)
        self.setFixedSize(pixmap.size())
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.rubber = QRubberBand(QRubberBand.Shape.Rectangle, self)
        self.origin = QPoint()
        self.bbox = [0.0, 0.0, 1.0, 1.0]

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.origin = event.position().toPoint()
            self.rubber.setGeometry(QRect(self.origin, self.origin))
            self.rubber.show()

    def mouseMoveEvent(self, event):
        if event.buttons() & Qt.MouseButton.LeftButton:
            self.rubber.setGeometry(QRect(self.origin, event.position().toPoint()).normalized().intersected(self.rect()))

    def mouseReleaseEvent(self, event):
        rect = self.rubber.geometry().intersected(self.rect())
        if rect.width() > 5 and rect.height() > 5:
            self.bbox = [rect.left() / self.width(), rect.top() / self.height(), min(1.0, (rect.right() + 1) / self.width()), min(1.0, (rect.bottom() + 1) / self.height())]
        else:
            self.reset()

    def reset(self):
        self.bbox = [0.0, 0.0, 1.0, 1.0]
        self.rubber.hide()


class FrameEvidenceDialog(QDialog):
    def __init__(self, path, parent=None):
        super().__init__(parent)
        self.setWindowTitle("框选公式 · 保留原画面证据")
        layout = QVBoxLayout(self)
        intro = QLabel("在原图上拖动框选公式。未框选时保留整帧；此操作不会运行自动 OCR。")
        intro.setWordWrap(True)
        layout.addWidget(intro)
        self.crop = CropLabel(path)
        layout.addWidget(self.crop, 0, Qt.AlignmentFlag.AlignCenter)
        form = QFormLayout()
        self.latex = QLineEdit()
        self.latex.setPlaceholderText("可留空并保留原图待校对")
        form.addRow("手动 LaTeX 候选", self.latex)
        layout.addLayout(form)
        row = QHBoxLayout()
        reset = QPushButton("改用整帧")
        reset.clicked.connect(self.crop.reset)
        row.addWidget(reset)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("添加为当前片段的公式证据")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        row.addWidget(buttons)
        layout.addLayout(row)

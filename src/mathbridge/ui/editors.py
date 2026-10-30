from __future__ import annotations

from PySide6.QtCore import QPoint, QRect, Qt, QTimer
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
                 default_reference=None, parent=None, *, reference_lookup=None, replay_reference=None, auxiliary_lookup=None):
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
        self._reference_suggestion = default_reference if isinstance(default_reference,dict) else None
        self._reference_lookup = reference_lookup
        self._reference_replay = replay_reference
        self._auxiliary_lookup = auxiliary_lookup
        self.auxiliary_references = [dict(r) for r in profile.get("auxiliary_references",[])]
        self._setting_reference = False
        saved_reference = bool(profile.get("reference_text","").strip()) and all(k in profile for k in ("reference_start_ms","reference_end_ms"))
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
        hint = QLabel("根据已识别的原文，自动带入同一说话人的 3–10 秒参考时间、原话和语言。请回听确认；生成配音时会自动提取音频，无需另做 WAV。")
        hint.setWordWrap(True)
        hint.setMaximumHeight(100)
        hint.setObjectName("notice")
        reference_form.addRow(hint)
        evidence = speaker.get("reference_evidence") or {}
        proposal = self._reference_suggestion if not saved_reference else None
        default_start, default_end = ((proposal["start_ms"],proposal["end_ms"]) if proposal else
            (default_reference if isinstance(default_reference,(tuple,list)) else (0,min(6000,duration_ms or 6000))))
        self.reference_start = QDoubleSpinBox()
        self.reference_end = QDoubleSpinBox()
        for spin in (self.reference_start, self.reference_end):
            spin.setDecimals(3)
            spin.setRange(0, duration_ms / 1000 if duration_ms else 999999)
            spin.setSingleStep(0.1)
            spin.setSuffix(" s")
        self.reference_start.setValue((default_start if proposal else profile.get("reference_start_ms", evidence.get("start_ms", default_start))) / 1000)
        self.reference_end.setValue((default_end if proposal else profile.get("reference_end_ms", evidence.get("end_ms", default_end))) / 1000)
        reference_form.addRow("参考原声起点", self.reference_start)
        reference_form.addRow("参考原声终点", self.reference_end)
        self.reference_language = QComboBox()
        self.reference_language.addItem("英语", "en")
        self.reference_language.addItem("中文", "zh")
        reference_language = str(proposal["reference_language"] if proposal else profile.get("reference_language", source_language)).split("-")[0]
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
        self.reference_text = QPlainTextEdit(proposal["reference_text"] if proposal else profile.get("reference_text", ""))
        self.reference_text.setPlaceholderText("填写参考片段中实际说出的原话，不是译文；不要包含公式 ID 占位符。")
        self.reference_text.setFixedHeight(100)
        reference_form.addRow("参考原话", self.reference_text)
        reference_buttons=QWidget()
        reference_row=QHBoxLayout(reference_buttons);reference_row.setContentsMargins(0,0,0,0)
        self.auto_reference_button=QPushButton("自动选取并填写")
        self.auto_reference_button.setEnabled(bool(self._reference_suggestion))
        self.auto_reference_button.clicked.connect(self._fill_reference)
        reference_row.addWidget(self.auto_reference_button)
        self.replay_reference_button=QPushButton("回听这段原声")
        self.replay_reference_button.setEnabled(replay_reference is not None)
        self.replay_reference_button.clicked.connect(self._replay_reference)
        reference_row.addWidget(self.replay_reference_button)
        reference_form.addRow(reference_buttons)
        self.reference_notice=QLabel("已保留保存过的参考片段和原话。" if saved_reference else
            "已根据课程原文自动填写，可直接回听检查。" if proposal else
            "未找到可可靠匹配的 3–10 秒片段。请先转写原文，或自行选择片段并填写原话。")
        self.reference_notice.setWordWrap(True)
        reference_form.addRow(self.reference_notice)
        self.use_auxiliary = QCheckBox("自动补充同一说话人的多段原声（最多 3 段）")
        self.use_auxiliary.setChecked(bool(self.auxiliary_references) or bool(auxiliary_lookup and not saved_reference))
        self.use_auxiliary.setEnabled(bool(auxiliary_lookup or self.auxiliary_references))
        reference_form.addRow(self.use_auxiliary)
        auxiliary_row = QWidget()
        auxiliary_layout = QHBoxLayout(auxiliary_row);auxiliary_layout.setContentsMargins(0,0,0,0)
        self.auxiliary_list = QComboBox()
        self.auxiliary_list.setMinimumContentsLength(20)
        self.auxiliary_list.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        auxiliary_layout.addWidget(self.auxiliary_list,1)
        self.replay_auxiliary_button = QPushButton("回听补充原声")
        self.replay_auxiliary_button.clicked.connect(self._replay_auxiliary)
        auxiliary_layout.addWidget(self.replay_auxiliary_button)
        reference_form.addRow(auxiliary_row)
        auxiliary_hint=QLabel("主参考保留原话；补充片段用于合成时融合音色，不会训练或修改模型。多段效果请通过试听比较。")
        auxiliary_hint.setWordWrap(True)
        reference_form.addRow(auxiliary_hint)
        self.use_auxiliary.toggled.connect(self._update_auxiliaries)
        self._update_auxiliaries(refresh=not bool(self.auxiliary_references))
        self._reference_timer=QTimer(self)
        self._reference_timer.setSingleShot(True)
        self._reference_timer.setInterval(200)
        self._reference_timer.timeout.connect(self._sync_reference_text)
        self.reference_start.valueChanged.connect(self._reference_time_changed)
        self.reference_end.valueChanged.connect(self._reference_time_changed)
        self.reference_text.textChanged.connect(self._reference_text_edited)
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

    def _fill_reference(self):
        proposal=self._reference_suggestion
        if not proposal:return
        self._reference_timer.stop()
        self._setting_reference=True
        self.reference_start.setValue(proposal["start_ms"]/1000)
        self.reference_end.setValue(proposal["end_ms"]/1000)
        self.reference_text.setPlainText(proposal["reference_text"])
        self.reference_language.setCurrentIndex(max(0,self.reference_language.findData(proposal["reference_language"])))
        self._setting_reference=False
        self.reference_notice.setText("已自动匹配原声时间和原话，请回听确认没有其他人抢话或转写错误。")
        self._update_auxiliaries()

    def _update_auxiliaries(self,*_,refresh=True):
        if self.use_auxiliary.isChecked() and refresh and self._auxiliary_lookup:
            self.auxiliary_references=self._auxiliary_lookup(round(self.reference_start.value()*1000),round(self.reference_end.value()*1000))
        self.auxiliary_list.clear()
        for r in self.auxiliary_references if self.use_auxiliary.isChecked() else []:
            self.auxiliary_list.addItem(f"{r['start_ms']/1000:.2f}–{r['end_ms']/1000:.2f} 秒 · {r['reference_text']}",r)
        if not self.auxiliary_list.count():
            self.auxiliary_list.addItem("未启用补充参考" if not self.use_auxiliary.isChecked() else "没有其他可靠的同人片段")
        self.replay_auxiliary_button.setEnabled(bool(self.auxiliary_list.currentData()) and self._reference_replay is not None)
        self._auxiliary_primary_range=(round(self.reference_start.value()*1000),round(self.reference_end.value()*1000))

    def _replay_auxiliary(self):
        r=self.auxiliary_list.currentData()
        if r and self._reference_replay:
            self._reference_replay(r["start_ms"],r["end_ms"])

    def _reference_time_changed(self,*_):
        if not self._setting_reference and self._reference_lookup is not None:
            self._reference_timer.start()

    def _reference_text_edited(self):
        if not self._setting_reference:
            self._reference_timer.stop()  # Explicitly corrected text takes priority.

    def _sync_reference_text(self):
        if self._reference_lookup is None:return
        start,end=round(self.reference_start.value()*1000),round(self.reference_end.value()*1000)
        proposal=self._reference_lookup(start,end)
        self._setting_reference=True
        if proposal:
            self.reference_text.setPlainText(proposal["reference_text"])
            self.reference_language.setCurrentIndex(max(0,self.reference_language.findData(proposal["reference_language"])))
            self.reference_notice.setText("已按新时间重新匹配原话。")
        else:
            self.reference_text.clear()
            self.reference_notice.setText("这个时间段无法可靠对应完整原话。请自动选取，或回听后手动填写；已清除上个片段的文字。")
        self._setting_reference=False
        self._update_auxiliaries()

    def _replay_reference(self):
        if self._reference_replay is not None:
            start,end=round(self.reference_start.value()*1000),round(self.reference_end.value()*1000)
            if 0<=start<end:self._reference_replay(start,end)

    def accept(self):
        if self._reference_timer.isActive():
            self._reference_timer.stop()
            self._sync_reference_text()
        if self._auxiliary_primary_range != (round(self.reference_start.value()*1000),round(self.reference_end.value()*1000)):
            self._update_auxiliaries()
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
            if self.use_auxiliary.isChecked() and self.auxiliary_references:
                profile["auxiliary_references"]=[dict(r) for r in self.auxiliary_references]
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

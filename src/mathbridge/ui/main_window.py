from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, QUrl, QSettings
from PySide6.QtGui import QAction, QColor, QDesktopServices, QKeySequence, QPixmap
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QCheckBox, QComboBox, QDialog, QDoubleSpinBox,
    QFileDialog, QFormLayout, QFrame, QGridLayout, QGroupBox, QHBoxLayout,
    QHeaderView, QInputDialog, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMainWindow,
    QMessageBox, QPlainTextEdit, QProgressBar, QPushButton, QScrollArea, QSlider,
    QSplitter, QTabWidget, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from .editors import FormulaDialog, FrameEvidenceDialog, REVIEW_LABELS, SpeakerDialog, TermDialog, review_combo
from .settings import SettingsDialog, load_settings, save_settings
from .theme import STYLESHEET


STAGES = {"create": "建立项目", "transcribe": "本地转写", "diarize": "区分说话人", "analyze": "数学解析", "translate": "课程翻译", "dub": "生成配音", "speaker_reference": "提取参考原声", "notes": "整理讲义", "export": "导出", "frame": "提取当前帧", "preview": "生成预览", "pipeline": "自动处理草稿", "import_subtitles": "导入字幕"}


def timecode(ms):
    seconds, millis = divmod(max(0, int(ms)), 1000)
    hours, seconds = divmod(seconds, 3600)
    minutes, seconds = divmod(seconds, 60)
    return f"{hours:02}:{minutes:02}:{seconds:02}.{millis:03}"


def label(text, name=None):
    widget = QLabel(text)
    widget.setWordWrap(True)
    if name:
        widget.setObjectName(name)
    return widget


def button(text, callback, primary=False):
    widget = QPushButton(text)
    if primary:
        widget.setObjectName("primary")
    widget.clicked.connect(callback)
    return widget


class MainWindow(QMainWindow):
    """The view never mutates project dictionaries; all edits go through Coordinator."""

    def __init__(self, coordinator):
        super().__init__()
        self.coordinator = coordinator
        self.coordinator.settings.update(load_settings())
        self._credential_error = ""
        if self.coordinator.settings.get("remember_api_key"):
            try:
                from mathbridge.storage.credentials import load_secret
                self.coordinator.settings["api_key"] = load_secret("text-api")
            except OSError:
                self._credential_error = "无法读取 Windows 凭据库，请在处理设置中重新输入 API Key。"
        self._project = None
        self._segment_id = None
        self._note_id = None
        self._loading = False
        self._committing = False
        self._segment_dirty = False
        self._note_dirty = False
        self._source_loaded = ""
        self._latest_audio = None
        self._frame_path = None
        self._frame_time = 0
        self._frame_requested_ms = 0
        self._last_outputs = []
        self._closing_requested = False
        self._stage_buttons = []
        self._segment_timer = QTimer(self)
        self._segment_timer.setSingleShot(True)
        self._segment_timer.setInterval(650)
        self._segment_timer.timeout.connect(self._commit_segment)
        self._note_timer = QTimer(self)
        self._note_timer.setSingleShot(True)
        self._note_timer.setInterval(650)
        self._note_timer.timeout.connect(self._commit_note)
        self.setWindowTitle("课译 MathBridge · 数学课程翻译工作台")
        self.resize(1480, 940)
        self.setMinimumSize(1120, 760)
        self.setStyleSheet(STYLESHEET)
        self._build()
        self._connect()
        self._build_menu()
        self._refresh_project(None)
        if self._credential_error:
            self._log(self._credential_error)

    def _build(self):
        root = QWidget()
        outer = QVBoxLayout(root)
        outer.setContentsMargins(22, 18, 22, 14)
        outer.setSpacing(12)
        self.setCentralWidget(root)
        header = QHBoxLayout()
        title = QVBoxLayout()
        title.setSpacing(1)
        title.addWidget(label("课译 MathBridge", "brand"))
        title.addWidget(label("数学课程 · 翻译与证据校对", "eyebrow"))
        header.addLayout(title)
        header.addStretch()
        self.project_title = label("尚未打开项目")
        self.project_title.setMinimumWidth(200)
        self.project_title.setMaximumWidth(400)
        header.addWidget(self.project_title)
        self.saved = label("本地工作空间", "saveStatus")
        header.addWidget(self.saved)
        header.addWidget(button("打开项目", self._open_project))
        header.addWidget(button("处理设置", self._settings))
        outer.addLayout(header)
        self.tabs = QTabWidget()
        self.tabs.addTab(self._import_page(), "01  导入")
        self.tabs.addTab(self._review_page(), "02  校对")
        self.tabs.addTab(self._dub_page(), "03  配音")
        self.tabs.addTab(self._notes_page(), "04  讲义")
        self.tabs.addTab(self._export_page(), "05  导出")
        outer.addWidget(self.tabs, 1)
        footer = QHBoxLayout()
        self.stage_label = label("准备就绪 · 从一个课程视频开始")
        footer.addWidget(self.stage_label, 1)
        self.count_label = label("")
        self.count_label.setWordWrap(False)
        footer.addWidget(self.count_label)
        self.usage_label = label("API 尚未运行", "description")
        self.usage_label.setWordWrap(False)
        footer.addWidget(self.usage_label)
        self.cancel_button = button("停止任务", self._cancel)
        self.cancel_button.setObjectName("danger")
        self.cancel_button.setEnabled(False)
        footer.addWidget(self.cancel_button)
        self.log_toggle = button("任务日志", self._toggle_log)
        footer.addWidget(self.log_toggle)
        outer.addLayout(footer)
        self.progress_bar = QProgressBar()
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setValue(0)
        outer.addWidget(self.progress_bar)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(700)
        self.log.setMaximumHeight(140)
        self.log.hide()
        outer.addWidget(self.log)

    def _build_menu(self):
        file_menu = self.menuBar().addMenu("项目")
        open_action = QAction("打开项目…", self)
        open_action.setShortcut(QKeySequence.StandardKey.Open)
        open_action.triggered.connect(self._open_project)
        file_menu.addAction(open_action)
        save = QAction("提交当前编辑", self)
        save.setShortcut(QKeySequence.StandardKey.Save)
        save.triggered.connect(self._commit_all)
        file_menu.addAction(save)
        undo = QAction("撤销上次项目修改", self)
        undo.setShortcut(QKeySequence.StandardKey.Undo)
        undo.triggered.connect(lambda: self._call("undo"))
        file_menu.addAction(undo)
        file_menu.addSeparator()
        action = QAction("打开项目文件夹", self)
        action.triggered.connect(self._open_project_folder)
        file_menu.addAction(action)
        help_menu = self.menuBar().addMenu("帮助")
        action = QAction("关于当前版本", self)
        action.triggered.connect(self._about)
        help_menu.addAction(action)

    def _import_page(self):
        page = QWidget()
        layout = QHBoxLayout(page)
        layout.setSpacing(24)
        left = QVBoxLayout()
        left.addSpacing(15)
        left.addWidget(label("把数学课，带到另一种语言。", "pageTitle"))
        left.addWidget(label("在本地保留视频时间轴，逐段校对译文、公式与课程讲义。", "description"))
        form_box = QGroupBox("新建课程项目")
        form = QFormLayout(form_box)
        form.setVerticalSpacing(14)
        self.media_path = QLineEdit()
        self.media_path.setPlaceholderText("选择本地课程视频")
        row = QHBoxLayout()
        row.addWidget(self.media_path, 1)
        row.addWidget(button("选择文件", self._pick_media))
        form.addRow("课程媒体", row)
        self.directory_path = QLineEdit()
        self.directory_path.setPlaceholderText("选择用于保存项目的新文件夹")
        row = QHBoxLayout()
        row.addWidget(self.directory_path, 1)
        row.addWidget(button("选择目录", self._pick_directory))
        form.addRow("项目目录", row)
        self.title_input = QLineEdit()
        self.title_input.setPlaceholderText("默认使用媒体文件名")
        form.addRow("课程名称", self.title_input)
        languages = QHBoxLayout()
        self.source_language = QComboBox()
        self.target_language = QComboBox()
        for code, title in [("en", "英语"), ("zh-CN", "简体中文")]:
            self.source_language.addItem(title, code)
            self.target_language.addItem(title, code)
        self.target_language.setCurrentIndex(1)
        languages.addWidget(self.source_language)
        languages.addWidget(label("→"))
        languages.addWidget(self.target_language)
        form.addRow("翻译方向", languages)
        self.create_button = button("建立项目并检查媒体", self._create_project, True)
        form.addRow(self.create_button)
        left.addWidget(form_box)
        existing = QHBoxLayout()
        existing.addWidget(button("打开已有课程", self._open_project))
        self.import_subtitles_button = button("导入 SRT / VTT 转写", self._import_subtitles)
        existing.addWidget(self.import_subtitles_button)
        left.addLayout(existing)
        left.addWidget(label("项目按 revision 保存校对内容；大文件检查与处理在独立进程运行。", "description"))
        left.addStretch()
        layout.addLayout(left, 3)
        right = QVBoxLayout()
        guide = QGroupBox("从原课到可发布材料")
        steps = QVBoxLayout(guide)
        for n, title, desc in [
            ("1", "转写与证据", "本地 Whisper 识别，或导入已有字幕。"),
            ("2", "逐段翻译与校对", "源句、译文、字幕和读法分别编辑。"),
            ("3", "标准配音与试听", "系统声音合成，检查语速和片段时长。"),
            ("4", "讲义与多格式导出", "保留公式来源，固定 revision 后导出。"),
        ]:
            steps.addWidget(label(f"{n}   {title}"))
            steps.addWidget(label(desc, "description"))
            steps.addSpacing(12)
        right.addWidget(guide)
        self.capabilities = label("", "notice")
        right.addWidget(self.capabilities)
        self.pipeline_button = self._stage_button("开始自动处理草稿", "pipeline", True)
        right.addWidget(self.pipeline_button)
        right.addWidget(label("使用 API 前请在“处理设置”选择提供程序，并明确授权课程文本上传。", "description"))
        right.addStretch()
        layout.addLayout(right, 2)
        return page

    def _review_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        commands = QHBoxLayout()
        commands.addWidget(self._stage_button("① 本地转写", "transcribe"))
        commands.addWidget(self._stage_button("区分说话人", "diarize"))
        commands.addWidget(self._stage_button("② 数学解析", "analyze"))
        commands.addWidget(self._stage_button("③ 翻译与读法", "translate", True))
        commands.addWidget(button("拆分原文片段", self._split_segment))
        commands.addStretch()
        self.filter = QCheckBox("只看待校对")
        self.filter.toggled.connect(self._refresh_segment_table)
        commands.addWidget(self.filter)
        layout.addLayout(commands)
        split = QSplitter(Qt.Orientation.Horizontal)
        layout.addWidget(split, 1)
        self.preview_panel = self._media_panel()
        split.addWidget(self.preview_panel)
        center = QWidget()
        middle = QVBoxLayout(center)
        middle.setContentsMargins(0, 0, 0, 0)
        self.segment_table = QTableWidget(0, 5)
        self.segment_table.setMinimumHeight(180)
        self.segment_table.setHorizontalHeaderLabels(["时间 / 秒", "说话人", "课程原文", "译文 / 字幕", "状态"])
        self.segment_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.segment_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.segment_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.segment_table.setAlternatingRowColors(True)
        self.segment_table.verticalHeader().hide()
        self.segment_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.segment_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.segment_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.segment_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.segment_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        self.segment_table.itemSelectionChanged.connect(self._segment_selected)
        middle.addWidget(self.segment_table, 3)
        self.editor_box = QGroupBox("片段编辑")
        form = QGridLayout(self.editor_box)
        form.setVerticalSpacing(5)
        self.segment_caption = label("选择片段后开始编辑", "description")
        self.segment_caption.setWordWrap(False)
        self.segment_fields = {}
        for index, (key, title) in enumerate([("source_text", "源句"), ("target_text", "译文 · 支持公式 ID"), ("subtitle_plain", "字幕 · 纯文本"), ("tts_text", "配音 · 实际读法")]):
            row, col = divmod(index, 2)
            form.addWidget(label(title), 1 + row * 2, col)
            field = QPlainTextEdit()
            field.setMinimumHeight(55)
            field.setMaximumHeight(100)
            field.textChanged.connect(self._mark_segment_dirty)
            self.segment_fields[key] = field
            form.addWidget(field, 2 + row * 2, col)
        timing = QHBoxLayout()
        self.start_time = QDoubleSpinBox()
        self.end_time = QDoubleSpinBox()
        for spin in (self.start_time, self.end_time):
            spin.setRange(0, 999999)
            spin.setDecimals(3)
            spin.setSuffix(" s")
            spin.setSingleStep(0.1)
            spin.valueChanged.connect(self._mark_segment_dirty)
        timing.addWidget(label("起点"))
        timing.addWidget(self.start_time)
        timing.addWidget(label("终点"))
        timing.addWidget(self.end_time)
        self.segment_review = review_combo()
        self.segment_review.currentIndexChanged.connect(self._mark_segment_dirty)
        timing.addWidget(self.segment_review)
        form.addLayout(timing, 5, 0, 1, 2)
        speaker_row = QHBoxLayout()
        speaker_row.setSpacing(8)
        speaker_row.addWidget(self.segment_caption)
        speaker_row.addWidget(label("说话人"))
        self.segment_speaker = QComboBox()
        self.segment_speaker.setMinimumWidth(100)
        self.segment_speaker.currentIndexChanged.connect(self._mark_segment_dirty)
        speaker_row.addWidget(self.segment_speaker, 1)
        speaker_row.addWidget(button("添加", self._add_speaker))
        speaker_row.addWidget(button("声音配置", self._edit_segment_speaker))
        form.addLayout(speaker_row, 0, 0, 1, 2)
        middle.addWidget(self.editor_box, 2)
        split.addWidget(center)
        evidence = QWidget()
        side = QVBoxLayout(evidence)
        side.setContentsMargins(0, 0, 0, 0)
        side.addWidget(label("公式与原始证据"))
        self.formula_list = QListWidget()
        self.formula_list.itemDoubleClicked.connect(self._edit_formula)
        self.formula_list.currentItemChanged.connect(self._formula_selected)
        side.addWidget(self.formula_list, 2)
        side.addWidget(button("编辑选中公式", self._edit_formula))
        self.evidence_list = QListWidget()
        self.evidence_list.setMaximumHeight(120)
        self.evidence_list.itemClicked.connect(self._open_evidence)
        side.addWidget(self.evidence_list)
        side.addWidget(label("课程术语"))
        self.term_list = QListWidget()
        self.term_list.itemDoubleClicked.connect(self._edit_term)
        side.addWidget(self.term_list, 1)
        side.addWidget(button("编辑课程术语", self._edit_term))
        side.addWidget(label("双击公式或术语可修改。来源列表可跳回原课；待确认表达请回听原音。", "description"))
        split.addWidget(evidence)
        split.setSizes([300, 720, 270])
        return page

    def _media_panel(self):
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(label("原课预览"))
        self.media_status = label("未关联可播放媒体", "description")
        layout.addWidget(self.media_status)
        self.video = QVideoWidget()
        self.video.setMinimumHeight(200)
        self.video.setStyleSheet("background: #19253b; border-radius: 8px;")
        layout.addWidget(self.video)
        self.audio_output = QAudioOutput(self)
        self.audio_output.setVolume(0.75)
        self.player = QMediaPlayer(self)
        self.player.setAudioOutput(self.audio_output)
        self.player.setVideoOutput(self.video)
        self.play_slider = QSlider(Qt.Orientation.Horizontal)
        self.play_slider.setRange(0, 0)
        self.play_slider.sliderMoved.connect(self.player.setPosition)
        layout.addWidget(self.play_slider)
        self.player_time = label("00:00:00.000 / 00:00:00.000", "description")
        layout.addWidget(self.player_time)
        controls = QHBoxLayout()
        self.play_button = button("播放 / 暂停", self._toggle_play)
        controls.addWidget(self.play_button)
        controls.addWidget(button("重连媒体", self._relink_media))
        layout.addLayout(controls)
        layout.addWidget(self._stage_button("提取当前帧证据", "frame", options=lambda: {"time_ms": self.player.position()}))
        self.frame_preview = label("尚无提取帧\n可在原课中定位并提取当前画面", "description")
        self.frame_preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.frame_preview.setMinimumHeight(130)
        self.frame_preview.setMaximumHeight(230)
        layout.addWidget(self.frame_preview)
        self.add_formula_button = button("框选并添加公式证据", self._add_visual_formula)
        self.add_formula_button.setEnabled(False)
        layout.addWidget(self.add_formula_button)
        layout.addWidget(label("帧提取不会自动认定公式。复杂手写或画面遮挡时应保留原图并人工校对。", "description"))
        layout.addStretch()
        self.player.positionChanged.connect(self._player_position)
        self.player.durationChanged.connect(lambda value: self.play_slider.setRange(0, value))
        self.player.errorOccurred.connect(lambda _error, message: self._log("播放器：" + message))
        return panel

    def _dub_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.addWidget(label("让译文，回到原课的时间线上。", "pageTitle"))
        layout.addWidget(label("配音使用单独校对的读法文本。合成后检查每段时长；过长片段需要修订。", "description"))
        group = QGroupBox("默认声音与本地服务")
        form = QFormLayout(group)
        self.voice_summary = label("")
        form.addRow("当前声音", self.voice_summary)
        form.addRow(button("设置声音与语速", self._settings))
        layout.addWidget(group)
        self.speaker_table = QTableWidget(0, 4)
        self.speaker_table.setHorizontalHeaderLabels(["说话人", "片段数", "配音方式 / 声音", "参考原声"])
        self.speaker_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.speaker_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.speaker_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.speaker_table.setAlternatingRowColors(True)
        self.speaker_table.verticalHeader().hide()
        for column in (0, 1, 3):
            self.speaker_table.horizontalHeader().setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        self.speaker_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.speaker_table.setMinimumHeight(145)
        self.speaker_table.setMaximumHeight(270)
        self.speaker_table.itemDoubleClicked.connect(self._edit_speaker)
        layout.addWidget(self.speaker_table, 1)
        speakers_row = QHBoxLayout()
        for title, callback in [("添加说话人", self._add_speaker), ("配置选中说话人", self._edit_speaker),
                                ("试听参考原声", self._preview_speaker_reference), ("试听该人一段配音", self._preview_speaker_dub)]:
            control = button(title, callback)
            self._stage_buttons.append(control)
            speakers_row.addWidget(control)
        speakers_row.addStretch()
        layout.addLayout(speakers_row)
        layout.addWidget(label("可在校对页为每段指定说话人。原声音色需 3–10 秒单人清晰参考原声及本机 GPT-SoVITS 服务；多人同时讲话暂需人工校对，声音相似度需试听确认。", "description"))
        row = QHBoxLayout()
        row.addWidget(self._stage_button("按说话人生成配音", "dub", True))
        self.listen_dub_button = button("试听最新配音", self._listen_dub)
        self.listen_dub_button.setEnabled(False)
        row.addWidget(self.listen_dub_button)
        row.addStretch()
        layout.addLayout(row)
        self.dub_status = label("尚未生成配音。请先校对片段中的“配音 · 实际读法”。", "notice")
        layout.addWidget(self.dub_status)
        self.dub_player = QMediaPlayer(self)
        self.dub_audio = QAudioOutput(self)
        self.dub_player.setAudioOutput(self.dub_audio)
        self.dub_player.errorOccurred.connect(lambda _error, message: self._log("试听：" + message))
        layout.addStretch()
        return page

    def _notes_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        header = QHBoxLayout()
        header.addWidget(label("有出处的课程讲义", "pageTitle"), 1)
        header.addWidget(self._stage_button("整理 / 更新讲义草稿", "notes", True))
        layout.addLayout(header)
        layout.addWidget(label("核心内容应引用课程片段或公式。编辑后自动保存，确认前请逐项核对证据。", "description"))
        split = QSplitter(Qt.Orientation.Horizontal)
        self.note_list = QListWidget()
        self.note_list.currentItemChanged.connect(self._note_selected)
        split.addWidget(self.note_list)
        editor = QWidget()
        form = QVBoxLayout(editor)
        self.note_heading = QLineEdit()
        self.note_heading.setPlaceholderText("章节标题")
        self.note_heading.textChanged.connect(self._mark_note_dirty)
        form.addWidget(self.note_heading)
        self.note_body = QPlainTextEdit()
        self.note_body.setPlaceholderText("整理课程支持的定义、推导和结论；公式使用 {{formula-id}} 引用。")
        self.note_body.textChanged.connect(self._mark_note_dirty)
        form.addWidget(self.note_body, 1)
        self.note_review = review_combo()
        self.note_review.currentIndexChanged.connect(self._mark_note_dirty)
        form.addWidget(self.note_review)
        form.addWidget(label("证据引用（双击跳回课程）", "description"))
        self.note_sources = QListWidget()
        self.note_sources.setMaximumHeight(130)
        self.note_sources.itemDoubleClicked.connect(self._note_source_clicked)
        form.addWidget(self.note_sources)
        split.addWidget(editor)
        split.setSizes([300, 1000])
        layout.addWidget(split, 1)
        return page

    def _export_page(self):
        page = QWidget()
        layout = QHBoxLayout(page)
        left = QVBoxLayout()
        left.addWidget(label("课程的下一站", "pageTitle"))
        left.addWidget(label("检查问题，选择音轨策略，再从当前 revision 导出。", "description"))
        group = QGroupBox("导出内容")
        formats = QGridLayout(group)
        self.format_checks = {}
        choices = [("video", "MP4 课程视频", True), ("srt", "SRT 字幕", True), ("vtt", "VTT 字幕", True), ("pdf", "PDF 讲义", True), ("wav", "WAV 配音", False), ("ass", "ASS 字幕", False), ("markdown", "Markdown 讲义", False), ("json", "项目 / 公式 JSON", False)]
        for i, (key, title, checked) in enumerate(choices):
            check = QCheckBox(title)
            check.setChecked(checked)
            self.format_checks[key] = check
            formats.addWidget(check, i // 2, i % 2)
        left.addWidget(group)
        group = QGroupBox("声音与画面")
        form = QFormLayout(group)
        self.background = QComboBox()
        self.background.addItem("只用译声", "dub_only")
        self.background.addItem("保留原声，只加字幕", "original")
        self.background.addItem("低音量原声 + 译声（双语旁白）", "quiet_original")
        form.addRow("音轨策略", self.background)
        form.addRow(label("混合原声会包含教师人声，此处不提供未经验证的干净背景声分离。", "description"))
        self.export_directory = QLineEdit()
        self.export_directory.setPlaceholderText("留空：项目内 exports 文件夹")
        row = QHBoxLayout()
        row.addWidget(self.export_directory, 1)
        row.addWidget(button("浏览", self._pick_export_directory))
        form.addRow("输出目录", row)
        left.addWidget(group)
        row = QHBoxLayout()
        row.addWidget(self._stage_button("生成待校对预览（≤30秒）", "preview", options=self._export_options))
        row.addWidget(self._stage_button("导出所选文件", "export", True, options=self._export_options))
        left.addLayout(row)
        left.addStretch()
        layout.addLayout(left, 3)
        right = QVBoxLayout()
        row = QHBoxLayout()
        row.addWidget(label("导出前检查"), 1)
        row.addWidget(button("刷新检查", self._refresh_issues))
        right.addLayout(row)
        self.issues = QListWidget()
        self.issues.itemDoubleClicked.connect(self._issue_clicked)
        right.addWidget(self.issues, 2)
        right.addWidget(label("最近完成的文件", "description"))
        self.output_list = QListWidget()
        self.output_list.itemDoubleClicked.connect(self._open_output)
        right.addWidget(self.output_list, 1)
        right.addWidget(button("打开项目目录", self._open_project_folder))
        layout.addLayout(right, 2)
        return page

    def _connect(self):
        c = self.coordinator
        c.project_changed.connect(self._refresh_project)
        c.progress.connect(self._progress)
        c.task_finished.connect(self._finished)
        c.task_failed.connect(self._failed)
        c.busy_changed.connect(self._busy)
        c.log_message.connect(self._log)
        self.tabs.currentChanged.connect(self._tab_changed)
        self.background.currentIndexChanged.connect(self._refresh_issues)
        for check in self.format_checks.values():
            check.toggled.connect(self._refresh_issues)

    def _stage_button(self, text, stage, primary=False, options=None):
        result = button(text, lambda: self._run(stage, options() if callable(options) else options), primary)
        self._stage_buttons.append(result)
        return result

    def _run(self, stage, options=None):
        if not self._commit_all():
            return
        try:
            if stage == "frame":
                self._frame_requested_ms = (options or {}).get("time_ms", self.player.position())
            self.coordinator.run_stage(stage, options or {})
        except Exception as error:
            self._show_error(str(error))

    def _call(self, method, *args):
        try:
            if self._commit_all():
                return getattr(self.coordinator, method)(*args)
        except Exception as error:
            self._show_error(str(error))

    def _speaker(self, identifier):
        return next((s for s in (self._project or {}).get("speakers", []) if s["id"] == identifier), None)

    def _selected_speaker_id(self):
        items = self.speaker_table.selectedItems()
        if items:
            return items[0].data(Qt.ItemDataRole.UserRole)
        return self.segment_speaker.currentData()

    def _refresh_speakers(self):
        selected_id = self._selected_speaker_id()
        pending_id = self.segment_speaker.currentData()
        self.segment_speaker.blockSignals(True)
        self.segment_speaker.clear()
        speakers = (self._project or {}).get("speakers", [])
        segments = (self._project or {}).get("segments", [])
        self.speaker_table.setRowCount(len(speakers))
        selected_row = 0
        for row, speaker in enumerate(speakers):
            identifier = speaker["id"]
            self.segment_speaker.addItem(speaker.get("label", identifier), identifier)
            if identifier == selected_id:
                selected_row = row
            profile = speaker.get("voice_profile") or {}
            cloning = profile.get("provider") == "gpt_sovits" or speaker.get("voice_mode") == "reference"
            mode = "参考原声音色 · GPT-SoVITS" if cloning else "Windows · " + (profile.get("voice_name") or "默认声音")
            reference = speaker.get("reference_evidence") or {}
            start = profile.get("reference_start_ms", reference.get("start_ms"))
            end = profile.get("reference_end_ms", reference.get("end_ms"))
            reference_label = f"{start / 1000:.2f}–{end / 1000:.2f} s" if cloning and start is not None and end is not None else "—"
            values = [speaker.get("label", identifier), str(sum(s.get("speaker_id") == identifier for s in segments)), mode, reference_label]
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setData(Qt.ItemDataRole.UserRole, identifier)
                item.setToolTip(value)
                self.speaker_table.setItem(row, column, item)
            self.speaker_table.setRowHeight(row, 44)
        segment = self._segment(self._segment_id)
        current_id = pending_id if self._segment_dirty else (segment or {}).get("speaker_id", pending_id)
        self.segment_speaker.setCurrentIndex(max(0, self.segment_speaker.findData(current_id)))
        self.segment_speaker.blockSignals(False)
        if speakers:
            self.speaker_table.selectRow(selected_row)

    def _add_speaker(self):
        if not self._project:
            self._show_error("请先建立或打开课程项目。")
            return
        title, accepted = QInputDialog.getText(self, "添加说话人", "显示名称", text=f"说话人 {len(self._project.get('speakers', [])) + 1}")
        if not accepted or not title.strip():
            return
        identifier = self._call("add_speaker", title.strip())
        if identifier:
            for row in range(self.speaker_table.rowCount()):
                if self.speaker_table.item(row, 0).data(Qt.ItemDataRole.UserRole) == identifier:
                    self.speaker_table.selectRow(row)
                    break
            if self.tabs.currentIndex() == 1 and self._segment_id:
                self.segment_speaker.setCurrentIndex(self.segment_speaker.findData(identifier))

    def _edit_segment_speaker(self):
        self._edit_speaker(identifier=self.segment_speaker.currentData())

    def _split_segment(self):
        segment = self._segment(self._segment_id)
        if not segment:
            self._show_error("请先选择一条片段，并在“源句”输入框中将光标放在两位说话人的交界处。")
            return
        field = self.segment_fields["source_text"]
        cursor = field.textCursor()
        cursor.setPosition(0, cursor.MoveMode.KeepAnchor)
        offset = len(cursor.selectedText().replace("\u2029", "\n"))
        if not 0 < offset < len(field.toPlainText()):
            self._show_error("请在“源句”文字内部，将光标放在需要拆分的位置。")
            field.setFocus()
            return
        if not self._commit_all():
            return
        segment = self._segment(self._segment_id)
        start, end = segment["start_ms"], segment["end_ms"]
        if end - start < 2:
            self._show_error("该片段过短，无法拆分。")
            return
        suggested = self.player.position()
        if not start < suggested < end:
            suggested = (start + end) / 2
        seconds, accepted = QInputDialog.getDouble(
            self, "按原文光标拆分片段", "填写对应的拆分时间（秒）。两段均需重新翻译、配音并校对。",
            suggested / 1000, (start + 1) / 1000, (end - 1) / 1000, 3,
        )
        if accepted:
            identifier = self._call("split_segment", segment["id"], round(seconds * 1000), offset)
            if identifier:
                self.filter.setChecked(False)
                self._select_segment(identifier)

    def _edit_speaker(self, *_, identifier=None):
        if not self._commit_all():
            return
        identifier = identifier or self._selected_speaker_id()
        speaker = self._speaker(identifier)
        if not speaker:
            self._show_error("请先选择一位说话人。")
            return
        segment = self._segment(self._segment_id)
        if not segment or segment.get("speaker_id") != identifier:
            segment = next((s for s in self._project.get("segments", []) if s.get("speaker_id") == identifier), None)
        reference = (segment["start_ms"], min(segment["end_ms"], segment["start_ms"] + 10000)) if segment else None
        dialog = SpeakerDialog(speaker, self._project.get("languages", {}).get("source", "en"),
                               self._project.get("source", {}).get("duration_ms", 0), reference, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._call("update_speaker", identifier, dialog.changes())

    def _preview_speaker_reference(self):
        identifier = self._selected_speaker_id()
        speaker = self._speaker(identifier)
        if not speaker or speaker.get("voice_mode") != "reference":
            self._show_error("请先配置选中说话人的参考原声音色及参考片段。")
            return
        self._run("speaker_reference", {"speaker_id": identifier})

    def _preview_speaker_dub(self):
        identifier = self._selected_speaker_id()
        segment = self._segment(self._segment_id)
        if not segment or segment.get("speaker_id") != identifier or not segment.get("tts_text", "").strip():
            segment = next((s for s in (self._project or {}).get("segments", [])
                            if s.get("speaker_id") == identifier and s.get("tts_text", "").strip()), None)
        if not segment:
            self._show_error("该说话人尚无可试听的配音文本，请先在校对页填写“配音 · 实际读法”。")
            return
        self._run("dub", {"segment_ids": [segment["id"]], "sample_only": True})

    def _pick_media(self):
        path, _ = QFileDialog.getOpenFileName(self, "选择课程视频或音频", "", "媒体文件 (*.mp4 *.mkv *.mov *.webm *.avi *.m4v *.mp3 *.wav *.m4a *.flac);;所有文件 (*)")
        if path:
            self.media_path.setText(path)
            if not self.title_input.text():
                self.title_input.setText(Path(path).stem)

    def _pick_directory(self):
        path = QFileDialog.getExistingDirectory(self, "选择项目目录（建议使用空文件夹）")
        if path:
            self.directory_path.setText(path)

    def _pick_export_directory(self):
        path = QFileDialog.getExistingDirectory(self, "选择导出目录")
        if path:
            self.export_directory.setText(path)

    def _create_project(self):
        if self.source_language.currentData() == self.target_language.currentData():
            self._show_error("源语言和目标语言应不同。")
            return
        if not self.media_path.text().strip() or not self.directory_path.text().strip():
            self._show_error("请选择真实的课程媒体文件和项目目录。")
            return
        if not self._commit_all():
            return
        try:
            self.coordinator.create_project(self.media_path.text().strip(), self.directory_path.text().strip(), self.source_language.currentData(), self.target_language.currentData(), self.title_input.text().strip())
        except Exception as error:
            self._show_error(str(error))

    def _open_project(self):
        path = QFileDialog.getExistingDirectory(self, "打开 MathBridge 项目目录")
        if path:
            self._call("open_project", path)

    def _import_subtitles(self):
        if not self._project:
            self._show_error("请先建立或打开课程项目，再导入字幕。")
            return
        path, _ = QFileDialog.getOpenFileName(self, "导入源语言字幕", "", "字幕 (*.srt *.vtt)")
        if path:
            self._call("import_subtitles", path)

    def _relink_media(self):
        path, _ = QFileDialog.getOpenFileName(self, "重连原始课程媒体（将核对指纹）")
        if path:
            self._call("relink_source", path)

    def _settings(self):
        dialog = SettingsDialog(self.coordinator.settings, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            values = dialog.result_values()
            try:
                from mathbridge.storage.credentials import delete_secret, save_secret
                if values.get("remember_api_key"):
                    if values.get("api_key"):
                        save_secret("text-api", values["api_key"])
                elif self.coordinator.settings.get("remember_api_key"):
                    delete_secret("text-api")
            except OSError:
                values["remember_api_key"] = False
                self._show_error("无法访问 Windows 凭据库，API Key 仍仅保留在当前会话。")
            self.coordinator.settings.update(values)
            save_settings(values)
            self._update_capabilities()

    def _update_capabilities(self):
        cfg = self.coordinator.settings
        model = cfg.get("whisper_model_path", "")
        model_state = "本地模型已选择" if model and Path(model).is_dir() else "未选择本地 Whisper 模型"
        provider = {"manual": "手动校对", "deepseek": "DeepSeek API", "offline": "本地 llama.cpp"}.get(cfg.get("text_provider"), "手动校对")
        permission = "课程文本已授权上传" if cfg.get("allow_text_api") else "尚未授权课程文本上传"
        diarization = "自动流程已启用" if cfg.get("diarization_enabled") else "手动分配 / 按需区分"
        self.capabilities.setText(f"识别：{model_state}\n说话人：{diarization}\n文本：{provider}\n隐私：{permission}\n参考音色：可配置本机 GPT-SoVITS")
        self.voice_summary.setText(f"{cfg.get('voice') or '自动选择已安装的目标语言声音'} · 语速 {cfg.get('speech_rate', 0)}\n原声克隆服务：{cfg.get('voice_clone_api_url', 'http://127.0.0.1:9880')}")
        self._update_usage()

    def _update_usage(self):
        usage = getattr(self.coordinator, "usage", {}) or {}
        if not self._project:
            self.usage_label.setText("API 尚未运行")
            self.usage_label.setToolTip("API 使用量按项目记录；实际费用以服务商账单为准。")
            return
        cost = float(usage.get("cost_cny", 0) or 0)
        uncertain = float(usage.get("uncertain_cost_cny", 0) or 0)
        reserved = float(usage.get("reserved_total_cny", cost + uncertain) or 0)
        calls = int(usage.get("calls", 0) or 0)
        uncertain_requests = int(usage.get("uncertain_requests", 0) or 0)
        budget = float(self.coordinator.settings.get("budget_cny", 0) or 0)
        limit = int(self.coordinator.settings.get("max_calls", 0) or 0)
        text = f"API 估计 ¥{reserved:.2f} / ¥{budget:.2f} · {calls}/{limit} 次"
        if uncertain:
            text += f"（含待核实预留 ¥{uncertain:.2f}）"
        self.usage_label.setText(text)
        self.usage_label.setToolTip(
            f"按返回用量估计：¥{cost:.4f}\n"
            f"未能确认结果的请求：{uncertain_requests} 次，预留 ¥{uncertain:.4f}\n"
            f"预算占用合计：¥{reserved:.4f}\n"
            "网络中断或取消后，请求可能仍被计费，因此保留不确定金额。实际费用以服务商账单为准。"
        )
        reached = budget > 0 and reserved >= budget or limit > 0 and calls >= limit
        self.usage_label.setStyleSheet("color: #a45440;" if reached else "color: #65738a;")

    def _refresh_project(self, project):
        old_project_id = (self._project or {}).get("project_id")
        new_project_id = (project or {}).get("project_id")
        if new_project_id != old_project_id:
            self._segment_id = None
            self._note_id = None
            self._latest_audio = None
            self._frame_path = None
            self.frame_preview.clear()
            self.frame_preview.setText("尚无提取帧\n可在原课中定位并提取当前画面")
            self.add_formula_button.setEnabled(False)
            self.listen_dub_button.setEnabled(False)
            self.output_list.clear()
            self.note_sources.clear()
            self._segment_dirty = self._note_dirty = False
            self._load_note(None)
            self._sync_editor_dirty()
        self._project = project or None
        self._loading = True
        self._refresh_speakers()
        if project:
            self.project_title.setText(project.get("title", "课程项目"))
            self.saved.setText(f"已保存 · revision {project.get('revision', '?')}")
            source_path = str(getattr(self.coordinator, "source_path", "") or "")
            self.media_status.setText(Path(source_path).name if source_path and Path(source_path).is_file() else "未关联可播放媒体 · 请重连原课文件")
            if source_path != self._source_loaded:
                self.player.stop()
                self.player.setSource(QUrl.fromLocalFile(source_path) if source_path else QUrl())
                self._source_loaded = source_path
            pending = sum(s.get("review") != "confirmed" for s in project.get("segments", []))
            self.count_label.setText(f"{len(project.get('segments', []))} 段 · {pending} 段待确认")
            self._refresh_segment_table()
            self._refresh_terms()
            self._refresh_notes()
            if self._segment_id and not self._committing and not self._segment_dirty:
                self._load_segment(self._segment_id)
            elif not self._segment_id:
                self._load_segment(None)
        else:
            self.project_title.setText("尚未打开项目")
            self.count_label.setText("")
            self.segment_table.setRowCount(0)
            self.formula_list.clear()
            self.term_list.clear()
            self.note_list.clear()
            self._load_segment(None)
            self._load_note(None)
        self._loading = False
        self._busy(bool(getattr(self.coordinator, "busy", False)))
        self._update_capabilities()
        self._refresh_issues()

    def _refresh_segment_table(self, *_):
        current_id = self._segment_id
        old = self._loading
        self._loading = True
        self.segment_table.blockSignals(True)
        segments = (self._project or {}).get("segments", [])
        if self.filter.isChecked():
            segments = [s for s in segments if s.get("review") != "confirmed"]
        self.segment_table.setRowCount(len(segments))
        selected_row = -1
        for row, segment in enumerate(segments):
            if segment["id"] == current_id:
                selected_row = row
            speaker = self._speaker(segment.get("speaker_id")) or {}
            texts = [f"{segment['start_ms']/1000:.2f} –\n{segment['end_ms']/1000:.2f}", speaker.get("label", segment.get("speaker_id", "")), segment.get("source_text", ""), segment.get("subtitle_plain") or segment.get("target_text", ""), REVIEW_LABELS.get(segment.get("review"), "待校对")]
            for col, text in enumerate(texts):
                item = QTableWidgetItem(text)
                item.setData(Qt.ItemDataRole.UserRole, segment["id"])
                item.setToolTip(text)
                if col == 4:
                    item.setForeground(QColor("#348264" if segment.get("review") == "confirmed" else "#a5742e"))
                self.segment_table.setItem(row, col, item)
            self.segment_table.setRowHeight(row, 82)
        if selected_row >= 0:
            self.segment_table.selectRow(selected_row)
        self.segment_table.blockSignals(False)
        self._loading = old

    def _segment_selected(self):
        if self._loading:
            return
        items = self.segment_table.selectedItems()
        new_id = items[0].data(Qt.ItemDataRole.UserRole) if items else None
        if new_id == self._segment_id:
            return
        if not self._commit_segment():
            self.segment_table.blockSignals(True)
            for row in range(self.segment_table.rowCount()):
                if self.segment_table.item(row, 0).data(Qt.ItemDataRole.UserRole) == self._segment_id:
                    self.segment_table.selectRow(row)
                    break
            self.segment_table.blockSignals(False)
            return
        self._load_segment(new_id)

    def _segment(self, identifier):
        return next((s for s in (self._project or {}).get("segments", []) if s["id"] == identifier), None)

    def _load_segment(self, identifier):
        old = self._loading
        self._loading = True
        self._segment_id = identifier
        segment = self._segment(identifier)
        self.editor_box.setEnabled(bool(segment))
        self.segment_caption.setText(identifier if segment else "选择片段")
        self.segment_caption.setToolTip("修改内容会自动创建新 revision；顶部显示已保存后表示写入成功。")
        for key, field in self.segment_fields.items():
            field.setPlainText(segment.get(key, "") if segment else "")
        self.start_time.setValue(segment.get("start_ms", 0) / 1000 if segment else 0)
        self.end_time.setValue(segment.get("end_ms", 0) / 1000 if segment else 0)
        self.segment_review.setCurrentIndex(max(0, self.segment_review.findData(segment.get("review") if segment else "needs_review")))
        self.segment_speaker.setCurrentIndex(max(0, self.segment_speaker.findData(segment.get("speaker_id") if segment else None)))
        self._refresh_formulas(segment)
        if segment:
            self.player.setPosition(segment["start_ms"])
        self._segment_dirty = False
        self._loading = old

    def _mark_segment_dirty(self, *_):
        if self._loading or not self._segment_id:
            return
        self._segment_dirty = True
        self._sync_editor_dirty()
        self.saved.setText("编辑中 · 等待自动保存")
        self._segment_timer.start()
        if self._latest_audio:
            self.dub_status.setText("文案已修改；已有配音需要重新生成后才能作为当前版本使用。")

    def _commit_segment(self):
        self._segment_timer.stop()
        if not self._segment_dirty or not self._segment_id:
            return True
        changes = {k: field.toPlainText() for k, field in self.segment_fields.items()}
        changes.update(start_ms=round(self.start_time.value() * 1000), end_ms=round(self.end_time.value() * 1000), review=self.segment_review.currentData())
        if self.segment_speaker.currentData():
            changes["speaker_id"] = self.segment_speaker.currentData()
        if changes["end_ms"] <= changes["start_ms"]:
            self.saved.setText("尚未保存 · 终点需晚于起点")
            return False
        try:
            self._committing = True
            self.coordinator.update_segment(self._segment_id, changes)
            self._segment_dirty = False
            self._sync_editor_dirty()
            return True
        except Exception as error:
            self.saved.setText("尚未保存 · 请检查输入")
            self._log("片段保存失败：" + str(error))
            return False
        finally:
            self._committing = False

    def _refresh_formulas(self, segment=None):
        self.formula_list.clear()
        self.evidence_list.clear()
        formulas = (self._project or {}).get("formulas", [])
        if segment:
            formulas = [f for f in formulas if f["id"] in segment.get("formula_ids", [])]
        for formula in formulas:
            text = formula.get("latex") or formula.get("raw", "原始公式")
            item = QListWidgetItem(f"{text}\n{REVIEW_LABELS.get(formula.get('review'), '待校对')} · {formula['id']}")
            item.setData(Qt.ItemDataRole.UserRole, formula["id"])
            item.setToolTip(formula.get("raw", ""))
            self.formula_list.addItem(item)
        if self.formula_list.count():
            self.formula_list.setCurrentRow(0)

    def _formula_selected(self, *_):
        self.evidence_list.clear()
        item = self.formula_list.currentItem()
        if not item:
            return
        formula = next((x for x in (self._project or {}).get("formulas", []) if x["id"] == item.data(Qt.ItemDataRole.UserRole)), None)
        for evidence in (formula or {}).get("evidence", []):
            item = QListWidgetItem(f"{'原画面' if evidence['kind'] == 'frame' else '原音'} · {timecode(evidence['start_ms'])}")
            item.setData(Qt.ItemDataRole.UserRole, evidence)
            self.evidence_list.addItem(item)

    def _edit_formula(self, *_):
        item = self.formula_list.currentItem()
        if not item:
            return
        formula = next(x for x in self._project["formulas"] if x["id"] == item.data(Qt.ItemDataRole.UserRole))
        dialog = FormulaDialog(formula, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._call("update_formula", formula["id"], dialog.changes())

    def _refresh_terms(self):
        self.term_list.clear()
        for term in (self._project or {}).get("glossary", []):
            item = QListWidgetItem(f"{term['source']}  →  {term['target']}" + ("\n已锁定课程译法" if term.get("locked") else ""))
            item.setData(Qt.ItemDataRole.UserRole, term["id"])
            self.term_list.addItem(item)

    def _edit_term(self, *_):
        item = self.term_list.currentItem()
        if not item:
            return
        term = next(x for x in self._project["glossary"] if x["id"] == item.data(Qt.ItemDataRole.UserRole))
        dialog = TermDialog(term, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._call("update_term", term["id"], dialog.changes())

    def _refresh_notes(self):
        selected = self._note_id
        self.note_list.blockSignals(True)
        self.note_list.clear()
        for note in (self._project or {}).get("notes", []):
            item = QListWidgetItem(f"{note['heading']}\n{REVIEW_LABELS.get(note.get('review'), '待校对')}")
            item.setData(Qt.ItemDataRole.UserRole, note["id"])
            self.note_list.addItem(item)
            if note["id"] == selected:
                self.note_list.setCurrentItem(item)
        self.note_list.blockSignals(False)
        if not selected and self.note_list.count():
            self._load_note(self.note_list.item(0).data(Qt.ItemDataRole.UserRole))
            self.note_list.setCurrentRow(0)
        elif selected and not self._committing and not self._note_dirty:
            self._load_note(selected)

    def _note_selected(self, current, previous):
        if self._loading:
            return
        if not self._commit_note():
            self.note_list.blockSignals(True)
            for row in range(self.note_list.count()):
                candidate = self.note_list.item(row)
                if candidate.data(Qt.ItemDataRole.UserRole) == self._note_id:
                    self.note_list.setCurrentRow(row)
                    break
            self.note_list.blockSignals(False)
            return
        self._load_note(current.data(Qt.ItemDataRole.UserRole) if current else None)

    def _load_note(self, identifier):
        old = self._loading
        self._loading = True
        self._note_id = identifier
        note = next((n for n in (self._project or {}).get("notes", []) if n["id"] == identifier), None)
        self.note_heading.setEnabled(bool(note))
        self.note_body.setEnabled(bool(note))
        self.note_review.setEnabled(bool(note))
        self.note_heading.setText(note.get("heading", "") if note else "")
        self.note_body.setPlainText(note.get("body", "") if note else "")
        self.note_review.setCurrentIndex(max(0, self.note_review.findData(note.get("review") if note else "needs_review")))
        self.note_sources.clear()
        for source_id in (note or {}).get("segment_ids", []):
            segment = self._segment(source_id)
            if segment:
                item = QListWidgetItem(f"{timecode(segment['start_ms'])} · {segment['source_text'][:100]}")
                item.setData(Qt.ItemDataRole.UserRole, source_id)
                self.note_sources.addItem(item)
        for formula_id in (note or {}).get("formula_ids", []):
            formula = next((f for f in (self._project or {}).get("formulas", []) if f["id"] == formula_id), None)
            if formula:
                item = QListWidgetItem(f"公式 {formula_id} · {formula.get('latex') or formula['raw']}")
                item.setData(Qt.ItemDataRole.UserRole, {"formula_id": formula_id})
                self.note_sources.addItem(item)
        self._note_dirty = False
        self._loading = old

    def _mark_note_dirty(self, *_):
        if self._loading or not self._note_id:
            return
        self._note_dirty = True
        self._sync_editor_dirty()
        self.saved.setText("讲义编辑中 · 等待自动保存")
        self._note_timer.start()

    def _commit_note(self):
        self._note_timer.stop()
        if not self._note_dirty or not self._note_id:
            return True
        try:
            self._committing = True
            self.coordinator.update_note(self._note_id, {"heading": self.note_heading.text(), "body": self.note_body.toPlainText(), "review": self.note_review.currentData()})
            self._note_dirty = False
            self._sync_editor_dirty()
            return True
        except Exception as error:
            self.saved.setText("讲义尚未保存 · 请检查输入")
            self._log("讲义保存失败：" + str(error))
            return False
        finally:
            self._committing = False

    def _commit_all(self):
        return self._commit_segment() and self._commit_note()

    def _sync_editor_dirty(self):
        setter = getattr(self.coordinator, "set_editor_dirty", None)
        if setter:
            setter(self._segment_dirty or self._note_dirty)

    def _open_evidence(self, item):
        evidence = item.data(Qt.ItemDataRole.UserRole)
        self._frame_time = evidence["start_ms"]
        self.player.setPosition(evidence["start_ms"])
        if evidence.get("frame_path") and getattr(self.coordinator, "project_dir", None):
            base = Path(self.coordinator.project_dir).resolve()
            candidate = (base / evidence["frame_path"]).resolve()
            if candidate.is_relative_to(base):
                self._display_frame(candidate)

    def _add_visual_formula(self):
        if not self._frame_path or not self._segment_id:
            self._show_error("请先选择一个片段并提取当前画面，再添加公式证据。")
            return
        dialog = FrameEvidenceDialog(self._frame_path, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._call("add_visual_formula", self._segment_id, str(self._frame_path), dialog.crop.bbox, dialog.latex.text().strip() or None, self._frame_time)

    def _note_source_clicked(self, item):
        identifier = item.data(Qt.ItemDataRole.UserRole)
        self.filter.setChecked(False)
        self.tabs.setCurrentIndex(1)
        if isinstance(identifier, dict):
            formula_id = identifier["formula_id"]
            segment = next((s for s in self._project["segments"] if formula_id in s["formula_ids"]), None)
            if segment:
                self._select_segment(segment["id"])
            else:
                self._load_segment(None)
                for row in range(self.formula_list.count()):
                    candidate = self.formula_list.item(row)
                    if candidate.data(Qt.ItemDataRole.UserRole) == formula_id:
                        self.formula_list.setCurrentRow(row)
                        break
        else:
            self._select_segment(identifier)

    def _select_segment(self, identifier):
        for row in range(self.segment_table.rowCount()):
            if self.segment_table.item(row, 0).data(Qt.ItemDataRole.UserRole) == identifier:
                self.segment_table.selectRow(row)
                self.segment_table.scrollToItem(self.segment_table.item(row, 0))
                break

    def _export_options(self):
        options = {"formats": [key for key, check in self.format_checks.items() if check.isChecked()], "background": self.background.currentData(), "subtitle_position": self.coordinator.settings.get("subtitle_position", "bottom")}
        if self.export_directory.text().strip():
            options["output_dir"] = self.export_directory.text().strip()
        return options

    def _refresh_issues(self, *_):
        self.issues.clear()
        if not self._project:
            self.issues.addItem("建立项目后显示导出检查结果。")
            return
        try:
            formats = [key for key, check in self.format_checks.items() if check.isChecked()]
            issues = self.coordinator.export_issues(formats=formats)
        except TypeError:
            issues = self.coordinator.export_issues()
        except Exception as error:
            self.issues.addItem(str(error))
            return
        for issue in issues:
            icon = "●" if issue.get("severity") == "error" else "○"
            item = QListWidgetItem(f"{icon} {issue.get('message', '')}\n{issue.get('code', '')} · {issue.get('entity_id', '')}")
            item.setData(Qt.ItemDataRole.UserRole, issue)
            item.setForeground(QColor("#a45440" if issue.get("severity") == "error" else "#8b733b"))
            self.issues.addItem(item)
        if not issues:
            self.issues.addItem("当前数据检查未发现阻断问题；媒体产物将在导出时验证。")

    def _issue_clicked(self, item):
        issue = item.data(Qt.ItemDataRole.UserRole)
        if issue and self._segment(issue.get("entity_id")):
            self.filter.setChecked(False)
            self.tabs.setCurrentIndex(1)
            self._select_segment(issue["entity_id"])

    def _busy(self, busy):
        self._update_usage()
        for widget in self._stage_buttons:
            widget.setEnabled(bool(self._project) and not busy)
        self.cancel_button.setEnabled(busy)
        self.create_button.setEnabled(not busy)
        self.import_subtitles_button.setEnabled(bool(self._project) and not busy)
        if not busy:
            self.progress_bar.setRange(0, 100)
            self.progress_bar.setValue(0)
            if self._closing_requested:
                QTimer.singleShot(0, self.close)

    def _progress(self, stage, completed, total, message):
        self._update_usage()
        title = STAGES.get(stage, stage)
        if total > 0:
            self.progress_bar.setRange(0, total)
            self.progress_bar.setValue(completed)
            self.stage_label.setText(f"{title} · {completed}/{total}   {message}")
        else:
            self.progress_bar.setRange(0, 0)
            self.stage_label.setText(f"{title} · {message}")

    def _finished(self, stage, payload):
        self._update_usage()
        metadata = payload.get("metadata") or {}
        warnings = []
        for entries in (metadata.get("warnings", []), payload.get("warnings", [])):
            if isinstance(entries, (str, dict)):
                entries = [entries]
            for entry in entries or []:
                message = str(entry.get("message") or entry.get("code") or entry) if isinstance(entry, dict) else str(entry)
                if message and message not in warnings:
                    warnings.append(message)
        if payload.get("metadata", {}).get("stale"):
            self.stage_label.setText("内容已编辑 · 旧处理结果保留为独立草稿")
        else:
            self.stage_label.setText(f"{STAGES.get(stage, stage)}完成" + (f" · {len(warnings)} 条提示" if warnings else ""))
        self._last_outputs = list(payload.get("outputs", []))
        dub_audio_found = False
        for path in self._last_outputs:
            if not isinstance(path, str):
                continue
            is_preview_video = stage == "preview" and Path(path).suffix.lower() == ".mp4"
            title = Path(path).name + (" · 待校对预览，双击播放" if is_preview_video else "")
            item = QListWidgetItem(title)
            item.setToolTip(path + ("\n待校对草稿：请检查字幕、读法、音量和衔接。" if is_preview_video else ""))
            item.setData(Qt.ItemDataRole.UserRole, path)
            self.output_list.addItem(item)
            suffix = Path(path).suffix.lower()
            if suffix in {".png", ".jpg", ".jpeg"} and stage == "frame":
                self._frame_time = self._frame_requested_ms
                self._display_frame(path)
            if suffix == ".wav" and stage in {"dub", "preview"} and not dub_audio_found:
                dub_audio_found = True
                self._latest_audio = path
                self.listen_dub_button.setEnabled(True)
                sample = payload.get("metadata", {}).get("sample", False)
                prefix = "片段试听已生成" if sample else "待校对预览配音" if stage == "preview" else "配音已生成"
                self.dub_status.setText(f"{prefix} · 文案 revision {(self._project or {}).get('revision', '?')}。请试听公式读法与片段衔接。")
                if sample and not payload.get("metadata", {}).get("stale"):
                    self.player.pause()
                    self.dub_player.setSource(QUrl.fromLocalFile(path))
                    self.dub_player.play()
            if suffix == ".wav" and stage == "speaker_reference" and not payload.get("metadata", {}).get("stale"):
                self.player.pause()
                self.dub_player.setSource(QUrl.fromLocalFile(path))
                self.dub_player.play()
                self.dub_status.setText("正在试听选中说话人的参考原声。请核对该片段只有此人讲话，且原话与参考文本一致。")
        for warning in warnings:
            self._log(warning)
        if stage == "diarize" and warnings and not metadata.get("stale"):
            self.stage_label.setText(f"说话人区分完成 · {len(warnings)} 条待复核提示，点击“任务日志”查看")
            self.dub_status.setText("说话人区分存在需复核的归属、重叠或证据提示。请回听原音并检查每段说话人，点击“任务日志”查看详情。")
        if stage in {"create", "create_project", "import_subtitles"} and self._project:
            self.tabs.setCurrentIndex(1)
        if stage == "relink":
            self._refresh_project(self.coordinator.project)
        if stage == "preview" and not payload.get("metadata", {}).get("stale"):
            self.stage_label.setText("待校对预览已生成 · 在最近文件中双击 preview.mp4 播放")
        self._refresh_issues()

    def _failed(self, stage, message):
        self._update_usage()
        self.stage_label.setText(f"{STAGES.get(stage, stage)}未完成")
        self._log(f"{stage}: {message}")
        if not self._closing_requested and "取消" not in message:
            self._show_error(message)

    def _cancel(self):
        self.coordinator.cancel()
        self.stage_label.setText("已请求停止 · 正在等待当前任务退出")
        self.cancel_button.setEnabled(False)

    def _toggle_play(self):
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
        else:
            self.dub_player.pause()
            self.player.play()

    def _listen_dub(self):
        if self._latest_audio:
            self.player.pause()
            if self.dub_player.source().toLocalFile() != self._latest_audio:
                self.dub_player.setSource(QUrl.fromLocalFile(self._latest_audio))
            if self.dub_player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
                self.dub_player.pause()
            else:
                self.dub_player.play()

    def _player_position(self, value):
        if not self.play_slider.isSliderDown():
            self.play_slider.setValue(value)
        self.player_time.setText(f"{timecode(value)} / {timecode(self.player.duration())}")

    def _display_frame(self, path):
        pixmap = QPixmap(str(path))
        if not pixmap.isNull():
            self._frame_path = Path(path)
            self.add_formula_button.setEnabled(True)
            self.frame_preview.setPixmap(pixmap.scaled(max(200, self.frame_preview.width()), 225, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
            self.frame_preview.setToolTip(str(path))
        else:
            self._log("无法显示证据帧：" + str(path))

    def _tab_changed(self, _index):
        self._commit_all()
        if self.tabs.currentIndex() == 4:
            self._refresh_issues()

    def _open_output(self, item):
        path = item.data(Qt.ItemDataRole.UserRole)
        if path and Path(path).is_file():
            if not QDesktopServices.openUrl(QUrl.fromLocalFile(path)):
                self._log("系统未能打开该文件，请在项目目录中使用对应播放器或阅读器打开：" + path)

    def _open_project_folder(self):
        if getattr(self.coordinator, "project_dir", None):
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.coordinator.project_dir)))

    def _toggle_log(self):
        self.log.setVisible(not self.log.isVisible())

    def _log(self, message):
        self.log.appendPlainText(str(message))

    def _show_error(self, message):
        QMessageBox.warning(self, "课译 MathBridge", str(message))

    def _about(self):
        QMessageBox.information(self, "关于课译 MathBridge", "课译 MathBridge 0.1\n\n本地数学课程翻译与校对工作台。\n支持字幕校对、课程术语、公式证据、多人声线配置和多格式导出。\n\nASR 与自动区分说话人需本地模型；原声克隆需另行启动本机 GPT-SoVITS 服务。文本 API 需单独授权。克隆音色需试听确认，自动手写公式 OCR 仍待实现。")

    def closeEvent(self, event):
        if not self._commit_all():
            QMessageBox.warning(self, "当前编辑尚未保存", "输入未通过校验，请检查片段时间、必填文本或讲义内容后再关闭。")
            event.ignore()
            return
        if getattr(self.coordinator, "busy", False):
            if not self._closing_requested:
                self._closing_requested = True
                self.coordinator.cancel()
                self.stage_label.setText("正在停止任务并安全关闭窗口…")
            event.ignore()
            return
        self.player.stop()
        self.dub_player.stop()
        event.accept()

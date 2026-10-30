from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QSettings, QStandardPaths
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFileDialog, QFormLayout,
    QFrame, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QPushButton, QScrollArea, QSpinBox,
    QTabWidget, QVBoxLayout, QWidget,
)
from mathbridge.application.components import component_paths,default_components_directory,resolve_component_settings,whisper_available,gpt_sovits_available

_MODEL_ROOT = (Path(sys.executable).resolve().parents[2] if getattr(sys, "frozen", False)
               else Path(__file__).resolve().parents[3]) / "models" / "speakers"


def _available_model(name):
    model = _MODEL_ROOT / name
    return str(model) if model.is_file() else ""


def _available_whisper_model():
    desktop = Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DesktopLocation) or Path.home() / "Desktop")
    for directory in (_MODEL_ROOT.parent / "faster-whisper-small", desktop / "faster-whisper-small", desktop / "faster-whisper-mini"):
        if all((directory / name).is_file() for name in ("model.bin", "config.json", "tokenizer.json")):
            return str(directory)
    return ""


DEFAULTS = {
    "text_provider": "deepseek", "api_base": "https://api.deepseek.com",
    "model": "deepseek-flash", "allow_text_api": False, "allow_image_api": False,
    "whisper_model_path": _available_whisper_model(), "whisper_device": "cpu", "whisper_compute_type": "int8",
    "diarization_enabled": False, "diarization_segmentation_path": _available_model("segmentation.onnx"),
    "diarization_embedding_path": _available_model("embedding.onnx"), "diarization_num_speakers": 0,
    "voice_clone_api_url": "http://127.0.0.1:9880", "voice_clone_model": "default",
    "ffmpeg_path": "", "ffprobe_path": "", "voice": "", "speech_rate": 0,
    "llama_cli_path": "", "llama_model_path": "", "subtitle_position": "bottom",
    "budget_cny": 5.0, "max_calls": 10000,
    "input_price_per_million": 2.0, "output_price_per_million": 8.0,
    "remember_api_key": False,
    "components_root": "", "gpt_sovits_path": "",
}


def load_settings() -> dict:
    store = QSettings("MathBridge", "MathBridge")
    result = {}
    for key, default in DEFAULTS.items():
        value = store.value(key, default)
        if key in ("whisper_model_path", "diarization_segmentation_path", "diarization_embedding_path") and not value:
            value = default
        if isinstance(default, bool):
            value = value is True or str(value).lower() == "true"
        elif isinstance(default, (int, float)):
            try:
                value = type(default)(value)
            except (ValueError, TypeError):
                value = default
        result[key] = value
    # Legacy 100-call limits stopped otherwise affordable long courses. The
    # single user-facing limit is now the project budget; retain a high fuse.
    result["max_calls"] = DEFAULTS["max_calls"]
    return resolve_component_settings(result)


def save_settings(values: dict) -> None:
    store = QSettings("MathBridge", "MathBridge")
    for key in DEFAULTS:
        if key in values:
            store.setValue(key, values[key])
    store.sync()


class SettingsDialog(QDialog):
    def __init__(self, values: dict, parent=None, *, service_manager=None):
        super().__init__(parent)
        self.setWindowTitle("处理设置")
        self.setMinimumSize(680, 610)
        self.values = dict(values)
        self.fields = {}
        if service_manager is None:
            from .voice_service import LocalVoiceService
            service_manager=LocalVoiceService(self)
        self.service_manager=service_manager
        layout = QVBoxLayout(self)
        tabs = QTabWidget()
        layout.addWidget(tabs)

        local = QWidget()
        form = QFormLayout(local)
        self._path(form,"components_root","组件目录",directory=True)
        if not self.fields['components_root'].text():self.fields['components_root'].setText(str(default_components_directory()))
        self.component_status=QLabel();self.component_status.setWordWrap(True)
        form.addRow(self.component_status)
        custom=QCheckBox("自定义组件位置")
        positions=QWidget();positions_form=QFormLayout(positions);positions_form.setContentsMargins(0,0,0,0)
        self._path(positions_form,"whisper_model_path","Whisper 模型",directory=True)
        self._path(positions_form,"gpt_sovits_path","GPT-SoVITS",directory=True)
        custom.toggled.connect(positions.setVisible);positions.hide()
        form.addRow(custom);form.addRow(positions)
        self.fields['components_root'].textChanged.connect(self._mount_components)
        self.fields['whisper_model_path'].textChanged.connect(self._component_status)
        self.fields['gpt_sovits_path'].textChanged.connect(self._component_status)
        self._component_status()
        advanced_processing=QWidget()
        advanced_form=QFormLayout(advanced_processing);advanced_form.setContentsMargins(0,0,0,0)
        processing_toggle=QCheckBox("高级识别设置")
        processing_toggle.toggled.connect(advanced_processing.setVisible);advanced_processing.hide()
        form.addRow(processing_toggle);form.addRow(advanced_processing)
        form=advanced_form
        self._combo(form, "whisper_device", "运算设备", [("CPU", "cpu")])
        self._combo(form, "whisper_compute_type", "Whisper 精度", [("int8", "int8")])
        diarize = QCheckBox("自动处理时，在转写后区分说话人")
        diarize.setChecked(bool(values.get("diarization_enabled", False)))
        self.fields["diarization_enabled"] = diarize
        form.addRow(diarize)
        self._path(form, "diarization_segmentation_path", "说话人分段 ONNX 模型")
        self._path(form, "diarization_embedding_path", "声纹 ONNX 模型")
        self._number(form, "diarization_num_speakers", "说话人数（0 = 自动判断）", 0, 100)
        speaker_hint = QLabel("自动区分说话人需要两份 ONNX 模型，也可在校对页手动分配。")
        speaker_hint.setWordWrap(True)
        form.addRow(speaker_hint)
        self._path(form, "ffmpeg_path", "FFmpeg 可执行文件")
        self._path(form, "ffprobe_path", "FFprobe 可执行文件")
        tabs.addTab(self._scroll(local), "组件与识别")

        api = QWidget()
        form = QFormLayout(api)
        self._combo(form, "text_provider", "文本处理方式", [
            ("手动校对（不发送内容）", "manual"), ("DeepSeek API", "deepseek"),
            ("本地 llama.cpp", "offline"),
        ])
        self._line(form, "api_base", "API 地址")
        self._line(form, "model", "模型标识")
        key = self._line(form, "api_key", "API Key")
        key.setEchoMode(QLineEdit.EchoMode.Password)
        key.setPlaceholderText("不会写入项目或普通配置")
        remember = QCheckBox("将 Key 保存在 Windows 凭据库（当前账户）")
        remember.setChecked(bool(values.get("remember_api_key", False)))
        self.fields["remember_api_key"] = remember
        form.addRow(remember)
        allow = QCheckBox("允许向所选 API 发送本次任务所需的课程文本")
        allow.setChecked(bool(values.get("allow_text_api", False)))
        allow.setToolTip("包括转写、公式候选的文本和课程术语。原始视频不会自动上传。")
        self.fields["allow_text_api"] = allow
        form.addRow(allow)
        self._number(form, "budget_cny", "本项目最多花费（元）", 0.01, 100000, 2)
        form.addRow(QLabel("断句、翻译和讲义共用这一预算，达到上限自动停止。"))
        advanced_toggle = QCheckBox("高级：费用估算单价")
        advanced = QWidget()
        advanced_form = QFormLayout(advanced)
        advanced_form.setContentsMargins(0, 0, 0, 0)
        self._number(advanced_form, "input_price_per_million", "输入（元 / 百万 token）", 0.0001, 10000, 4)
        self._number(advanced_form, "output_price_per_million", "输出（元 / 百万 token）", 0.0001, 10000, 4)
        advanced.hide()
        advanced_toggle.toggled.connect(advanced.setVisible)
        form.addRow(advanced_toggle)
        form.addRow(advanced)
        explanation = QLabel("费用按设置的单价估算，以服务商账单为准。")
        explanation.setWordWrap(True)
        explanation.setObjectName("notice")
        form.addRow(explanation)
        tabs.addTab(self._scroll(api), "文本与隐私")

        audio = QWidget()
        form = QFormLayout(audio)
        self._line(form, "voice", "Windows 声音名称 / ID")
        self.fields["voice"].setPlaceholderText("留空：自动选取已安装的目标语言声音")
        rate = QSpinBox()
        rate.setRange(-10, 10)
        rate.setValue(int(values.get("speech_rate", 0)))
        self.fields["speech_rate"] = rate
        form.addRow("系统语速（−10 至 10）", rate)
        self._line(form, "voice_clone_api_url", "本地 GPT-SoVITS 服务地址")
        model = self._line(form, "voice_clone_model", "克隆模型版本")
        model.setToolTip("本地服务更换模型后修改此项，以重新生成配音。")
        service_row=QWidget();service_layout=QHBoxLayout(service_row);service_layout.setContentsMargins(0,0,0,0)
        self.start_voice_button=QPushButton("启动声音服务")
        self.stop_voice_button=QPushButton("停止服务")
        service_layout.addWidget(self.start_voice_button);service_layout.addWidget(self.stop_voice_button)
        logs=QPushButton("打开日志");service_layout.addWidget(logs)
        form.addRow(service_row)
        self.voice_service_status=QLabel(self.service_manager.message);self.voice_service_status.setWordWrap(True)
        form.addRow(self.voice_service_status)
        self.start_voice_button.clicked.connect(lambda:self.service_manager.start(self.result_values()))
        self.stop_voice_button.clicked.connect(lambda:self.service_manager.stop(self.result_values()))
        self.service_manager.changed.connect(self._voice_service_changed)
        self.service_manager.busyChanged.connect(self._voice_service_busy)
        def open_logs():
            from PySide6.QtCore import QUrl
            from PySide6.QtGui import QDesktopServices
            root=Path(self.fields['gpt_sovits_path'].text())/'output'
            if root.is_dir():QDesktopServices.openUrl(QUrl.fromLocalFile(str(root)))
        logs.clicked.connect(open_logs)
        self._combo(form, "subtitle_position", "字幕位置", [("底部", "bottom"), ("顶部", "top")])
        note = QLabel("每位说话人的声音和参考原声在“03 配音”中配置。")
        note.setWordWrap(True)
        note.setObjectName("notice")
        form.addRow(note)
        tabs.addTab(self._scroll(audio), "配音与字幕")

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("应用设置")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _voice_service_changed(self,_status,message):
        self.voice_service_status.setText(message)

    def _voice_service_busy(self,busy):
        self.start_voice_button.setEnabled(not busy);self.stop_voice_button.setEnabled(not busy)

    def _mount_components(self,text):
        paths=component_paths(text)
        # Mount the selected root as a unit; never leave an old component from
        # another installation silently attached when the new one is missing.
        self.fields['whisper_model_path'].setText(paths['whisper_model_path'])
        self.fields['gpt_sovits_path'].setText(paths['gpt_sovits_path'])
        self._component_status()

    def _component_status(self,*_):
        whisper=whisper_available(self.fields['whisper_model_path'].text())
        gpt=gpt_sovits_available(self.fields['gpt_sovits_path'].text())
        self.component_status.setText(f"Whisper：{'已找到' if whisper else '未找到'}    GPT-SoVITS：{'已找到' if gpt else '未找到'}")

    @staticmethod
    def _scroll(content):
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(content)
        return scroll

    def _line(self, form, key, label):
        field = QLineEdit(str(self.values.get(key, DEFAULTS.get(key, ""))))
        self.fields[key] = field
        form.addRow(label, field)
        return field

    def _combo(self, form, key, label, choices):
        field = QComboBox()
        for title, value in choices:
            field.addItem(title, value)
        index = field.findData(self.values.get(key, DEFAULTS.get(key)))
        field.setCurrentIndex(max(index, 0))
        self.fields[key] = field
        form.addRow(label, field)
        return field

    def _path(self, form, key, label, directory=False):
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        edit = QLineEdit(str(self.values.get(key, "")))
        edit.setPlaceholderText("使用内置工具" if key in ('ffmpeg_path','ffprobe_path') else "未配置")
        self.fields[key] = edit
        button = QPushButton("浏览")
        def choose():
            if directory:
                path = QFileDialog.getExistingDirectory(self, label, edit.text())
            else:
                path, _ = QFileDialog.getOpenFileName(self, label, edit.text())
            if path:
                edit.setText(path)
        button.clicked.connect(choose)
        layout.addWidget(edit, 1)
        layout.addWidget(button)
        form.addRow(label, row)

    def _number(self, form, key, label, minimum, maximum, decimals=None):
        field = QSpinBox() if decimals is None else QDoubleSpinBox()
        field.setRange(minimum, maximum)
        if decimals is not None:
            field.setDecimals(decimals)
        field.setValue(self.values.get(key, DEFAULTS[key]))
        self.fields[key] = field
        form.addRow(label, field)

    def result_values(self):
        result = dict(self.values)
        for key, widget in self.fields.items():
            if isinstance(widget, QCheckBox):
                result[key] = widget.isChecked()
            elif isinstance(widget, QComboBox):
                result[key] = widget.currentData()
            elif isinstance(widget, (QSpinBox, QDoubleSpinBox)):
                result[key] = widget.value()
            else:
                result[key] = widget.text().strip()
        result["allow_image_api"] = False
        result["max_calls"] = DEFAULTS["max_calls"]
        return result

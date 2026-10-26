from __future__ import annotations

import importlib.util
import shutil
import sys
from pathlib import Path

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFileDialog, QFormLayout,
    QFrame, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QPushButton, QScrollArea, QSpinBox,
    QTabWidget, QVBoxLayout, QWidget,
)

_MODEL_ROOT = (Path(sys.executable).resolve().parents[2] if getattr(sys, "frozen", False)
               else Path(__file__).resolve().parents[3]) / "models" / "speakers"


def _available_model(name):
    model = _MODEL_ROOT / name
    return str(model) if model.is_file() else ""


DEFAULTS = {
    "text_provider": "manual", "api_base": "https://api.deepseek.com",
    "model": "deepseek-flash", "allow_text_api": False, "allow_image_api": False,
    "whisper_model_path": "", "whisper_device": "cpu", "whisper_compute_type": "int8",
    "diarization_enabled": False, "diarization_segmentation_path": _available_model("segmentation.onnx"),
    "diarization_embedding_path": _available_model("embedding.onnx"), "diarization_num_speakers": 0,
    "voice_clone_api_url": "http://127.0.0.1:9880", "voice_clone_model": "default",
    "ffmpeg_path": "", "ffprobe_path": "", "voice": "", "speech_rate": 0,
    "llama_cli_path": "", "llama_model_path": "", "subtitle_position": "bottom",
    "budget_cny": 5.0, "max_calls": 100,
    "input_price_per_million": 2.0, "output_price_per_million": 8.0,
    "remember_api_key": False,
}


def load_settings() -> dict:
    store = QSettings("MathBridge", "MathBridge")
    result = {}
    for key, default in DEFAULTS.items():
        value = store.value(key, default)
        if key in ("diarization_segmentation_path", "diarization_embedding_path") and not value:
            value = default
        if isinstance(default, bool):
            value = value is True or str(value).lower() == "true"
        elif isinstance(default, (int, float)):
            try:
                value = type(default)(value)
            except (ValueError, TypeError):
                value = default
        result[key] = value
    return result


def save_settings(values: dict) -> None:
    store = QSettings("MathBridge", "MathBridge")
    for key in DEFAULTS:
        if key in values:
            store.setValue(key, values[key])
    store.sync()


class SettingsDialog(QDialog):
    def __init__(self, values: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle("处理设置 · 本地模型与 API")
        self.setMinimumSize(680, 610)
        self.values = dict(values)
        self.fields = {}
        layout = QVBoxLayout(self)
        tabs = QTabWidget()
        layout.addWidget(tabs)

        local = QWidget()
        form = QFormLayout(local)
        intro = QLabel("模型需提前准备。应用不会在识别或翻译时自动下载模型。")
        intro.setWordWrap(True)
        intro.setObjectName("description")
        form.addRow(intro)
        self._path(form, "whisper_model_path", "Whisper 本地模型目录", directory=True)
        self._combo(form, "whisper_device", "运算设备", [("CPU（当前验证基线）", "cpu")])
        self._combo(form, "whisper_compute_type", "Whisper 精度", [("int8", "int8")])
        diarize = QCheckBox("自动处理时，在转写后区分说话人")
        diarize.setChecked(bool(values.get("diarization_enabled", False)))
        self.fields["diarization_enabled"] = diarize
        form.addRow(diarize)
        self._path(form, "diarization_segmentation_path", "说话人分段 ONNX 模型")
        self._path(form, "diarization_embedding_path", "声纹 ONNX 模型")
        self._number(form, "diarization_num_speakers", "说话人数（0 = 自动判断）", 0, 100)
        speaker_hint = QLabel("自动区分需要本地 sherpa-onnx 组件和两份模型；未配置时仍可手动添加、分配说话人。多人同时讲话的片段需人工校对。")
        speaker_hint.setWordWrap(True)
        form.addRow(speaker_hint)
        self._path(form, "ffmpeg_path", "FFmpeg 可执行文件")
        self._path(form, "ffprobe_path", "FFprobe 可执行文件")
        local_hint = QLabel("离线文本模式需提前启动本机 llama.cpp 服务，并在 API 地址中填写 http://127.0.0.1:端口/v1；当前版本不会自动启动服务。")
        local_hint.setWordWrap(True)
        form.addRow(local_hint)
        state = []
        for label, module in [("faster-whisper", "faster_whisper"), ("说话人区分", "sherpa_onnx"), ("PDF 排版", "reportlab")]:
            state.append(f"{label}：{'已安装' if importlib.util.find_spec(module) else '未安装'}")
        state.append(f"系统 FFmpeg：{shutil.which('ffmpeg') or '未在 PATH 中找到（可使用内置工具）'}")
        form.addRow(QLabel("\n".join(state)))
        tabs.addTab(self._scroll(local), "本地处理")

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
        self._number(form, "budget_cny", "项目 API 预算（元）", 0.01, 100000, 2)
        self._number(form, "max_calls", "最多 API 调用次数", 1, 100000)
        self._number(form, "input_price_per_million", "输入估价（元 / 百万 token）", 0, 10000, 4)
        self._number(form, "output_price_per_million", "输出估价（元 / 百万 token）", 0, 10000, 4)
        explanation = QLabel("API 任务由你点击后运行。未授权时不会发送文本；未勾选保存时密钥仅留在本次会话。价格由你按账户计费规则填写，用量为估算，以服务商账单为准。视觉截图和参考声音上传尚未开放。")
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
        self._combo(form, "subtitle_position", "字幕位置", [("底部", "bottom"), ("顶部", "top")])
        note = QLabel("每位说话人的声音在“03 配音”单独配置。原声音色需先准备 GPT-SoVITS 模型并启动本机服务，仅接受本机回环地址。克隆效果取决于参考片段和模型，需试听确认。")
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
        edit.setPlaceholderText("未配置")
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
        return result

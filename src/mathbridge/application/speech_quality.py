"""Check actual synthesized speech before accepting a cache or exporting it."""
from __future__ import annotations

from array import array
from difflib import SequenceMatcher
import math
from pathlib import Path
import re
import sys
import wave

from mathbridge.media.engine import CancelledError, MediaError

VERSION = "speech-content-2"


def signal_quality(path, cancel=None):
    """Reject inaudible decoder noise, including the model's nonzero mute output."""
    squares = count = peak = active_ms = 0
    with wave.open(str(path), "rb") as audio:
        rate, channels, width = audio.getframerate(), audio.getnchannels(), audio.getsampwidth()
        if audio.getcomptype() != "NONE" or width not in (1, 2, 3, 4):
            raise MediaError("INVALID_AUDIO", "配音内容检查需要 PCM WAV。")
        duration = audio.getnframes() * 1000 / rate
        while raw := audio.readframes(max(1, rate // 50)):
            if cancel is not None and cancel.is_set():
                raise CancelledError()
            if width in (2, 4):
                values = array("h" if width == 2 else "i")
                values.frombytes(raw)
                if sys.byteorder != "little":
                    values.byteswap()
                scale = 1 if width == 2 else 65536
                values = [v / scale for v in values]
            elif width == 1:
                values = [(v - 128) * 256 for v in raw]
            else:
                values = [int.from_bytes(raw[i:i+3], "little", signed=True) / 256
                          for i in range(0, len(raw), 3)]
            total = sum(v * v for v in values)
            squares += total
            count += len(values)
            peak = max(peak, max(map(abs, values), default=0))
            if math.sqrt(total / max(1, len(values))) >= 32:
                active_ms += len(values) * 1000 / (rate * channels)
    rms = math.sqrt(squares / max(1, count))
    if not count or peak < 128 or rms < 12 or active_ms < 60:
        raise MediaError("TTS_SILENT", "声音模型返回了静音或几乎只有底噪，未接受为成功配音。")
    return dict(version=VERSION, method="signal", duration_ms=round(duration, 3),
                rms=round(rms, 3), peak=round(peak, 3), active_ms=round(active_ms, 3))


def _simplified(text):
    # Windows supplies script conversion without downloading a language package.
    if sys.platform == "win32":
        import ctypes
        output = ctypes.create_unicode_buffer(len(text) * 2 + 2)
        if ctypes.windll.kernel32.LCMapStringEx("zh-CN", 0x02000000, text, len(text),
                                               output, len(output), None, None, 0):
            return output.value
    return text


def _chinese_number(match):
    text = match[0]
    digits = dict(zip("零〇一二两三四五六七八九", [0,0,1,2,2,3,4,5,6,7,8,9]))
    units = {"十":10,"百":100,"千":1000,"万":10000,"亿":100000000}
    if all(c in digits for c in text):
        return "".join(str(digits[c]) for c in text)
    total = section = number = 0
    for char in text:
        if char in digits:
            number = digits[char]
        elif units[char] < 10000:
            section += (number or 1) * units[char]
            number = 0
        else:
            section += number
            total = (total + section) * units[char] if char == "亿" else total + section * units[char]
            section = number = 0
    return str(total + section + number)


def normalized_chinese(text):
    text = re.sub(r"[零〇一二两三四五六七八九十百千万亿]+", _chinese_number, _simplified(text))
    text = re.sub(r"(?<=\d)点(?=\d)", ".", text)
    return "".join(re.findall(r"[\u3400-\u9fff0-9]", text))


def compare_speech(expected, heard, language):
    """Detect large omissions, tolerating punctuation, numerals and ASR errors.

    This is a screening result, not a claim of word-for-word correctness.
    Spoken Chinese numerals and ASR digits share a common representation.
    """
    chinese = language.lower().startswith("zh")
    if chinese:
        source, result = normalized_chinese(expected), normalized_chinese(heard)
        minimum, edge = 8, 5
    else:
        source, result = (re.findall(r"[a-z]+", t.casefold()) for t in (expected, heard))
        minimum, edge = 8, 4
    blocks = SequenceMatcher(None, source, result, autojunk=False).get_matching_blocks()
    matches = sum(b.size for b in blocks)
    coverage = matches / max(1, len(source))
    reliable = [b for b in blocks if b.size >= 2]
    prefix = reliable[0].a if reliable else len(source)
    suffix = len(source) - reliable[-1].a - reliable[-1].size if reliable else len(source)
    omitted_edge = max(prefix, suffix) >= edge and len(result) < len(source) * .85 and coverage < .85
    # Several explicit numbers missing from an otherwise short equation matter
    # even when its total text is below the normal long-utterance threshold.
    expected_numbers = re.findall(r"\d+", source) if chinese else []
    heard_numbers = re.findall(r"\d+", result) if chinese else []
    missing_numbers = (len(source) <= 10 and len(expected_numbers) >= 2
                       and len(heard_numbers) < len(expected_numbers) and coverage < .8
                       and len(result) < len(source) * .8)
    incomplete = len(source) >= minimum and (coverage < .55 or omitted_edge) or missing_numbers
    return dict(heard=heard.strip(), coverage=round(coverage, 4),
                suspected_omission=incomplete, missing_prefix=prefix, missing_suffix=suffix,
                missing_numbers=missing_numbers)


class SpeechVerifier:
    """Reuse one offline recognizer for all clips in a worker, never a UI thread."""
    def __init__(self, settings, cancel=None):
        self.settings, self.cancel, self.model = settings, cancel, None
        root = Path(settings.get("whisper_model_path") or "")
        self.can_recognize = bool(settings.get("whisper_model_path") and (root / "model.bin").is_file())

    def verify(self, path, text, language):
        quality = signal_quality(path, self.cancel)
        if not self.can_recognize or not language.lower().startswith(("zh", "en")):
            return quality
        if self.cancel is not None and self.cancel.is_set():
            raise CancelledError()
        import numpy as np
        if self.model is None:
            from faster_whisper import WhisperModel
            self.model = WhisperModel(self.settings["whisper_model_path"], device="cpu", compute_type="int8",
                                      cpu_threads=8, local_files_only=True)
        # Input arrays avoid the optional PyAV decoder and its version-specific API.
        with wave.open(str(path), "rb") as audio:
            if audio.getsampwidth() != 2:
                return quality
            data = np.frombuffer(audio.readframes(audio.getnframes()), "<i2").astype(np.float32)
            data = data.reshape(-1, audio.getnchannels()).mean(axis=1) / 32768
            rate = audio.getframerate()
        if rate != 16000:
            data = np.interp(np.arange(round(len(data) * 16000 / rate)) * rate / 16000,
                             np.arange(len(data)), data).astype(np.float32)
        lang = "zh" if language.lower().startswith("zh") else "en"
        pieces, _ = self.model.transcribe(data, language=lang, beam_size=5, vad_filter=False,
                                          condition_on_previous_text=False)
        heard = []
        for piece in pieces:
            if self.cancel is not None and self.cancel.is_set():
                raise CancelledError()
            heard.append(piece.text)
        comparison = compare_speech(text, "".join(heard), language)
        if comparison["suspected_omission"]:
            error = MediaError("TTS_INCOMPLETE", "本地回听发现配音疑似漏读。应读：" + text[:180]
                               + "；识别到：" + (comparison["heard"][:180] or "未识别到语音"))
            error.quality = dict(quality, method="local-asr", **comparison)
            raise error
        return dict(quality, method="local-asr", **comparison)

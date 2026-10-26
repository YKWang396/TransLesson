"""Windows SAPI voices. Text enters PowerShell as UTF-8 JSON stdin.

SAPI COM also works on machines where System.Speech's installed-voice wrapper
fails. Only voices actually enumerated by SAPI are offered; no registry edits.
"""
from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
import uuid
import wave
import xml.etree.ElementTree as ET

from ...media.engine import CancelledError, MediaError


_PREFIX = r"""
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Console]::InputEncoding = New-Object System.Text.UTF8Encoding($false)
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$synth = New-Object -ComObject SAPI.SpVoice
"""


def _powershell(script, payload=None, cancel=None, timeout=180):
    if os.name != "nt":
        raise MediaError("LANGUAGE_UNAVAILABLE", "系统配音需要 Windows 已安装的 System.Speech 声音。")
    executable = shutil.which("powershell.exe") or shutil.which("powershell")
    if not executable:
        raise MediaError("MISSING_DEPENDENCY", "未找到 Windows PowerShell。")
    if cancel is not None and cancel.is_set():
        raise CancelledError()
    encoded = base64.b64encode((_PREFIX + script).encode("utf-16-le")).decode("ascii")
    process = subprocess.Popen([executable, "-NoProfile", "-NonInteractive", "-Sta", "-EncodedCommand", encoded],
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               creationflags=subprocess.CREATE_NO_WINDOW)
    request = json.dumps(payload or {}, ensure_ascii=False).encode("utf-8")
    started, first = time.monotonic(), True
    try:
        while True:
            if cancel is not None and cancel.is_set():
                raise CancelledError()
            if time.monotonic() - started > timeout:
                raise MediaError("PROVIDER_TIMEOUT", "Windows 系统配音超时。")
            try:
                out, err = process.communicate(input=request if first else None, timeout=.2)
                break
            except subprocess.TimeoutExpired:
                first = False
        if process.returncode:
            try:
                error = err.decode("utf-8", errors="strict")
            except UnicodeDecodeError:
                error = err.decode("mbcs", errors="replace")
            if "<Objs" in error:
                try:
                    xml = ET.fromstring(error[error.index("<Objs"):])
                    error = "\n".join(node.text or "" for node in xml.iter()
                                      if node.attrib.get("S") == "Error")
                    error = error.replace("_x000D_", "").replace("_x000A_", "\n")
                except ET.ParseError:
                    pass
            code = "LANGUAGE_UNAVAILABLE" if "LANGUAGE_UNAVAILABLE" in error else "TTS_FAILED"
            raise MediaError(code, error[-2500:])
        return out.decode("utf-8-sig", errors="replace").strip()
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


def list_voices() -> list[dict]:
    out = _powershell(r"""
try {
  $items = @($synth.GetVoices() | ForEach-Object {
    $lcid = [Convert]::ToInt32($_.GetAttribute('Language').Split(';')[0], 16)
    @{id=$_.Id; name=$_.GetDescription(); language=[System.Globalization.CultureInfo]::GetCultureInfo($lcid).Name}
  })
  ConvertTo-Json -InputObject $items -Compress
} finally { [void][System.Runtime.InteropServices.Marshal]::FinalReleaseComObject($synth) }
""")
    value = json.loads(out or "[]")
    return value if isinstance(value, list) else [value]


def capabilities():
    voices = list_voices()
    return {"provider": "windows-sapi", "languages": sorted({v["language"] for v in voices}),
            "cross_language_reference": False, "reference_duration_seconds": None,
            "device": "cpu", "cancellation": "subprocess", "audio_formats": ["wav"],
            "voices": voices}


def synthesize(text, language, voice, dest, cancel=None, *, rate=0) -> Path:
    if not isinstance(text, str) or not text.strip():
        raise MediaError("INVALID_TRANSLATION", "配音读法不能为空。")
    if len(text) > 20000:
        raise MediaError("TTS_TOO_LONG", "请按语义片段配音，每段读法不得超过 20,000 字符。")
    if not isinstance(rate, int) or not -10 <= rate <= 10:
        raise MediaError("INVALID_VOICE", "系统声音语速应为 -10 至 10 的整数。")
    dest = Path(dest).resolve()
    dest.parent.mkdir(parents=True, exist_ok=True)
    temporary = dest.with_name(f".{dest.stem}-{uuid.uuid4().hex}.tmp.wav")
    voice_id = voice.get("id", "") if isinstance(voice, dict) else (voice or "")
    script = r"""
$payload = [Console]::In.ReadToEnd() | ConvertFrom-Json
try {
  $lang = ([string]$payload.language).Split('-')[0]
  $available = @($synth.GetVoices() | Where-Object {
    $lcid = [Convert]::ToInt32($_.GetAttribute('Language').Split(';')[0], 16)
    [System.Globalization.CultureInfo]::GetCultureInfo($lcid).TwoLetterISOLanguageName -eq $lang
  })
  if ($payload.voice) {
    $available = @($available | Where-Object { $_.Id -eq $payload.voice -or $_.GetDescription() -eq $payload.voice })
  }
  if ($available.Count -eq 0) { throw 'LANGUAGE_UNAVAILABLE: No installed voice matches target language.' }
  $synth.Voice = $available[0]
  $synth.Rate = [int]$payload.rate
  $format = New-Object -ComObject SAPI.SpAudioFormat
  $format.Type = 26
  $stream = New-Object -ComObject SAPI.SpFileStream
  $stream.Format = $format
  $stream.Open([string]$payload.dest, 3, $false)
  $synth.AudioOutputStream = $stream
  # SVSFIsNotXML: course/model text is data, never interpreted as SSML.
  [void]$synth.Speak([string]$payload.text, 16)
  $stream.Close()
} finally {
  if ($stream) { [void][System.Runtime.InteropServices.Marshal]::FinalReleaseComObject($stream) }
  if ($format) { [void][System.Runtime.InteropServices.Marshal]::FinalReleaseComObject($format) }
  [void][System.Runtime.InteropServices.Marshal]::FinalReleaseComObject($synth)
}
"""
    try:
        _powershell(script, {"text": text, "language": language, "voice": voice_id,
                             "dest": str(temporary), "rate": rate}, cancel)
        with wave.open(str(temporary), "rb") as audio:
            if not audio.getnframes() or not audio.getframerate():
                raise MediaError("TTS_FAILED", "系统声音没有生成有效 WAV。")
        if cancel is not None and cancel.is_set():
            raise CancelledError()
        os.replace(temporary, dest)
    finally:
        temporary.unlink(missing_ok=True)
    return dest

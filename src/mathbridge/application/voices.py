"""Extract reproducible reference audio from the normalized source timeline."""
from __future__ import annotations

import json
import os
from pathlib import Path
import uuid
import wave

from mathbridge.domain.project import file_hash, fingerprint, safe_path
from mathbridge.storage.project_store import atomic_json


def prepare_reference(project, speaker, root, source_audio, cancel=None) -> Path:
    profile = speaker.get("voice_profile", {})
    start, end = profile.get("reference_start_ms", 0), profile.get("reference_end_ms", 0)
    if not isinstance(start, int) or not isinstance(end, int) or not 0 <= start < end <= project["source"]["duration_ms"]:
        raise ValueError("VOICE_REFERENCE_INVALID: 请设置视频内的参考声音起止时间")
    if not 3000 <= end - start <= 10000:
        raise ValueError("VOICE_REFERENCE_INVALID: 请选择 3–10 秒清晰、连续的单人原声")
    fp = fingerprint(dict(source_sha256=project["source"]["sha256"], start_ms=start, end_ms=end, format="pcm16-mono-16k"))
    dest = safe_path(root, f"audio/references/{fp}.wav")
    marker = dest.with_suffix(".json")
    try:
        cached = json.loads(marker.read_text(encoding="utf-8"))
        if isinstance(cached, dict) and cached.get("input_fingerprint") == fp and dest.is_file() and cached.get("sha256") == file_hash(dest, cancel):
            return dest
    except (OSError, ValueError):
        pass
    if cancel is not None and cancel.is_set():
        raise InterruptedError("参考声音提取已取消")
    dest.parent.mkdir(parents=True, exist_ok=True)
    temporary = dest.with_name("." + uuid.uuid4().hex + ".wav")
    try:
        with wave.open(str(source_audio), "rb") as source:
            if (source.getnchannels(), source.getsampwidth(), source.getframerate(), source.getcomptype()) != (1, 2, 16000, "NONE"):
                raise ValueError("VOICE_REFERENCE_INVALID: 原声必须先转换为 16kHz 单声道 PCM")
            first, count = start * 16, (end - start) * 16
            if first + count > source.getnframes():
                raise ValueError("VOICE_REFERENCE_INVALID: 参考声音超出源音轨")
            source.setpos(first)
            data = source.readframes(count)
            if len(data) != count * 2:
                raise ValueError("VOICE_REFERENCE_INVALID: 源音轨内容被截断，请重新提取音轨")
            with wave.open(str(temporary), "wb") as output:
                output.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
                output.writeframes(data)
        if cancel is not None and cancel.is_set():
            raise InterruptedError("参考声音提取已取消")
        os.replace(temporary, dest)
        atomic_json(marker, dict(input_fingerprint=fp, sha256=file_hash(dest, cancel), speaker_id=speaker["id"],
                                 start_ms=start, end_ms=end, source_sha256=project["source"]["sha256"]))
    finally:
        temporary.unlink(missing_ok=True)
    return dest

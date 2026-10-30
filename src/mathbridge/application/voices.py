"""Extract reproducible reference audio from the normalized source timeline."""
from __future__ import annotations

import json
import os
from pathlib import Path
import uuid
import wave

from mathbridge.domain.project import file_hash, fingerprint, safe_path
from mathbridge.storage.project_store import atomic_json


def prepare_reference(project, speaker, root, source_audio, cancel=None, *, source_media=None, media_engine=None) -> Path:
    profile = speaker.get("voice_profile", {})
    start, end = profile.get("reference_start_ms", 0), profile.get("reference_end_ms", 0)
    if not isinstance(start, int) or not isinstance(end, int) or not 0 <= start < end <= project["source"]["duration_ms"]:
        raise ValueError("VOICE_REFERENCE_INVALID: 请设置视频内的参考声音起止时间")
    if not 3000 <= end - start <= 10000:
        raise ValueError("VOICE_REFERENCE_INVALID: 请选择 3–10 秒清晰、连续的单人原声")
    high_quality = source_media is not None and media_engine is not None
    fp = fingerprint(dict(source_sha256=project["source"]["sha256"], start_ms=start, end_ms=end,
                          format="pcm16-mono-32k-original-v1" if high_quality else "pcm16-mono-16k"))
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
        if high_quality:
            # ASR is deliberately 16 kHz. Timbre conditioning instead retains
            # the original source bandwidth, using the model's 32 kHz input.
            media_engine._ffmpeg(["-i",str(source_media),"-ss",f"{start/1000:.3f}","-t",f"{(end-start)/1000:.3f}",
                                  "-vn","-ar","32000","-ac","1","-c:a","pcm_s16le",temporary],stage="speaker_reference")
            with wave.open(str(temporary),"rb") as reference:
                if abs(reference.getnframes()*1000/reference.getframerate()-(end-start)) > 2:
                    raise ValueError("VOICE_REFERENCE_INVALID: 原始音轨参考片段不完整")
        else:
            _extract_normalized(source_audio,temporary,start,end)
        if cancel is not None and cancel.is_set():
            raise InterruptedError("参考声音提取已取消")
        os.replace(temporary, dest)
        atomic_json(marker, dict(input_fingerprint=fp, sha256=file_hash(dest, cancel), speaker_id=speaker["id"],
                                 start_ms=start, end_ms=end, source_sha256=project["source"]["sha256"]))
    finally:
        temporary.unlink(missing_ok=True)
    return dest


def _extract_normalized(source_audio,temporary,start,end):
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

def prepare_auxiliary_references(project,speaker,root,source_audio,cancel=None,**kwargs):
    result = []
    for reference in speaker.get("voice_profile",{}).get("auxiliary_references",[]):
        profile = dict(reference_start_ms=reference["start_ms"],reference_end_ms=reference["end_ms"])
        result.append(prepare_reference(project,dict(speaker,voice_profile=profile),root,source_audio,cancel,**kwargs))
    return result

"""Reuse natural TTS and adapt quiet intervals without changing course text."""
from __future__ import annotations

import json
from pathlib import Path
import wave

from mathbridge.domain.project import file_hash, fingerprint, safe_path, segment_fingerprint
from mathbridge.media.rhythm import VERSION, analyze_wave, compact_pauses, profile_for
from mathbridge.storage.project_store import atomic_json


def timing_path(root, project, segment, settings):
    key = fingerprint(dict(version=VERSION, tts=segment_fingerprint(project,segment,settings),
                           source=project["source"]["sha256"],start=segment["start_ms"],end=segment["end_ms"]))
    return safe_path(Path(root),f"audio/tts-timing/{key}.wav")


def prepare_timing(project, settings, root, clips, source_audio, cancel=None, progress=None):
    root = Path(root)
    key = fingerprint(dict(version=VERSION,source=project["source"]["sha256"]))
    profile_path = safe_path(root,"audio/source-rhythm.json")
    try:
        source = json.loads(profile_path.read_text(encoding="utf8"))
        if source.get("input_fingerprint") != key:
            raise ValueError()
    except (OSError,ValueError,AttributeError):
        source = analyze_wave(source_audio,cancel,noise_aware=True)
        source["input_fingerprint"] = key
        atomic_json(profile_path,source)
    segments = {s["id"]:s for s in project["segments"]}
    result = []
    for i,clip in enumerate(clips):
        if progress:
            progress("dub",i,len(clips),"测量译声，保留语音并调整停顿")
        segment = segments[clip["segment_id"]]
        raw = Path(clip["path"])
        dest = timing_path(root,project,segment,settings)
        raw_sha = file_hash(raw,cancel)
        try:
            cached = json.loads(dest.with_suffix(".json").read_text(encoding="utf8"))
            if cached["raw_sha256"] != raw_sha or cached["sha256"] != file_hash(dest,cancel):
                raise ValueError()
        except (OSError,ValueError,KeyError,TypeError):
            with wave.open(str(raw),"rb") as audio:
                duration = audio.getnframes()*1000/audio.getframerate()
            profile = profile_for(source,segment["start_ms"],segment["end_ms"])
            dest.parent.mkdir(parents=True,exist_ok=True)
            if duration > segment["end_ms"]-segment["start_ms"]:
                compact = compact_pauses(raw,dest,analyze_wave(raw,cancel),profile)
                if not dest.exists():
                    # A silent recording is preserved and reported, never deleted.
                    import shutil
                    shutil.copyfile(raw,dest)
            else:
                import shutil
                shutil.copyfile(raw,dest)
                compact = dict(saved_ms=0,duration_ms=duration,pause_count=0)
            cached = dict(raw_sha256=raw_sha,sha256=file_hash(dest,cancel),version=VERSION,
                          original_speech_ms=duration,source_profile=profile,**compact)
            atomic_json(dest.with_suffix(".json"),cached)
        result.append(dict(clip,path=str(dest),speech_ms=cached["duration_ms"],
                           original_speech_ms=cached["original_speech_ms"],pause_saved_ms=cached["saved_ms"]))
    return result

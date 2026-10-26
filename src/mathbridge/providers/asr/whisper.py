"""Local-only faster-whisper adapter. No model name or implicit download."""
from __future__ import annotations

import hashlib
import importlib.metadata
import inspect
import json
import math
import os
from pathlib import Path
import uuid

from ...media.engine import CancelledError, MediaError


_PARAMETERS = {"device": "cpu", "compute_type": "int8", "task": "transcribe",
               "word_timestamps": True, "vad_filter": False,
               "condition_on_previous_text": False, "beam_size": 5}


def _digest(value):
    serialized = json.dumps(value, sort_keys=True, ensure_ascii=False,
                            separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _hash_file(path, check):
    before = path.stat()
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            check()
            digest.update(chunk)
    after = path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise MediaError("SOURCE_CHANGED", f"校验期间文件发生变化：{path.name}")
    return digest.hexdigest()


def _installed_version(name):
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return "unavailable"


def _inputs(audio, model_path, language, check):
    check()
    files = []
    for path in sorted(model_path.rglob("*")):
        check()
        if path.is_file():
            files.append({"path": path.relative_to(model_path).as_posix(),
                          "sha256": _hash_file(path, check)})
    return {"audio_sha256": _hash_file(audio, check), "model_files": files,
            "language": language, "provider": "faster-whisper",
            "provider_version": _installed_version("faster-whisper"),
            "ctranslate2_version": _installed_version("ctranslate2"),
            "adapter_version": "mathbridge-asr-checkpoint-1", "parameters": _PARAMETERS}


def _atomic_checkpoint(path, payload):
    """Commit a complete safe unit even when cancellation arrives during yield."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}-{uuid.uuid4().hex}.tmp")
    value = {**payload, "checkpoint_checksum": _digest(payload)}
    try:
        with temporary.open("x", encoding="utf-8", newline="\n") as output:
            json.dump(value, output, ensure_ascii=False, indent=2, allow_nan=False)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _core_segment(raw, index, duration_ms):
    if not isinstance(raw, dict) or raw.get("segment_id") != f"seg-{index}":
        raise ValueError("Invalid checkpoint segment ID")
    if not isinstance(raw.get("text"), str) or not raw["text"].strip():
        raise ValueError("Invalid checkpoint transcript")
    if not all(_number(raw.get(key)) for key in ("start", "end", "avg_logprob", "no_speech_prob")):
        raise ValueError("Invalid checkpoint numeric evidence")
    if not (0 <= raw["start"] < raw["end"] <= duration_ms / 1000 + .5):
        raise ValueError("Invalid checkpoint timeline")
    if not 0 <= raw["no_speech_prob"] <= 1 or not isinstance(raw.get("words"), list):
        raise ValueError("Invalid checkpoint speech evidence")
    for word in raw["words"]:
        if (not isinstance(word, dict) or not isinstance(word.get("word"), str)
                or not all(_number(word.get(key)) for key in ("start", "end", "probability"))
                or not 0 <= word["start"] <= word["end"] <= duration_ms / 1000 + .5
                or not 0 <= word["probability"] <= 1):
            raise ValueError("Invalid checkpoint word evidence")
    start = max(0, round(raw["start"] * 1000))
    end = min(duration_ms, round(raw["end"] * 1000))
    if end <= start:
        raise ValueError("Empty checkpoint segment")
    return {"id": raw["segment_id"], "speaker_id": "teacher", "start_ms": start,
            "end_ms": end, "source_text": raw["text"].strip(), "target_text": "",
            "subtitle_plain": "", "tts_text": "", "formula_ids": [],
            "term_ids": [], "review": "needs_review"}


def _load_checkpoint(path, inputs):
    if not path.is_file():
        return None
    try:
        if path.stat().st_size > 64 * 1024 * 1024:
            raise ValueError("Checkpoint exceeds size limit")
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("Invalid checkpoint object")
        checksum = data.pop("checkpoint_checksum", None)
        if checksum != _digest(data):
            raise ValueError("Checkpoint checksum mismatch")
        if (data.get("schema_version") != 1 or data.get("inputs") != inputs
                or data.get("input_fingerprint") != _digest(inputs)
                or not isinstance(data.get("complete"), bool)
                or not isinstance(data.get("segments"), list)):
            raise ValueError("Checkpoint does not match current input")
        duration = data.get("duration_ms")
        if (not isinstance(duration, int) or isinstance(duration, bool) or duration <= 0):
            if data["segments"] or data["complete"] or duration is not None:
                raise ValueError("Invalid checkpoint duration")
        result = [_core_segment(raw, index + 1, duration) for index, raw in enumerate(data["segments"])]
        if any(current["start_ms"] < previous["end_ms"] for previous, current in zip(result, result[1:])):
            raise ValueError("Overlapping checkpoint segments")
        expected_resume = min(data["segments"][-1]["end"], duration / 1000) if result else 0.0
        if data.get("resume_from_seconds") != expected_resume:
            raise ValueError("Invalid checkpoint resume cursor")
        return data, result
    except (ValueError, TypeError, KeyError, UnicodeError):
        # Retain invalid evidence for inspection; never pretend it is reusable.
        rejected = path.with_name(f"{path.stem}.rejected-{uuid.uuid4().hex}{path.suffix}")
        os.replace(path, rejected)
        return None


def transcribe(audio, language, model_path, cancel=None, progress=None, *, raw_output=None):
    """Return core segments and atomically checkpoint each yielded ASR segment.

    Cancellation is checked between model segments. The worker process remains
    responsible for terminating an inference call that does not yield promptly.
    With ``raw_output``, verified prefixes resume using ``clip_timestamps`` on
    the original audio. Faster-whisper 1.2.1's ``generate_segments`` calculates
    time_offset from the original seek position, so no offset is added here.
    Restarted decoding can differ near a chunk boundary and still needs review.
    """
    def check():
        if cancel is not None and cancel.is_set():
            raise CancelledError()

    check()
    audio = Path(audio).resolve()
    model_path = Path(model_path).resolve() if model_path else None
    if not audio.is_file():
        raise MediaError("SOURCE_MISSING", "待识别音频不存在。")
    required = ("model.bin", "config.json", "tokenizer.json")
    if model_path is None or not model_path.is_dir() or any(not (model_path / name).is_file() for name in required):
        raise MediaError("MISSING_MODEL", "请导入完整本地 CTranslate2 多语种 Whisper 模型（model.bin/config.json/tokenizer.json），不会自动下载。")
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise MediaError("MISSING_DEPENDENCY", "未安装可选 faster-whisper 识别组件。") from exc
    language = language.split("-")[0].lower()
    if language not in ("zh", "en"):
        raise MediaError("LANGUAGE_UNAVAILABLE", "首版已启用中文与英语识别。")
    checkpoint = Path(raw_output).resolve() if raw_output is not None else None
    inputs = _inputs(audio, model_path, language, check) if checkpoint else None
    state = {"schema_version": 1, "provider": "faster-whisper", "language": language,
             "model_path": str(model_path), "inputs": inputs,
             "input_fingerprint": _digest(inputs) if inputs is not None else None,
             "complete": False, "duration_ms": None, "resume_from_seconds": 0.0,
             "resume_policy": "clip_timestamps_original_timeline", "segments": []}
    loaded = _load_checkpoint(checkpoint, inputs) if checkpoint else None
    if loaded is not None:
        state, result = loaded
        if state["complete"]:
            check()
            if progress:
                progress("asr", state["duration_ms"], state["duration_ms"], "已验证并复用完整识别结果")
            return result
    else:
        result = []
        if checkpoint:
            _atomic_checkpoint(checkpoint, state)
    raw_records = state["segments"]
    resume_from = state["resume_from_seconds"]
    if resume_from and "clip_timestamps" not in inspect.signature(WhisperModel.transcribe).parameters:
        # Keep the partial file intact if an incompatible provider is selected.
        raise MediaError("MISSING_DEPENDENCY", "已保留逐词识别检查点；此版本不支持 clip_timestamps，请更新 faster-whisper 后续跑。")
    try:
        model = WhisperModel(str(model_path), device="cpu", compute_type="int8", local_files_only=True)
        if language != "en" and not model.model.is_multilingual:
            raise MediaError("LANGUAGE_UNAVAILABLE", "所选模型只支持英语，请导入多语种模型。")
        check()
        # No VAD concatenation: leading/intermediate silence stays on video time.
        options = {key: value for key, value in _PARAMETERS.items() if key not in ("device", "compute_type")}
        if resume_from:
            options["clip_timestamps"] = [resume_from]
            if progress:
                progress("asr", round(resume_from * 1000), state["duration_ms"], f"复用已验证的 {len(result)} 段，从原音频时间继续")
        segments, info = model.transcribe(str(audio), language=language, **options)
        total_ms = round(info.duration * 1000)
        if total_ms <= 0:
            raise MediaError("UNSUPPORTED_MEDIA", "识别音频没有有效时长。")
        if state["duration_ms"] is not None and state["duration_ms"] != total_ms:
            raise MediaError("SOURCE_CHANGED", "音频解码时长与已验证的识别检查点不符，已保留原结果。")
        state["duration_ms"] = total_ms
        iterator = iter(segments)
        while True:
            check()
            try:
                segment = next(iterator)
            except StopIteration:
                break
            text = segment.text.strip()
            if not text:
                continue
            start = max(0, round(segment.start * 1000))
            end = min(total_ms, round(segment.end * 1000))
            if end <= start:
                continue
            identifier = f"seg-{len(result) + 1}"
            raw = {"segment_id": identifier, "text": segment.text,
                   "start": segment.start, "end": segment.end,
                   "avg_logprob": segment.avg_logprob, "no_speech_prob": segment.no_speech_prob,
                   "words": [{"start": w.start, "end": w.end, "word": w.word,
                              "probability": w.probability} for w in (segment.words or [])]}
            try:
                core = _core_segment(raw, len(result) + 1, total_ms)
                if result and core["start_ms"] < result[-1]["end_ms"]:
                    raise ValueError("Overlapping ASR segments")
            except ValueError as exc:
                raise MediaError("INVALID_TIMELINE", "识别返回无效或重叠时间；已保留此前逐词检查点，请校对后重试。") from exc
            result.append(core)
            raw_records.append(raw)
            state["resume_from_seconds"] = min(segment.end, total_ms / 1000)
            if checkpoint:
                _atomic_checkpoint(checkpoint, state)
            if progress:
                progress("asr", end, total_ms, f"已识别 {len(result)} 段")
        check()
        state["complete"] = True
        if checkpoint:
            _atomic_checkpoint(checkpoint, state)
    except MemoryError as exc:
        raise MediaError("OUT_OF_MEMORY", "识别模型内存不足，请使用较小的多语种模型。") from exc
    finally:
        if "model" in locals():
            del model
    return result

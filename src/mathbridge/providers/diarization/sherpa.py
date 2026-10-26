"""Local ONNX speaker turns, with no network or automatic model acquisition.

The provider follows sherpa-onnx's offline-speaker-diarization Python example:
https://github.com/k2-fsa/sherpa-onnx/blob/master/python-api-examples/offline-speaker-diarization.py
Speech recognition and diarization are separate evidence sources. Words are
split only when their original ASR evidence still matches the editable text.
"""
from __future__ import annotations

import copy
import math
from pathlib import Path
import re
import wave

from ...domain.project import validate_project
from ...media.engine import CancelledError, MediaError

_SPEAKER_ID = re.compile(r"[a-z][a-z0-9_-]{0,63}\Z")


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def diarize(audio, segmentation_model, embedding_model, *, num_speakers=0,
            threshold=0.5, cancel=None, progress=None):
    """Return ordered ``speaker_id/start_ms/end_ms`` turns from 16k mono PCM16.

    ``num_speakers=0`` requests automatic clustering; positive values supply a
    known speaker count. IDs are assigned by first audible appearance, with
    ``teacher`` first, then ``speaker-2``, etc. Overlapping speakers remain
    overlapping here: the assignment function marks that ambiguity for review.
    Cancellation is checked before loading, during waveform reads and at native
    progress callbacks; the worker owns termination of unresponsive native code.
    """
    def check():
        if cancel is not None and cancel.is_set():
            raise CancelledError()

    check()
    if (not isinstance(num_speakers, int) or isinstance(num_speakers, bool)
            or not 0 <= num_speakers <= 100):
        raise MediaError("INVALID_SPEAKER_COUNT", "说话人数须为 0（自动）至 100 的整数。")
    if not _number(threshold) or not 0 < threshold < 2:
        raise MediaError("INVALID_CLUSTER_THRESHOLD", "声线聚类阈值须大于 0 且小于 2。")
    models = []
    for label, value in (("说话人分段", segmentation_model), ("声线特征", embedding_model)):
        path = Path(value).resolve() if value else None
        if path is None or not path.is_file() or path.suffix.lower() != ".onnx" or path.stat().st_size == 0:
            raise MediaError("MISSING_MODEL", f"请先选择本地{label} ONNX 模型；程序不会自动下载。")
        models.append(path)
    audio = Path(audio).resolve()
    if not audio.is_file():
        raise MediaError("SOURCE_MISSING", "说话人分析音频不存在。")
    try:
        import numpy as np
        import sherpa_onnx
    except ImportError as exc:
        raise MediaError("MISSING_DEPENDENCY", "未安装 sherpa-onnx 多人说话人识别组件。") from exc
    try:
        with wave.open(str(audio), "rb") as source:
            if (source.getframerate(), source.getnchannels(), source.getsampwidth(), source.getcomptype()) != (16000, 1, 2, "NONE"):
                raise MediaError("INVALID_AUDIO", "说话人分析需要 16kHz、单声道、16-bit PCM WAV。")
            total_frames = source.getnframes()
            if not total_frames:
                raise MediaError("INVALID_AUDIO", "说话人分析音频为空。")
            samples = np.empty(total_frames, dtype=np.float32)
            position = 0
            while position < total_frames:
                check()
                chunk = source.readframes(min(16000 * 10, total_frames - position))
                pcm = np.frombuffer(chunk, dtype="<i2")
                if not pcm.size:
                    raise MediaError("INVALID_AUDIO", "说话人分析 WAV 内容被截断。")
                samples[position:position + pcm.size] = pcm.astype(np.float32) / 32768.0
                position += pcm.size
            duration_ms = round(total_frames / 16)
    except (wave.Error, EOFError, OSError) as exc:
        raise MediaError("INVALID_AUDIO", "无法读取说话人分析 WAV。") from exc
    check()
    if progress:
        progress("diarize", 0, 1, "正在加载本地说话人模型")
    try:
        config = sherpa_onnx.OfflineSpeakerDiarizationConfig(
            segmentation=sherpa_onnx.OfflineSpeakerSegmentationModelConfig(
                pyannote=sherpa_onnx.OfflineSpeakerSegmentationPyannoteModelConfig(model=str(models[0])),
                num_threads=2, provider="cpu"),
            embedding=sherpa_onnx.SpeakerEmbeddingExtractorConfig(
                model=str(models[1]), num_threads=2, provider="cpu"),
            clustering=sherpa_onnx.FastClusteringConfig(
                num_clusters=num_speakers if num_speakers else -1, threshold=float(threshold)),
            min_duration_on=0.3, min_duration_off=0.5)
        if not config.validate():
            raise MediaError("INVALID_MODEL", "说话人 ONNX 模型配置无效，请核对两个模型文件。")
        pipeline = sherpa_onnx.OfflineSpeakerDiarization(config)
        if pipeline.sample_rate != 16000:
            raise MediaError("INVALID_MODEL", "所选说话人模型不使用 16kHz 音频。")
        check()

        def callback(completed, total):
            if cancel is not None and cancel.is_set():
                return 1
            if progress:
                progress("diarize", completed, max(total, 1), "正在区分视频中的说话人")
            return 0

        native = pipeline.process(samples, callback=callback).sort_by_start_time()
        check()
        raw_turns = []
        for turn in native:
            if (not _number(turn.start) or not _number(turn.end)
                    or not isinstance(turn.speaker, int) or turn.speaker < 0
                    or not 0 <= turn.start < turn.end <= total_frames / 16000 + .5):
                raise MediaError("INVALID_DIARIZATION", "说话人模型返回了无效时间或声线编号。")
            start = max(0, round(turn.start * 1000))
            end = min(duration_ms, round(turn.end * 1000))
            if start < end:
                raw_turns.append((start, end, turn.speaker))
        mapping = {}
        result = []
        for start, end, speaker in sorted(raw_turns):
            if speaker not in mapping:
                mapping[speaker] = "teacher" if not mapping else f"speaker-{len(mapping) + 1}"
            result.append({"speaker_id": mapping[speaker], "start_ms": start, "end_ms": end})
        if progress:
            progress("diarize", 1, 1, f"已检测到 {len(mapping)} 位说话人，请校对声线归属")
        return result
    except (MediaError, MemoryError):
        raise
    except Exception as exc:
        check()
        raise MediaError("DIARIZATION_FAILED", "本地说话人分析失败，请检查 ONNX 模型是否兼容。") from exc


def _turns(turns, duration):
    result = []
    if not isinstance(turns, list):
        raise MediaError("INVALID_DIARIZATION", "说话人时间线格式无效。")
    for turn in turns:
        if (not isinstance(turn, dict) or not isinstance(turn.get("speaker_id"), str)
                or not _SPEAKER_ID.fullmatch(turn["speaker_id"])
                or any(not isinstance(turn.get(key), int) or isinstance(turn[key], bool)
                       for key in ("start_ms", "end_ms"))
                or not 0 <= turn["start_ms"] < turn["end_ms"] <= duration):
            raise MediaError("INVALID_DIARIZATION", "说话人时间线超出视频范围或声线编号无效。")
        result.append({key: turn[key] for key in ("speaker_id", "start_ms", "end_ms")})
    return sorted(result, key=lambda turn: (turn["start_ms"], turn["end_ms"], turn["speaker_id"]))


def _scores(start, end, turns):
    intervals = {}
    for turn in turns:
        a, b = max(start, turn["start_ms"]), min(end, turn["end_ms"])
        if a < b:
            intervals.setdefault(turn["speaker_id"], []).append((a, b))
    scores = {}
    for speaker, ranges in intervals.items():
        merged = []
        for a, b in sorted(ranges):
            if merged and a <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(b, merged[-1][1]))
            else:
                merged.append((a, b))
        scores[speaker] = sum(b - a for a, b in merged)
    return scores


def _has_overlap(start, end, turns):
    active = [turn for turn in turns if turn["start_ms"] < end and turn["end_ms"] > start]
    for index, turn in enumerate(active):
        for other in active[index + 1:]:
            if (turn["speaker_id"] != other["speaker_id"]
                    and max(start, turn["start_ms"], other["start_ms"])
                    < min(end, turn["end_ms"], other["end_ms"])):
                return True
    return False


def _words(segment, raw):
    """Return valid original words only if editable text/time still match."""
    try:
        if (not isinstance(raw, dict) or raw.get("segment_id") != segment["id"]
                or not isinstance(raw.get("text"), str)
                or raw["text"].strip() != segment["source_text"].strip()
                or not all(_number(raw.get(key)) for key in ("start", "end"))
                or round(raw["start"] * 1000) != segment["start_ms"]
                or round(raw["end"] * 1000) != segment["end_ms"]
                or not isinstance(raw.get("words"), list) or not raw["words"]):
            return None
        result = []
        previous_end = segment["start_ms"]
        for word in raw["words"]:
            if (not isinstance(word, dict) or not isinstance(word.get("word"), str)
                    or not word["word"].strip()
                    or not all(_number(word.get(key)) for key in ("start", "end"))):
                return None
            start, end = round(word["start"] * 1000), round(word["end"] * 1000)
            if not previous_end <= start < end <= segment["end_ms"]:
                return None
            result.append({"word": word["word"], "start_ms": start, "end_ms": end})
            previous_end = end
        if "".join(word["word"] for word in result).strip() != segment["source_text"].strip():
            return None
        return result
    except (KeyError, TypeError, ValueError, OverflowError):
        return None


def apply_diarization(project, turns, raw_records=None):
    """Return ``(candidate, warnings: list[str])`` without incrementing revision.

    Segments with translations, formulas, terminology, confirmed review or note
    references retain their text/IDs/timing. Mixed speakers in those segments
    require manual correction. Untouched ASR drafts can be split at verified
    word boundaries; the first part keeps the old ID. No text is inferred from
    audio turns, and no overlapping subtitle segments are introduced.
    """
    validate_project(project)
    candidate = copy.deepcopy(project)
    ordered = _turns(turns, project["source"]["duration_ms"])
    warnings = []
    if not ordered:
        return candidate, ["未检测到可分配的说话人，已保留原有归属。"]
    known = {speaker["id"] for speaker in candidate["speakers"]}
    for turn in ordered:
        if turn["speaker_id"] not in known:
            known.add(turn["speaker_id"])
            candidate["speakers"].append({"id": turn["speaker_id"],
                                           "label": f"说话人 {len(known)}", "voice_mode": "standard"})
    records = raw_records.get("segments", []) if isinstance(raw_records, dict) else raw_records
    records = records if isinstance(records, list) else []
    raw_by_id = {raw.get("segment_id"): raw for raw in records if isinstance(raw, dict)}
    referenced = {identifier for note in candidate["notes"] for identifier in note["segment_ids"]}
    identifiers = {segment["id"] for segment in candidate["segments"]}
    changed = set()
    output = []
    for segment in candidate["segments"]:
        start, end = segment["start_ms"], segment["end_ms"]
        scores = _scores(start, end, ordered)
        if not scores:
            segment["review"] = "needs_review"
            changed.add(segment["id"])
            warnings.append(f"{segment['id']}：没有检测到对应声线，已保留原归属，请人工校对。")
            output.append(segment)
            continue
        speaker = max(scores, key=scores.get)
        overlap = _has_overlap(start, end, ordered)
        low_coverage = max(scores.values()) < (end - start) / 2
        protected = (segment["review"] == "confirmed" or segment["id"] in referenced
                     or segment["formula_ids"] or segment["term_ids"]
                     or any(segment[key].strip() for key in ("target_text", "subtitle_plain", "tts_text")))
        words = None if protected or overlap else _words(segment, raw_by_id.get(segment["id"]))
        groups = []
        uncertain_words = False
        if len(scores) > 1 and words:
            for word in words:
                word_scores = _scores(word["start_ms"], word["end_ms"], ordered)
                if not word_scores:
                    owner = speaker
                    uncertain_words = True
                else:
                    owner = max(word_scores, key=word_scores.get)
                    ordered_scores = sorted(word_scores.values(), reverse=True)
                    if (ordered_scores[0] / (word["end_ms"] - word["start_ms"]) < .5
                            or len(ordered_scores) > 1 and ordered_scores[0] == ordered_scores[1]):
                        uncertain_words = True
                if groups and groups[-1]["speaker_id"] == owner:
                    groups[-1]["words"].append(word)
                else:
                    groups.append({"speaker_id": owner, "words": [word]})
        if len(groups) > 1:
            boundaries = [start]
            for previous, current in zip(groups, groups[1:]):
                boundaries.append((previous["words"][-1]["end_ms"] + current["words"][0]["start_ms"]) // 2)
            boundaries.append(end)
            for index, group in enumerate(groups):
                part = copy.deepcopy(segment)
                if index:
                    counter = index + 1
                    while True:
                        identifier = f"{segment['id'][:50]}-spk-{counter}"
                        if identifier not in identifiers:
                            identifiers.add(identifier)
                            break
                        counter += 1
                    part["id"] = identifier
                part.update(speaker_id=group["speaker_id"], start_ms=boundaries[index],
                            end_ms=boundaries[index + 1], review="needs_review",
                            source_text="".join(word["word"] for word in group["words"]).strip())
                output.append(part)
            changed.add(segment["id"])
            warnings.append(f"{segment['id']}：已按逐词时间拆为 {len(groups)} 段，请检查换人边界与文字。")
            if uncertain_words:
                warnings.append(f"{segment['id']}：部分词的声线证据不足，请人工确认归属。")
            continue
        if speaker != segment["speaker_id"] or overlap or len(scores) > 1 or low_coverage:
            segment["review"] = "needs_review"
            changed.add(segment["id"])
        segment["speaker_id"] = speaker
        if overlap:
            warnings.append(f"{segment['id']}：检测到多人同时讲话；已保留完整字幕，请人工分配，当前不分离重叠人声。")
        elif len(scores) > 1:
            warnings.append(f"{segment['id']}：同段包含多位说话人；已保留文字并暂用主要声线，请人工校对。")
        elif low_coverage:
            warnings.append(f"{segment['id']}：声线证据覆盖不足一半时长，已暂用检测结果，请人工确认。")
        output.append(segment)
    candidate["segments"] = output
    for note in candidate["notes"]:
        if changed.intersection(note["segment_ids"]):
            note["review"] = "needs_review"
    validate_project(candidate)
    return candidate, warnings

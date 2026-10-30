"""Choose reference intervals with matching original text, without inference."""
from __future__ import annotations

import json
import math
from pathlib import Path
import re

from mathbridge.domain.project import safe_path

_TOKEN = re.compile(r"[\u3400-\u9fff]|[^\s\u3400-\u9fff]+")


def load_reference_words(project, root):
    """Reject stale ASR timings or evidence from a different source."""
    if not root:
        return []
    try:
        marker_path = safe_path(Path(root), "audio/source-16k.json")
        raw_path = safe_path(Path(root), "audio/asr-raw.json")
        if marker_path.stat().st_size > 100_000 or raw_path.stat().st_size > 20_000_000:
            return []
        marker = json.loads(marker_path.read_text(encoding="utf8"))
        raw = json.loads(raw_path.read_text(encoding="utf8"))
        if (marker["source_sha256"] != project["source"]["sha256"]
                or marker["sha256"] != raw["inputs"]["audio_sha256"]):
            return []
        words = []
        for segment in raw["segments"]:
            for word in segment.get("words", []):
                start, end = float(word["start"])*1000, float(word["end"])*1000
                if (math.isfinite(start) and math.isfinite(end)
                        and 0 <= start < end <= project["source"]["duration_ms"]+1
                        and isinstance(word["word"], str) and word["word"].strip()):
                    words.append(dict(start_ms=round(start), end_ms=round(end), text=word["word"]))
        return sorted(words, key=lambda w:(w["start_ms"], w["end_ms"]))
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return []


def _aligned(segment, words):
    source = segment["source_text"].strip()
    tokens = list(_TOKEN.finditer(source))
    selected = [w for w in words if segment["start_ms"] <= w["start_ms"]
                and w["end_ms"] <= segment["end_ms"]]
    raw_tokens = [t for w in selected for t in _TOKEN.findall(w["text"])]
    if [t[0] for t in tokens] != raw_tokens:
        return []
    mapped, offset, previous_end = [], 0, -1
    for word in selected:
        count = len(_TOKEN.findall(word["text"]))
        if not count or word["start_ms"] < previous_end:
            return []
        mapped.append(dict(word, left=tokens[offset].start(), right=tokens[offset+count-1].end()))
        offset += count
        previous_end = word["end_ms"]
    return mapped


def _language(text, default):
    chinese = len(re.findall(r"[\u3400-\u9fff]", text))
    latin = len(re.findall(r"[A-Za-z]", text))
    if chinese and (not latin or chinese >= latin/2):
        return "zh"
    if latin >= 4 and latin > chinese:
        return "en"
    return "zh" if default.startswith("zh") else "en"


def _join(parts, language):
    if language.startswith("zh"):
        return "".join(parts)
    return " ".join(parts)


def reference_for_range(project, speaker_id, start, end, words=()):
    """Match complete source segments or exact whole words inside this range."""
    if not 0 <= start < end <= project["source"]["duration_ms"] or not 3000 <= end-start <= 10000:
        return None
    segments = sorted((s for s in project["segments"] if s["start_ms"] < end and s["end_ms"] > start), key=lambda s:s["start_ms"])
    if not segments or any(s["speaker_id"] != speaker_id for s in segments):
        return None
    parts, ids, word_timed = [], [], False
    for segment in segments:
        source = segment["source_text"].strip()
        if not source or "{{" in source or "}}" in source:
            return None
        if start <= segment["start_ms"] and segment["end_ms"] <= end:
            parts.append(source)
        else:
            mapped = _aligned(segment, words)
            if not mapped:
                return None
            selected = [w for w in mapped if w["start_ms"] < end and w["end_ms"] > start]
            if not selected:
                continue
            if any(w["start_ms"] < start or w["end_ms"] > end for w in selected):
                return None  # Never attach a whole word to a cut phoneme.
            parts.append(source[selected[0]["left"]:selected[-1]["right"]])
            word_timed = True
        ids.append(segment["id"])
    if not parts:
        return None
    language = _language(" ".join(parts), project["languages"]["source"])
    text = _join(parts, language)
    return dict(start_ms=start, end_ms=end, reference_text=text,
                reference_language=_language(text, project["languages"]["source"]),
                segment_ids=ids, word_timed=word_timed)


def suggest_reference(project, speaker_id, words=(), preferred_segment_id=None):
    """Prefer 4–8 second complete source units; join fragments of one speaker.

    Long units can provide smaller references only with verified word alignment.
    Original text edits that no longer match the ASR invalidate that fallback.
    """
    ordered = sorted(project["segments"], key=lambda s:s["start_ms"])
    candidates = []
    for i, first in enumerate(ordered):
        if first["speaker_id"] != speaker_id:
            continue
        for j in range(i, len(ordered)):
            last = ordered[j]
            if last["speaker_id"] != speaker_id or (j > i and last["start_ms"]-ordered[j-1]["end_ms"] > 750):
                break
            duration = last["end_ms"]-first["start_ms"]
            if duration > 10000:
                break
            if duration >= 3000:
                candidate = reference_for_range(project,speaker_id,first["start_ms"],last["end_ms"])
                if candidate:
                    candidates.append(candidate)
        if first["end_ms"]-first["start_ms"] <= 10000:
            continue
        mapped = _aligned(first,words)
        for left in range(len(mapped)):
            options = []
            for right in range(left,len(mapped)):
                duration = mapped[right]["end_ms"]-mapped[left]["start_ms"]
                if duration > 10000:
                    break
                if duration >= 3000:
                    text = first["source_text"].strip()[mapped[left]["left"]:mapped[right]["right"]]
                    options.append(dict(start_ms=mapped[left]["start_ms"],end_ms=mapped[right]["end_ms"],
                        reference_text=text,reference_language=_language(text,project["languages"]["source"]),
                        segment_ids=[first["id"]],word_timed=True))
            if options:
                candidates.append(min(options,key=lambda c:abs(c["end_ms"]-c["start_ms"]-6000)))
    if not candidates:
        return None

    def score(candidate):
        text = candidate["reference_text"]
        duration = candidate["end_ms"]-candidate["start_ms"]
        density = len(re.findall(r"[\u3400-\u9fff]|[A-Za-z]+",text))/(duration/1000)
        return (0 if preferred_segment_id in candidate["segment_ids"] else 1,
                0 if 4000 <= duration <= 8000 else 1,
                max(0,density-7),
                0 if re.search(r"[.!?。！？][\"'’”]*$",text) else 1,
                len(candidate["segment_ids"])-1,abs(duration-6000),candidate["start_ms"])
    return min(candidates,key=score)

from __future__ import annotations

import re
import textwrap
from pathlib import Path


def timestamp(ms: int, separator=",") -> str:
    hours, remainder = divmod(ms, 3600000)
    minutes, remainder = divmod(remainder, 60000)
    seconds, milliseconds = divmod(remainder, 1000)
    return f"{hours:02}:{minutes:02}:{seconds:02}{separator}{milliseconds:03}"


def build_cues(project: dict) -> list[dict]:
    cues = []
    for segment in sorted(project["segments"], key=lambda x: x["start_ms"]):
        text = segment["subtitle_plain"].strip()
        if not text:
            raise ValueError(f"MISSING_TRANSLATION: {segment['id']} 字幕为空")
        # One semantic segment may own several readable cues; no TTS dependency here.
        width = 20 if project["languages"]["target"] == "zh-CN" else 42
        lines = []
        for line in text.splitlines():
            lines.extend(textwrap.wrap(line, width=width, break_long_words=True, break_on_hyphens=False) or [line])
        chunks = ["\n".join(lines[i:i + 2]) for i in range(0, len(lines), 2)]
        total_weight = sum(len(chunk) for chunk in chunks)
        cursor = segment["start_ms"]
        accumulated = 0
        for index, chunk in enumerate(chunks):
            accumulated += len(chunk)
            end = segment["end_ms"] if index == len(chunks) - 1 else segment["start_ms"] + round(
                (segment["end_ms"] - segment["start_ms"]) * accumulated / total_weight)
            if end <= cursor:
                raise ValueError("TIMELINE_INVALID: 字幕过密，请扩大时长或缩短字幕")
            cues.append(dict(id=f"cue-{segment['id']}-{index}", segment_id=segment["id"],
                             start_ms=cursor, end_ms=end, text=chunk, formula_ids=segment["formula_ids"]))
            cursor = end
    return cues


def write_subtitles(project: dict, directory: Path) -> tuple[list[dict], dict[str, Path]]:
    directory.mkdir(parents=True, exist_ok=True)
    cues = build_cues(project)
    outputs = {}
    for ext, separator in (("srt", ","), ("vtt", ".")):
        text = "WEBVTT\n\n" if ext == "vtt" else ""
        for index, cue in enumerate(cues, 1):
            text += f"{index}\n{timestamp(cue['start_ms'], separator)} --> {timestamp(cue['end_ms'], separator)}\n{cue['text']}\n\n"
        outputs[ext] = directory / f"lesson.{ext}"
        outputs[ext].write_text(text, encoding="utf-8", newline="\n")
    header = "[Script Info]\nScriptType: v4.00+\nPlayResX: 1920\nPlayResY: 1080\n\n[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\nStyle: Default,Microsoft YaHei,42,&H00FFFFFF,&H000000FF,&H00000000,&H80000000,0,0,0,0,100,100,0,0,1,2,1,2,80,80,54,1\n\n[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    for cue in cues:
        def ass_time(ms):
            return timestamp(ms, ".")[1:-1]
        # Escape ASS control sequences and overrides from untrusted course text.
        content = cue["text"].replace("\\", "＼").replace("{", "｛").replace("}", "｝").replace("\n", "\\N")
        header += f"Dialogue: 0,{ass_time(cue['start_ms'])},{ass_time(cue['end_ms'])},Default,,0,0,0,,{content}\n"
    outputs["ass"] = directory / "lesson.ass"
    outputs["ass"].write_text(header, encoding="utf-8-sig", newline="\n")
    return cues, outputs


TIMING = re.compile(r"(?:(\d{1,3}):)?(\d{2}):(\d{2})[,.](\d{3})\s*-->\s*(?:(\d{1,3}):)?(\d{2}):(\d{2})[,.](\d{3})")


def import_source_subtitles(path: Path, duration_ms: int) -> list[dict]:
    text = path.read_text(encoding="utf-8-sig").replace("\r\n", "\n")
    segments = []
    for block in re.split(r"\n\s*\n", text):
        match = TIMING.search(block)
        if not match:
            continue
        values = [int(x or 0) for x in match.groups()]
        start = ((values[0] * 60 + values[1]) * 60 + values[2]) * 1000 + values[3]
        end = ((values[4] * 60 + values[5]) * 60 + values[6]) * 1000 + values[7]
        source = re.sub(r"<[^>]*>", "", block[match.end():].strip())
        if not source or not 0 <= start < end <= duration_ms:
            raise ValueError("INVALID_SUBTITLES: 字幕内容为空或超出视频时长")
        if segments and start < segments[-1]["end_ms"]:
            raise ValueError("TIMELINE_OVERLAP: 请先消除字幕重叠")
        segments.append(dict(id=f"seg-{len(segments) + 1}", speaker_id="teacher", start_ms=start,
                             end_ms=end, source_text=source, target_text="", subtitle_plain="", tts_text="",
                             formula_ids=[], term_ids=[], review="needs_review"))
    if not segments:
        raise ValueError("INVALID_SUBTITLES: 未找到 SRT/VTT 字幕")
    return segments

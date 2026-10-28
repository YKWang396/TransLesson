from __future__ import annotations

import copy
import hashlib
import json
import re
import uuid
from functools import lru_cache
from pathlib import Path, PurePosixPath

PLACEHOLDER = re.compile(r"\{\{([a-z][a-z0-9_-]{0,63})\}\}")


def fingerprint(value) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode("utf-8")).hexdigest()


def file_hash(path: Path, cancel=None) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            if cancel is not None and cancel.is_set():
                raise InterruptedError("已取消文件校验")
            digest.update(chunk)
    return digest.hexdigest()


def safe_path(root: Path, relative: str) -> Path:
    if not isinstance(relative, str) or not relative or "\\" in relative or ":" in relative:
        raise ValueError("INVALID_PATH: 项目路径必须为相对 POSIX 路径")
    pure = PurePosixPath(relative)
    if pure.is_absolute() or ".." in pure.parts:
        raise ValueError("INVALID_PATH: 拒绝项目目录之外的路径")
    resolved = (Path(root) / relative).resolve()
    if not resolved.is_relative_to(Path(root).resolve()):
        raise ValueError("INVALID_PATH: 路径或符号链接越界")
    return resolved


def new_project(title: str, source: dict, source_language="en", target_language="zh-CN") -> dict:
    if (source_language, target_language) not in (("en", "zh-CN"), ("zh-CN", "en")):
        raise ValueError("首版仅支持中英互译")
    return dict(schema_version="1.0", project_id="project-" + uuid.uuid4().hex[:16],
                revision=1, title=title or "未命名课程", languages={"source": source_language, "target": target_language},
                source=source, speakers=[dict(id="teacher", label="主讲人", voice_mode="standard")],
                glossary=[], formulas=[], segments=[], notes=[])


def _schema(name: str) -> dict:
    bundled = Path(__file__).resolve().parents[1] / "schemas" / name
    source = Path(__file__).resolve().parents[3] / "contracts" / name
    return json.loads((bundled if bundled.exists() else source).read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def _project_validator():
    from jsonschema import Draft202012Validator
    return Draft202012Validator(_schema("project.schema.json"))


def validate_project(project: dict) -> None:
    _project_validator().validate(project)
    duration = project["source"]["duration_ms"]
    collections = {key: {item["id"]: item for item in project[key]}
                   for key in ("speakers", "segments", "formulas", "glossary", "notes")}
    for key, items in collections.items():
        if len(items) != len(project[key]):
            raise ValueError(f"DUPLICATE_ID: {key}")
    for speaker in project["speakers"]:
        profile = speaker.get("voice_profile", {})
        if speaker["voice_mode"] == "reference":
            if profile.get("provider") != "gpt_sovits":
                raise ValueError("VOICE_PROFILE_INVALID: 参考原声需要选择本地 GPT-SoVITS")
            evidence = speaker["reference_evidence"]
            start, end = evidence["start_ms"], evidence["end_ms"]
            if not 0 <= start < end <= duration or not 3000 <= end - start <= 10000:
                raise ValueError("VOICE_REFERENCE_INVALID: 参考声音须为视频内 3–10 秒的单人语音")
            if (profile.get("reference_start_ms"), profile.get("reference_end_ms")) != (start, end):
                raise ValueError("VOICE_REFERENCE_INVALID: 参考声音时间与来源不一致")
            if not profile.get("reference_text", "").strip():
                raise ValueError("VOICE_REFERENCE_INVALID: 请填写参考片段中实际说出的原文")
            if profile.get("reference_language") not in {"en", "zh", "zh-CN"}:
                raise ValueError("VOICE_REFERENCE_INVALID: 请选择参考声音中实际使用的语言")
        elif profile.get("provider", "windows") != "windows":
            raise ValueError("VOICE_PROFILE_INVALID: 标准声音应选择 Windows 声音")
    previous_end = 0
    for segment in sorted(project["segments"], key=lambda s: s["start_ms"]):
        start, end = segment["start_ms"], segment["end_ms"]
        if not 0 <= start < end <= duration:
            raise ValueError(f"TIMELINE_INVALID: {segment['id']} 超出视频时间")
        if start < previous_end:
            raise ValueError(f"TIMELINE_OVERLAP: {segment['id']} 与上一段重叠")
        previous_end = end
        if segment["speaker_id"] not in collections["speakers"]:
            raise ValueError("INVALID_REFERENCE: speaker")
        for field, collection in (("formula_ids", "formulas"), ("term_ids", "glossary")):
            if not set(segment[field]).issubset(collections[collection]):
                raise ValueError(f"INVALID_REFERENCE: {segment['id']} {field}")
        refs = set(PLACEHOLDER.findall(segment["target_text"]))
        remainder = PLACEHOLDER.sub("", segment["target_text"])
        if "{{" in remainder or "}}" in remainder:
            raise ValueError(f"INVALID_PLACEHOLDER: {segment['id']} 公式占位符不完整")
        if segment["target_text"].strip() and refs != set(segment["formula_ids"]):
            raise ValueError(f"INVALID_PLACEHOLDER: {segment['id']} 公式映射不一致")
        if "{{" in segment["subtitle_plain"] or "{{" in segment["tts_text"]:
            raise ValueError("INVALID_PLACEHOLDER: 字幕和读法必须为纯文本")
    for kind in ("formulas", "glossary"):
        for item in project[kind]:
            for ev in item["evidence"]:
                if not 0 <= ev["start_ms"] <= ev["end_ms"] <= duration:
                    raise ValueError(f"INVALID_EVIDENCE: {ev['id']}")
                if ev["kind"] == "speech" and ev["start_ms"] == ev["end_ms"]:
                    raise ValueError("INVALID_EVIDENCE: 语音来源时长为空")
                if ev["kind"] == "frame":
                    safe_path(Path.cwd(), ev["frame_path"])
                    x1, y1, x2, y2 = ev["bbox_xyxy"]
                    if not (x1 < x2 and y1 < y2):
                        raise ValueError("INVALID_EVIDENCE: 无效裁剪框")
            if kind == "formulas" and item["latex"]:
                from mathbridge.math.analysis import validate_latex
                validate_latex(item["latex"])
    for note in project["notes"]:
        if not set(note["segment_ids"]).issubset(collections["segments"]):
            raise ValueError("INVALID_REFERENCE: 讲义语音来源不存在")
        if not set(note["formula_ids"]).issubset(collections["formulas"]):
            raise ValueError("INVALID_REFERENCE: 讲义公式来源不存在")
        if not set(PLACEHOLDER.findall(note["body"])).issubset(note["formula_ids"]):
            raise ValueError("INVALID_PLACEHOLDER: 讲义公式映射不一致")


def speaker_voice(speaker: dict, settings: dict) -> dict:
    """Resolve one speaker without allowing another speaker's voice to leak in."""
    profile = speaker.get("voice_profile", {})
    if speaker["voice_mode"] == "reference":
        return dict(provider="gpt_sovits", endpoint=settings.get("voice_clone_api_url", "http://127.0.0.1:9880"),
                    model=settings.get("voice_clone_model", "default"),
                    reference_start_ms=profile.get("reference_start_ms"),
                    reference_end_ms=profile.get("reference_end_ms"),
                    reference_text=profile.get("reference_text", ""),
                    reference_language=profile.get("reference_language", ""),
                    speed=profile.get("speed", 1.0))
    return dict(provider="windows", voice=profile.get("voice_name") or settings.get("voice", ""),
                rate=profile.get("speech_rate", settings.get("speech_rate", 0)), model="system-installed")


def segment_fingerprint(project: dict, segment: dict, settings: dict) -> str:
    speaker = next(s for s in project["speakers"] if s["id"] == segment["speaker_id"])
    return fingerprint({"stage": "tts-v2", "source_hash": project["source"]["sha256"],
                        "id": segment["id"], "text": segment["tts_text"], "target_text": segment["target_text"],
                        "language": project["languages"]["target"],
                        "voice": speaker_voice(speaker, settings),
                        "formula_readings": {f["id"]: f["spoken_target"] for f in project["formulas"] if f["id"] in segment["formula_ids"]},
                        "speaker_id": speaker["id"]})


def export_issues(project: dict, formats=None) -> list[dict]:
    formats = formats or ["srt", "vtt", "pdf", "video"]
    issues = []
    def issue(code, message, entity_id="", severity="error"):
        issues.append(dict(code=code, message=message, entity_id=entity_id, severity=severity))
    try:
        validate_project(project)
    except Exception as exc:
        issue("INVALID_PROJECT", str(exc))
        return issues
    if not project["segments"]:
        issue("EMPTY_TRANSCRIPT", "请先转写或导入字幕")
    segments = sorted(project["segments"], key=lambda item: item["start_ms"])
    segment_positions = {item["id"]: number for number, item in enumerate(segments, 1)}
    formula_owners = {}
    def position(segment):
        ms = segment["start_ms"]
        return f"第 {segment_positions[segment['id']]} 句（{ms // 60000:02d}:{ms // 1000 % 60:02d}.{ms % 1000:03d}）"
    for segment in segments:
        for identifier in segment["formula_ids"]:
            formula_owners.setdefault(identifier, segment)
        fields = ["target_text", "subtitle_plain"]
        if set(formats) & {"video", "wav"}:
            fields.append("tts_text")
        for field in fields:
            if not segment[field].strip():
                labels = {"target_text": "译文", "subtitle_plain": "目标字幕", "tts_text": "配音读法"}
                issue("MISSING_TRANSLATION", f"{position(segment)}缺少{labels[field]}，请在校对页填写", segment["id"])
    if set(formats) & {"pdf", "video", "markdown"}:
        for formula in project["formulas"]:
            if formula["review"] == "needs_review":
                owner = formula_owners.get(formula["id"])
                if owner:
                    label = position(owner)
                else:
                    ms = formula["evidence"][0]["start_ms"]
                    label = f"{ms // 60000:02d}:{ms // 1000 % 60:02d}.{ms % 1000:03d}"
                raw = formula["raw"].replace("\n", " ")
                raw = raw[:65] + ("…" if len(raw) > 65 else "")
                issue("FORMULA_AMBIGUOUS", f"{label}公式“{raw}”待核对：点击问题可定位并确认，或明确选择保留原始证据", formula["id"])
        for note in project["notes"]:
            if note["kind"] == "supplement":
                issue("UNSUPPORTED_SUPPLEMENT", "首版不导出 AI 补充解释", note["id"])
    for segment in segments:
        if segment["review"] == "needs_review":
            issue("REVIEW_RECOMMENDED", f"{position(segment)}译文尚未确认（不阻止试配音或字幕导出）", segment["id"], "warning")
    return issues


def changed_copy(project: dict, collection: str, entity_id: str, changes: dict, *, _validate=True) -> dict:
    candidate = copy.deepcopy(project)
    entity = next((x for x in candidate[collection] if x["id"] == entity_id), None)
    if entity is None:
        raise ValueError("对象不存在")
    if "id" in changes and changes["id"] != entity_id:
        raise ValueError("不允许修改对象 ID")
    entity.update(changes)
    candidate["revision"] += 1
    if _validate:
        validate_project(candidate)
    return candidate

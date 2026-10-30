from __future__ import annotations

from pathlib import Path
from mathbridge.domain.project import file_hash, safe_path, _schema


def validate_render_plan(plan: dict, project: dict, root: Path) -> None:
    from jsonschema import Draft202012Validator
    Draft202012Validator(_schema("render-plan.schema.json")).validate(plan)
    if plan["illustrative"]:
        raise ValueError("ILLUSTRATIVE_PLAN: 演示导出计划不可执行")
    if plan["project_id"] != project["project_id"] or plan["project_revision"] != project["revision"] or plan["source_sha256"] != project["source"]["sha256"]:
        raise ValueError("STALE_SNAPSHOT: 导出快照与课程不一致")
    segments = {s["id"]: s for s in project["segments"]}
    timing = {t["segment_id"]:t for t in plan.get("dubbing_timing",[])}
    if "dubbing_timing" in plan:
        if len(timing) != len(plan["dubbing_timing"]) or set(timing) != set(segments):
            raise ValueError("INVALID_CUE: 配音时间映射缺失或重复")
        previous_end = 0
        ordered = sorted(project["segments"],key=lambda s:s["start_ms"])
        for i,segment in enumerate(ordered):
            mapped = timing[segment["id"]]
            start,end = mapped["start_ms"],mapped["end_ms"]
            if not previous_end <= start < end <= project["source"]["duration_ms"] or abs(start-segment["start_ms"]) > 2001:
                raise ValueError("INVALID_CUE: 配音时间映射越界或漂移过大")
            if i and ordered[i-1]["speaker_id"] != segment["speaker_id"] and start < ordered[i-1]["end_ms"]:
                raise ValueError("INVALID_CUE: 配音跨越上一位说话人")
            if i+1 < len(ordered) and ordered[i+1]["speaker_id"] != segment["speaker_id"] and end > ordered[i+1]["start_ms"]:
                raise ValueError("INVALID_CUE: 配音跨越下一位说话人")
            previous_end = end
    artifacts = {a["id"]: a for a in plan["artifacts"]}
    if len(artifacts) != len(plan["artifacts"]):
        raise ValueError("DUPLICATE_ID: 产物 ID 重复")
    cue_ids = set()
    end = 0
    for cue in plan["cues"]:
        segment = segments.get(cue["segment_id"])
        interval = timing.get(cue["segment_id"],segment)
        if not interval or not interval["start_ms"] <= cue["start_ms"] < cue["end_ms"] <= interval["end_ms"]:
            raise ValueError("INVALID_CUE: 字幕不在来源句的区间内")
        if cue["id"] in cue_ids or cue["start_ms"] < end or not set(cue["formula_ids"]).issubset(segment["formula_ids"]):
            raise ValueError("INVALID_CUE: 字幕重复、重叠或公式归属错误")
        cue_ids.add(cue["id"])
        end = cue["end_ms"]
    for artifact in artifacts.values():
        path = safe_path(root, artifact["path"])
        if not path.is_file() or file_hash(path) != artifact["sha256"]:
            raise ValueError(f"ARTIFACT_CHANGED: {artifact['id']} 文件校验失败")
        if not set(artifact["segment_ids"]).issubset(segments):
            raise ValueError("INVALID_REFERENCE: 产物引用不存在的语音段")
        for dependency in artifact["depends_on"]:
            other = artifacts.get(dependency["artifact_id"])
            if not other or other["sha256"] != dependency["sha256"]:
                raise ValueError("INVALID_DEPENDENCY: 依赖hash不一致")
    visited, active = set(), set()
    def visit(key):
        if key in active:
            raise ValueError("DEPENDENCY_CYCLE: 产物依赖成环")
        if key in visited:
            return
        if key not in artifacts:
            raise ValueError("INVALID_REFERENCE: 导出产物不存在")
        active.add(key)
        for dep in artifacts[key]["depends_on"]:
            visit(dep["artifact_id"])
        active.remove(key)
        visited.add(key)
    for key in list(artifacts) + plan["export_artifact_ids"]:
        visit(key)

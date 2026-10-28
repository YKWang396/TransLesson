"""Locate review problems in the lesson, without changing their review status."""
from __future__ import annotations

import hashlib
from pathlib import Path
import re

from mathbridge.domain.project import safe_path


def time_label(milliseconds):
    value = max(0, int(milliseconds or 0))
    return f"{value // 60000:02d}:{value // 1000 % 60:02d}.{value % 1000:03d}"


def _indexes(project):
    segments = sorted((project or {}).get("segments", []), key=lambda item: item["start_ms"])
    by_id = {item["id"]: (number, item) for number, item in enumerate(segments, 1)}
    formula_segments = {}
    for number, segment in enumerate(segments, 1):
        for identifier in segment.get("formula_ids", []):
            formula_segments.setdefault(identifier, []).append((number, segment))
    formulas = {item["id"]: item for item in (project or {}).get("formulas", [])}
    notes = {item["id"]: item for item in (project or {}).get("notes", [])}
    return segments, by_id, formula_segments, formulas, notes


def _locate(issue, indexes):
    segments, by_id, formula_segments, formulas, notes = indexes
    result = dict(issue)
    entity = issue.get("entity_id", "")
    formula_id = issue.get("formula_id") or (entity if entity in formulas else "")
    formula = formulas.get(formula_id)
    segment_id = issue.get("segment_id") or (entity if entity in by_id else "")
    owners = formula_segments.get(formula_id, [])
    if not segment_id and owners:
        segment_id = owners[0][1]["id"]
    note = notes.get(entity)
    if not segment_id and note:
        segment_id = next((sid for sid in note.get("segment_ids", []) if sid in by_id), "")
    # Old projects can contain formula evidence without a segment formula link.
    # Navigate by its actual evidence time in that case, never by an opaque ID.
    evidence = formula.get("evidence", []) if formula else []
    if not segment_id and evidence:
        stamp = evidence[0].get("start_ms", 0)
        owner = next((segment for segment in segments if segment["start_ms"] <= stamp < segment["end_ms"]), None)
        if owner:
            segment_id = owner["id"]
    number, segment = by_id.get(segment_id, (None, None))
    start = segment["start_ms"] if segment else evidence[0].get("start_ms", 0) if evidence else issue.get("start_ms", 0)
    end = segment["end_ms"] if segment else evidence[0].get("end_ms", start) if evidence else issue.get("end_ms", start)
    stamp = time_label(start)
    location = f"第 {number} 句 · {stamp}" if number else f"{stamp}" if evidence else "项目检查"
    context = (segment.get("source_text", "") if segment else formula.get("raw", "") if formula else "").strip()
    code = issue.get("code", "")
    action = "formula" if formula else "edit_tts" if code in {"TTS_TOO_LONG", "STALE_AUDIO", "MISSING_TTS"} else "edit_note" if note else "edit_subtitle" if segment else "review"
    title = f"公式原始表达：{formula.get('raw', '')}" if formula else "配音读法过长" if code == "TTS_TOO_LONG" else issue.get("message", "待检查")
    result.update(segment_id=segment_id, segment_index=number, segment_number=number,
                  start_ms=start, end_ms=end, time_label=stamp, location=location,
                  context=context, title=title, action=action, formula_id=formula_id)
    if formula:
        result["candidate"] = formula.get("latex") or ""
        result["evidence_summary"] = "；".join(
            ("原音 " if item.get("kind") == "speech" else "画面 ")
            + time_label(item.get("start_ms", 0)) + "–" + time_label(item.get("end_ms", 0))
            for item in evidence)
        result["segment_ids"] = [item[1]["id"] for item in owners]
    return result


def locate_issue(project, issue):
    """Return navigation fields for one diagnostic; segment_index is one based."""
    return _locate(issue, _indexes(project))


def enriched_issues(project, raw_issues):
    """Enrich a whole issue list with one shared index, including long lessons."""
    indexes = _indexes(project)
    return [_locate(issue, indexes) for issue in raw_issues]


def preservable_formula_ids(project, project_dir=None):
    """Pending formulas whose original expression still has usable source evidence.

    Speech preservation keeps the original transcription and source time. A frame
    counts only when its safe project-relative image file still exists. This is
    an explicit evidence fallback, never mathematical confirmation of a candidate.
    """
    duration = (project or {}).get("source", {}).get("duration_ms", 0)
    result = []
    for formula in (project or {}).get("formulas", []):
        if formula.get("review") != "needs_review" or not formula.get("raw", "").strip():
            continue
        for evidence in formula.get("evidence", []):
            start, end = evidence.get("start_ms"), evidence.get("end_ms")
            if not isinstance(start, int) or not isinstance(end, int) or not 0 <= start <= end <= duration:
                continue
            if evidence.get("kind") == "speech" and start < end:
                result.append(formula["id"])
                break
            if evidence.get("kind") != "frame" or project_dir is None:
                continue
            bbox = evidence.get("bbox_xyxy", [])
            if len(bbox) != 4 or not all(isinstance(value, (int, float)) for value in bbox):
                continue
            x1, y1, x2, y2 = bbox
            if not (0 <= x1 < x2 <= 1 and 0 <= y1 < y2 <= 1):
                continue
            try:
                frame = safe_path(Path(project_dir), evidence.get("frame_path", ""))
            except (ValueError, OSError):
                continue
            if frame.is_file():
                result.append(formula["id"])
                break
    return result


def obsolete_rule_formula_ids(project):
    """Identify only the former word-substring bug, leaving human review intact.

    The old rule read the last letter of ``the``/``take``/``suppose`` followed
    by x as e^x. A pending rule entity must retain its generated ID and matching
    speech evidence before it can be offered for explicit cleanup. Confirmed,
    preserved, manually created, or genuinely standalone e expressions stay.
    """
    _, _, owners, _, _ = _indexes(project)
    result = []
    for formula in (project or {}).get("formulas", []):
        raw = formula.get("raw", "")
        if (formula.get("review") != "needs_review" or raw.casefold() != "e x"
                or formula.get("latex") != "e^x"
                or "RULE_CANDIDATE_REQUIRES_REVIEW" not in formula.get("warnings", [])):
            continue
        references = owners.get(formula["id"], [])
        if not references:
            continue
        false_match = True
        for _, segment in references:
            source = segment.get("source_text", "")
            match = re.search(r"e (?:to the (?:power of )?)?x", source, re.I)
            expected = "formula-" + hashlib.sha256((segment["id"] + "\0" + raw).encode()).hexdigest()[:16]
            evidence_matches = any(item.get("kind") == "speech"
                and item.get("start_ms") == segment["start_ms"] and item.get("end_ms") == segment["end_ms"]
                for item in formula.get("evidence", []))
            if (not match or match.group(0) != raw or match.start() == 0
                    or not source[match.start() - 1].isalpha() or formula["id"] != expected
                    or not evidence_matches or re.search(r"\be\s+x\b", source, re.I)):
                false_match = False
                break
        if false_match:
            result.append(formula["id"])
    return result

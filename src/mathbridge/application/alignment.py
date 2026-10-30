"""Inspect local speech duration without changing text, timing, or cached audio."""
from __future__ import annotations

import json
from pathlib import Path
import wave

from mathbridge.domain.project import fingerprint, safe_path, segment_fingerprint


DEFAULT_MAX_SPEED = 1.15
DEFAULT_BORROW_MS = 1000


def alignment_options(options=None):
    options = options or {}
    speed = options.get("alignment_max_speed", DEFAULT_MAX_SPEED)
    borrow = options.get("alignment_borrow_ms", DEFAULT_BORROW_MS)
    mode = options.get("alignment_mode", "adaptive")
    if mode not in ("adaptive", "strict"):
        raise ValueError("INVALID_ALIGNMENT: 未知时长适配模式")
    if isinstance(speed, bool) or not isinstance(speed, (int, float)) or not 1 <= speed <= 1.5:
        raise ValueError("INVALID_ALIGNMENT: 配音加速上限须为 1.00–1.50 倍")
    if isinstance(borrow, bool) or not isinstance(borrow, int) or not 0 <= borrow <= 2000:
        raise ValueError("INVALID_ALIGNMENT: 可借用的句后停顿须为 0–2000 毫秒")
    return dict(alignment_max_speed=float(speed), alignment_borrow_ms=borrow, alignment_mode=mode)


def time_label(ms):
    seconds, millis = divmod(round(ms), 1000)
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}.{millis:03d}"


def duration_issue(plan, max_speed, *, actual_ms=None):
    speech_ms = plan["speech_ms"] if actual_ms is None else actual_ms
    required = speech_ms / plan["available_ms"]
    number = plan["index"]
    message = (f"第 {number} 句（{time_label(plan['start_ms'])}）配音 {speech_ms / 1000:.2f} 秒，"
               f"可用 {plan['available_ms'] / 1000:.2f} 秒，需 {required:.2f} 倍速，"
               f"超过当前 {max_speed:.2f} 倍上限。请缩短配音读法，或合并同一说话人的相邻句。")
    return dict(code="TTS_TOO_LONG", severity="error", entity_id=plan.get("segment_id", ""),
                segment_id=plan.get("segment_id", ""), index=number,
                start_ms=plan["start_ms"], end_ms=plan["end_ms"],
                speech_ms=round(speech_ms, 3), available_ms=plan["available_ms"],
                available_end_ms=plan["available_end_ms"], borrowed_ms=plan["borrowed_ms"],
                required_speed=round(required, 4), needed_rate=round(required, 4),
                max_speed=max_speed, message=message, action="调整配音读法或合并相邻句")


def plan_alignment(clips, duration_ms, options=None):
    """Plan bounded gap borrowing; no clip may delay the following speaker.

    Each input contains measured speech_ms. limit_end_ms preserves the next
    subtitle boundary when a preview or cache inspection supplies only a subset.
    """
    config = alignment_options(options)
    ordered = sorted(clips, key=lambda clip: clip["start_ms"])
    if config["alignment_mode"] == "adaptive" and ordered and all(c.get("speaker_id") for c in ordered):
        return _adaptive(ordered, duration_ms, config)
    plans, issues = [], []
    previous_end = 0
    if duration_ms <= 0:
        raise ValueError("INVALID_TIMELINE: 输出时长必须大于零")
    for index, clip in enumerate(ordered):
        start, end = clip["start_ms"], clip["end_ms"]
        if not (0 <= start < end <= duration_ms) or start < previous_end:
            raise ValueError("INVALID_TIMELINE: 配音区间越界或重叠")
        previous_end = end
        next_start = ordered[index + 1]["start_ms"] if index + 1 < len(ordered) else duration_ms
        available_end = min(end + config["alignment_borrow_ms"], next_start, duration_ms,
                            clip.get("limit_end_ms", duration_ms))
        if available_end < end:
            raise ValueError("INVALID_TIMELINE: 后续配音边界与本句重叠")
        speech_ms = clip["speech_ms"]
        if not isinstance(speech_ms, (int, float)) or not 0 < speech_ms < float("inf"):
            raise ValueError("INVALID_AUDIO: 配音音频时长无效")
        plan = dict(clip, index=clip.get("index", index + 1), available_end_ms=available_end,
                    available_ms=available_end - start, borrowed_ms=available_end - end,
                    speed=max(1., speech_ms / (available_end - start)))
        plans.append(plan)
        if plan["speed"] > config["alignment_max_speed"] + 1e-8:
            issues.append(duration_issue(plan, config["alignment_max_speed"]))
    return plans, issues


def _adaptive(clips, duration_ms, config):
    # Validate using the strict planner first; its bounds remain the fallback.
    fixed, fixed_issues = plan_alignment(clips, duration_ms, dict(config, alignment_mode="strict"))
    groups = []
    for clip in fixed:
        if (not groups or clip["speaker_id"] != groups[-1][-1]["speaker_id"]
                or clip["start_ms"]-groups[-1][-1]["end_ms"] > 2000
                or clip["start_ms"] > groups[-1][-1].get("limit_end_ms",clip["start_ms"])):
            groups.append([])
        groups[-1].append(clip)
    plans, issues = [], []
    for group in groups:
        count = len(group)
        begin = group[0]["start_ms"]
        finish = group[-1]["available_end_ms"]
        release = [max(begin,c["start_ms"]-2000) for c in group]
        deadline = [min(finish,c["start_ms"]+2000) for c in group]
        pauses = [min(180,max(0,b["start_ms"]-a["end_ms"])) for a,b in zip(group,group[1:])] + [0]
        natural = [c["speech_ms"] for c in group]
        # Reserve codec rounding headroom instead of planning at the exact cap.
        lengths = [min(n,n/config["alignment_max_speed"]+30) for n in natural]

        def feasible():
            position = begin
            for i in range(count):
                position = max(position,release[i])
                if position > deadline[i]+.01:
                    return False
                position += lengths[i]+pauses[i]
            return position <= finish+.01

        if not feasible():
            plans.extend(group)
            ids = {c["segment_id"] for c in group}
            issues.extend(i for i in fixed_issues if i.get("segment_id") in ids)
            continue
        # Relax each sentence toward its natural duration while preserving a
        # feasible schedule. Easy sentences keep their natural pace first.
        for i in sorted(range(count),key=lambda i:natural[i]/max(1,group[i]["end_ms"]-group[i]["start_ms"])):
            low, high = lengths[i], natural[i]
            lengths[i] = high
            if not feasible():
                for _ in range(20):
                    middle = (low+high)/2
                    lengths[i] = middle
                    if feasible(): low = middle
                    else: high = middle
                lengths[i] = low
        latest = [0.]*count
        limit = finish
        for i in range(count-1,-1,-1):
            latest[i] = min(deadline[i],limit-lengths[i])
            limit = latest[i]-pauses[i-1] if i else begin
        starts, position = [], begin
        for i,c in enumerate(group):
            start = max(position,release[i],min(c["start_ms"],latest[i]))
            starts.append(start)
            position = start+lengths[i]+pauses[i]
        for i,c in enumerate(group):
            end = starts[i+1]-pauses[i] if i+1 < count else finish
            available = max(.001,end-starts[i])
            plans.append(dict(c, aligned_start_ms=starts[i], shift_ms=round(starts[i]-c["start_ms"],3),
                              available_end_ms=end, available_ms=available,
                              borrowed_ms=max(0,end-c["end_ms"]),
                              speed=max(1.,natural[i]/available)))
    return plans, issues


def alignment_fingerprint(project, settings, options=None):
    return fingerprint(dict(version="adaptive-rhythm-1", source=project["source"]["sha256"],
                            duration_ms=project["source"]["duration_ms"],
                            segments=[dict(id=s["id"], start_ms=s["start_ms"], end_ms=s["end_ms"],
                                           tts=segment_fingerprint(project, s, settings))
                                      for s in project["segments"]], options=alignment_options(options)))


def suggest_merges(project, speech_durations, options=None):
    """Find a small, disjoint set of bounded same-speaker sentence groups.

    Cached durations are added conservatively. A new combined TTS recording can
    have a different duration and must be measured again before output.
    """
    config = alignment_options(options)
    segments = sorted(project["segments"], key=lambda s: s["start_ms"])
    count = len(segments)
    costs, choices = [(0, 0)] * (count + 1), [None] * count
    for first in range(count - 1, -1, -1):
        current = segments[first]
        measured = speech_durations.get(current["id"])
        next_start = segments[first + 1]["start_ms"] if first + 1 < count else project["source"]["duration_ms"]
        capacity = min(current["end_ms"] + config["alignment_borrow_ms"], next_start) - current["start_ms"]
        bad = measured is not None and measured > capacity * config["alignment_max_speed"] + 1e-8
        costs[first] = (costs[first + 1][0] + int(bad), costs[first + 1][1])
        choices[first] = first + 1
        if measured is None:
            continue
        speech_ms, contains_bad = measured, bad
        for last in range(first + 1, count):
            segment, previous = segments[last], segments[last - 1]
            if (segment["speaker_id"] != current["speaker_id"] or
                    segment["start_ms"] - previous["end_ms"] > 1000 or
                    segment["end_ms"] - current["start_ms"] > 20000 or
                    segment["id"] not in speech_durations):
                break
            duration = speech_durations[segment["id"]]
            speech_ms += duration
            next_start = segments[last + 1]["start_ms"] if last + 1 < count else project["source"]["duration_ms"]
            available_end = min(segment["end_ms"] + config["alignment_borrow_ms"], next_start)
            own_capacity = available_end - segment["start_ms"]
            contains_bad |= duration > own_capacity * config["alignment_max_speed"] + 1e-8
            if not contains_bad or speech_ms > (available_end - current["start_ms"]) * config["alignment_max_speed"]:
                continue
            candidate = (costs[last + 1][0], costs[last + 1][1] + last - first)
            if candidate < costs[first]:
                costs[first], choices[first] = candidate, last + 1
    groups, position = [], 0
    while position < count:
        stop = choices[position]
        if stop > position + 1:
            groups.append([segment["id"] for segment in segments[position:stop]])
        position = stop
    return dict(suggested_merges=groups, predicted_remaining_count=costs[0][0],
                prediction_note="根据现有配音时长估算；合并后会重新生成并检查配音，实际结果可能不同。")


def cached_alignment_report(project, settings, root, options=None):
    """Estimate current problems from matching cache headers, never synthesize.

    Editing one sentence leaves other current cached sentences inspectable.
    The worker verifies complete audio hashes before using these files in output;
    this quick UI inspection deliberately only reads markers and WAV headers.
    """
    config = alignment_options(options)
    segments = sorted(project["segments"], key=lambda s: s["start_ms"])
    clips, missing = [], []
    for index, segment in enumerate(segments):
        fp = segment_fingerprint(project, segment, settings)
        path = safe_path(Path(root), f"audio/tts/{fp}.wav")
        try:
            marker = json.loads(path.with_suffix(".json").read_text(encoding="utf-8"))
            if not isinstance(marker, dict) or marker.get("input_fingerprint") != fp or not marker.get("sha256"):
                raise ValueError("cache marker mismatch")
            with wave.open(str(path), "rb") as audio:
                speech_ms = audio.getnframes() * 1000 / audio.getframerate()
                if not audio.getnframes():
                    raise ValueError("empty audio")
        except (OSError, ValueError, EOFError, wave.Error, ZeroDivisionError):
            missing.append(segment["id"])
            continue
        original_ms, saved_ms = speech_ms, 0
        if config["alignment_mode"] == "adaptive":
            from .dubbing import timing_path
            prepared = timing_path(root,project,segment,settings)
            try:
                timing = json.loads(prepared.with_suffix(".json").read_text(encoding="utf8"))
                if prepared.is_file() and timing["raw_sha256"] == marker["sha256"]:
                    speech_ms, saved_ms = timing["duration_ms"],timing["saved_ms"]
                    path = prepared
            except (OSError,ValueError,KeyError,TypeError):
                pass
        clips.append(dict(segment_id=segment["id"], index=index + 1, path=str(path),
                          start_ms=segment["start_ms"], end_ms=segment["end_ms"], speech_ms=speech_ms,
                          speaker_id=segment["speaker_id"],original_speech_ms=original_ms,pause_saved_ms=saved_ms,
                          limit_end_ms=segments[index + 1]["start_ms"] if index + 1 < len(segments)
                          else project["source"]["duration_ms"]))
    plans, issues = plan_alignment(clips, project["source"]["duration_ms"], config)
    return dict(input_fingerprint=alignment_fingerprint(project, settings, config),
                project_revision=project["revision"], options=config, issues=issues,
                measured_count=len(plans), missing_count=len(missing), missing_segment_ids=missing,
                borrowed_count=sum(plan["speech_ms"] > plan["end_ms"] - plan["start_ms"]
                                   and plan["speed"] <= config["alignment_max_speed"] for plan in plans),
                estimated=True, plans=plans,
                pause_compacted_count=sum(c.get("pause_saved_ms",0)>20 for c in plans),
                shifted_count=sum(abs(c.get("shift_ms",0))>20 for c in plans),
                accelerated_count=sum(c["speed"]>1.005 for c in plans),
                **suggest_merges(project, {clip["segment_id"]: clip["speech_ms"] for clip in clips}, config))


# Kept as an explicit read-only entry point for UI and diagnostics.
read_cached_alignment = cached_alignment_report

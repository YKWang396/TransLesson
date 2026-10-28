import copy
import json
from pathlib import Path
import wave

import pytest

from mathbridge.application.alignment import alignment_options, cached_alignment_report, plan_alignment, suggest_merges
from mathbridge.domain.project import segment_fingerprint
from mathbridge.media.engine import MediaEngine, MediaError


def clip(identifier, start, end, speech, **kwargs):
    return dict(segment_id=identifier, start_ms=start, end_ms=end, speech_ms=speech, **kwargs)


def wav(path, duration_ms):
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as out:
        out.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
        out.writeframes(b"\x10\x00" * (16 * duration_ms))
    return path


def test_borrows_pause_without_moving_following_sentence():
    plans, issues = plan_alignment([clip("seg-2", 14600, 15020, 1350),
                                    clip("seg-3", 15960, 17520, 1000)], 20000)
    assert not issues
    assert plans[0]["available_end_ms"] == 15960
    assert plans[0]["speed"] == 1
    assert plans[1]["start_ms"] == 15960
    # A preview containing one selected sentence still respects the next one.
    _, issues = plan_alignment([clip("seg-2", 14600, 15020, 1600, limit_end_ms=15960)], 20000)
    assert issues[0]["available_ms"] == 1360


def test_collects_every_issue_and_keeps_meaningful_identifiers():
    _, issues = plan_alignment([clip("seg-a", 0, 500, 2000), clip("seg-b", 600, 1000, 3000)], 2000)
    assert [item["segment_id"] for item in issues] == ["seg-a", "seg-b"]
    assert issues[0]["index"] == 1
    assert issues[0]["speech_ms"] == 2000
    assert issues[0]["available_end_ms"] == 600
    assert "00:00:00.000" in issues[0]["message"]
    assert "合并" in issues[0]["message"]


@pytest.mark.parametrize("options", [dict(alignment_max_speed=3.2), dict(alignment_max_speed=float("nan")),
                                     dict(alignment_borrow_ms=10000), dict(alignment_borrow_ms=True)])
def test_alignment_controls_remain_bounded(options):
    with pytest.raises(ValueError, match="INVALID_ALIGNMENT"):
        alignment_options(options)


def test_real_mixer_uses_following_silence_without_cutting_audio(tmp_path):
    engine = MediaEngine()
    first, second = wav(tmp_path / "first.wav", 1350), wav(tmp_path / "second.wav", 500)
    output = engine.align_and_mix([dict(path=first, start_ms=0, end_ms=420, segment_id="short"),
                                   dict(path=second, start_ms=1360, end_ms=2000, segment_id="next")],
                                  tmp_path / "out.wav", 3000)
    with wave.open(str(output)) as audio:
        assert audio.getnframes() == 48000 * 3
        audio.setpos(48000)
        assert any(audio.readframes(1000))  # first sentence survives past old end


def test_real_mixer_reports_all_problems_before_replacing_output(tmp_path):
    engine = MediaEngine()
    source = wav(tmp_path / "long.wav", 2000)
    output = tmp_path / "keep.wav"
    output.write_bytes(b"existing output")
    with pytest.raises(MediaError) as caught:
        engine.align_and_mix([dict(path=source, start_ms=0, end_ms=500, segment_id="one"),
                              dict(path=source, start_ms=600, end_ms=1000, segment_id="two")], output, 1500)
    assert len(caught.value.issues) == 2
    assert output.read_bytes() == b"existing output"


def sample_project():
    return json.loads((Path(__file__).resolve().parents[1] / "examples" / "project.json").read_text(encoding="utf-8"))


def test_cache_inspection_retains_other_issues_after_one_sentence_changes(tmp_path):
    project = sample_project()
    for segment in project["segments"]:
        fp = segment_fingerprint(project, segment, {})
        path = wav(tmp_path / "audio" / "tts" / f"{fp}.wav", 15000)
        path.with_suffix(".json").write_text(json.dumps(dict(input_fingerprint=fp, sha256="hash-for-display-inspection")), encoding="utf-8")
    report = cached_alignment_report(project, {}, tmp_path)
    assert len(report["issues"]) == 2
    changed = copy.deepcopy(project)
    changed["segments"][0]["tts_text"] = "Edited reading"
    report = cached_alignment_report(changed, {}, tmp_path)
    assert report["missing_count"] == 1
    assert len(report["issues"]) == 1
    assert report["issues"][0]["segment_id"] == project["segments"][1]["id"]


def test_merge_planner_uses_available_time_and_never_crosses_speakers():
    project = dict(source=dict(duration_ms=9000), segments=[
        dict(id="a", start_ms=0, end_ms=500, speaker_id="teacher"),
        dict(id="b", start_ms=600, end_ms=3000, speaker_id="teacher"),
        dict(id="c", start_ms=3100, end_ms=3500, speaker_id="student"),
        dict(id="d", start_ms=3600, end_ms=5000, speaker_id="teacher")])
    report = suggest_merges(project, dict(a=1000, b=1000, c=2000, d=100))
    assert report["suggested_merges"] == [["a", "b"]]
    assert report["predicted_remaining_count"] == 1
    # Missing audio cannot be guessed into a merge.
    assert suggest_merges(project, dict(a=1000, c=2000, d=100))["suggested_merges"] == []


def test_merge_planner_does_not_bridge_large_gaps_or_unbounded_groups():
    project = dict(source=dict(duration_ms=40000), segments=[
        dict(id="a", start_ms=0, end_ms=500, speaker_id="teacher"),
        dict(id="b", start_ms=2000, end_ms=21000, speaker_id="teacher")])
    assert not suggest_merges(project, dict(a=4000, b=100))["suggested_merges"]

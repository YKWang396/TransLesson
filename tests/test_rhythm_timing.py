import copy
from array import array
import json
from pathlib import Path
import random
import wave

import pytest

from mathbridge.application.alignment import plan_alignment
from mathbridge.application.artifacts import validate_render_plan
from mathbridge.application.subtitles import build_cues,dubbing_timing
from mathbridge.media.rhythm import analyze_wave,compact_pauses


def clip(identifier,start,end,speech,speaker="teacher"):
    return dict(segment_id=identifier,start_ms=start,end_ms=end,speech_ms=speech,speaker_id=speaker)


def test_shares_time_without_merging_text_or_crossing_speakers():
    clips=[clip("a",0,500,1200),clip("b",600,3000,1200),clip("c",3200,6000,1000)]
    before=copy.deepcopy(clips)
    _,old=plan_alignment(clips,6500,{"alignment_mode":"strict"})
    assert old
    plans,issues=plan_alignment(clips,6500)
    assert not issues
    assert all(p["speed"]==1 for p in plans)
    assert plans[1]["aligned_start_ms"]>=1200
    assert clips==before
    clips[1]["speaker_id"]="student"
    plans,issues=plan_alignment(clips,6500)
    assert issues[0]["segment_id"]=="a"
    assert plans[0]["available_end_ms"]<=600


def test_rejects_dense_speech_instead_of_allowing_unbounded_drift():
    clips=[clip("a",0,500,6000),clip("b",600,1500,6000),clip("c",1600,2000,6000)]
    _,issues=plan_alignment(clips,10000)
    assert [i["segment_id"] for i in issues]==["a","b"]
    # The last sentence can now use the real long pause at the end of the video.


def test_partial_audio_cannot_borrow_time_across_an_omitted_sentence():
    a=clip("a",0,500,1800)
    a["limit_end_ms"]=700  # An omitted speaker or uncached sentence starts here.
    b=clip("b",1500,4000,1000)
    _,issues=plan_alignment([a,b],5000)
    assert issues[0]["segment_id"]=="a"
    assert issues[0]["available_end_ms"]==700


def test_uses_both_sides_of_long_silence_without_double_spending_it():
    clips=[clip("a",3000,4000,6000),clip("b",10000,11000,6500)]
    before=copy.deepcopy(clips)
    plans,issues=plan_alignment(clips,15000)
    assert not issues and all(p["speed"]==1 for p in plans)
    assert plans[0]["aligned_start_ms"]<3000
    assert plans[0]["available_end_ms"]>6000  # Beyond the old two-second cap.
    assert plans[1]["aligned_start_ms"]<10000
    assert plans[0]["available_end_ms"]<=plans[1]["aligned_start_ms"]
    assert clips==before


def test_partial_clip_cannot_use_missing_previous_speech():
    item=clip("a",5000,5500,3500);item["limit_start_ms"]=4900;item["limit_end_ms"]=6000
    plans,issues=plan_alignment([item],10000)
    assert issues and plans[0]["aligned_start_ms"]>=4900 and plans[0]["available_end_ms"]<=6000


def test_one_impossible_clip_does_not_disable_sharing_for_its_neighbors():
    clips=[clip('a',0,500,1200),clip('b',600,3000,1200),clip('bad',3200,3300,7000),
           clip('d',4000,4500,1000),clip('e',4600,7000,1000)]
    plans,issues=plan_alignment(clips,7500)
    assert [i['segment_id'] for i in issues]==['bad']
    assert [p['segment_id'] for p in plans]==[c['segment_id'] for c in clips]
    assert plans[0]['speed']==1 and plans[1]['aligned_start_ms']>=1200


def test_adaptive_schedules_preserve_order_bounds_and_codec_headroom():
    rng=random.Random(42)
    for _ in range(60):
        clips=[];start=0
        for i in range(12):
            duration=rng.randint(700,3000)
            clips.append(clip(str(i),start,start+duration,rng.randint(500,3500)))
            start+=duration+rng.randint(0,800)
        plans,issues=plan_alignment(clips,start+1000)
        if issues:continue
        previous=0
        for p in plans:
            aligned=p["aligned_start_ms"]
            assert previous<=aligned+.05
            assert abs(aligned-p["start_ms"])<=2000.05
            assert 1<=p["speed"]<=1.15
            previous=aligned+p["speech_ms"]/p["speed"]
        assert previous<=start+1000+.05


def make_wave(path,parts):
    samples=array("h")
    for milliseconds,amplitude in parts:samples.extend([amplitude]*(milliseconds*16))
    with wave.open(str(path),"wb") as audio:
        audio.setparams((1,2,16000,0,"NONE","not compressed"));audio.writeframes(samples.tobytes())
    return samples


def test_pause_compaction_preserves_every_voiced_sample_including_quiet_speech(tmp_path):
    source=tmp_path/"natural.wav";output=tmp_path/"compact.wav"
    original=make_wave(source,[(400,0),(300,2000),(150,20),(700,0),(300,2500),(500,0)])
    before=source.read_bytes()
    info=compact_pauses(source,output,analyze_wave(source),dict(pause_ratio=.2))
    with wave.open(str(output)) as audio:
        samples=array("h");samples.frombytes(audio.readframes(audio.getnframes()))
    assert [v for v in samples if v]!=[]
    assert [v for v in samples if v]==[v for v in original if v]
    assert info["saved_ms"]>500
    assert source.read_bytes()==before


def test_silent_input_is_not_mistaken_for_removable_speech(tmp_path):
    source=tmp_path/"silent.wav";output=tmp_path/"compact.wav"
    make_wave(source,[(500,0)])
    result=compact_pauses(source,output,analyze_wave(source),{})
    assert result["saved_ms"]==0


def test_subtitles_follow_dubbing_but_original_evidence_is_unchanged(tmp_path):
    project=json.loads((Path(__file__).parents[1]/"examples/project.json").read_text(encoding="utf8"))
    before=copy.deepcopy(project)
    plans=[dict(segment_id=s["id"],start_ms=s["start_ms"],end_ms=s["end_ms"],
                aligned_start_ms=s["start_ms"]+200,speech_ms=s["end_ms"]-s["start_ms"],speed=1)
           for s in project["segments"]]
    timing=dubbing_timing(plans,project["source"]["duration_ms"])
    cues=build_cues(project,timing)
    plan=dict(schema_version="1.0",illustrative=False,project_id=project["project_id"],project_revision=project["revision"],
              source_sha256=project["source"]["sha256"],cues=cues,artifacts=[],export_artifact_ids=[],dubbing_timing=timing)
    validate_render_plan(plan,project,tmp_path)
    assert project==before
    assert cues[0]["start_ms"]==project["segments"][0]["start_ms"]+200
    plan.pop("dubbing_timing")
    with pytest.raises(ValueError,match="INVALID_CUE"):validate_render_plan(plan,project,tmp_path)


@pytest.mark.parametrize("change",["duplicate","missing","drift","overlap","speaker"])
def test_rejects_invalid_dubbing_maps(tmp_path,change):
    project=json.loads((Path(__file__).parents[1]/"examples/project.json").read_text(encoding="utf8"))
    timing=[dict(segment_id=s["id"],start_ms=s["start_ms"],end_ms=s["end_ms"]) for s in project["segments"]]
    if change=="duplicate":timing.append(timing[0])
    elif change=="missing":timing.pop()
    elif change=="drift":timing[0]["start_ms"]+=2100
    elif change=="overlap":timing[0]["end_ms"]=timing[1]["start_ms"]+1
    elif change=="speaker":
        project["segments"][1]["speaker_id"]="student";timing[0]["end_ms"]=timing[1]["start_ms"]+1
    plan=dict(schema_version="1.0",illustrative=False,project_id=project["project_id"],project_revision=project["revision"],
              source_sha256=project["source"]["sha256"],cues=[],artifacts=[],export_artifact_ids=[],dubbing_timing=timing)
    with pytest.raises(ValueError,match="INVALID_CUE"):validate_render_plan(plan,project,tmp_path)

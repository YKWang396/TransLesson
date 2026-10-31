from array import array
import json
from pathlib import Path
import threading
import wave

import pytest

from mathbridge.application.speech_quality import compare_speech, signal_quality, normalized_chinese, VERSION
from mathbridge.media.engine import MediaError


def pcm(path, amplitude, seconds=1):
    with wave.open(str(path), "wb") as w:
        w.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
        w.writeframes(array("h", [amplitude, -amplitude] * round(8000*seconds)).tobytes())
    return path


@pytest.mark.parametrize("amplitude", [0, 5, 22, 52])
def test_rejects_nonzero_model_mute_noise(tmp_path, amplitude):
    with pytest.raises(MediaError, match="TTS_SILENT"):
        signal_quality(pcm(tmp_path/"mute.wav", amplitude))


def test_accepts_quiet_but_audible_speech(tmp_path):
    assert signal_quality(pcm(tmp_path/"voice.wav", 180))["rms"] == 180


@pytest.mark.parametrize("expected,heard", [
    ("连接这两个点，就能得到整条直线。", "就能得到整条直线"),
    ("如果我做得还算不错，我想它恰好会经过，不是恰好，是它被安排成经过那个点。", "我想它恰好会经过，不是恰好，是它被安排成经过那个点。"),
    ("所以我认为第二条线就是这一条，而这就是那个至关重要的、同时位于两条直线上的点。", "所以我认为第二条线就是这条而这就是那个至关重要的"),
    ("再说一次，我不想写下一个冗长的定义。", "再说一次"),
    ("光合作用主要发生在叶绿体中，叶绿素负责吸收光能。", "叶绿素负责吸收光能"),
    ("接下来我们比较两种历史材料，讨论它们记录事件的不同角度。", "接下来我们比较两种历史材料"),
])
def test_detects_real_course_missing_prefix_and_suffix(expected, heard):
    assert compare_speech(expected, heard, "zh-CN")["suspected_omission"]


def test_tolerates_asr_typos_digits_and_punctuation():
    result = compare_speech("这是M I T课程十八点零六线性代数的第一讲。", "这是MIT课程18.06，线形代数的第1讲", "zh-CN")
    assert not result["suspected_omission"]
    assert not compare_speech("对吗？", "对吧", "zh-CN")["suspected_omission"]


def test_mathematical_numbers_are_preserved_in_short_utterances():
    assert normalized_chinese("十八点零六，一千零二") == normalized_chinese("18.06，1002")
    assert not compare_speech("二加三等于五。", "2加3等于5", "zh-CN")["suspected_omission"]
    assert compare_speech("二加三等于五。", "3等于5", "zh-CN")["suspected_omission"]


def test_english_missing_clause_and_recognition_errors():
    assert compare_speech("When we connect these two points we get the whole line", "we get the whole line", "en")["suspected_omission"]
    assert not compare_speech("When we connect these two points we get the whole line", "when we connect these two point we get a whole line", "en")["suspected_omission"]


def test_worker_rechecks_old_mute_cache_and_commits_only_verified_regeneration(tmp_path, monkeypatch):
    from mathbridge.domain.project import file_hash, segment_fingerprint
    from mathbridge.storage.project_store import atomic_json
    from mathbridge.worker.__main__ import execute
    from mathbridge.providers.tts import gpt_sovits
    from mathbridge.application import voices
    project=json.loads((Path(__file__).parents[1]/'examples/project.json').read_text(encoding='utf8'))
    project['segments']=project['segments'][:1]
    project['notes']=[]
    speaker=project['speakers'][0]
    speaker.update(voice_mode='reference',voice_authorized=True,voice_profile=dict(provider='gpt_sovits',
        reference_start_ms=0,reference_end_ms=4000,reference_text='This is a reference.',reference_language='en',speed=1.))
    speaker['reference_evidence']=dict(id='ref-teacher',kind='speech',start_ms=0,end_ms=4000)
    source=pcm(tmp_path/'source.wav',180)
    project['source']['sha256']=file_hash(source)
    audio=tmp_path/'audio/source-16k.wav';audio.parent.mkdir()
    audio.write_bytes(source.read_bytes())
    atomic_json(audio.with_suffix('.json'),dict(source_sha256=project['source']['sha256'],sha256=file_hash(audio)))
    fp=segment_fingerprint(project,project['segments'][0],{})
    cached=tmp_path/f'audio/tts/{fp}.wav';cached.parent.mkdir()
    pcm(cached,22)
    atomic_json(cached.with_suffix('.json'),dict(sha256=file_hash(cached),input_fingerprint=fp,input_revision=1))
    monkeypatch.setattr(voices,'prepare_reference',lambda *a,**k:source)
    monkeypatch.setattr(voices,'prepare_auxiliary_references',lambda *a,**k:[])
    calls=[]
    def synthesize(*args,verify=None,**kwargs):
        output=args[5];calls.append(output);pcm(output,180);verify(output);return output
    monkeypatch.setattr(gpt_sovits,'synthesize',synthesize)
    command=dict(stage='dub',request_id='qa-old-mute',project=project,settings={},options=dict(sample_only=True),
                 project_dir=str(tmp_path),source_path=str(source))
    result=execute(command,threading.Event(),lambda event:None)
    assert len(calls)==1 and result['metadata']['sample']
    assert json.loads(cached.with_suffix('.json').read_text('utf8'))['quality']['version']==VERSION
    execute(command,threading.Event(),lambda event:None)
    assert len(calls)==1  # A passed, unchanged cache is reused.


def test_target_context_never_reuses_another_speaker_or_changed_audio(tmp_path):
    from mathbridge.application.voices import cached_target_reference
    from mathbridge.domain.project import file_hash, segment_fingerprint
    from mathbridge.storage.project_store import atomic_json
    project=json.loads((Path(__file__).parents[1]/'examples/project.json').read_text(encoding='utf8'))
    segment=project['segments'][0]
    segment['tts_text']='这是一段已经检查通过的完整课程配音。'
    fp=segment_fingerprint(project,segment,{})
    path=tmp_path/f'audio/tts/{fp}.wav';path.parent.mkdir(parents=True)
    pcm(path,180,4)
    marker=dict(input_fingerprint=fp,sha256=file_hash(path),quality=dict(version=VERSION,method='local-asr',
        coverage=.98,duration_ms=4000,suspected_omission=False))
    atomic_json(path.with_suffix('.json'),marker)
    found=cached_target_reference(project,'teacher','another',tmp_path,{})
    assert found and found[0]==path
    assert cached_target_reference(project,'student','another',tmp_path,{}) is None
    assert cached_target_reference(project,'teacher',segment['id'],tmp_path,{}) is None
    pcm(path,300,4)
    assert cached_target_reference(project,'teacher','another',tmp_path,{}) is None

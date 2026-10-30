import copy
import json

from mathbridge.application.reference import load_reference_words,reference_for_range,suggest_reference


def project(segments,language="en"):
    return dict(source=dict(sha256="source",duration_ms=120000),languages=dict(source=language),segments=segments)


def segment(identifier,start,end,text,speaker="teacher"):
    return dict(id=identifier,start_ms=start,end_ms=end,source_text=text,speaker_id=speaker,
                target_text="这是译文，不是参考原话。",subtitle_plain="译文")


def test_prefills_original_text_time_and_language_without_mutating_project():
    p=project([segment("a",1000,7000,"This is the original sentence.")])
    before=copy.deepcopy(p)
    candidate=suggest_reference(p,"teacher")
    assert (candidate["start_ms"],candidate["end_ms"])==(1000,7000)
    assert candidate["reference_text"]==p["segments"][0]["source_text"]
    assert candidate["reference_language"]=="en"
    assert p==before


def test_combines_short_fragments_of_one_speaker_and_respects_preferred_sentence():
    p=project([segment("a",0,1500,"This is"),segment("b",1550,5000,"one complete sentence."),
               segment("c",6000,12000,"Another complete sentence.")])
    candidate=suggest_reference(p,"teacher",preferred_segment_id="a")
    assert candidate["reference_text"]=="This is one complete sentence."
    assert candidate["segment_ids"]==["a","b"]
    assert suggest_reference(p,"teacher",preferred_segment_id="c")["segment_ids"]==["c"]


def test_never_joins_other_speakers_or_long_pauses_to_reach_three_seconds():
    p=project([segment("a",0,1500,"Hello."),segment("b",1500,3000,"Yes.","student"),
               segment("c",4000,5500,"Thank you.")])
    assert suggest_reference(p,"teacher") is None
    assert reference_for_range(p,"teacher",0,5000) is None
    assert suggest_reference(p,"nobody") is None


def test_long_segment_is_not_blindly_cut_with_its_entire_transcript():
    p=project([segment("long",0,20000,"one two three four five six seven eight nine ten")])
    assert suggest_reference(p,"teacher") is None
    assert reference_for_range(p,"teacher",0,6000) is None


def test_verified_whole_words_supply_an_exact_shorter_reference():
    words=[dict(text=word,start_ms=i*1500,end_ms=(i+1)*1500) for i,word in enumerate("one two three four five six seven eight nine ten".split())]
    p=project([segment("long",0,15000,"one two three four five six seven eight nine ten")])
    candidate=suggest_reference(p,"teacher",words)
    assert candidate["reference_text"]=="one two three four"
    assert candidate["start_ms"]==0 and candidate["end_ms"]==6000
    assert candidate["word_timed"]
    assert reference_for_range(p,"teacher",100,6000,words) is None  # Cuts the first word.
    assert reference_for_range(p,"teacher",1500,6000,words)["reference_text"]=="two three four"
    p["segments"][0]["source_text"]="Edited text no longer matching the original recognition."
    assert suggest_reference(p,"teacher",words) is None


def test_chinese_word_boundaries_are_not_invented_from_character_counts():
    words=[dict(text="这是",start_ms=0,end_ms=1800),dict(text="完整的",start_ms=1800,end_ms=3500),
           dict(text="中文句子。",start_ms=3500,end_ms=6000),dict(text="接下来讲课。",start_ms=6000,end_ms=15000)]
    p=project([segment("long",0,15000,"这是完整的中文句子。接下来讲课。")],"zh-CN")
    c=suggest_reference(p,"teacher",words)
    assert c["reference_text"]=="这是完整的中文句子。"
    assert (c["start_ms"],c["end_ms"])==(0,6000)
    assert reference_for_range(p,"teacher",1000,6000,words) is None


def test_reference_language_follows_the_speakers_actual_source_text():
    p=project([segment("a",0,6000,"我们从这个问题开始讲起。")])
    assert suggest_reference(p,"teacher")["reference_language"]=="zh"
    p=project([segment("a",0,1500,"This is"),segment("b",1500,6000,"an English sentence.")],"zh-CN")
    c=suggest_reference(p,"teacher",preferred_segment_id="a")
    assert c["reference_text"]=="This is an English sentence."
    assert c["reference_language"]=="en"


def test_raw_timings_require_matching_source_and_normalized_audio_hash(tmp_path):
    p=project([])
    audio=tmp_path/"audio";audio.mkdir()
    marker=dict(source_sha256="source",sha256="audio")
    raw=dict(inputs=dict(audio_sha256="audio"),segments=[dict(words=[dict(word=" Hello",start=1,end=2)])])
    (audio/"source-16k.json").write_text(json.dumps(marker),encoding="utf8")
    (audio/"asr-raw.json").write_text(json.dumps(raw),encoding="utf8")
    assert len(load_reference_words(p,tmp_path))==1
    marker["source_sha256"]="different-course"
    (audio/"source-16k.json").write_text(json.dumps(marker),encoding="utf8")
    assert load_reference_words(p,tmp_path)==[]

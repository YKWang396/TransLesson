from __future__ import annotations

import copy
import io
import json
from pathlib import Path
import threading

import pytest

from mathbridge.domain.project import validate_project
from mathbridge.math.plain import display_math
from mathbridge.providers.translation.client import TranslationError
from mathbridge.providers.translation.course import organize_sentences, generate_handout
from mathbridge.providers.translation.course import review_sentence_boundaries
from mathbridge.providers.translation.deepseek import translate


@pytest.fixture
def course():
    project = json.loads((Path(__file__).parents[1]/"examples/project.json").read_text(encoding="utf-8"))
    project["formulas"], project["glossary"], project["notes"] = [], [], []
    texts = ["This is linear", "algebra. We solve systems.", "Two plus three equals five. Then we study matrices."]
    project["segments"] = [dict(id=f"seg-{i+1}", speaker_id="teacher", start_ms=i*6000,
        end_ms=(i+1)*6000, source_text=text, target_text="旧译文", subtitle_plain="旧字幕", tts_text="旧读法",
        formula_ids=[], term_ids=[], review="needs_review") for i, text in enumerate(texts)]
    project["source"]["duration_ms"] = 60000
    return project


def settings(tmp_path):
    return dict(text_provider="offline", model="test-local", api_base="http://127.0.0.1:8080/v1",
                budget_cny=1, max_calls=100, input_price_per_million=1, output_price_per_million=4,
                _project_dir=str(tmp_path))


def fake_api(monkeypatch, reply):
    import urllib.request
    sent = []
    class Opener:
        def open(self, request, timeout):
            payload = json.loads(request.data)
            context = json.loads(payload["messages"][1]["content"])
            sent.append(payload)
            data = {"repairs":[]} if "boundaries" in context else reply(context, len(sent))
            return io.BytesIO(json.dumps({"model":"test-local", "usage":{"prompt_tokens":100,"completion_tokens":80},
                "choices":[{"finish_reason":"stop","message":{"content":json.dumps(data,ensure_ascii=False)}}]}).encode())
    monkeypatch.setattr(urllib.request, "build_opener", lambda *a, **k: Opener())
    return sent


def test_ai_merges_fragments_splits_long_asr_segments_and_rebinds_notes(course, tmp_path, monkeypatch):
    before = copy.deepcopy(course)
    course["notes"] = [dict(id="old-note", kind="course", heading="旧节", body="旧内容", segment_ids=["seg-2"], formula_ids=[], review="confirmed")]
    sent = fake_api(monkeypatch, lambda context, call: {"ends":[3,6,12,len(context["tokens"])-1]})
    data = organize_sentences(course, settings(tmp_path))
    assert data["segments"][0]["source_text"] == "This is linear algebra."
    assert data["segments"][1]["source_text"] == "We solve systems."
    assert " ".join(s["source_text"] for s in data["segments"]) == " ".join(s["source_text"] for s in course["segments"])
    assert len(data["notes"][0]["segment_ids"]) == 2
    assert data["notes"][0]["review"] == "needs_review"
    assert all(not s["target_text"] for s in data["segments"])
    assert course["segments"] == before["segments"]
    validate_project({key: data[key] for key in course})
    again = organize_sentences(course, settings(tmp_path))
    assert again["segments"] == data["segments"] and again["usage"]["calls"] == 0
    assert len(sent) == 2


def test_window_tail_continues_into_next_window_without_losing_words(course, tmp_path, monkeypatch):
    words = ["word"]*419+["linear", "algebra."]+["word"]*30
    course["segments"] = [{**course["segments"][0], "source_text":" ".join(words),"end_ms":60000}]
    def reply(context, call):
        if call == 1:
            return {"ends": list(range(19,400,20))+[419]}
        return {"ends": [len(context["tokens"])-1]}
    sent = fake_api(monkeypatch, reply)
    data = organize_sentences(course, settings(tmp_path))
    assert len(sent) == 3
    assert [word for s in data["segments"] for word in s["source_text"].split()] == words
    assert any("linear algebra." in s["source_text"] for s in data["segments"])
    validate_project({key: data[key] for key in course})


def test_ai_never_merges_speakers(course, tmp_path, monkeypatch):
    course["speakers"].append(dict(id="student",label="学生",voice_mode="standard"))
    course["segments"][1]["speaker_id"] = "student"
    sent = fake_api(monkeypatch, lambda context, call: {"ends":[len(context["tokens"])-1]})
    data = organize_sentences(course, settings(tmp_path))
    assert [s["speaker_id"] for s in data["segments"]] == ["teacher","student","teacher"]
    assert len(sent) == 3


def test_boundary_review_repairs_false_asr_periods_and_preserves_independent_sentences(course):
    texts=["Can I put that in different words?", "in linear combination words.",
           "That's the normal nice case.", "Now we describe a row picture."]
    units=[dict(course['segments'][0],id=f'seg-{i}',source_text=text,start_ms=i*4000,end_ms=(i+1)*4000)
           for i,text in enumerate(texts)]
    class Client:
        usage={}
        def json(self,prompt,context,validate):
            return validate({'repairs':[{'left':0,'fragment':texts[1],
                'reason':'The attached prepositional phrase depends on the preceding question.'}]})
    output,mapping=review_sentence_boundaries(units,Client(),{},lambda *a:None)
    assert len(output)==3
    assert output[0]['source_text']==' '.join(texts[:2])
    assert output[1]['source_text']==texts[2] and output[2]['source_text']==texts[3]
    assert mapping['seg-0']==mapping['seg-1']
    assert not output[0]['tts_text'] and units[0]['tts_text']=='旧读法'


def test_boundary_review_owns_every_seam_and_cannot_join_speakers(course):
    units=[dict(course['segments'][0],id=f'seg-{i}',source_text=f'Sentence {i}.',start_ms=i*1000,end_ms=(i+1)*1000)
           for i in range(30)]
    units[24]['speaker_id']='student'
    seen=[]
    class Client:
        usage={}
        def json(self,prompt,context,validate):
            seen.extend(context['boundaries'])
            with pytest.raises(ValueError):
                validate({'repairs':[{'left':23,'fragment':'Sentence 23.','reason':'This invented repair crosses a speaker.'}]})
            return validate({'repairs':[]})
    output,_=review_sentence_boundaries(units,Client(),{},lambda *a:None)
    assert set(seen)==set(range(29))-{23,24}
    assert len(output)==len(units)


def test_boundary_review_defers_context_only_proposals_to_their_owner(course):
    units=[dict(course['segments'][0],id=f'seg-{i}',source_text=f'Sentence {i}.',start_ms=i*1000,end_ms=(i+1)*1000)
           for i in range(30)]
    units[25]['source_text']='Can we express that differently?'
    units[26]['source_text']='in linear combination words.'
    repair=dict(left=25,fragment=units[26]['source_text'],reason='This prepositional phrase continues the preceding question.')
    saved={}
    class Client:
        usage={}
        def json(self,prompt,context,validate):
            # Both windows can see this boundary, but only one owns it.
            assert 25 in {u['index'] for u in context['units']}
            return validate({'repairs':[repair]})
    output,mapping=review_sentence_boundaries(units,Client(),saved,lambda key,value:saved.update({key:value}))
    assert saved['review-0']=={'repairs':[]}
    assert saved['review-24']=={'repairs':[repair]}
    assert len(output)==29 and mapping['seg-25']==mapping['seg-26']
    assert ' '.join(s['source_text'] for s in output)==' '.join(s['source_text'] for s in units)


@pytest.mark.parametrize("text,bad_end", [("The course web page, which has exercises, is available.",4),
                                         ("This is linear algebra.",2),
                                         ("Shall we check that point, which is x=1 and y=2?",4),
                                         ("If the columns lie in one plane, we are in trouble.",7),
                                         ("Somehow that's the point where the planes met.",3)])
def test_ai_repairs_grammar_and_math_term_boundaries(course,tmp_path,monkeypatch,text,bad_end):
    course["segments"] = [{**course["segments"][0],"source_text":text}]
    sent = fake_api(monkeypatch, lambda context,call:{"ends":([bad_end,len(context["tokens"])-1] if call == 1 else [len(context["tokens"])-1])})
    data = organize_sentences(course,settings(tmp_path))
    assert len(sent) == 1
    assert [s["source_text"] for s in data["segments"]] == [text]


@pytest.mark.parametrize("ends", [[-1,22],[1,1,22],[2,1,22],[1],[True,22]])
def test_invalid_boundary_response_cannot_drop_or_duplicate_words(course,tmp_path,monkeypatch,ends):
    before = copy.deepcopy(course)
    sent = fake_api(monkeypatch,lambda context,call:{"ends":ends})
    with pytest.raises(TranslationError,match="INVALID_TRANSLATION") as error:
        organize_sentences(course,settings(tmp_path))
    assert len(sent) == error.value.usage["calls"] == 2
    assert course == before


def test_cancelled_paid_sentence_batch_resumes_without_repaying(course,tmp_path,monkeypatch):
    fake_api(monkeypatch,lambda context,call:{"ends":[len(context["tokens"])-1]})
    cancel = threading.Event()
    def progress(*args):
        cancel.set()
    # Cancellation immediately after the final unit must also be observed.
    with pytest.raises(TranslationError,match="CANCELLED"):
        organize_sentences(course,settings(tmp_path),cancel,progress)
    cancel.clear()
    data = organize_sentences(course,settings(tmp_path),cancel)
    assert data["usage"]["calls"] == 0


def test_handout_uses_two_pass_topic_synthesis_and_replaces_old_transcript(course,tmp_path,monkeypatch):
    course["notes"] = [dict(id="old",kind="course",heading="课程片段 1",body="照抄",segment_ids=["seg-1"],formula_ids=[],review="needs_review")]
    note = dict(heading="线性方程组的视角",body="学习重点：把方程组与矩阵联系起来。\n课堂例子：2+3=5。",
                segment_ids=["s1","s2","s3"],formula_ids=[])
    sent = fake_api(monkeypatch,lambda context,call:{"notes":[note]})
    data = generate_handout(course,settings(tmp_path))
    assert len(sent) == 2
    assert "segments" in json.loads(sent[0]["messages"][1]["content"])
    assert "chapter_digests" in json.loads(sent[1]["messages"][1]["content"])
    assert data["notes"][0]["heading"] == note["heading"]
    assert data["notes"][0]["id"] != "old"
    assert data["notes"][0]["segment_ids"] == ["seg-1","seg-2","seg-3"]
    assert course["notes"][0]["body"] == "照抄"
    validate_project({**course,"notes":data["notes"]})
    assert generate_handout(course,settings(tmp_path))["usage"]["calls"] == 0


def test_handout_rejects_fabricated_evidence(course,tmp_path,monkeypatch):
    fake_api(monkeypatch,lambda context,call:{"notes":[dict(heading="结论",body="不存在的课程内容",segment_ids=["invented"],formula_ids=[])]})
    with pytest.raises(TranslationError,match="unknown"):
        generate_handout(course,settings(tmp_path))


@pytest.mark.parametrize("source,expected", [
    ("two plus three equals five", "2+3=5"), ("2 plus 3", "2+3"),
    ("2 加 3 等于 5", "2+3=5"), ("x plus one", "x+1"),
    ("one over x plus one", "one over x plus one"),
    ("example plus three", "example plus three"), ("不要把 2 加 3 算成 5", "不要把 2+3 算成 5"),
])
def test_math_notation_keeps_operation_order_and_never_evaluates(source,expected):
    assert display_math(source) == expected


def test_translation_uses_symbols_for_subtitle_but_preserves_spoken_reading(course,tmp_path,monkeypatch):
    course["segments"] = [course["segments"][2]]
    fake_api(monkeypatch,lambda context,call:{"segments":[dict(id="seg-3",target_text="2 加 3 等于 5。",
        subtitle_plain="2 加 3 等于 5。",tts_text="二加三等于五。")],"formulas":[],"notes":[]})
    result = translate(course,settings(tmp_path))
    assert result["segments"][0]["subtitle_plain"] == "2+3=5。"
    assert result["segments"][0]["tts_text"] == "二加三等于五。"

"""Exercise provider protocols through actual course tasks, without paid network calls."""
from __future__ import annotations

import copy
import io
import json
from pathlib import Path
import urllib.error
import urllib.request

import pytest

from mathbridge.domain.project import validate_project
from mathbridge.providers.translation.client import TextClient, TranslationError
from mathbridge.providers.translation.course import generate_handout, organize_sentences
from mathbridge.providers.translation.deepseek import translate
from mathbridge.providers.translation.providers import credential_namespace, preset
from mathbridge.ui.review_workflow import review_guidance


VENDORS = ["deepseek", "qwen", "doubao", "kimi", "openai", "custom"]


def configuration(vendor, root=None):
    values = dict(preset(vendor), text_provider=vendor, api_key="fake-test-key",
                  allow_text_api=True, budget_cny=20, max_calls=100)
    if vendor == "custom":
        values.update(api_base="https://compatible.example/v1", model="custom-text-model")
    if root: values["_project_dir"] = str(root)
    return values


def check_protocol(vendor, payload):
    # Strict fake servers reject parameters belonging to a different vendor.
    shared = {"model", "messages", "stream", "response_format"}
    extra = {"max_tokens", "temperature"}
    if vendor in {"openai", "kimi"} or vendor == "custom" and payload["model"].startswith("gpt-"):
        extra = {"max_completion_tokens", "reasoning_effort"}
    elif vendor == "qwen":
        extra |= {"enable_thinking"}
        assert payload["enable_thinking"] is False
    elif vendor in {"deepseek", "doubao"}:
        extra |= {"thinking"}
        assert payload["thinking"] == {"type": "disabled"}
    assert set(payload) == shared | extra
    assert payload["response_format"] == {"type": "json_object"}
    assert payload["stream"] is False
    if vendor == "kimi":
        assert payload["reasoning_effort"] == "low"


def fake_api(monkeypatch, callback, vendor):
    sent = []
    class Opener:
        def open(self, request, timeout):
            payload = json.loads(request.data)
            check_protocol(vendor, payload)
            sent.append((request, copy.deepcopy(payload)))
            data = callback(payload, len(sent))
            message = {"content": json.dumps(data, ensure_ascii=False)}
            if vendor == "kimi": message["reasoning_content"] = "test reasoning preserved for repair"
            return io.BytesIO(json.dumps({"model": payload["model"],
                "usage": {"prompt_tokens": 100, "completion_tokens": 80,
                          "prompt_tokens_details": {"cached_tokens": 25}},
                "choices": [{"finish_reason": "stop", "message": message}]}).encode())
    monkeypatch.setattr(urllib.request, "build_opener", lambda *a, **k: Opener())
    return sent


@pytest.fixture
def lesson():
    project = json.loads((Path(__file__).parents[1]/"examples/project.json").read_text(encoding="utf-8"))
    project["formulas"], project["glossary"], project["notes"] = [], [], []
    for segment, text in zip(project["segments"], ["This is linear", "algebra. Two plus three equals five."]):
        segment.update(source_text=text, target_text="", subtitle_plain="", tts_text="",
                       formula_ids=[], term_ids=[], review="needs_review")
    return project


@pytest.mark.parametrize("vendor", VENDORS)
def test_each_provider_runs_sentence_translation_and_teaching_handout(vendor, lesson, tmp_path, monkeypatch):
    def respond(payload, _):
        context = json.loads(payload["messages"][1]["content"])
        if "tokens" in context: return {"ends": [3, len(context["tokens"])-1]}
        if "boundaries" in context: return {"repairs": []}
        if "languages" in context:
            return {"segments": [dict(id=s["id"], target_text="这是线性代数。二加三等于五。",
                subtitle_plain="这是线性代数。2+3=5。", tts_text="这是线性代数。二加三等于五。")
                for s in context["segments"]], "formulas": [], "notes": []}
        return {"notes": [dict(heading="线性代数入门", body="学习重点：理解线性代数的课程主题，保留课堂例子 2+3=5。",
                              segment_ids=["s1", "s2"], formula_ids=[])]}
    sent = fake_api(monkeypatch, respond, vendor)
    config = configuration(vendor, tmp_path)
    before = copy.deepcopy(lesson)
    arranged = organize_sentences(lesson, config)
    assert arranged["segments"][0]["source_text"] == "This is linear algebra."
    assert " ".join(s["source_text"] for s in arranged["segments"]) == " ".join(s["source_text"] for s in lesson["segments"])
    lesson.update({k: arranged[k] for k in ("segments", "formulas", "glossary", "notes")})
    assert review_guidance(lesson, config)["action"] == "translate_missing"
    translated = translate(lesson, config)
    lesson.update({k: translated[k] for k in ("segments", "formulas", "glossary", "notes")})
    notes = generate_handout(lesson, config)
    assert notes["notes"][0]["segment_ids"] == [s["id"] for s in lesson["segments"]]
    validate_project({**lesson, "notes": notes["notes"]})
    assert len(sent) == 5  # boundaries, seam review, translation, chapter digest, final handout
    for result in (arranged, translated, notes):
        assert result["usage"]["provider"] == vendor
        assert result["usage"]["cost_cny"] > 0
        assert result["usage"]["cache_hit_tokens"] > 0
    assert all(r.full_url == config["api_base"]+"/chat/completions" for r, _ in sent)
    # Completed work resumes; changing provider must invalidate all paid caches.
    generate_handout(lesson, config)
    assert len(sent) == 5
    config["text_provider"] = "custom" if vendor != "custom" else "deepseek"
    second = fake_api(monkeypatch, respond, config["text_provider"])
    generate_handout(lesson, config)
    assert len(second) == 2
    assert before["segments"][0]["source_text"] == "This is linear"


@pytest.mark.parametrize("task", ["json", "translation"])
def test_kimi_repair_preserves_reasoning_message(task, lesson, monkeypatch):
    def respond(payload, number):
        if number == 1: return {"invalid": True}
        assistant = payload["messages"][-2]
        assert assistant["role"] == "assistant"
        assert assistant["reasoning_content"] == "test reasoning preserved for repair"
        if task == "json": return {"ok": True}
        context = json.loads(payload["messages"][1]["content"])
        return {"segments": [dict(id=s["id"], target_text="这是测试译文。", subtitle_plain="这是测试译文。", tts_text="这是测试译文。")
                              for s in context["segments"]], "formulas": [], "notes": []}
    sent = fake_api(monkeypatch, respond, "kimi")
    config = configuration("kimi")
    if task == "json":
        def validate(data):
            if data != {"ok": True}: raise ValueError("wrong structure")
            return data
        assert TextClient(config).json("Return JSON", {}, validate) == {"ok": True}
    else: translate(lesson, config)
    assert len(sent) == 2


def test_full_endpoint_and_optional_json_mode(monkeypatch):
    settings = configuration("custom")
    settings.update(api_base="https://compatible.example/v1/chat/completions/", text_json_mode=False, model="gpt-5-test")
    captured = []
    class Opener:
        def open(self, request, timeout):
            captured.append(request)
            payload = json.loads(request.data)
            assert "response_format" not in payload
            assert "thinking" not in payload and "temperature" not in payload
            assert payload["max_completion_tokens"] == 4096
            return io.BytesIO(json.dumps({"usage": {"prompt_tokens": 1, "completion_tokens": 1},
                "choices": [{"finish_reason": "stop", "message": {"content": '{"ok":true}'}}]}).encode())
    monkeypatch.setattr(urllib.request, "build_opener", lambda *a: Opener())
    assert TextClient(settings).json("JSON", {}, lambda value: value)["ok"]
    assert captured[0].full_url == "https://compatible.example/v1/chat/completions"


def test_http_detail_is_useful_redacted_and_not_silently_retried(monkeypatch):
    config = configuration("qwen")
    class Opener:
        calls = 0
        def open(self, request, timeout):
            self.calls += 1
            body = json.dumps({"error": {"message": "unsupported model; received "+config["api_key"]}}).encode()
            raise urllib.error.HTTPError(request.full_url, 400, "bad", {}, io.BytesIO(body))
    opener = Opener()
    monkeypatch.setattr(urllib.request, "build_opener", lambda *a: opener)
    with pytest.raises(TranslationError, match="千问.*HTTP 400.*unsupported model") as error:
        TextClient(config).json("JSON", {}, lambda value: value)
    assert config["api_key"] not in str(error.value)
    assert opener.calls == 1


def test_key_scope_preserves_only_official_legacy_key():
    deepseek = configuration("deepseek")
    assert credential_namespace(deepseek) == "text-api"
    assert credential_namespace({**deepseek, "api_base": "https://API.DEEPSEEK.COM/v1/chat/completions/"}) == "text-api"
    scopes = {credential_namespace(configuration(vendor)) for vendor in VENDORS}
    assert len(scopes) == len(VENDORS)
    qwen = configuration("qwen")
    assert credential_namespace(qwen) != credential_namespace({**qwen, "api_base": "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"})
    assert credential_namespace(deepseek) != credential_namespace({**deepseek, "api_base": "https://proxy.example/v1"})

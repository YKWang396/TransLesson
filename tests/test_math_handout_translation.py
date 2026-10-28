from __future__ import annotations

import copy
import io
import json
from pathlib import Path

import pytest

from mathbridge.math.analysis import analyze, validate_latex
from mathbridge.handout.export import export_handout
from mathbridge.providers.translation.deepseek import translate, TranslationError
from mathbridge.providers.visual_math.pix2text import manual_candidate, recognize


@pytest.fixture
def lesson():
    path = Path(__file__).parents[1] / "examples/project.json"
    return json.loads(path.read_text(encoding="utf-8"))


def test_rules_never_confirm_or_overwrite_source(lesson):
    original = copy.deepcopy(lesson)
    lesson["formulas"] = []
    lesson["segments"][0]["formula_ids"] = []
    lesson["segments"][0]["target_text"] = ""
    lesson["segments"][0]["review"] = "needs_review"
    result = analyze(lesson)
    assert result["formulas"][0]["latex"] == "x^2 + 2x + 1"
    assert result["formulas"][0]["review"] == "needs_review"
    assert result["segments"][0]["source_text"] == original["segments"][0]["source_text"]
    assert len(analyze({**lesson, **result})["formulas"]) == 1
    lesson["segments"][0]["source_text"] = "one over x plus one"
    ambiguous = analyze(lesson)["formulas"][0]
    assert ambiguous["latex"] is None
    assert "AMBIGUOUS_DENOMINATOR" in ambiguous["warnings"]


def test_analysis_preserves_confirmed_content_and_extracts_unlocked_terms(lesson):
    before = copy.deepcopy(lesson)
    assert analyze(lesson)["segments"] == before["segments"]
    lesson["segments"][1]["review"] = "needs_review"
    lesson["segments"][1]["source_text"] = "The partial derivative of this polynomial."
    lesson["segments"][1]["term_ids"] = []
    result = analyze(lesson)
    extracted = [term for term in result["glossary"] if term["id"] not in {t["id"] for t in before["glossary"]}]
    assert {term["source"] for term in extracted} == {"partial derivative", "polynomial"}
    assert all(term["locked"] is False for term in extracted)


@pytest.mark.parametrize("latex", [r"\require{html}", r"\href{https://evil.test}{x}", r"\def\x{x}",
    r"\input{secret}", r"\unknown{x}", "{x", "x}", "x%comment", "<script>", "{" * 40 + "x" + "}" * 40])
def test_latex_rejects_unsafe(latex):
    with pytest.raises(ValueError):
        validate_latex(latex)


@pytest.mark.parametrize("latex", [r"\frac{1}{x+1}", r"e^x", r"\begin{pmatrix}1&2\\3&4\end{pmatrix}", r"\sum_{i=1}^{n}x_i"])
def test_latex_safe_subset(latex):
    assert validate_latex(latex) is None


def test_translation_defaults_block_network(lesson):
    with pytest.raises(TranslationError, match="NETWORK_DISABLED"):
        translate(lesson, {})


def _settings():
    return {"provider": "llama_cpp", "model": "explicit-local-model", "budget_cny": 1, "max_calls": 5,
            "api_base": "http://127.0.0.1:8080/v1", "input_price_per_million": 1, "output_price_per_million": 4}


def _reply():
    return {"segments": [{"id": "seg-1", "target_text": "考虑这个多项式 {{formula-1}}。",
        "subtitle_plain": "考虑多项式 x² 加 2x 加 1。", "tts_text": "考虑多项式，x 的平方加二 x 加一。"}],
        "formulas": [{"id": "formula-1", "spoken_target": "x 的平方加二 x 加一"}],
        "notes": [{"heading": "多项式", "body": "讲者给出 {{formula-1}}。", "segment_ids": ["seg-1"], "formula_ids": ["formula-1"]}]}


def _fake_opener(monkeypatch, replies):
    import urllib.request
    sent = []
    class Opener:
        def open(self, request, timeout):
            sent.append(json.loads(request.data))
            body = replies.pop(0)
            if isinstance(body, dict):
                body = json.dumps(body, ensure_ascii=False)
            envelope = {"model": "test-returned-model", "usage": {"prompt_tokens": 100, "completion_tokens": 80},
                "choices": [{"finish_reason": "stop", "message": {"content": body}}]}
            return io.BytesIO(json.dumps(envelope, ensure_ascii=False).encode())
    monkeypatch.setattr(urllib.request, "build_opener", lambda *a, **k: Opener())
    return sent


def test_translation_maps_ids_usage_and_checkpoint(monkeypatch, lesson, tmp_path):
    lesson["segments"][0]["review"] = "needs_review"
    settings = {**_settings(), "_project_dir": str(tmp_path)}
    sent = _fake_opener(monkeypatch, [_reply()])
    result = translate(lesson, settings)
    assert result["segments"][0]["review"] == "needs_review"
    assert result["segments"][1] == lesson["segments"][1]
    assert result["usage"]["calls"] == 1
    assert result["usage"]["cost_cny"] == pytest.approx(0.00042)
    assert sent[0]["model"] == "explicit-local-model"
    assert (tmp_path / "cache/translation-checkpoint.json").is_file()
    assert "api_key" not in (tmp_path / "cache/translation-checkpoint.json").read_text(encoding="utf-8")
    resumed = translate(lesson, settings)
    assert resumed["usage"]["calls"] == 0
    assert resumed["usage"]["resumed_segments"] == 1
    assert len(sent) == 1
    assert resumed["segments"][0]["target_text"] == result["segments"][0]["target_text"]


def test_checkpoint_content_changes_invalidate(monkeypatch, lesson, tmp_path):
    lesson["segments"][0]["review"] = "needs_review"
    settings = {**_settings(), "_project_dir": str(tmp_path), "text_provider": "offline"}
    sent = _fake_opener(monkeypatch, [_reply(), _reply()])
    translate(lesson, settings)
    lesson["segments"][0]["source_text"] += " Changed."
    result = translate(lesson, settings)
    assert len(sent) == 2
    assert result["usage"]["calls"] == 1


def test_missing_only_repair_preserves_593_filled_unconfirmed_segments(monkeypatch, lesson):
    template = copy.deepcopy(lesson["segments"][1])
    template.update(review="needs_review", formula_ids=[], term_ids=[])
    lesson["segments"] = [{**copy.deepcopy(template), "id": f"seg-{number}"} for number in range(1, 595)]
    lesson["segments"][-1].update(target_text="", subtitle_plain="", tts_text="")
    unchanged = copy.deepcopy(lesson["segments"][:-1])
    reply = {"segments": [{"id": "seg-594", "target_text": "重新生成本句译文。",
        "subtitle_plain": "重新生成本句译文。", "tts_text": "重新生成本句译文。"}], "formulas": [], "notes": []}
    sent = _fake_opener(monkeypatch, [reply])
    result = translate(lesson, {**_settings(), "_translation_missing_only": True})
    assert len(sent) == 1
    context = json.loads(sent[0]["messages"][1]["content"])
    assert [item["id"] for item in context["segments"]] == ["seg-594"]
    assert result["segments"][:-1] == unchanged
    assert result["segments"][-1]["target_text"] == "重新生成本句译文。"
    assert all(item["review"] == "needs_review" for item in result["segments"])
    # A subsequent missing-only run has nothing to send, even though no human
    # has confirmed any of these filled drafts.
    sent.clear()
    lesson["segments"] = result["segments"]
    again = translate(lesson, {**_settings(), "_translation_missing_only": True})
    assert sent == []
    assert again["usage"]["calls"] == 0


def test_missing_only_and_full_translation_do_not_share_checkpoint(monkeypatch, lesson, tmp_path):
    lesson["segments"][0]["review"] = "needs_review"
    sent = _fake_opener(monkeypatch, [_reply()])
    settings = {**_settings(), "_project_dir": str(tmp_path)}
    translate(lesson, settings)
    result = translate(lesson, {**settings, "_translation_missing_only": True})
    assert len(sent) == 1
    assert result["usage"]["calls"] == 0
    assert result["segments"] == lesson["segments"]


def test_missing_placeholders_repaired_only_once(monkeypatch, lesson):
    lesson["segments"][0]["review"] = "needs_review"
    broken = _reply()
    broken["segments"][0]["target_text"] = "考虑这个多项式。"
    sent = _fake_opener(monkeypatch, [broken, broken])
    with pytest.raises(TranslationError, match="INVALID_TRANSLATION") as caught:
        translate(lesson, _settings())
    assert len(sent) == 2
    assert caught.value.usage["calls"] == 2


def test_prior_spend_reserves_before_request(monkeypatch, lesson):
    lesson["segments"][0]["review"] = "needs_review"
    sent = _fake_opener(monkeypatch, [_reply()])
    with pytest.raises(TranslationError, match="BUDGET_EXCEEDED"):
        translate(lesson, {**_settings(), "usage_spent_cny": 1})
    assert not sent


def test_cancel_after_paid_batch_retains_usage(monkeypatch, lesson, tmp_path):
    lesson["segments"][0]["review"] = "needs_review"
    _fake_opener(monkeypatch, [_reply()])
    cancelled = [False]
    def progress(stage, done, total, message):
        assert stage == "translate"
        cancelled[0] = True
    with pytest.raises(TranslationError, match="CANCELLED") as caught:
        translate(lesson, {**_settings(), "_project_dir": str(tmp_path)},
                  cancel=lambda: cancelled[0], progress=progress)
    assert caught.value.usage["calls"] == 1
    assert caught.value.usage["cost_cny"] > 0
    assert (tmp_path / "cache/translation-checkpoint.json").is_file()


def test_cancel_progress_callback_preserves_provider_cost(monkeypatch, lesson, tmp_path):
    lesson["segments"][0]["review"] = "needs_review"
    _fake_opener(monkeypatch, [_reply()])
    def progress(*args):
        raise InterruptedError("worker cancellation")
    settings = {**_settings(), "_project_dir": str(tmp_path), "_request_id": "req-cancel-progress"}
    with pytest.raises(TranslationError, match="CANCELLED") as caught:
        translate(lesson, settings, progress=progress)
    ledger = json.loads((tmp_path / "cache/api-reservations/req-cancel-progress.json").read_text(encoding="utf-8"))
    assert ledger["usage"]["calls"] == caught.value.usage["calls"] == 1
    assert ledger["usage"]["cost_cny"] > 0
    assert ledger["pending_cost_cny"] == 0


def test_reservation_ledger_exists_before_post_and_survives_abrupt_stop(monkeypatch, lesson, tmp_path):
    import urllib.request
    lesson["segments"][0]["review"] = "needs_review"
    ledger_path = tmp_path / "cache/api-reservations/req-stop-during-post.json"
    class AbruptWorkerStop(BaseException):
        pass
    class Opener:
        def open(self, request, timeout):
            ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
            assert ledger["request_id"] == "req-stop-during-post"
            assert ledger["usage"]["calls"] == 1
            assert ledger["pending_calls"] == 0
            assert ledger["pending_cost_cny"] > 0
            assert "TOP_SECRET_TEST_KEY" not in ledger_path.read_text(encoding="utf-8")
            raise AbruptWorkerStop()
    monkeypatch.setattr(urllib.request, "build_opener", lambda *a, **k: Opener())
    with pytest.raises(AbruptWorkerStop):
        translate(lesson, {**_settings(), "_project_dir": str(tmp_path),
            "_request_id": "req-stop-during-post", "api_key": "TOP_SECRET_TEST_KEY"})
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    assert ledger["pending_cost_cny"] > 0
    assert ledger["usage"]["cost_cny"] == 0


def test_timeout_settles_pending_to_uncertain_cost(monkeypatch, lesson, tmp_path):
    import urllib.request
    lesson["segments"][0]["review"] = "needs_review"
    class Opener:
        def open(self, request, timeout):
            raise TimeoutError("timed out after send")
    monkeypatch.setattr(urllib.request, "build_opener", lambda *a, **k: Opener())
    with pytest.raises(TranslationError, match="PROVIDER_TIMEOUT"):
        translate(lesson, {**_settings(), "_project_dir": str(tmp_path), "_request_id": "req-timeout-ledger"})
    ledger = json.loads((tmp_path / "cache/api-reservations/req-timeout-ledger.json").read_text(encoding="utf-8"))
    assert ledger["pending_cost_cny"] == 0
    assert ledger["usage"]["calls"] == 1
    assert ledger["usage"]["uncertain_requests"] == 1
    assert ledger["usage"]["uncertain_cost_cny"] > 0


def test_ledger_accumulates_paid_structure_repair_calls(monkeypatch, lesson, tmp_path):
    lesson["segments"][0]["review"] = "needs_review"
    broken = _reply()
    broken["segments"][0]["target_text"] = "占位符丢失。"
    _fake_opener(monkeypatch, [broken, _reply()])
    result = translate(lesson, {**_settings(), "_project_dir": str(tmp_path), "_request_id": "req-repair-ledger"})
    ledger = json.loads((tmp_path / "cache/api-reservations/req-repair-ledger.json").read_text(encoding="utf-8"))
    assert ledger["usage"]["calls"] == result["usage"]["calls"] == 2
    assert ledger["usage"]["cost_cny"] == result["usage"]["cost_cny"] > 0
    assert ledger["pending_cost_cny"] == 0


def test_local_route_cannot_be_external(lesson):
    with pytest.raises(TranslationError, match="127.0.0.1"):
        translate(lesson, {**_settings(), "api_base": "http://example.test"})


def test_manual_frame_evidence_is_preserved(tmp_path):
    from PIL import Image
    (tmp_path / "frames").mkdir()
    source = tmp_path / "frames/one.png"
    Image.new("RGB", (100, 60), "white").save(source)
    region = {"frame_path": "frames/one.png", "start_ms": 100, "bbox_xyxy": [0.1, 0.1, 0.9, 0.8]}
    candidate = manual_candidate(tmp_path, region, "x^2")
    assert candidate["review"] == "needs_review"
    assert candidate["evidence"][0]["frame_path"] == "frames/one.png"
    assert source.exists()
    with pytest.raises(RuntimeError, match="MISSING_MODEL"):
        recognize(tmp_path, [region], {})
    with pytest.raises(ValueError):
        manual_candidate(tmp_path, {**region, "frame_path": "../outside.png"})


def test_pdf_rejects_invalid_evidence_before_writing(lesson, tmp_path):
    lesson["notes"][0]["segment_ids"] = ["missing-segment"]
    with pytest.raises(ValueError, match="Unknown handout"):
        export_handout(lesson, tmp_path / "broken.pdf")
    assert not (tmp_path / "broken.pdf").exists()


def test_pdf_embedded_chinese_and_formula(lesson, tmp_path):
    from pypdf import PdfReader
    output = export_handout(lesson, tmp_path / "handout.pdf", tmp_path / "handout.md")
    reader = PdfReader(output)
    assert len(reader.pages) >= 1
    text = "".join(p.extract_text() for p in reader.pages)
    assert "课程先给出多项式" in text
    assert "revision 1" in text
    assert list((tmp_path / "handout-formulas").glob("*.svg"))
    assert list((tmp_path / "handout-formulas").glob("*.tex"))

import copy
import hashlib
import json
from pathlib import Path

import pytest

from mathbridge.application.issues import enriched_issues, locate_issue, obsolete_rule_formula_ids, preservable_formula_ids
from mathbridge.domain.project import export_issues
from mathbridge.math.analysis import _candidate


@pytest.fixture
def lesson():
    return json.loads((Path(__file__).parents[1] / "examples/project.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("source", ["here the x, y plane", "Let me suppose x is 1.",
    "So I'll take x to be 1.", "maybe x equal a half", "minus one x is", "the x-axis", "e x"])
def test_ordinary_variable_mentions_never_invent_exponential_formula(source):
    assert _candidate(source) is None


@pytest.mark.parametrize("source", ["consider e to the x", "e to the power of x", "e 的 x 次方"])
def test_explicit_exponent_still_produces_review_candidate(source):
    candidate = _candidate(source)
    assert candidate[1] == "e^x"
    assert "RULE_CANDIDATE_REQUIRES_REVIEW" in candidate[2]


def test_obsolete_rule_detection_requires_proof_and_preserves_human_decisions(lesson):
    segment, formula = lesson["segments"][0], lesson["formulas"][0]
    segment["source_text"] = "Let me suppose x is one."
    identifier = "formula-" + hashlib.sha256((segment["id"] + "\0e x").encode()).hexdigest()[:16]
    formula.update(id=identifier, raw="e x", latex="e^x", review="needs_review",
                   warnings=["RULE_CANDIDATE_REQUIRES_REVIEW"])
    segment["formula_ids"] = [identifier]
    assert obsolete_rule_formula_ids(lesson) == [identifier]
    formula["review"] = "confirmed"
    assert obsolete_rule_formula_ids(lesson) == []
    formula["review"] = "preserve_source"
    assert obsolete_rule_formula_ids(lesson) == []
    formula["review"] = "needs_review"
    segment["source_text"] = "e x is an expression"
    assert obsolete_rule_formula_ids(lesson) == []
    segment["source_text"] = "the x axis and e x"
    assert obsolete_rule_formula_ids(lesson) == []
    segment["source_text"] = "the x axis"
    formula["id"] = "manually-created"
    segment["formula_ids"] = [formula["id"]]
    assert obsolete_rule_formula_ids(lesson) == []


def test_formula_export_problem_names_sentence_time_and_expression(lesson):
    lesson["formulas"][0]["review"] = "needs_review"
    issue = next(item for item in export_issues(lesson, ["pdf"]) if item["code"] == "FORMULA_AMBIGUOUS")
    assert "第 1 句" in issue["message"]
    assert "00:01.000" in issue["message"]
    assert "x squared plus two x plus one" in issue["message"]
    assert "formula-1" not in issue["message"]
    located = locate_issue(lesson, issue)
    assert located["segment_id"] == "seg-1"
    assert located["segment_index"] == 1
    assert located["action"] == "formula"
    assert located["formula_id"] == "formula-1"
    assert "原音 00:01.000–00:06.000" in located["evidence_summary"]


def test_bulk_issue_navigation_respects_sorted_timeline_and_metadata(lesson):
    lesson["segments"].reverse()
    raw = [dict(code="TTS_TOO_LONG", entity_id="seg-2", message="too long", required_speed=3.21),
           dict(code="MISSING_TRANSLATION", entity_id="seg-1", message="empty")]
    before = copy.deepcopy(raw)
    issues = enriched_issues(lesson, raw)
    assert [item["segment_index"] for item in issues] == [2, 1]
    assert issues[0]["required_speed"] == 3.21
    assert issues[0]["action"] == "edit_tts"
    assert issues[1]["action"] == "edit_subtitle"
    assert raw == before


def test_formula_without_segment_reference_navigates_source_evidence(lesson):
    lesson["segments"][0]["formula_ids"] = []
    located = locate_issue(lesson, dict(code="FORMULA_AMBIGUOUS", entity_id="formula-1"))
    assert located["segment_id"] == "seg-1"
    assert located["start_ms"] == 1000
    unknown = locate_issue(lesson, dict(code="INVALID_PROJECT", message="broken"))
    assert unknown["segment_id"] == ""
    assert unknown["segment_index"] is None


def test_preserve_fallback_requires_original_expression_and_valid_source(lesson, tmp_path):
    formula = lesson["formulas"][0]
    formula["review"] = "needs_review"
    assert preservable_formula_ids(lesson, tmp_path) == ["formula-1"]
    formula["raw"] = "  "
    assert preservable_formula_ids(lesson, tmp_path) == []
    formula["raw"] = "original"
    formula["evidence"] = [{"kind": "speech", "start_ms": 5, "end_ms": 5}]
    assert preservable_formula_ids(lesson, tmp_path) == []
    formula["evidence"][0]["end_ms"] = lesson["source"]["duration_ms"] + 1
    assert preservable_formula_ids(lesson, tmp_path) == []


def test_preserve_frame_requires_existing_safe_image_and_valid_crop(lesson, tmp_path):
    formula = lesson["formulas"][0]
    formula["review"] = "needs_review"
    frame = copy.deepcopy(formula["evidence"][1])
    formula["evidence"] = [frame]
    assert preservable_formula_ids(lesson, tmp_path) == []
    image = tmp_path / frame["frame_path"]
    image.parent.mkdir(parents=True)
    image.write_bytes(b"test-frame")
    assert preservable_formula_ids(lesson, tmp_path) == ["formula-1"]
    assert preservable_formula_ids(lesson) == []
    frame["bbox_xyxy"] = [0.8, 0.2, 0.1, 0.5]
    assert preservable_formula_ids(lesson, tmp_path) == []
    frame["bbox_xyxy"] = [0, 0, 1, 1]
    frame["frame_path"] = "../outside.png"
    assert preservable_formula_ids(lesson, tmp_path) == []


def test_preserve_source_handout_keeps_original_not_unconfirmed_candidate(lesson, tmp_path, monkeypatch):
    from pypdf import PdfReader
    from mathbridge.handout import export as handout
    formula = lesson["formulas"][0]
    formula.update(review="preserve_source", raw="original spoken expression", latex="x^9")
    formula["evidence"] = formula["evidence"][:1]
    rendered = []
    original = handout._render_formulas
    def capture(formulas, destination):
        result = original(formulas, destination)
        rendered.extend(result)
        return result
    monkeypatch.setattr(handout, "_render_formulas", capture)
    pdf, markdown = tmp_path / "preserved.pdf", tmp_path / "preserved.md"
    handout.export_handout(lesson, pdf, markdown)
    text = "".join(page.extract_text() for page in PdfReader(pdf).pages)
    assert "original spoken expression" in text
    assert "未确认公式含义" in text
    assert "original spoken expression" in markdown.read_text(encoding="utf-8")
    assert "x^9" not in markdown.read_text(encoding="utf-8")
    assert rendered == []

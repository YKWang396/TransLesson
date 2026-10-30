"""Guided review decisions and visible-to-derived text consistency."""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from mathbridge.application.coordinator import Coordinator
from mathbridge.application.review import simple_subtitle_changes
from mathbridge.domain.project import PLACEHOLDER, validate_project
from mathbridge.storage.project_store import ProjectStore
from mathbridge.ui.review_workflow import review_guidance


@pytest.fixture
def lesson():
    return json.loads((Path(__file__).parents[1] / "examples/project.json").read_text(encoding="utf-8"))


def test_guide_starts_with_the_missing_prerequisite(lesson, tmp_path):
    assert review_guidance(None, {})["action"] == "import_video"
    lesson["segments"], lesson["notes"] = [], []
    assert review_guidance(lesson, {})["action"] == "configure_asr"
    for name in ("model.bin", "config.json"):
        (tmp_path / name).write_text("test", encoding="utf-8")
    settings = {"whisper_model_path": str(tmp_path)}
    assert review_guidance(lesson, settings)["action"] == "configure_asr"
    (tmp_path / "tokenizer.json").write_text("test", encoding="utf-8")
    assert review_guidance(lesson, settings)["action"] == "transcribe"


def test_guide_offers_manual_or_configured_translation(lesson):
    lesson["segments"][0].update(target_text="", subtitle_plain="", tts_text="", review="needs_review")
    assert review_guidance(lesson, {"text_provider": "manual"})["action"] == "configure_translation"
    settings = dict(text_provider="deepseek", model="test", api_key="test-key", allow_text_api=False)
    assert review_guidance(lesson, settings)["action"] == "configure_translation"
    settings["allow_text_api"] = True
    assert review_guidance(lesson, settings)["action"] == "translate_missing"
    local = dict(text_provider="offline", model="local", api_base="https://example.invalid/v1")
    assert review_guidance(lesson, local)["action"] == "configure_translation"
    local["api_base"] = "http://127.0.0.1:8080/v1"
    assert review_guidance(lesson, local)["action"] == "translate_missing"
    local["api_base"] = "http://[broken"
    assert review_guidance(lesson, local)["action"] == "configure_translation"


def test_guide_makes_sentence_confirmation_optional_and_calls_out_math(lesson):
    lesson["segments"][0]["review"] = "needs_review"
    assert review_guidance(lesson, {})["action"] == "go_dub"
    lesson["segments"][0]["review"] = "confirmed"
    lesson["formulas"][0]["review"] = "needs_review"
    guide = review_guidance(lesson, {})
    assert guide["action"] == "go_dub" and guide["pending_formulas"] == 1
    assert "公式" in guide["description"]
    lesson["formulas"][0]["review"] = "confirmed"
    assert review_guidance(lesson, {})["action"] == "go_dub"
    assert not review_guidance(lesson, {}, busy=True)["enabled"]
    assert not review_guidance(lesson, {}, dirty=True)["enabled"]


def test_guide_resumes_translation_without_repeating_completed_ai_segmentation(lesson):
    for number, segment in enumerate(lesson["segments"]):
        segment.update(id=f"sentence-{number}", target_text="", subtitle_plain="", tts_text="", review="needs_review")
    settings = dict(text_provider="deepseek",model="test",api_key="test-key",allow_text_api=True)
    assert review_guidance(lesson,settings)["action"] == "translate_missing"


def test_simple_formula_edit_keeps_math_and_spoken_reading(lesson):
    text = "现在考虑多项式 x² + 2x + 1。"
    changes = simple_subtitle_changes(lesson, lesson["segments"][0], text)
    assert changes["subtitle_plain"] == text
    assert changes["target_text"] == "现在考虑多项式 {{formula-1}}。"
    assert changes["tts_text"] == "现在考虑多项式 x 的平方加二 x 加一。"
    assert set(PLACEHOLDER.findall(changes["target_text"])) == {"formula-1"}


def test_changed_formula_requires_explicit_review_instead_of_silent_corruption(lesson):
    with pytest.raises(ValueError, match="FORMULA_REVIEW_REQUIRED"):
        simple_subtitle_changes(lesson, lesson["segments"][0], "现在考虑多项式 x² + 3x + 1。")
    with pytest.raises(ValueError):
        simple_subtitle_changes(lesson, lesson["segments"][1], "错误 {{formula-1}}")


def test_variables_are_not_replaced_inside_english_words(lesson):
    lesson["formulas"][0].update(latex="x", spoken_target="the variable x")
    segment = lesson["segments"][0]
    segment.update(target_text="Find {{formula-1}}.", subtitle_plain="Find x.")
    changes = simple_subtitle_changes(lesson, segment, "The expression contains x.")
    assert changes["target_text"] == "The expression contains {{formula-1}}."
    assert changes["tts_text"] == "The expression contains the variable x."


def test_simple_updates_are_saved_and_source_correction_invalidates_translation(lesson, tmp_path):
    store = ProjectStore(tmp_path)
    store.save(lesson)
    coordinator = Coordinator()
    coordinator.project, coordinator.project_dir = copy.deepcopy(lesson), tmp_path
    coordinator.update_simple_subtitle("seg-2", "然后求导。")
    segment = coordinator.project["segments"][1]
    assert all(segment[key] == "然后求导。" for key in ("target_text", "subtitle_plain", "tts_text"))
    assert segment["review"] == "needs_review"
    assert coordinator.project["notes"][0]["review"] == "needs_review"
    assert store.load()["segments"][1]["subtitle_plain"] == "然后求导。"
    coordinator.update_simple_subtitle("seg-2", "然后求导。", changes={"source_text": "Now integrate the expression."})
    segment = coordinator.project["segments"][1]
    assert all(not segment[key] for key in ("target_text", "subtitle_plain", "tts_text"))


def test_fresh_formula_reading_is_not_cleared_as_stale(lesson, tmp_path):
    store = ProjectStore(tmp_path)
    store.save(lesson)
    coordinator = Coordinator()
    coordinator.project, coordinator.project_dir = copy.deepcopy(lesson), tmp_path
    # This new subtitle happens to produce the exact previous custom TTS.
    coordinator.update_simple_subtitle("seg-1", "考虑这个多项式，x² + 2x + 1。")
    assert coordinator.project["segments"][0]["tts_text"] == lesson["segments"][0]["tts_text"]
    validate_project(coordinator.project)
    previous = copy.deepcopy(coordinator.project)
    with pytest.raises(ValueError, match="FORMULA_REVIEW_REQUIRED"):
        coordinator.update_simple_subtitle("seg-1", "公式已经全部删掉。")
    assert coordinator.project == previous


def test_confirming_an_unchanged_subtitle_keeps_custom_spoken_text(lesson, tmp_path):
    store = ProjectStore(tmp_path)
    store.save(lesson)
    coordinator = Coordinator()
    coordinator.project, coordinator.project_dir = copy.deepcopy(lesson), tmp_path
    segment = lesson["segments"][0]
    coordinator.update_simple_subtitle("seg-1", segment["subtitle_plain"], changes={"review": "needs_review"})
    assert coordinator.project["segments"][0]["tts_text"] == segment["tts_text"]


def test_prepare_translation_organizes_sentences_before_analysis_and_translation(lesson, monkeypatch):
    coordinator = Coordinator()
    coordinator.project = lesson
    started = []
    monkeypatch.setattr(coordinator, "_start", lambda stage, options: started.append(stage))
    coordinator.run_stage("prepare_translation")
    assert started == ["sentences"]
    assert coordinator._pipeline == ["analyze", "translate"]


def test_bulk_confirmation_saves_once_and_preserves_formula_review(lesson, tmp_path):
    lesson["segments"][0]["review"] = "needs_review"
    lesson["segments"][1].update(review="needs_review", tts_text="")
    lesson["formulas"][0]["review"] = "needs_review"
    coordinator = Coordinator()
    coordinator.project, coordinator.project_dir = copy.deepcopy(lesson), tmp_path
    result = coordinator.bulk_confirm_segments()
    assert result == dict(confirmed=1, skipped=1)
    assert coordinator.project["revision"] == lesson["revision"] + 1
    assert coordinator.project["segments"][1]["review"] == "needs_review"
    assert coordinator.project["formulas"] == lesson["formulas"]
    assert len(list((tmp_path / "history").glob("*.json"))) == 1
    assert coordinator.bulk_confirm_segments() == dict(confirmed=0, skipped=1)
    assert coordinator.project["revision"] == lesson["revision"] + 1
    coordinator.undo()
    assert coordinator.project["segments"] == lesson["segments"]


def test_merge_preserves_formula_evidence_and_note_references(lesson, tmp_path):
    coordinator = Coordinator()
    coordinator.project, coordinator.project_dir = copy.deepcopy(lesson), tmp_path
    assert coordinator.merge_segments("seg-1", "seg-2") == "seg-1"
    merged, = coordinator.project["segments"]
    assert merged["start_ms"] == 1000 and merged["end_ms"] == 10500
    assert merged["source_text"] == " ".join(segment["source_text"] for segment in lesson["segments"])
    assert merged["tts_text"] == "".join(segment["tts_text"] for segment in lesson["segments"])
    assert merged["formula_ids"] == ["formula-1"] and merged["term_ids"] == ["term-derivative"]
    assert merged["review"] == "needs_review"
    assert coordinator.project["formulas"] == lesson["formulas"]
    assert coordinator.project["notes"][0]["segment_ids"] == ["seg-1"]
    assert coordinator.project["revision"] == lesson["revision"] + 1
    validate_project(coordinator.project)
    coordinator.undo()
    assert coordinator.project["segments"] == lesson["segments"]


def test_bulk_merge_rejects_cross_speaker_duplicate_and_reversed_groups(lesson, tmp_path):
    coordinator = Coordinator()
    coordinator.project, coordinator.project_dir = copy.deepcopy(lesson), tmp_path
    for groups in ([["seg-2", "seg-1"]], [["seg-1", "seg-2"], ["seg-1", "seg-2"]]):
        with pytest.raises(ValueError):
            coordinator.bulk_merge_segments(groups)
    assert coordinator.project == lesson
    coordinator.project["segments"][1]["speaker_id"] = "other"
    with pytest.raises(ValueError, match="说话人"):
        coordinator.merge_segments("seg-1", "seg-2")


def test_repair_obsolete_formula_clears_only_affected_translation(lesson, tmp_path):
    import hashlib
    identifier = "formula-" + hashlib.sha256(b"seg-1\0e x").hexdigest()[:16]
    formula = lesson["formulas"][0]
    formula.update(id=identifier, raw="e x", latex="e^x", review="needs_review", warnings=["RULE_CANDIDATE_REQUIRES_REVIEW"])
    lesson["segments"][0].update(source_text="Take x to solve it.", formula_ids=[identifier], target_text="取 {{" + identifier + "}}。")
    lesson["notes"][0].update(formula_ids=[identifier], body="课程给出 {{" + identifier + "}}。")
    coordinator = Coordinator()
    coordinator.project, coordinator.project_dir = copy.deepcopy(lesson), tmp_path
    assert review_guidance(lesson, {})["action"] == "repair_formulas"
    result = coordinator.repair_obsolete_formulas()
    assert result == dict(removed=1, affected=1, segment_ids=["seg-1"])
    assert coordinator.project["formulas"] == []
    assert coordinator.project["segments"][1] == lesson["segments"][1]
    assert coordinator.project["segments"][0]["source_text"] == "Take x to solve it."
    assert all(not coordinator.project["segments"][0][field] for field in ("target_text", "subtitle_plain", "tts_text"))
    assert "{{" not in coordinator.project["notes"][0]["body"]
    validate_project(coordinator.project)
    coordinator.undo()
    assert coordinator.project["formulas"] == lesson["formulas"]


def test_translate_missing_does_not_rerun_analysis(lesson, monkeypatch):
    coordinator = Coordinator()
    coordinator.project = lesson
    started = []
    monkeypatch.setattr(coordinator, "_start", lambda stage, options: started.append((stage, options)))
    coordinator.run_stage("translate_missing")
    assert started == [("translate", {"missing_only": True})]
    assert coordinator._pipeline == []

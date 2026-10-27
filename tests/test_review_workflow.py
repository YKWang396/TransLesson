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
    assert review_guidance(lesson, {"text_provider": "manual"})["action"] == "review"
    settings = dict(text_provider="deepseek", model="test", api_key="test-key", allow_text_api=False)
    assert review_guidance(lesson, settings)["action"] == "configure_translation"
    settings["allow_text_api"] = True
    assert review_guidance(lesson, settings)["action"] == "prepare_translation"
    local = dict(text_provider="offline", model="local", api_base="https://example.invalid/v1")
    assert review_guidance(lesson, local)["action"] == "configure_translation"
    local["api_base"] = "http://127.0.0.1:8080/v1"
    assert review_guidance(lesson, local)["action"] == "prepare_translation"
    local["api_base"] = "http://[broken"
    assert review_guidance(lesson, local)["action"] == "configure_translation"


def test_guide_blocks_pending_math_and_unsaved_work(lesson):
    lesson["segments"][0]["review"] = "needs_review"
    assert review_guidance(lesson, {})["action"] == "next_pending"
    lesson["segments"][0]["review"] = "confirmed"
    lesson["formulas"][0]["review"] = "needs_review"
    guide = review_guidance(lesson, {})
    assert guide["action"] == "review" and guide["pending_formulas"] == 1
    lesson["formulas"][0]["review"] = "confirmed"
    assert review_guidance(lesson, {})["action"] == "go_dub"
    assert not review_guidance(lesson, {}, busy=True)["enabled"]
    assert not review_guidance(lesson, {}, dirty=True)["enabled"]


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


def test_prepare_translation_runs_analysis_before_translation(lesson, monkeypatch):
    coordinator = Coordinator()
    coordinator.project = lesson
    started = []
    monkeypatch.setattr(coordinator, "_start", lambda stage, options: started.append(stage))
    coordinator.run_stage("prepare_translation")
    assert started == ["analyze"]
    assert coordinator._pipeline == ["translate"]

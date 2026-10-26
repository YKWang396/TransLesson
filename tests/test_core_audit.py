"""Cross-module regression checks for snapshot, budget and dependency integrity."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from mathbridge.application.artifacts import validate_render_plan
from mathbridge.application.coordinator import Coordinator
from mathbridge.application.subtitles import build_cues
from mathbridge.domain.project import changed_copy, file_hash, fingerprint, segment_fingerprint, validate_project
from mathbridge.math.analysis import analyze
from mathbridge.storage.project_store import ProjectStore


@pytest.fixture
def audit_project():
    return json.loads((Path(__file__).parents[1] / "examples/project.json").read_text(encoding="utf-8"))


def test_analyze_untranslated_formula_can_cross_worker_validation(audit_project):
    audit_project["formulas"] = []
    audit_project["notes"] = []
    audit_project["segments"][0].update(review="needs_review", formula_ids=[], target_text="", subtitle_plain="", tts_text="")
    result = analyze(audit_project)
    candidate = {**audit_project, **{k: result[k] for k in ("segments", "formulas", "glossary", "notes")}}
    validate_project(candidate)


def test_stale_translation_result_still_records_real_provider_cost(audit_project, tmp_path):
    coordinator = Coordinator()
    coordinator.project_dir = tmp_path
    coordinator.project = {**audit_project, "revision": 2}
    coordinator._command = {"stage": "translate", "request_id": "req-audit", "project_dir": str(tmp_path),
                            "project_revision": 1, "project": audit_project, "options": {}}
    coordinator._receive_result({"status": "succeeded", "input_revision": 1, "result": {
        "project": audit_project, "outputs": [], "artifacts": [],
        "metadata": {"usage": {"cost_cny": 0.25, "calls": 1}}}})
    runtime = ProjectStore(tmp_path).read("runtime.json", {})
    assert runtime["stages"]["translate"]["status"] == "stale"
    assert runtime.get("usage", {}).get("cost_cny") == pytest.approx(0.25)
    assert coordinator.project["revision"] == 2


def test_formula_target_reading_change_invalidates_referenced_tts(audit_project):
    segment = audit_project["segments"][0]
    before = segment_fingerprint(audit_project, segment, {})
    edited = changed_copy(audit_project, "formulas", "formula-1", {"spoken_target": "此处改成需要重新配音的新读法"})
    assert segment_fingerprint(edited, edited["segments"][0], {}) != before


def test_manifest_rejects_cycles_even_outside_final_export_dependencies(audit_project, tmp_path):
    artifacts = []
    for name in ("first", "second", "subtitles"):
        path = tmp_path / (name + ".txt")
        path.write_text(name, encoding="utf-8")
        artifacts.append({"id": name, "kind": "subtitles", "path": path.name,
            "sha256": file_hash(path), "input_fingerprint": fingerprint(audit_project),
            "input_revision": 1, "segment_ids": ["seg-1"], "depends_on": []})
    artifacts[0]["depends_on"] = [{"artifact_id": "second", "sha256": artifacts[1]["sha256"]}]
    artifacts[1]["depends_on"] = [{"artifact_id": "first", "sha256": artifacts[0]["sha256"]}]
    plan = {"schema_version": "1.0", "illustrative": False, "project_id": audit_project["project_id"],
            "project_revision": 1, "source_sha256": audit_project["source"]["sha256"],
            "cues": build_cues(audit_project), "artifacts": artifacts, "export_artifact_ids": ["subtitles"]}
    with pytest.raises(ValueError, match="CYCLE"):
        validate_render_plan(plan, audit_project, tmp_path)

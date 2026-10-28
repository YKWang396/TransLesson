"""Exercise the long-course review UI in an isolated copy; never call an API.

The default input is the user's local test project. Only project text and two
matching cached WAV files are copied into a unique repository QA directory.
No original project, media, model, credential, or settings file is modified.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import time
from unittest.mock import patch
import uuid

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from PySide6.QtCore import QCoreApplication, QPoint, QRect, Qt
from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import QApplication, QDialog, QPlainTextEdit, QScrollArea

from mathbridge.application.alignment import cached_alignment_report
from mathbridge.application.coordinator import Coordinator
from mathbridge.domain.project import segment_fingerprint, validate_project
from mathbridge.storage.project_store import ProjectStore
from mathbridge.ui.issue_dialogs import SpokenTextDialog
from mathbridge.ui.main_window import MainWindow


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path.home() / "Desktop" / "trans")
    args = parser.parse_args()
    source = args.source.resolve()
    original = (source / "project.json").read_bytes()
    original_hash = hashlib.sha256(original).hexdigest()
    project = json.loads(original)
    assert len(project["segments"]) >= 100, "Use a real long-course project for this performance check"
    settings = dict(voice="", speech_rate=0)
    report = cached_alignment_report(project, settings, source)
    assert report["suggested_merges"], "This QA needs at least one group of matching cached speech clips"
    group = next(group for group in report["suggested_merges"] if len(group) == 2)
    group_issues = [issue for issue in report["issues"] if issue["segment_id"] in group]
    assert group_issues
    repair_id = group_issues[0]["segment_id"]

    qa = ROOT / "tmp" / ("long-review-" + uuid.uuid4().hex[:10])
    qa.mkdir(parents=True)
    artifacts = ROOT / "artifacts"
    artifacts.mkdir(exist_ok=True)
    project["project_id"] = "qa-long-" + uuid.uuid4().hex[:12]
    store = ProjectStore(qa)
    store.save(project)
    for identifier in group:
        segment = next(s for s in project["segments"] if s["id"] == identifier)
        fp = segment_fingerprint(project, segment, settings)
        relative = Path("audio") / "tts" / f"{fp}.wav"
        target = qa / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source / relative, target)
        shutil.copy2((source / relative).with_suffix(".json"), target.with_suffix(".json"))

    app = QApplication([])
    app.setStyle("Fusion")
    fonts = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
    for name in ("msyh.ttc", "msyhbd.ttc", "consola.ttf"):
        if (fonts / name).is_file():
            QFontDatabase.addApplicationFont(str(fonts / name))
    coordinator = Coordinator()
    with patch("mathbridge.ui.main_window.load_settings", return_value={}):
        window = MainWindow(coordinator)
    coordinator.settings.update(settings, api_key="", allow_text_api=False, remember_api_key=False)
    requests, errors, timings = [], [], {}
    coordinator.run_stage = lambda *args, **kwargs: requests.append((args, kwargs))
    window._show_error = errors.append
    window.show()
    window.resize(1120, 760)

    def events():
        QCoreApplication.processEvents()

    def snapshot_items():
        return [window.segment_table.item(row, col) for row in range(window.segment_table.rowCount()) for col in range(5)]

    def assert_geometry(control):
        page = window.tabs.currentWidget()
        if isinstance(page, QScrollArea):
            page.ensureWidgetVisible(control, 8, 8)
            events()
        area = QRect(control.mapTo(window, QPoint(0, 0)), control.size())
        assert control.isVisible() and control.height() >= 24 and window.rect().contains(area), (control, area, window.rect())

    try:
        start = time.perf_counter()
        coordinator.open_project(str(qa))
        window.tabs.setCurrentIndex(1)
        events()
        timings["load_seconds"] = round(time.perf_counter() - start, 3)
        total = len(project["segments"])
        assert window.segment_table.rowCount() == total
        assert window.review_guide_state["action"] in {"go_dub", "repair_formulas"}, window.review_guide_state
        assert window.review_next_button.isEnabled()
        window.tabs.setCurrentIndex(2)
        events()
        assert window.tabs.currentIndex() == 2, "Hundreds of pending sentences must not block going to dubbing"
        window.tabs.setCurrentIndex(4)
        events()
        assert window.grab().save(str(artifacts / "ui-long-issues.png"))
        window.tabs.setCurrentIndex(1)
        events()

        # Save through the real editor. Reuse all table items and preserve scroll.
        first = coordinator.project["segments"][0]
        window._select_segment(first["id"])
        events()
        old_items = snapshot_items()
        window.segment_table.verticalScrollBar().setValue(140)
        scroll = window.segment_table.verticalScrollBar().value()
        new_subtitle = first["subtitle_plain"] + "（界面保存检查）"
        revision = coordinator.project["revision"]
        window.segment_fields["subtitle_plain"].setPlainText(new_subtitle)
        start = time.perf_counter()
        assert window._commit_all()
        events()
        timings["edit_save_seconds"] = round(time.perf_counter() - start, 3)
        assert coordinator.project["revision"] == revision + 1
        assert all(before is after for before, after in zip(old_items, snapshot_items()))
        assert window.segment_table.verticalScrollBar().value() == scroll

        formulas_before = copy.deepcopy(coordinator.project["formulas"])
        revision = coordinator.project["revision"]
        histories = len(list((qa / "history").glob("*.json")))
        start = time.perf_counter()
        window.bulk_confirm_button.click()
        events()
        timings["bulk_confirm_seconds"] = round(time.perf_counter() - start, 3)
        assert not errors, errors
        assert coordinator.project["revision"] == revision + 1
        assert len(list((qa / "history").glob("*.json"))) == histories + 1
        assert all(s["review"] == "confirmed" for s in coordinator.project["segments"])
        assert coordinator.project["formulas"] == formulas_before
        coordinator.undo()
        events()

        # A formula error must navigate to its actual owner before opening repair.
        window._reload_issues()
        formula_issue = next(issue for issue in window._problem_issues if issue.get("formula_id"))
        opened = []
        class FormulaProbe:
            def __init__(self, formula, parent):
                opened.append((formula["id"], parent._segment_id))
            def exec(self):
                return QDialog.DialogCode.Rejected
        with patch("mathbridge.ui.main_window.FormulaDialog", FormulaProbe):
            window._navigate_issue(formula_issue)
        events()
        assert opened == [(formula_issue["formula_id"], formula_issue["segment_id"])]
        assert window.advanced_toggle.isChecked()
        assert window.formula_list.currentItem().data(Qt.ItemDataRole.UserRole) == formula_issue["formula_id"]
        window.advanced_toggle.setChecked(False)

        # The former word-substring formula bug has an explicit, reversible fix.
        original_segments = copy.deepcopy(coordinator.project["segments"])
        result = coordinator.repair_obsolete_formulas()
        if result["removed"]:
            assert result["removed"] <= len(formulas_before)
            affected = set(result["segment_ids"])
            for before, after in zip(original_segments, coordinator.project["segments"]):
                if after["id"] in affected:
                    assert not after["target_text"] and not after["tts_text"]
                    assert after["source_text"] == before["source_text"]
                else:
                    assert after == before
            validate_project(coordinator.project)
            coordinator.undo()
            events()

        # Evidence preservation is an explicit choice, never math confirmation.
        result = coordinator.preserve_pending_formulas()
        assert result["preserved"] > 0
        assert all(formula["review"] != "confirmed" for formula in coordinator.project["formulas"]
                   if formula["id"] in {item["id"] for item in formulas_before if item["review"] == "needs_review"})
        formulas_before = copy.deepcopy(coordinator.project["formulas"])
        window.tabs.setCurrentIndex(1)
        events()
        assert window.review_guide_state["action"] == "go_dub", window.review_guide_state
        window.review_next_button.click()
        events()
        assert window.tabs.currentIndex() == 2

        # Explicit automatic regrouping changes one revision, preserves references.
        window.tabs.setCurrentIndex(2)
        window._reload_issues()
        events()
        assert window._alignment_report["suggested_merges"]
        revision = coordinator.project["revision"]
        before_count = len(coordinator.project["segments"])
        groups = window._alignment_report["suggested_merges"]
        window.regroup_dub_button.click()
        events()
        assert not errors, errors
        assert coordinator.project["revision"] == revision + 1
        assert len(coordinator.project["segments"]) == before_count - sum(len(g) - 1 for g in groups)
        validate_project(coordinator.project)
        assert coordinator.project["formulas"] == formulas_before
        coordinator.undo()
        events()

        # A TTS repair edits only the spoken reading through the focused dialog.
        window._reload_issues()
        spoken_issue = next(issue for issue in window._problem_issues if issue.get("segment_id") == repair_id and issue.get("code") == "TTS_TOO_LONG")
        before = copy.deepcopy(window._segment(repair_id))
        segment_index = next(index for index, segment in enumerate(coordinator.project["segments"]) if segment["id"] == repair_id)
        preview = SpokenTextDialog(before, spoken_issue,
            previous=coordinator.project["segments"][segment_index - 1] if segment_index else None,
            following=coordinator.project["segments"][segment_index + 1] if segment_index + 1 < total else None,
            replay=window._replay_segment, parent=window)
        preview.show()
        events()
        assert preview.grab().save(str(artifacts / "ui-long-tts-repair.png"))
        preview.reject()
        class SpokenProbe:
            def __init__(self, segment, issue, **kwargs):
                assert segment["id"] == repair_id
                assert issue["available_ms"] > 0
                self.action = "save"
                self.spoken_text = QPlainTextEdit("简短配音检查。")
            def exec(self):
                return QDialog.DialogCode.Accepted
        revision = coordinator.project["revision"]
        with patch("mathbridge.ui.main_window.SpokenTextDialog", SpokenProbe):
            window._navigate_issue(spoken_issue)
        events()
        after = window._segment(repair_id)
        assert after["tts_text"] == "简短配音检查。"
        assert after["subtitle_plain"] == before["subtitle_plain"]
        assert after["target_text"] == before["target_text"]
        assert coordinator.project["revision"] == revision + 1
        assert window._segment_id == repair_id
        assert not errors, errors

        window.tabs.setCurrentIndex(1)
        events()
        assert_geometry(window.bulk_confirm_button)
        assert_geometry(window.confirm_next_button)
        assert window.grab().save(str(artifacts / "ui-long-review.png"))
        window.tabs.setCurrentIndex(2)
        events()
        assert_geometry(window.alignment_speed)
        assert_geometry(window.alignment_gap)
        assert window.grab().save(str(artifacts / "ui-long-dub-settings.png"))
        assert_geometry(window.repair_dub_button)
        assert_geometry(window.regroup_dub_button)
        assert window.grab().save(str(artifacts / "ui-long-dub.png"))
        window.tabs.setCurrentIndex(4)
        events()
        assert window.grab().save(str(artifacts / "ui-long-export.png"))
        assert not requests, "Review QA must never start a worker or call any API"
        validate_project(store.load())
        assert hashlib.sha256((source / "project.json").read_bytes()).hexdigest() == original_hash
        result = dict(qa=str(qa), total=total, timings=timings, copied_tts=group,
                      screenshot_size=[window.width(), window.height()], api_calls=0,
                      original_project_unchanged=True)
        (artifacts / "long-review-report.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print("PASS: long-course review, reused table items, one-save bulk confirmation, located formula/TTS repairs and reversible regrouping")
        print(json.dumps(result, ensure_ascii=True))
        return 0
    finally:
        coordinator.busy = False
        window._segment_timer.stop()
        window._note_timer.stop()
        window._segment_dirty = window._note_dirty = False
        coordinator.set_editor_dirty(False)
        window.close()
        events()


if __name__ == "__main__":
    raise SystemExit(main())

"""Exercise the guided review UI with real local project saves.

Uses copied illustrative text fixtures, no real course media, ASR model, voice
model or API. Stage requests/settings dialogs are intercepted before they run.
"""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import sys
import uuid

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from PySide6.QtCore import QCoreApplication, QPoint, QRect
from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import QApplication, QPushButton

from mathbridge.application.coordinator import Coordinator
from mathbridge.storage.project_store import ProjectStore
from mathbridge.ui.main_window import MainWindow
from mathbridge.ui.review_workflow import review_guidance


def main():
    app = QApplication([])
    app.setStyle("Fusion")
    fonts = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
    for name in ("msyh.ttc", "msyhbd.ttc", "consola.ttf"):
        if (fonts / name).is_file():
            QFontDatabase.addApplicationFont(str(fonts / name))
    qa = ROOT / "tmp" / ("guided-review-" + uuid.uuid4().hex[:10])
    qa.mkdir(parents=True)
    sample = json.loads((ROOT / "examples/project.json").read_text(encoding="utf-8"))
    coordinator = Coordinator()
    window = MainWindow(coordinator)
    requests, settings_requests, errors = [], [], []
    coordinator.run_stage = lambda stage, options=None: requests.append((stage, options))
    window._settings = lambda *args, **kwargs: settings_requests.append((args, kwargs))
    window._show_error = errors.append
    coordinator.settings.update(text_provider="manual", allow_text_api=False, api_key="", whisper_model_path="")
    window.show()
    window.tabs.setCurrentIndex(1)

    def events():
        QCoreApplication.processEvents()

    def refresh():
        window._refresh_project(copy.deepcopy(coordinator.project))
        events()

    def verify_default_geometry():
        viewport = window.editor_scroll.viewport()
        buttons = [window.confirm_next_button]
        buttons.extend(control for control in window.editor_box.findChildren(QPushButton) if control.text() == "回听这一句")
        assert len(buttons) == 2
        for control in buttons:
            area = QRect(control.mapTo(viewport, QPoint(0, 0)), control.size())
            assert control.isVisible() and viewport.rect().contains(area), (
                control.text(), area, viewport.rect(), window.size())

    def open_case(name, *, empty=False, untranslated=False, pending=False):
        project = copy.deepcopy(sample)
        project["project_id"] = "qa-guided-" + uuid.uuid4().hex[:12]
        project["title"] = "校对操作检查 · 人工样例"
        if empty:
            for key in ("segments", "formulas", "glossary", "notes"):
                project[key] = []
        else:
            for segment in project["segments"]:
                if untranslated:
                    segment.update(target_text="", subtitle_plain="", tts_text="", review="needs_review")
                elif pending:
                    segment["review"] = "needs_review"
        directory = qa / name
        ProjectStore(directory).save(project)
        coordinator.open_project(str(directory))
        window.tabs.setCurrentIndex(1)
        events()
        return directory

    try:
        refresh()
        assert window.review_guide_state["action"] == "import_video"
        assert window.review_next_button.isEnabled()
        window.review_next_button.click()
        assert window.tabs.currentIndex() == 0
        window.tabs.setCurrentIndex(1)

        open_case("empty", empty=True)
        assert window.review_guide_state["action"] == "configure_asr"
        window.review_next_button.click()
        assert settings_requests, "Missing model must lead to an actionable settings entry"

        model = qa / "configuration-only-model"
        model.mkdir()
        for name in ("model.bin", "config.json", "tokenizer.json"):
            (model / name).write_text("QA path fixture only; never used for inference", encoding="utf-8")
        coordinator.settings["whisper_model_path"] = str(model)
        refresh()
        assert window.review_guide_state["action"] == "transcribe"
        window.review_next_button.click()
        assert requests[-1][0] == "transcribe"

        open_case("untranslated", untranslated=True)
        coordinator.settings.update(text_provider="deepseek", allow_text_api=False, api_key="")
        refresh()
        assert window.review_guide_state["action"] == "configure_translation"
        before = len(settings_requests)
        window.review_next_button.click()
        assert len(settings_requests) == before + 1
        coordinator.settings.update(allow_text_api=True, api_key="qa-not-a-real-api-key")
        refresh()
        assert window.review_guide_state["action"] == "prepare_translation"
        window.review_next_button.click()
        assert requests[-1][0] == "prepare_translation"

        coordinator.settings.update(text_provider="manual", allow_text_api=False, api_key="")
        folder = open_case("pending", pending=True)
        window._select_segment("seg-2")
        events()
        assert window.segment_fields["source_text"].isVisible()
        assert window.segment_fields["subtitle_plain"].isVisible()
        assert not window.segment_fields["target_text"].isVisible()
        assert not window.segment_fields["tts_text"].isVisible()
        assert not window.advanced_toggle.isChecked()

        new_subtitle = "下一步，我们来计算导数。"
        window.segment_fields["subtitle_plain"].setPlainText(new_subtitle)
        assert window._segment_dirty
        assert coordinator._editor_dirty
        assert window._commit_all()
        saved = ProjectStore(folder).load()["segments"][1]
        assert saved["subtitle_plain"] == saved["target_text"] == saved["tts_text"] == new_subtitle

        window._select_segment("seg-1")
        events()
        formula_subtitle = "考虑多项式：x² + 2x + 1。"
        window.segment_fields["subtitle_plain"].setPlainText(formula_subtitle)
        assert window._commit_all()
        saved = ProjectStore(folder).load()["segments"][0]
        assert saved["subtitle_plain"] == formula_subtitle
        assert "{{formula-1}}" in saved["target_text"]
        assert saved["target_text"] == "考虑多项式：{{formula-1}}。"
        assert coordinator.project["formulas"][0]["spoken_target"] in saved["tts_text"]
        assert "²" not in saved["tts_text"]

        revision = coordinator.project["revision"]
        window.segment_fields["subtitle_plain"].setPlainText("这个公式需要重新校对。")
        assert not window._commit_all()
        assert coordinator.project["revision"] == revision
        assert window.segment_fields["subtitle_plain"].toPlainText() == "这个公式需要重新校对。"
        assert window.advanced_toggle.isChecked(), "An ambiguous formula edit must reveal the repair tools"
        errors.clear()  # The deliberately invalid formula edit above is expected.
        window.segment_fields["subtitle_plain"].setPlainText(formula_subtitle)
        assert window._commit_all()
        window.advanced_toggle.setChecked(False)

        artifacts = ROOT / "artifacts"
        artifacts.mkdir(exist_ok=True)
        window.resize(1480, 940)
        events()
        verify_default_geometry()
        assert window.grab().save(str(artifacts / "ui-review-guided.png"))
        window.resize(1120, 760)
        events()
        verify_default_geometry()
        assert window.grab().save(str(artifacts / "ui-review-guided-compact.png"))
        window.advanced_toggle.setChecked(True)
        events()
        assert window.segment_fields["target_text"].isVisible()
        assert window.segment_fields["tts_text"].isVisible()
        assert window.grab().save(str(artifacts / "ui-review-guided-advanced.png"))
        window.advanced_toggle.setChecked(False)
        events()

        coordinator.busy = True
        coordinator.busy_changed.emit(True)
        events()
        assert not window.review_next_button.isEnabled()
        assert not window.confirm_next_button.isEnabled()
        coordinator.busy = False
        coordinator.busy_changed.emit(False)
        events()

        window._select_segment("seg-1")
        events()
        checked_reading = coordinator.project["segments"][0]["tts_text"]
        window.confirm_next_button.click()
        events()
        assert coordinator.project["segments"][0]["review"] == "confirmed"
        assert coordinator.project["segments"][0]["tts_text"] == checked_reading
        assert window._segment_id == "seg-2", "Confirmation must move to the next pending sentence"
        window.confirm_next_button.click()
        events()
        assert all(segment["review"] == "confirmed" for segment in coordinator.project["segments"])
        if window.tabs.currentIndex() == 1:
            assert window.review_guide_state["action"] == "go_dub"
            window.review_next_button.click()
        assert window.tabs.currentIndex() == 2, "The final review action must lead to the dubbing page"

        open_case("formula-review")
        coordinator.update_formula("formula-1", {"review": "needs_review"})
        refresh()
        assert window.review_guide_state["pending_formulas"] == 1
        assert window.review_guide_state["action"] == "review"
        window.review_next_button.click()
        events()
        assert window.advanced_toggle.isChecked(), "Unresolved formula guidance must expose the relevant tools"
        assert window.formula_list.count() > 0

        source_folder = open_case("source-correction", pending=True)
        window.advanced_toggle.setChecked(False)
        window._select_segment("seg-2")
        events()
        window.segment_fields["source_text"].setPlainText("Next, we will find its integral.")
        assert window._commit_all()
        corrected = ProjectStore(source_folder).load()["segments"][1]
        assert all(not corrected[field] for field in ("target_text", "subtitle_plain", "tts_text"))
        assert window.segment_fields["subtitle_plain"].toPlainText() == ""
        window.confirm_next_button.click()
        events()
        assert coordinator.project["segments"][1]["review"] == "needs_review"
        assert any(word in window.review_edit_notice.text() for word in ("原文", "填写", "缺少"))

        assert not errors, errors
        assert [request[0] for request in requests] == ["transcribe", "prepare_translation"]
        print("PASS: actionable guide, two visible editors, advanced tools, safe subtitle sync, confirmation flow, busy guard and real revision saves")
        print("Scope: illustrative text QA only; no ASR, API, real media or voice inference was run")
        print("QA project:", folder)
        return 0
    finally:
        coordinator.busy = False
        window.close()
        events()


if __name__ == "__main__":
    raise SystemExit(main())

"""Native UI integration smoke test; no models, API calls, or fake media.

Run from the repository root with: .venv/Scripts/python tools/smoke-ui.py
The sample is copied to a unique QA project before any edits are made.
"""
from __future__ import annotations

import json
import os
import sys
import uuid
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from PySide6.QtCore import QCoreApplication
from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import QApplication

from mathbridge.application.coordinator import Coordinator
from mathbridge.storage.project_store import ProjectStore
from mathbridge.ui.editors import FormulaDialog, TermDialog
from mathbridge.ui.main_window import MainWindow


def main():
    app = QApplication([])
    app.setStyle("Fusion")
    font_root = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
    for name in ("msyh.ttc", "msyhbd.ttc", "consola.ttf"):
        if (font_root / name).exists():
            QFontDatabase.addApplicationFont(str(font_root / name))
    folder = ROOT / "tmp" / ("ui-smoke-" + uuid.uuid4().hex[:10])
    project = json.loads((ROOT / "examples" / "project.json").read_text(encoding="utf-8"))
    store = ProjectStore(folder)
    store.save(project)
    store.write("runtime.json", {"stages": {}, "usage": {
        "cost_cny": 1.125, "uncertain_cost_cny": 0.25,
        "reserved_total_cny": 1.375, "calls": 3, "uncertain_requests": 1,
    }})
    coordinator = Coordinator()
    window = MainWindow(coordinator)
    coordinator.open_project(str(folder))
    if hasattr(coordinator, "usage"):
        assert "1.38" in window.usage_label.text()
        assert "0.25" in window.usage_label.text()
    window.show()
    window.tabs.setCurrentIndex(1)
    window._select_segment("seg-1")
    QCoreApplication.processEvents()
    assert window.segment_table.rowCount() == 2
    assert window._segment_id == "seg-1"
    window.segment_fields["subtitle_plain"].setPlainText("UI 校对集成验证：考虑多项式 x² + 2x + 1。")
    assert window._segment_dirty
    assert window._commit_all()
    assert coordinator.project["revision"] == 2
    assert "UI 校对集成验证" in store.load()["segments"][0]["subtitle_plain"]
    formula = FormulaDialog(coordinator.project["formulas"][0], window)
    formula.fields["spoken_target"].setPlainText("x 的平方加二 x 加一")
    coordinator.update_formula("formula-1", formula.changes())
    term = TermDialog(coordinator.project["glossary"][0], window)
    term.fields["context"].setText("UI 校对后的术语上下文")
    coordinator.update_term("term-derivative", term.changes())
    window.tabs.setCurrentIndex(3)
    window._load_note("note-1")
    window.note_heading.setText("UI 集成验证的课程小节")
    assert window._commit_all()
    assert coordinator.project["revision"] == 5
    assert store.load()["notes"][0]["heading"] == "UI 集成验证的课程小节"
    coordinator.undo()
    assert coordinator.project["revision"] == 6
    assert coordinator.project["notes"][0]["heading"] == "本节讨论对象"
    window.tabs.setCurrentIndex(1)
    window._select_segment("seg-1")
    QCoreApplication.processEvents()
    (ROOT / "artifacts").mkdir(exist_ok=True)
    window.grab().save(str(ROOT / "artifacts" / "ui-review.png"))
    window.resize(1120, 760)
    QCoreApplication.processEvents()
    window.grab().save(str(ROOT / "artifacts" / "ui-review-compact.png"))
    print(f"Compact UI size: {window.width()}x{window.height()}; editor: {window.editor_box.width()}x{window.editor_box.height()}")
    window.resize(1480, 940)
    window.tabs.setCurrentIndex(4)
    window._refresh_issues()
    QCoreApplication.processEvents()
    window.grab().save(str(ROOT / "artifacts" / "ui-export.png"))
    window.close()
    print("PASS: native UI, segment/formula/term/note saves, revision history, undo, export checks")
    print(f"QA project: {folder}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

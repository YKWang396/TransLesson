"""Check project context restoration and switching using isolated UI settings."""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import sys
import uuid

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/"src"))
from PySide6.QtCore import QSettings, QCoreApplication
from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import QApplication
from mathbridge.application.coordinator import Coordinator
from mathbridge.storage.project_store import ProjectStore
from mathbridge.ui.main_window import MainWindow
from mathbridge.ui.settings import SettingsDialog


def main():
    folder = ROOT/"tmp"/("project-flow-"+uuid.uuid4().hex[:10])
    folder.mkdir(parents=True)
    from ui_test_settings import isolate_settings
    isolate_settings(folder)
    app = QApplication([])
    app.setStyle("Fusion")
    for name in ("msyh.ttc", "msyhbd.ttc"):
        QFontDatabase.addApplicationFont(str(Path(os.environ.get("WINDIR","C:/Windows"))/"Fonts"/name))
    template = json.loads((ROOT/"examples/project.json").read_text(encoding="utf-8"))
    for name in ("one", "two"):
        root = folder/name
        project = {**template,"project_id":"project-"+name,"title":"测试课程 "+name}
        store = ProjectStore(root)
        store.save(project)
        media = root/"lesson.mp4"
        media.write_bytes(b"isolated source relocation QA")
        store.write("inputs/source.link.json",dict(absolute_path="Z:/unavailable-original/lesson.mp4",
            size=media.stat().st_size,sha256=hashlib.sha256(media.read_bytes()).hexdigest()))
    coordinator = Coordinator()
    window = MainWindow(coordinator)
    window.show()
    coordinator.open_project(folder/"one")
    QCoreApplication.processEvents()
    assert coordinator.source_path == str(folder/"one"/"lesson.mp4")
    assert window.current_project_box.isVisible() and not window.new_project_form.isVisible()
    assert str(folder/"one") in window.current_project_details.text()
    assert window._export_options().get("output_dir") is None
    window.export_directory.setText(str(folder/"elsewhere"))
    coordinator.open_project(folder/"two")
    assert not window.export_directory.text()
    assert coordinator.source_path == str(folder/"two"/"lesson.mp4")
    index = window.recent_projects.findData(str(folder/"one"))
    assert index > 0
    window._open_recent_project(index)
    assert coordinator.project["project_id"] == "project-one"
    window.tabs.setCurrentIndex(0)
    window.resize(1120,760)
    QCoreApplication.processEvents()
    (ROOT/"artifacts").mkdir(exist_ok=True)
    window.grab().save(str(ROOT/"artifacts/ui-project-context.png"))
    window.new_project_toggle.setChecked(True)
    assert window.new_project_form.isVisible()
    assert not window.media_path.text() and not window.directory_path.text()
    window.new_project_toggle.setChecked(False)
    assert not window.new_project_form.isVisible()
    dialog = SettingsDialog(coordinator.settings,window)
    tabs = dialog.findChild(__import__("PySide6.QtWidgets",fromlist=["QTabWidget"]).QTabWidget)
    tabs.setCurrentIndex(1)
    dialog.show()
    QCoreApplication.processEvents()
    assert "max_calls" not in dialog.fields
    assert not dialog.fields["input_price_per_million"].isVisible()
    assert dialog.fields["budget_cny"].isVisible()
    dialog.grab().save(str(ROOT/"artifacts/ui-ai-settings.png"))
    dialog.close()
    window.close()
    print("PASS: restored source and working directory, source relink fallback, recent-project switching, default internal export, isolated external destination, simplified budget")


if __name__ == "__main__":
    main()

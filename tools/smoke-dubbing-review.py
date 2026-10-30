"""Check the listening queue and refreshed audio status on an isolated QA course."""
from pathlib import Path
import argparse
import os
import sys

os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from PySide6.QtCore import QCoreApplication
from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import QApplication
from mathbridge.application.coordinator import Coordinator
from mathbridge.ui.main_window import MainWindow
from mathbridge.ui.issue_dialogs import DubbingReviewDialog
from ui_test_settings import isolate_settings

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project',type=Path,required=True)
    args=parser.parse_args();folder=args.project.resolve()
    assert folder.is_relative_to(ROOT/'tmp'),'Use an isolated QA copy'
    isolate_settings(folder)
    app=QApplication([]);app.setStyle('Fusion')
    for name in ('msyh.ttc','msyhbd.ttc'):
        QFontDatabase.addApplicationFont(str(Path(os.environ.get('WINDIR','C:/Windows'))/'Fonts'/name))
    coordinator=Coordinator();window=MainWindow(coordinator)
    coordinator.open_project(folder);window.show();window.resize(1120,760);window.tabs.setCurrentIndex(2)
    QCoreApplication.processEvents()
    assert window.auto_timing.isChecked()
    assert len(window._alignment_report['plans'])==407
    assert not window._alignment_report['issues']
    # A successful audio job does not change project revision; it must still
    # invalidate the old issue list rather than displaying the old 117 failures.
    window._alignment_report={'issues':[{'segment_id':'old-error'}]}
    window._finished('dub',{'outputs':[],'metadata':{}})
    assert not window._alignment_report['issues']
    window.grab().save(str(ROOT/'artifacts/ui-adaptive-dubbing.png'))
    calls=[]
    dialog=DubbingReviewDialog(coordinator.project,window._alignment_report,lambda *args:calls.append(args),lambda _issue:None,window)
    dialog.show();QCoreApplication.processEvents()
    assert dialog.listing.count()>100
    for kind in ('original','natural','matched'):dialog._listen(kind)
    assert [c[1] for c in calls]==['original','natural','matched']
    dialog.filter.setCurrentIndex(0)
    assert dialog.listing.count()==0 and not dialog.modify.isEnabled()
    dialog.filter.setCurrentIndex(2)
    assert dialog.listing.count()==407
    dialog.grab().save(str(ROOT/'artifacts/ui-dubbing-listening-queue.png'))
    dialog.close();window.close();QCoreApplication.processEvents()
    print('PASS: 407-clip review queue, original/natural/matched callbacks, filters, refreshed same-revision status and 1120x760 layout')
    return 0

if __name__=='__main__':raise SystemExit(main())
